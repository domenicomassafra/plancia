"""Severity rules.

Two principles the previous dashboard broke:

1. a metric the host cannot measure is never a healthy zero - it is unknown;
2. macOS RAM is judged by memory pressure, not by used-percentage, because
   macOS deliberately fills free RAM with reclaimable file cache.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

OK = "ok"
WARN = "warn"
CRIT = "crit"
UNKNOWN = "unknown"

SEVERITY_ORDER = {OK: 0, UNKNOWN: 1, WARN: 2, CRIT: 3}


@dataclass(frozen=True, slots=True)
class Signal:
    key: str
    label: str
    severity: str
    value: Optional[float]
    unit: str
    detail: str


def _threshold(value: Optional[float], warn: float, crit: float, reason: str) -> str:
    if value is None:
        return UNKNOWN
    if value >= crit:
        return CRIT
    if value >= warn:
        return WARN
    return OK


def disk_severity(disk: Dict[str, Any]) -> str:
    """Judge storage by genuinely writable headroom, not used-percent alone.

    APFS can report a high container used percentage while macOS still has a
    meaningful amount of reclaimable space. Absolute free bytes plus free
    percentage is a better operator signal across the mixed-size fleet.
    """
    free = disk.get("available_bytes")
    if free is None:
        free = disk.get("free_bytes")
    total = disk.get("total_bytes")
    if free is None or not total:
        return _threshold(disk.get("percent"), 90, 97, "disk")
    free_gb = float(free) / 1_000_000_000
    free_percent = float(free) / float(total) * 100.0
    if free_gb < 5 or free_percent < 2:
        return CRIT
    if free_gb < 15 or free_percent < 5:
        return WARN
    return OK


def memory_reading(sample: Dict[str, Any]) -> tuple[Optional[float], str, str]:
    """Return (percent, label, basis) using the honest metric for the platform."""
    memory = sample.get("memory") or {}
    if sample.get("os") == "darwin" and memory.get("pressure_used_percent") is not None:
        return (
            float(memory["pressure_used_percent"]),
            "Memory pressure",
            "macOS memory pressure (used-percentage is inflated by reclaimable cache)",
        )
    percent = memory.get("percent")
    return (
        float(percent) if percent is not None else None,
        "Memory",
        memory.get("source") or "unknown source",
    )


def evaluate(sample: Optional[Dict[str, Any]], reachable: bool) -> Dict[str, Any]:
    """Compute per-host signals and the single worst severity."""
    if not reachable or not sample:
        return {
            "severity": UNKNOWN,
            "headline": "No sample",
            "signals": [],
            "reason": "The agent did not answer; power state is unknown.",
        }

    signals: List[Signal] = []

    cpu = sample.get("cpu") or {}
    cpu_percent = cpu.get("percent")
    signals.append(
        Signal(
            key="cpu",
            label="CPU",
            # Utilization is saturation, not a hardware fault. A busy machine
            # may legitimately sit at 100%; reserve warning for near-total
            # saturation and let thermal/memory/storage drive critical health.
            severity=_threshold(cpu_percent, 95, 101, "cpu"),
            value=cpu_percent,
            unit="%",
            detail=cpu.get("reason") or cpu.get("source") or "",
        )
    )

    mem_percent, mem_label, mem_basis = memory_reading(sample)
    signals.append(
        Signal(
            key="memory",
            label=mem_label,
            severity=_threshold(mem_percent, 80, 92, "memory"),
            value=mem_percent,
            unit="%",
            detail=mem_basis,
        )
    )

    disks = sample.get("disk") or []
    if disks:
        worst_disk = max(disks, key=lambda d: d.get("percent") or 0)
        available = worst_disk.get("available_bytes")
        available_percent = worst_disk.get("available_percent")
        if available_percent is None:
            available_percent = worst_disk.get("free_percent")
        if available_percent is None and worst_disk.get("percent") is not None:
            available_percent = max(0.0, 100.0 - float(worst_disk["percent"]))
        if available is not None:
            free_detail = f"{_gb(available)} GB available"
            physical = _gb(worst_disk.get("free_bytes"))
            if physical is not None:
                free_detail += f" · {physical} GB physical free"
            if worst_disk.get("available_stale") is True:
                age = worst_disk.get("available_age_s")
                if isinstance(age, (int, float)):
                    free_detail += f" · APFS cached {round(float(age))}s after refresh failure"
                else:
                    free_detail += " · APFS cached after refresh failure"
        else:
            free_detail = f"{_gb(worst_disk.get('free_bytes'))} GB free"
            if sample.get("os") == "darwin" and worst_disk.get("scope") == "apfs-container":
                free_detail += " · APFS physical free; reclaimable space unknown"
        signals.append(
            Signal(
                key="disk",
                label=f"Storage available {worst_disk.get('mount', '')}".strip(),
                severity=disk_severity(worst_disk),
                value=available_percent,
                unit="%",
                detail=free_detail,
            )
        )

    gpu = sample.get("gpu") or {}
    gpu_percent = gpu.get("utilization_percent")
    if gpu_percent is not None:
        signals.append(
            Signal(
                key="gpu",
                label="GPU",
                severity=OK,
                value=gpu_percent,
                unit="%",
                detail=(gpu.get("name") or gpu.get("source") or "") + " · utilization",
            )
        )
    if gpu.get("temperature_c") is not None:
        signals.append(
            Signal(
                key="gpu-thermal",
                label="GPU temperature",
                severity=_threshold(gpu["temperature_c"], 85, 95, "gpu thermal"),
                value=gpu["temperature_c"],
                unit="°C",
                detail=gpu.get("name") or gpu.get("source") or "",
            )
        )

    thermal = sample.get("thermal") or {}
    if thermal.get("cpu_c") is not None:
        # Host hardware does not always publish a critical trip point through
        # hwmon. Keep temperature useful without treating ordinary mobile/APU
        # operating temperatures in the low 80s as an incident. When an agent
        # eventually provides explicit limits they can override these defaults.
        warn_c = float(thermal.get("warn_c") or 90.0)
        crit_c = float(thermal.get("crit_c") or 100.0)
        signals.append(
            Signal(
                key="thermal",
                label="Temperature",
                severity=_threshold(thermal["cpu_c"], warn_c, crit_c, "thermal"),
                value=thermal["cpu_c"],
                unit="°C",
                detail=(
                    ", ".join(s["label"] for s in (thermal.get("sensors") or [])[:2])
                    + f" · warning ≥{warn_c:.0f}°C"
                ).strip(" ·"),
            )
        )
    elif thermal.get("pressure_level"):
        pressure = str(thermal["pressure_level"]).strip()
        normalized = pressure.casefold()
        if normalized in {"nominal", "normal"}:
            pressure_severity = OK
        elif normalized in {"fair", "moderate"}:
            pressure_severity = WARN
        elif normalized in {"serious", "heavy", "critical", "trapping"}:
            pressure_severity = CRIT
        else:
            pressure_severity = UNKNOWN
        signals.append(
            Signal(
                key="thermal-pressure",
                label="Thermal pressure",
                severity=pressure_severity,
                value=None,
                unit="",
                detail=pressure,
            )
        )

    battery = sample.get("battery") or {}
    if battery.get("present") and battery.get("percent") is not None:
        low = battery["percent"] <= 15 and not battery.get("charging")
        signals.append(
            Signal(
                key="battery",
                label="Battery",
                severity=WARN if low else OK,
                value=battery["percent"],
                unit="%",
                detail=battery.get("state") or "",
            )
        )

    worst = max(signals, key=lambda s: SEVERITY_ORDER[s.severity]) if signals else None
    severity = worst.severity if worst else UNKNOWN
    if severity == OK:
        headline = "Healthy"
    elif worst is not None and worst.key == "thermal-pressure":
        headline = f"Thermal pressure {worst.detail}"
    elif worst is not None and severity == UNKNOWN:
        headline = f"{worst.label} unmeasured"
    elif worst is not None:
        headline = f"{worst.label} {_format(worst.value)}{worst.unit}"
    else:
        headline = "No signals"

    return {
        "severity": severity,
        "headline": headline,
        "signals": [asdict(signal) for signal in signals],
        "reason": None,
    }


def _format(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}"


def _gb(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return f"{value / 1_000_000_000:.0f}"
