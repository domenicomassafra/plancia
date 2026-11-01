"""Agent parsing rules, especially the ones that produced wrong numbers before."""

import builtins
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "fleetprobe", Path(__file__).resolve().parents[1] / "agent" / "fleetprobe.py"
)
fleetprobe = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(fleetprobe)


IOSTAT_OUTPUT = """      disk0       cpu    load average
    KB/t  tps  MB/s  us sy id   1m   5m   15m
   28.05    9  0.24  12  8 80  2.15 2.40 2.55
    0.00    0  0.00  33 12 55  2.15 2.40 2.55
"""


def test_darwin_cpu_uses_the_interval_row_not_the_boot_average():
    """The old dashboard read the first row and reported 89% on an idle Mac."""
    percent, reason = fleetprobe.parse_darwin_iostat_cpu(IOSTAT_OUTPUT)
    assert percent == 45.0  # 100 - 55 idle, from the second row
    assert reason is None


def test_darwin_cpu_reports_a_reason_when_iostat_output_is_unusable():
    percent, reason = fleetprobe.parse_darwin_iostat_cpu("garbage\n")
    assert percent is None
    assert reason


def test_darwin_cpu_uses_mach_tick_deltas_without_iostat(monkeypatch):
    collector = fleetprobe.DarwinCollector()
    samples = iter(
        [
            (100, 50, 850, 0),
            (120, 60, 920, 0),
        ]
    )
    monkeypatch.setattr(collector, "_cpu_ticks", lambda: next(samples))
    monkeypatch.setattr(fleetprobe.os, "getloadavg", lambda: (1.0, 2.0, 3.0))

    first = collector.cpu()
    second = collector.cpu()

    assert first["percent"] is None
    # Delta = user 20 + system 10 + idle 70 => 30% busy.
    assert second["percent"] == 30.0
    assert second["source"] == "Mach HOST_CPU_LOAD_INFO"


def test_darwin_cpu_keeps_python39_zip_compatibility(monkeypatch):
    """Managed Macs use Apple Python 3.9, where zip has no strict keyword."""
    collector = fleetprobe.DarwinCollector()
    samples = iter(
        [
            (100, 50, 850, 0),
            (120, 60, 920, 0),
        ]
    )

    def python39_zip(*iterables, **kwargs):
        assert kwargs == {}, "zip keyword arguments are not available on Python 3.9"
        return builtins.zip(*iterables)

    monkeypatch.setitem(fleetprobe.DarwinCollector.cpu.__globals__, "zip", python39_zip)
    monkeypatch.setattr(collector, "_cpu_ticks", lambda: next(samples))
    monkeypatch.setattr(fleetprobe.os, "getloadavg", lambda: (1.0, 2.0, 3.0))

    collector.cpu()
    second = collector.cpu()

    assert second["percent"] == 30.0
    assert second["reason"] is None


def test_darwin_slow_metric_cache_reuses_value(monkeypatch):
    collector = fleetprobe.DarwinCollector()
    calls = []

    def producer():
        calls.append(1)
        return "value"

    assert collector._cached("slow", 30.0, producer) == "value"
    assert collector._cached("slow", 30.0, producer) == "value"
    assert len(calls) == 1


def test_macos_launchagent_avoids_background_qos_but_keeps_low_priority():
    plist = (
        Path(__file__).resolve().parents[1]
        / "agent"
        / "install"
        / "com.controlcenter.fleetprobe.plist"
    ).read_text(encoding="utf-8")

    assert "<key>ProcessType</key>" not in plist
    assert "<key>LowPriorityIO</key>" in plist
    assert "<key>Nice</key>" in plist
    assert "ControlCenter/_local" not in plist
    assert "__DATA_DIR__" in plist
    assert "__STATE_DIR__" in plist
    assert "__TOKEN_FILE__" in plist


