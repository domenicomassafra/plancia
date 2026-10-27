"""macOS FleetProbe collector and interval-based CPU parser."""
from __future__ import annotations
import ctypes
import json
import os
import platform
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from fleetprobe_support import _run, _percent, _parse_ps_table

def parse_darwin_iostat_cpu(output: str) -> Tuple[Optional[float], Optional[str]]:
    """Read busy CPU from the last iostat row.

    iostat's first data row is the since-boot average; only the following rows
    cover the requested interval. Reading the first row is what made the old
    dashboard report 89% CPU on an idle Mac.
    """
    lines = [line for line in output.splitlines() if line.strip()]
    header_idx = next(
        (i for i, line in enumerate(lines) if " id" in line and "us" in line), None
    )
    if header_idx is None:
        return None, "iostat header without an id column"
    columns = lines[header_idx].split()
    try:
        idle_col = len(columns) - 1 - columns[::-1].index("id")
    except ValueError:
        return None, "iostat header without an id column"
    rows = [line.split() for line in lines[header_idx + 1 :]]
    rows = [row for row in rows if len(row) > idle_col]
    if not rows:
        return None, "iostat produced no data row"
    try:
        idle = float(rows[-1][idle_col])
    except ValueError:
        return None, "iostat idle column not numeric"
    return round(max(0.0, min(100.0, 100.0 - idle)), 1), None


