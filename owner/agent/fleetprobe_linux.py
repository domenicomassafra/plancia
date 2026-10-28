"""Linux FleetProbe collector; no third-party runtime dependencies."""
from __future__ import annotations
import glob
import os
import platform
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple
from fleetprobe_support import _run, _read_text, _percent, _parse_ps_table

class LinuxCollector:
    os_name = "linux"

    def __init__(self) -> None:
        self._prev_cpu: Optional[Tuple[int, int]] = None
        self._prev_net: Optional[Tuple[float, int, int]] = None
        self._gpu_name = self._detect_gpu_name()

    def _detect_gpu_name(self) -> Optional[str]:
        ok, out = _run(["lspci"], timeout=3.0)
        if not ok:
            return None
        for line in out.splitlines():
            lower = line.lower()
            if any(tag in lower for tag in ("vga compatible controller", "3d controller", "display controller")):
                return line.split(": ", 1)[-1].strip() or line.strip()
        return None

    def cpu(self) -> Dict[str, Any]:
        stat = _read_text("/proc/stat")
        percent: Optional[float] = None
        if stat:
            first = stat.splitlines()[0].split()
            if first and first[0] == "cpu":
                fields = [int(x) for x in first[1:] if x.isdigit()]
                idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
                total = sum(fields)
                if self._prev_cpu is not None:
                    d_total = total - self._prev_cpu[0]
                    d_idle = idle - self._prev_cpu[1]
                    if d_total > 0:
                        percent = round((1 - d_idle / d_total) * 100.0, 1)
                self._prev_cpu = (total, idle)
        load: List[Optional[float]] = [None, None, None]
        loadavg = _read_text("/proc/loadavg")
        if loadavg:
            parts = loadavg.split()
            if len(parts) >= 3:
                load = [float(parts[0]), float(parts[1]), float(parts[2])]
        return {
            "percent": percent,
            "cores": os.cpu_count(),
            "load1": load[0],
            "load5": load[1],
            "load15": load[2],
            "source": "/proc/stat",
            "reason": None if percent is not None else "first sample has no delta yet",
        }

    def memory(self) -> Dict[str, Any]:
        text = _read_text("/proc/meminfo")
        if not text:
            return {"total_bytes": None, "reason": "/proc/meminfo unreadable"}
        values: Dict[str, int] = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            number = rest.strip().split(" ")[0]
            if number.isdigit():
                values[key] = int(number) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        swap_total = values.get("SwapTotal", 0)
        swap_free = values.get("SwapFree", 0)
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": total - available,
            "percent": _percent(total - available, total),
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_total - swap_free,
            "source": "/proc/meminfo",
            "reason": None,
        }

    def thermal(self) -> Dict[str, Any]:
        sensors: List[Dict[str, Any]] = []
        base = "/sys/class/thermal"
        try:
            zones = sorted(z for z in os.listdir(base) if z.startswith("thermal_zone"))
        except OSError:
            zones = []
        for zone in zones:
            raw = _read_text(f"{base}/{zone}/temp")
            label = (_read_text(f"{base}/{zone}/type") or zone).strip()
            if raw and raw.strip().lstrip("-").isdigit():
                celsius = int(raw.strip()) / 1000.0
                if -20 < celsius < 150:
                    sensors.append({"label": label, "celsius": round(celsius, 1)})
        # AMD/Intel desktop boards often expose nothing under thermal_zone and
        # publish k10temp/coretemp through hwmon instead.
        sensors.extend(self._hwmon_sensors())
        cpu_c = None
        for sensor in sensors:
            if any(tag in sensor["label"].lower() for tag in ("x86_pkg", "coretemp", "cpu", "k10", "tctl")):
                cpu_c = sensor["celsius"]
                break
        if cpu_c is None and sensors:
            cpu_c = max(s["celsius"] for s in sensors)
        return {
            "cpu_c": cpu_c,
            "sensors": sensors,
            "reason": None if cpu_c is not None else "no usable thermal zone or hwmon sensor exposed",
        }

    def gpu(self) -> Dict[str, Any]:
        if shutil.which("nvidia-smi"):
            ok, out = _run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                timeout=4.0,
            )
            if ok and out.strip():
                fields = [part.strip() for part in out.splitlines()[0].split(",")]
                if len(fields) >= 5:
                    try:
                        used_mb = float(fields[2])
                        total_mb = float(fields[3])
                        return {
                            "name": fields[0],
                            "utilization_percent": float(fields[1]),
                            "memory_used_bytes": int(used_mb * 1024 * 1024),
                            "memory_total_bytes": int(total_mb * 1024 * 1024),
                            "temperature_c": float(fields[4]),
                            "source": "nvidia-smi",
                            "reason": None,
                        }
                    except ValueError:
                        pass

        busy_values: List[float] = []
        for path in glob.glob("/sys/class/drm/card[0-9]*/device/gpu_busy_percent"):
            raw = _read_text(path)
            if raw and raw.strip().replace(".", "", 1).isdigit():
                busy_values.append(float(raw.strip()))
        utilization = max(busy_values) if busy_values else None
        return {
            "name": self._gpu_name,
            "utilization_percent": round(utilization, 1) if utilization is not None else None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "temperature_c": None,
            "source": "sysfs gpu_busy_percent" if utilization is not None else "lspci/sysfs",
            "reason": None if utilization is not None else "GPU utilization counter not exposed by this driver",
        }

    def _hwmon_sensors(self) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        base = "/sys/class/hwmon"
        try:
            chips = sorted(os.listdir(base))
        except OSError:
            return found
        for chip in chips:
            chip_dir = f"{base}/{chip}"
            chip_name = (_read_text(f"{chip_dir}/name") or chip).strip()
            try:
                inputs = sorted(f for f in os.listdir(chip_dir) if re.fullmatch(r"temp\d+_input", f))
            except OSError:
                continue
            for entry in inputs:
                raw = _read_text(f"{chip_dir}/{entry}")
                if not raw or not raw.strip().lstrip("-").isdigit():
                    continue
                celsius = int(raw.strip()) / 1000.0
                if not -20 < celsius < 150:
                    continue
                label = (_read_text(f"{chip_dir}/{entry.replace('_input', '_label')}") or "").strip()
                found.append(
                    {
                        "label": f"{chip_name}:{label}" if label else chip_name,
                        "celsius": round(celsius, 1),
                    }
                )
        return found

    def battery(self) -> Dict[str, Any]:
        base = "/sys/class/power_supply"
        try:
            names = [n for n in os.listdir(base) if n.upper().startswith("BAT")]
        except OSError:
            names = []
        if not names:
            return {"present": False, "reason": "no battery device", "percent": None}
        name = names[0]
        capacity = (_read_text(f"{base}/{name}/capacity") or "").strip()
        status = (_read_text(f"{base}/{name}/status") or "").strip()
        return {
            "present": True,
            "percent": float(capacity) if capacity.replace(".", "").isdigit() else None,
            "charging": status.lower() in ("charging", "full"),
            "state": status or None,
            "minutes_remaining": None,
            "reason": None,
        }

    def uptime_s(self) -> Optional[float]:
        text = _read_text("/proc/uptime")
        if not text:
            return None
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return None

    def network(self) -> Dict[str, Any]:
        text = _read_text("/proc/net/dev")
        if not text:
            return {"rx_bps": None, "tx_bps": None, "reason": "/proc/net/dev unreadable"}
        rx_total = tx_total = 0
        for line in text.splitlines()[2:]:
            iface, _, rest = line.partition(":")
            iface = iface.strip()
            if iface == "lo" or iface.startswith(("docker", "veth", "br-")):
                continue
            fields = rest.split()
            if len(fields) >= 9:
                rx_total += int(fields[0])
                tx_total += int(fields[8])
        now = time.time()
        rx_bps = tx_bps = None
        if self._prev_net:
            elapsed = now - self._prev_net[0]
            if elapsed > 0:
                rx_bps = max(0.0, (rx_total - self._prev_net[1]) / elapsed)
                tx_bps = max(0.0, (tx_total - self._prev_net[2]) / elapsed)
        self._prev_net = (now, rx_total, tx_total)
        return {
            "rx_bps": round(rx_bps) if rx_bps is not None else None,
            "tx_bps": round(tx_bps) if tx_bps is not None else None,
            "reason": None if rx_bps is not None else "first sample has no delta yet",
        }

    def processes(self) -> List[Dict[str, Any]]:
        ok, out = _run(["ps", "-eo", "pid,pcpu,pmem,comm", "--sort=-pcpu"])
        return _parse_ps_table(out) if ok else []

    def os_version(self) -> str:
        text = _read_text("/etc/os-release") or ""
        match = re.search(r'PRETTY_NAME="([^"]+)"', text)
        return match.group(1) if match else platform.platform()
