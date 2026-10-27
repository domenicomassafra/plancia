"""Windows FleetProbe collector using bounded CIM PowerShell queries."""
from __future__ import annotations
import json
import os
import platform
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fleetprobe_support import _powershell, _percent

WINDOWS_SAMPLE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
$cpu = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor | Where-Object { $_.Name -eq '_Total' }).PercentProcessorTime
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$bat = Get-CimInstance Win32_Battery | Select-Object -First 1
$thermal = Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature | Select-Object -First 1
$gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1 Name, AdapterRAM
$gpuUtil = (Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | Where-Object { $_.Name -match 'engtype_3D' } | Measure-Object -Property UtilizationPercentage -Maximum).Maximum
$procs = Get-Process | Sort-Object CPU -Descending | Select-Object -First 12 Id, ProcessName, CPU, WorkingSet64
$netAdapters = Get-CimInstance Win32_PerfRawData_Tcpip_NetworkInterface | Where-Object { $_.Name -notmatch 'Loopback|isatap|Teredo' }
[pscustomobject]@{
  cpu_percent   = $cpu
  cores         = $env:NUMBER_OF_PROCESSORS
  mem_total_kb  = $os.TotalVisibleMemorySize
  mem_free_kb   = $os.FreePhysicalMemory
  swap_total_kb = $os.TotalVirtualMemorySize
  swap_free_kb  = $os.FreeVirtualMemory
  disk_total    = $disk.Size
  disk_free     = $disk.FreeSpace
  boot_time     = $os.LastBootUpTime.ToUniversalTime().ToString('o')
  bat_percent   = $bat.EstimatedChargeRemaining
  bat_runtime   = $bat.EstimatedRunTime
  bat_status    = $bat.BatteryStatus
  thermal_dk    = $thermal.CurrentTemperature
  gpu_name      = $gpu.Name
  gpu_ram       = $gpu.AdapterRAM
  gpu_util      = $gpuUtil
  os_caption    = $os.Caption
  net_rx        = ($netAdapters | Measure-Object -Property BytesReceivedPersec -Sum).Sum
  net_tx        = ($netAdapters | Measure-Object -Property BytesSentPersec -Sum).Sum
  procs         = @($procs | ForEach-Object { [pscustomobject]@{ pid = $_.Id; name = $_.ProcessName; cpu = $_.CPU; rss = $_.WorkingSet64 } })
} | ConvertTo-Json -Depth 4 -Compress
"""


class WindowsCollector:
    os_name = "windows"

    def __init__(self) -> None:
        self._snapshot: Dict[str, Any] = {}
        self._error: Optional[str] = None
        self._prev_net: Optional[Tuple[float, int, int]] = None
        self._prev_proc_cpu: Dict[int, float] = {}
        self._prev_proc_at: Optional[float] = None

    def refresh(self) -> None:
        ok, out = _powershell(WINDOWS_SAMPLE_SCRIPT, timeout=25.0)
        if not ok:
            self._error = out.strip()[:200]
            return
        try:
            self._snapshot = json.loads(out)
            self._error = None
        except json.JSONDecodeError as exc:
            self._error = f"CIM payload was not JSON: {exc}"

    def _get(self, key: str) -> Any:
        value = self._snapshot.get(key)
        return value if value not in ("", None) else None

    def cpu(self) -> Dict[str, Any]:
        percent = self._get("cpu_percent")
        cores = self._get("cores")
        return {
            "percent": round(float(percent), 1) if percent is not None else None,
            "cores": int(cores) if cores else os.cpu_count(),
            "load1": None,
            "load5": None,
            "load15": None,
            "source": "Win32_PerfFormattedData_PerfOS_Processor",
            "reason": self._error or (None if percent is not None else "counter unavailable"),
        }

    def memory(self) -> Dict[str, Any]:
        total_kb = self._get("mem_total_kb")
        free_kb = self._get("mem_free_kb")
        if total_kb is None or free_kb is None:
            return {"total_bytes": None, "reason": self._error or "Win32_OperatingSystem unavailable"}
        total = int(total_kb) * 1024
        available = int(free_kb) * 1024
        swap_total_kb = self._get("swap_total_kb")
        swap_free_kb = self._get("swap_free_kb")
        swap_total = int(swap_total_kb) * 1024 if swap_total_kb else None
        swap_used = (swap_total - int(swap_free_kb) * 1024) if (swap_total and swap_free_kb) else None
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": total - available,
            "percent": _percent(total - available, total),
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used,
            "source": "Win32_OperatingSystem",
            "reason": None,
        }

    def thermal(self) -> Dict[str, Any]:
        raw = self._get("thermal_dk")
        celsius = None
        if raw is not None:
            try:
                celsius = round(float(raw) / 10.0 - 273.15, 1)
            except (TypeError, ValueError):
                celsius = None
            if celsius is not None and not (-20 < celsius < 150):
                celsius = None
        return {
            "cpu_c": celsius,
            "sensors": [],
            "reason": None if celsius is not None else "MSAcpi_ThermalZoneTemperature not exposed by this firmware",
        }

    def gpu(self) -> Dict[str, Any]:
        name = self._get("gpu_name")
        raw_util = self._get("gpu_util")
        raw_ram = self._get("gpu_ram")
        try:
            utilization = round(float(raw_util), 1) if raw_util is not None else None
        except (TypeError, ValueError):
            utilization = None
        try:
            total_bytes = int(raw_ram) if raw_ram is not None else None
        except (TypeError, ValueError):
            total_bytes = None
        return {
            "name": str(name) if name is not None else None,
            "utilization_percent": utilization,
            "memory_used_bytes": None,
            "memory_total_bytes": total_bytes,
            "temperature_c": None,
            "source": "Win32_VideoController + GPUPerformanceCounters",
            "reason": None if utilization is not None else "Windows GPU performance counter unavailable",
        }

    def battery(self) -> Dict[str, Any]:
        percent = self._get("bat_percent")
        if percent is None:
            return {"present": False, "percent": None, "reason": "no battery reported"}
        status = self._get("bat_status")
        runtime = self._get("bat_runtime")
        # 2 = on AC; 71582788 is the sentinel for "unknown runtime".
        minutes = int(runtime) if runtime and int(runtime) < 6000 else None
        return {
            "present": True,
            "percent": float(percent),
            "charging": status == 2,
            "state": {1: "discharging", 2: "ac", 3: "fully charged"}.get(status, str(status)),
            "minutes_remaining": minutes,
            "reason": None,
        }

    def uptime_s(self) -> Optional[float]:
        boot = self._get("boot_time")
        if not boot:
            return None
        try:
            parsed = datetime.fromisoformat(boot.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - parsed).total_seconds()
        except ValueError:
            return None

    def network(self) -> Dict[str, Any]:
        rx = self._get("net_rx")
        tx = self._get("net_tx")
        if rx is None or tx is None:
            return {"rx_bps": None, "tx_bps": None, "reason": "network counters unavailable"}
        now = time.time()
        rx_bps = tx_bps = None
        if self._prev_net:
            elapsed = now - self._prev_net[0]
            if elapsed > 0:
                rx_bps = max(0.0, (int(rx) - self._prev_net[1]) / elapsed)
                tx_bps = max(0.0, (int(tx) - self._prev_net[2]) / elapsed)
        self._prev_net = (now, int(rx), int(tx))
        return {
            "rx_bps": round(rx_bps) if rx_bps is not None else None,
            "tx_bps": round(tx_bps) if tx_bps is not None else None,
            "reason": None if rx_bps is not None else "first sample has no delta yet",
        }

    def processes(self) -> List[Dict[str, Any]]:
        raw = self._snapshot.get("procs") or []
        total_mem = self._get("mem_total_kb")
        total_bytes = int(total_mem) * 1024 if total_mem else 0
        cores = int(self._get("cores") or os.cpu_count() or 1)
        now = time.time()
        elapsed = now - self._prev_proc_at if self._prev_proc_at else None
        current: Dict[int, float] = {}
        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            pid = item.get("pid")
            rss = item.get("rss") or 0
            cpu_seconds = float(item["cpu"]) if item.get("cpu") else None
            # Get-Process reports cumulative CPU seconds; a rate needs a delta.
            cpu_percent = None
            if pid is not None and cpu_seconds is not None:
                current[pid] = cpu_seconds
                previous = self._prev_proc_cpu.get(pid)
                if previous is not None and elapsed and elapsed > 0:
                    cpu_percent = round(
                        max(0.0, (cpu_seconds - previous) / elapsed / cores * 100.0), 1
                    )
            out.append(
                {
                    "pid": pid,
                    "name": item.get("name"),
                    "cpu": cpu_percent,
                    "cpu_seconds": round(cpu_seconds, 1) if cpu_seconds is not None else None,
                    "mem": _percent(rss, total_bytes) if total_bytes else None,
                }
            )
        self._prev_proc_cpu = current
        self._prev_proc_at = now
        out.sort(key=lambda row: (row["cpu"] is None, -(row["cpu"] or 0)))
        return out

    def os_version(self) -> str:
        return str(self._get("os_caption") or platform.platform())