class DarwinCollector:
    os_name = "darwin"

    def __init__(self) -> None:
        self._page_size = 4096
        ok, out = _run(["sysctl", "-n", "hw.pagesize"])
        if ok and out.strip().isdigit():
            self._page_size = int(out.strip())
        self._prev_net: Optional[Tuple[float, int, int]] = None
        self._prev_cpu_ticks: Optional[Tuple[int, int, int, int]] = None
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._libsystem = None
        try:
            self._libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            self._libsystem.mach_host_self.restype = ctypes.c_uint
            self._libsystem.host_statistics.argtypes = [
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_uint),
            ]
            self._libsystem.host_statistics.restype = ctypes.c_int
        except OSError:
            pass

    def _cached(self, key: str, ttl: float, producer: Callable[[], Any]) -> Any:
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
        value = producer()
        self._cache[key] = (now, value)
        return value

    def _cpu_ticks(self) -> Optional[Tuple[int, int, int, int]]:
        # HOST_CPU_LOAD_INFO = 3; CPU_STATE_MAX = 4. These are the public Mach
        # host statistics interfaces from <mach/host_info.h>/<mach/machine.h>.
        if self._libsystem is None:
            return None
        ticks = (ctypes.c_uint * 4)()
        count = ctypes.c_uint(4)
        rc = self._libsystem.host_statistics(
            self._libsystem.mach_host_self(),
            3,
            ctypes.cast(ticks, ctypes.POINTER(ctypes.c_int)),
            ctypes.byref(count),
        )
        if rc != 0 or count.value < 4:
            return None
        return tuple(int(value) for value in ticks)

    def cpu(self) -> Dict[str, Any]:
        current = self._cpu_ticks()
        percent = None
        reason = "first Mach CPU sample has no delta yet"
        if current is None:
            reason = "Mach HOST_CPU_LOAD_INFO unavailable"
        elif self._prev_cpu_ticks is not None:
            # Managed Macs still run the Apple system Python 3.9, which predates
            # ``zip(strict=...)``. Both tuples are fixed-width Mach structures,
            # so truncation cannot hide a variable-length input here.
            deltas = [
                max(0, now - old)
                for now, old in zip(current, self._prev_cpu_ticks)  # noqa: B905
            ]
            total = sum(deltas)
            if total > 0:
                idle = deltas[2]  # CPU_STATE_IDLE
                percent = round((1.0 - idle / total) * 100.0, 1)
                reason = None
        self._prev_cpu_ticks = current

        try:
            load = [round(float(value), 2) for value in os.getloadavg()]
        except OSError:
            load = [None, None, None]
        return {
            "percent": percent,
            "cores": os.cpu_count(),
            "load1": load[0],
            "load5": load[1],
            "load15": load[2],
            "source": "Mach HOST_CPU_LOAD_INFO",
            "reason": reason,
        }

    def memory(self) -> Dict[str, Any]:
        ok_total, total_out = self._cached(
            "mem_total",
            3600.0,
            lambda: _run(["sysctl", "-n", "hw.memsize"]),
        )
        total = int(total_out.strip()) if ok_total and total_out.strip().isdigit() else 0
        ok_vm, vm_out = _run(["vm_stat"])
        if not (total and ok_vm):
            return {"total_bytes": total or None, "reason": "vm_stat or hw.memsize unavailable"}
        pages: Dict[str, int] = {}
        for line in vm_out.splitlines():
            match = re.match(r'"?Pages ([^:"]+)"?:\s+(\d+)', line.strip())
            if match:
                pages[match.group(1).strip()] = int(match.group(2))
        free = (pages.get("free", 0) + pages.get("speculative", 0)) * self._page_size
        # macOS keeps purgeable file cache as reclaimable memory.
        purgeable = pages.get("purgeable", 0) * self._page_size
        available = free + purgeable
        swap_total = swap_used = None
        ok_swap, swap_out = self._cached(
            "swap",
            30.0,
            lambda: _run(["sysctl", "-n", "vm.swapusage"]),
        )
        if ok_swap:
            total_match = re.search(r"total = ([\d.]+)M", swap_out)
            used_match = re.search(r"used = ([\d.]+)M", swap_out)
            if total_match:
                swap_total = int(float(total_match.group(1)) * 1024 * 1024)
            if used_match:
                swap_used = int(float(used_match.group(1)) * 1024 * 1024)
        # On macOS most RAM is legitimately held as file cache, so a raw
        # used-percentage reads alarming while the machine is perfectly healthy.
        # memory_pressure is the signal the OS itself acts on.
        pressure_free = None
        ok_pressure, pressure_out = self._cached(
            "memory_pressure",
            30.0,
            lambda: _run(["memory_pressure", "-Q"], timeout=6.0),
        )
        if ok_pressure:
            match = re.search(r"System-wide memory free percentage:\s*(\d+)%", pressure_out)
            if match:
                pressure_free = int(match.group(1))
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": total - available,
            "percent": _percent(total - available, total),
            "pressure_used_percent": (100 - pressure_free) if pressure_free is not None else None,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used,
            "source": "vm_stat + sysctl + memory_pressure",
            "reason": None,
        }

    def thermal(self) -> Dict[str, Any]:
        # Apple Silicon does not expose honest Celsius values. powermetrics does
        # expose the OS thermal-pressure state, which is more useful than a fake
        # temperature and works when this exact command is sudo-allowlisted.
        ok, out = self._cached(
            "thermal",
            60.0,
            lambda: _run(
                ["sudo", "-n", "/usr/bin/powermetrics", "-n", "1", "-i", "200", "-s", "thermal"],
                timeout=5.0,
            ),
        )
        match = re.search(r"Current pressure level:\s*([A-Za-z ]+)", out) if ok else None
        pressure = match.group(1).strip() if match else None
        return {
            "cpu_c": None,
            "sensors": [],
            "pressure_level": pressure,
            "reason": (
                "macOS espone la pressione termica, non una temperatura Celsius affidabile"
                if pressure
                else "powermetrics thermal non disponibile senza sudo allowlist"
            ),
        }

    def gpu(self) -> Dict[str, Any]:
        def identity() -> Optional[str]:
            ok, out = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=8.0)
            if not ok:
                return None
            try:
                payload = json.loads(out)
            except json.JSONDecodeError:
                return None
            entries = payload.get("SPDisplaysDataType") or []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("sppci_model") or entry.get("_name")
                if name:
                    return str(name)
            return None

        name = self._cached("gpu_identity", 3600.0, identity)
        return {
            "name": name,
            "utilization_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "temperature_c": None,
            "source": "system_profiler SPDisplaysDataType",
            "reason": "macOS non espone un contatore GPU affidabile a un processo non privilegiato",
        }

    def battery(self) -> Dict[str, Any]:
        ok, out = self._cached("battery", 30.0, lambda: _run(["pmset", "-g", "batt"]))
        if not ok or "InternalBattery" not in out:
            return {"present": False, "percent": None, "reason": "no internal battery"}
        percent_match = re.search(r"(\d+)%", out)
        state_match = re.search(r";\s*([a-z ]+);", out)
        remaining_match = re.search(r"(\d+):(\d\d) remaining", out)
        minutes = None
        if remaining_match:
            minutes = int(remaining_match.group(1)) * 60 + int(remaining_match.group(2))
        state = state_match.group(1).strip() if state_match else None
        return {
            "present": True,
            "percent": float(percent_match.group(1)) if percent_match else None,
            "charging": state in ("charging", "AC attached", "charged") if state else None,
            "state": state,
            "minutes_remaining": minutes,
            "reason": None,
        }

    def uptime_s(self) -> Optional[float]:
        ok, out = self._cached(
            "boottime",
            3600.0,
            lambda: _run(["sysctl", "-n", "kern.boottime"]),
        )
        if not ok:
            return None
        match = re.search(r"sec = (\d+)", out)
        return time.time() - int(match.group(1)) if match else None

    def network(self) -> Dict[str, Any]:
        ok, out = _run(["netstat", "-ib"])
        if not ok:
            return {"rx_bps": None, "tx_bps": None, "reason": "netstat unavailable"}
        rx_total = tx_total = 0
        seen = set()
        for line in out.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[0] in seen or fields[0].startswith("lo"):
                continue
            try:
                rx_total += int(fields[6])
                tx_total += int(fields[9])
                seen.add(fields[0])
            except (ValueError, IndexError):
                continue
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
        ok, out = self._cached(
            "processes",
            15.0,
            lambda: _run(["ps", "-Ao", "pid,pcpu,pmem,comm", "-r"]),
        )
        return _parse_ps_table(out)[:12] if ok else []

    def os_version(self) -> str:
        ok, out = self._cached(
            "os_version",
            3600.0,
            lambda: _run(["sw_vers", "-productVersion"]),
        )
        name = "macOS " + out.strip() if ok else platform.platform()
        return name