def test_macos_capacity_retries_a_transient_foundation_failure(monkeypatch):
    fleetprobe._MACOS_CAPACITY_CACHE.clear()
    responses = iter([(False, "timeout"), (True, "700\t200\n")])
    calls = []

    monkeypatch.setattr(fleetprobe.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setitem(
        fleetprobe._macos_available_capacity.__globals__,
        "_run",
        lambda command, **kwargs: (calls.append(command), next(responses))[1],
    )
    times = iter([100.0, 101.0])
    monkeypatch.setattr(fleetprobe.time, "time", lambda: next(times))

    result = fleetprobe._macos_available_capacity("/System/Volumes/Data")

    assert calls[0][0] == "/usr/bin/osascript"
    assert calls[1][0] == "/usr/bin/swift"
    assert result == {
        "available_bytes": 700,
        "opportunistic_bytes": 200,
        "observed_age_s": 0.0,
        "refresh_stale": False,
    }


def test_macos_capacity_prefers_fast_jxa_path(monkeypatch):
    fleetprobe._MACOS_CAPACITY_CACHE.clear()
    calls = []
    monkeypatch.setattr(fleetprobe.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setitem(
        fleetprobe._macos_available_capacity.__globals__,
        "_run",
        lambda command, **kwargs: (calls.append((command, kwargs)), (True, "700\t200\n"))[1],
    )
    times = iter([100.0, 101.0])
    monkeypatch.setattr(fleetprobe.time, "time", lambda: next(times))

    result = fleetprobe._macos_available_capacity("/System/Volumes/Data")

    assert len(calls) == 1
    assert calls[0][0][0] == "/usr/bin/osascript"
    assert calls[0][1]["timeout"] == 3.0
    assert result["available_bytes"] == 700
    assert result["opportunistic_bytes"] == 200


def test_macos_capacity_uses_only_bounded_marked_last_known_good(monkeypatch):
    mount = "/System/Volumes/Data"
    fleetprobe._MACOS_CAPACITY_CACHE.clear()
    fleetprobe._MACOS_CAPACITY_CACHE[mount] = (
        100.0,
        {"available_bytes": 700, "opportunistic_bytes": 200},
    )
    monkeypatch.setattr(fleetprobe.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setitem(fleetprobe._macos_available_capacity.__globals__, "_run", lambda *args, **kwargs: (False, "timeout"))
    monkeypatch.setattr(fleetprobe.time, "time", lambda: 161.0)

    result = fleetprobe._macos_available_capacity(mount)

    assert result == {
        "available_bytes": 700,
        "opportunistic_bytes": 200,
        "observed_age_s": 61.0,
        "refresh_stale": True,
    }

    monkeypatch.setattr(fleetprobe.time, "time", lambda: 221.0)
    assert fleetprobe._macos_available_capacity(mount) is None

def test_macos_capacity_failure_budget_backoff_and_recovery(monkeypatch):
    mount = "/System/Volumes/Data"
    fleetprobe._MACOS_CAPACITY_CACHE.clear()
    fleetprobe._MACOS_CAPACITY_NEXT_RETRY.clear()
    fleetprobe._MACOS_CAPACITY_CACHE[mount] = (
        940.0,
        {"available_bytes": 700, "opportunistic_bytes": 200},
    )
    clock = {"wall": 1000.0, "mono": 100.0}
    monkeypatch.setattr(fleetprobe.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(fleetprobe.time, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(fleetprobe.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    recovering = [False]

    def fake_run(command, *, timeout):
        calls.append((command[0], timeout))
        if recovering[0]:
            return True, "900\t300\n"
        clock["wall"] += timeout
        clock["mono"] += timeout
        return False, "timeout"

    monkeypatch.setitem(fleetprobe._macos_available_capacity.__globals__, "_run", fake_run)
    result = fleetprobe._macos_available_capacity(mount)
    assert result["refresh_stale"] is True
    assert result["observed_age_s"] == 64.0
    assert sum(timeout for _, timeout in calls) <= 4.0
    assert calls[0][0] == "/usr/bin/osascript"
    assert calls[1][0] == "/usr/bin/swift"
    assert len(calls) == 2

    clock["wall"] += 5
    clock["mono"] += 5
    assert fleetprobe._macos_available_capacity(mount)["observed_age_s"] == 69.0
    assert len(calls) == 2

    clock["wall"] += 10
    clock["mono"] += 10
    recovering[0] = True
    result = fleetprobe._macos_available_capacity(mount)
    assert result["available_bytes"] == 900
    assert result["observed_age_s"] == 0.0
    assert result["refresh_stale"] is False
    assert len(calls) == 3
    assert mount not in fleetprobe._MACOS_CAPACITY_NEXT_RETRY

def test_ps_table_skips_the_collector_processes():
    output = """  PID %CPU %MEM COMM
  101 42.0  3.1 /usr/bin/python3
  102 12.0  1.0 ps
  103  9.0  0.5 iostat
  104  4.0  0.2 Google Chrome
"""
    rows = fleetprobe._parse_ps_table(output)
    names = [row["name"] for row in rows]
    assert "ps" not in names and "iostat" not in names
    assert names[0] == "python3"
    assert rows[0]["cpu"] == 42.0


def test_percent_helper_never_exceeds_bounds():
    assert fleetprobe._percent(150, 100) == 100.0
    assert fleetprobe._percent(-5, 100) == 0.0
    assert fleetprobe._percent(10, 0) is None


def test_windows_battery_sentinel_runtime_is_dropped():
    collector = fleetprobe.WindowsCollector()
    collector._snapshot = {"bat_percent": 88, "bat_status": 1, "bat_runtime": 71582788}
    battery = collector.battery()
    assert battery["present"] is True
    assert battery["percent"] == 88.0
    assert battery["minutes_remaining"] is None
    assert battery["charging"] is False


def test_windows_subprocesses_do_not_create_console_windows(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return fleetprobe.subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(fleetprobe.os, "name", "nt")
    monkeypatch.setattr(fleetprobe.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(fleetprobe.subprocess, "run", fake_run)

    assert fleetprobe._run(["powershell.exe", "-NoProfile"]) == (True, "ok")
    assert captured["creationflags"] == fleetprobe.subprocess.CREATE_NO_WINDOW


def test_matebook_installer_uses_native_stop_on_battery_defaults():
    installer = (
        Path(__file__).resolve().parents[1] / "agent" / "install" / "install-matebook.ps1"
    ).read_text(encoding="utf-8")

    assert "-AllowStartIfOnBatteries" not in installer
    assert "-DontStopIfGoingOnBatteries" not in installer


def test_matebook_installer_uses_owner_s4u_without_password_or_system():
    installer = (
        Path(__file__).resolve().parents[1] / "agent" / "install" / "install-matebook.ps1"
    ).read_text(encoding="utf-8")

    assert "New-ScheduledTaskPrincipal" in installer
    assert "-LogonType S4U" in installer
    assert "-RunLevel Limited" in installer
    assert "-Principal $principal" in installer
    assert "-Password" not in installer
    assert "SYSTEM" not in installer


def test_matebook_installer_restarts_on_ac_without_five_second_sampling():
    installer = (
        Path(__file__).resolve().parents[1] / "agent" / "install" / "install-matebook.ps1"
    ).read_text(encoding="utf-8")

    assert "--interval 60" in installer
    assert "-RepetitionInterval (New-TimeSpan -Minutes 1)" in installer
    assert "-RepetitionDuration (New-TimeSpan -Days 3650)" in installer
    assert "-Trigger $triggers" in installer


def test_matebook_installer_resolves_pythonw_instead_of_hardcoding_python311():
    installer = (
        Path(__file__).resolve().parents[1] / "agent" / "install" / "install-matebook.ps1"
    ).read_text(encoding="utf-8")

    assert '[string]$Python = ""' in installer
    assert "Get-Command pythonw.exe -ErrorAction Stop" in installer
    assert "pythonw.exe not found: $Python" in installer
    assert "Python311" not in installer


def test_matebook_installer_projects_and_validates_canonical_fleetprobe_agent():
    installer = (
        Path(__file__).resolve().parents[1] / "agent" / "install" / "install-matebook.ps1"
    ).read_text(encoding="utf-8")

    assert '$sourceScript = Join-Path (Split-Path -Parent $PSScriptRoot) "fleetprobe.py"' in installer
    assert "fleetprobe source not found: $sourceScript" in installer
    assert 'New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script)' in installer
    assert "Copy-Item -LiteralPath $sourceScript -Destination $script -Force" in installer
    assert "GetFolderPath('LocalApplicationData')" in installer
    assert "ControlCenter\\_local" not in installer


def test_all_installers_ship_fleetprobe_collector_modules():
    install_dir = Path(__file__).resolve().parents[1] / "agent" / "install"
    module_names = ("fleetprobe_support.py", "fleetprobe_linux.py", "fleetprobe_darwin.py", "fleetprobe_windows.py")
    for installer_name in ("install-linux.sh", "install-macos.sh", "install-matebook.ps1"):
        installer = (install_dir / installer_name).read_text()
        for module in module_names:
            assert module in installer, f"{installer_name} omits {module}"


def test_matebook_installer_behavior_is_exercised_in_powershell():
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is required for the PowerShell-native installer behavior harness")

    root = Path(__file__).resolve().parents[1]
    harness = root / "tests" / "powershell" / "test_install_matebook_behavior.ps1"
    installer = root / "agent" / "install" / "install-matebook.ps1"
    completed = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(harness),
            "-Installer",
            str(installer),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "PASS installer behavior" in completed.stdout


def test_windows_thermal_rejects_impossible_readings():
    collector = fleetprobe.WindowsCollector()
    collector._snapshot = {"thermal_dk": 100}  # 10K - 273.15 => nonsense
    assert collector.thermal()["cpu_c"] is None
    collector._snapshot = {"thermal_dk": 3131}  # 313.1K => 39.95C
    assert collector.thermal()["cpu_c"] == 40.0


def test_windows_gpu_uses_real_snapshot_values():
    collector = fleetprobe.WindowsCollector()
    collector._snapshot = {"gpu_name": "GPU Test", "gpu_util": 37.5, "gpu_ram": 8_000_000_000}
    gpu = collector.gpu()
    assert gpu["name"] == "GPU Test"
    assert gpu["utilization_percent"] == 37.5
    assert gpu["memory_total_bytes"] == 8_000_000_000


def test_darwin_thermal_reports_real_pressure_instead_of_fake_celsius(monkeypatch):
    monkeypatch.setitem(
        fleetprobe.DarwinCollector.thermal.__globals__,
        "_run",
        lambda *args, **kwargs: (True, "**** Thermal pressure ****\nCurrent pressure level: Nominal\n"),
    )
    thermal = fleetprobe.DarwinCollector().thermal()
    assert thermal["cpu_c"] is None
    assert thermal["pressure_level"] == "Nominal"


def test_windows_process_cpu_needs_two_samples_to_become_a_rate():
    collector = fleetprobe.WindowsCollector()
    collector._snapshot = {
        "mem_total_kb": 16_000_000,
        "cores": 8,
        "procs": [{"pid": 1, "name": "app", "cpu": 100.0, "rss": 1_000_000}],
    }
    first = collector.processes()
    assert first[0]["cpu"] is None  # cumulative seconds are not a percentage
    collector._prev_proc_at = collector._prev_proc_at - 10
    collector._snapshot["procs"] = [{"pid": 1, "name": "app", "cpu": 104.0, "rss": 1_000_000}]
    second = collector.processes()
    assert second[0]["cpu"] == 5.0  # 4s of CPU over 10s on 8 cores


def test_sampler_marks_unknown_values_rather_than_guessing(monkeypatch):
    class BlindCollector:
        os_name = "test"

        def cpu(self):
            return {"percent": None, "reason": "no delta"}

        def memory(self):
            return {"total_bytes": None, "percent": None, "reason": "unreadable"}

        def thermal(self):
            return {"cpu_c": None, "sensors": [], "reason": "no sensor"}

        def gpu(self):
            return {"name": None, "utilization_percent": None, "reason": "no gpu counter"}

        def battery(self):
            return {"present": False, "percent": None}

        def network(self):
            return {"rx_bps": None, "tx_bps": None, "reason": "first sample"}

        def processes(self):
            return []

        def uptime_s(self):
            return None

        def os_version(self):
            return "test 1.0"

    monkeypatch.setattr(fleetprobe, "build_collector", lambda: BlindCollector())
    sampler = fleetprobe.Sampler("test-host")
    payload = sampler.sample()
    assert payload["cpu"]["percent"] is None
    assert payload["cpu"]["reason"] == "no delta"
    assert payload["thermal"]["cpu_c"] is None
    assert payload["gpu"]["utilization_percent"] is None
