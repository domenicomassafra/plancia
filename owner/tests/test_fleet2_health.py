"""Severity rules must never turn an unmeasured value into a healthy one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.fleet2.health import disk_severity, evaluate, memory_reading


def sample(**overrides):
    base = {
        "os": "linux",
        "cpu": {"percent": 12.0, "cores": 8, "source": "/proc/stat", "reason": None},
        "memory": {"percent": 40.0, "source": "/proc/meminfo"},
        "disk": [{"mount": "/", "percent": 30.0, "free_bytes": 100_000_000_000}],
        "thermal": {"cpu_c": 45.0, "sensors": [{"label": "k10temp", "celsius": 45.0}]},
        "battery": {"present": False, "percent": None},
    }
    base.update(overrides)
    return base


def test_unreachable_host_is_unknown_not_healthy():
    result = evaluate(None, reachable=False)
    assert result["severity"] == "unknown"
    assert "power state is unknown" in result["reason"]


def test_healthy_sample_reports_ok():
    result = evaluate(sample(), reachable=True)
    assert result["severity"] == "ok"
    assert result["headline"] == "Healthy"


def test_unmeasured_cpu_is_unknown_rather_than_zero():
    result = evaluate(
        sample(cpu={"percent": None, "cores": 8, "source": "x", "reason": "no delta"}),
        reachable=True,
    )
    assert result["severity"] == "unknown"
    cpu_signal = next(s for s in result["signals"] if s["key"] == "cpu")
    assert cpu_signal["value"] is None
    assert cpu_signal["severity"] == "unknown"


def test_critical_beats_unknown():
    result = evaluate(
        sample(
            cpu={"percent": None, "cores": 8, "source": "x", "reason": "no delta"},
            memory={"percent": 97.0, "source": "/proc/meminfo"},
        ),
        reachable=True,
    )
    assert result["severity"] == "crit"


def test_busy_cpu_and_gpu_are_not_mislabeled_as_hardware_critical():
    result = evaluate(
        sample(
            cpu={"percent": 100.0, "cores": 8, "source": "/proc/stat", "reason": None},
            gpu={"utilization_percent": 100.0, "temperature_c": None, "name": "test-gpu"},
        ),
        reachable=True,
    )
    assert result["severity"] == "warn"
    cpu = next(signal for signal in result["signals"] if signal["key"] == "cpu")
    gpu = next(signal for signal in result["signals"] if signal["key"] == "gpu")
    assert cpu["severity"] == "warn"
    assert gpu["severity"] == "ok"


def test_gpu_temperature_can_raise_health_independently_of_utilization():
    result = evaluate(
        sample(gpu={"utilization_percent": 20.0, "temperature_c": 96.0, "name": "test-gpu"}),
        reachable=True,
    )
    assert result["severity"] == "crit"
    assert next(signal for signal in result["signals"] if signal["key"] == "gpu-thermal")["severity"] == "crit"


def test_macos_uses_memory_pressure_instead_of_used_percentage():
    macos = sample(
        os="darwin",
        memory={"percent": 99.1, "pressure_used_percent": 13, "source": "vm_stat"},
    )
    value, label, basis = memory_reading(macos)
    assert value == 13
    assert label == "Memory pressure"
    assert "reclaimable" in basis
    assert evaluate(macos, reachable=True)["severity"] == "ok"


def test_macos_without_pressure_falls_back_to_used_percentage():
    macos = sample(os="darwin", memory={"percent": 70.0, "source": "vm_stat"})
    value, label, _ = memory_reading(macos)
    assert value == 70.0
    assert label == "Memory"


def test_thermal_signal_is_absent_when_the_host_cannot_measure_it():
    result = evaluate(
        sample(thermal={"cpu_c": None, "sensors": [], "reason": "needs root"}),
        reachable=True,
    )
    assert all(signal["key"] != "thermal" for signal in result["signals"])
    assert result["severity"] == "ok"


def test_macos_thermal_pressure_drives_health_without_fake_celsius():
    moderate = evaluate(
        sample(
            os="darwin",
            thermal={
                "cpu_c": None,
                "sensors": [],
                "pressure_level": "Moderate",
                "reason": "macOS thermal pressure",
            },
        ),
        reachable=True,
    )
    assert moderate["severity"] == "warn"
    assert moderate["headline"] == "Thermal pressure Moderate"
    signal = next(s for s in moderate["signals"] if s["key"] == "thermal-pressure")
    assert signal["value"] is None

    heavy = evaluate(
        sample(
            os="darwin",
            thermal={"cpu_c": None, "sensors": [], "pressure_level": "Heavy", "reason": None},
        ),
        reachable=True,
    )
    assert heavy["severity"] == "crit"


def test_cpu_temperature_in_low_eighties_is_informational_not_warning():
    result = evaluate(
        sample(thermal={"cpu_c": 82.0, "sensors": [{"label": "k10temp:Tctl", "celsius": 82.0}]}),
        reachable=True,
    )
    thermal = next(signal for signal in result["signals"] if signal["key"] == "thermal")
    assert thermal["severity"] == "ok"
    assert "warning ≥90°C" in thermal["detail"]


def test_explicit_thermal_limits_override_generic_defaults():
    result = evaluate(
        sample(
            thermal={
                "cpu_c": 86.0,
                "warn_c": 85.0,
                "crit_c": 95.0,
                "sensors": [{"label": "board:cpu", "celsius": 86.0}],
            }
        ),
        reachable=True,
    )
    assert next(signal for signal in result["signals"] if signal["key"] == "thermal")["severity"] == "warn"


def test_discharging_low_battery_warns_but_charging_does_not():
    low = evaluate(
        sample(battery={"present": True, "percent": 9, "charging": False}), reachable=True
    )
    charging = evaluate(
        sample(battery={"present": True, "percent": 9, "charging": True}), reachable=True
    )
    assert low["severity"] == "warn"
    assert charging["severity"] == "ok"


def test_disk_health_uses_writable_headroom_not_used_percent_alone():
    assert disk_severity({"percent": 94.0, "free_bytes": 30_000_000_000, "total_bytes": 494_000_000_000}) == "ok"
    assert disk_severity({"percent": 98.5, "free_bytes": 7_000_000_000, "total_bytes": 494_000_000_000}) == "crit"


def test_disk_health_prefers_platform_available_capacity_when_present():
    disk = {
        "percent": 96.0,
        "free_bytes": 20_000_000_000,
        "available_bytes": 70_000_000_000,
        "total_bytes": 494_000_000_000,
    }
    assert disk_severity(disk) == "ok"


def test_disk_signal_marks_cached_apfs_capacity_as_stale_evidence():
    result = evaluate(
        sample(
            os="darwin",
            disk=[{
                "mount": "/System/Volumes/Data",
                "percent": 95.0,
                "free_bytes": 22_000_000_000,
                "available_bytes": 70_000_000_000,
                "available_percent": 14.2,
                "available_stale": True,
                "available_age_s": 72.0,
                "total_bytes": 494_000_000_000,
            }],
        ),
        reachable=True,
    )
    disk = next(signal for signal in result["signals"] if signal["key"] == "disk")
    assert disk["value"] == 14.2
    assert disk["severity"] == "ok"
    assert "APFS cached 72s after refresh failure" in disk["detail"]


def test_disk_signal_reports_available_percent_not_used_percent():
    result = evaluate(
        sample(
            disk=[{
                "mount": "/",
                "percent": 95.0,
                "free_percent": 5.0,
                "free_bytes": 25_000_000_000,
                "total_bytes": 500_000_000_000,
            }]
        ),
        reachable=True,
    )
    disk = next(signal for signal in result["signals"] if signal["key"] == "disk")
    assert disk["value"] == 5.0
    assert disk["label"].startswith("Storage available")
