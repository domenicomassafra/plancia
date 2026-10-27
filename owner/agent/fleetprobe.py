#!/usr/bin/env python3
"""fleetprobe - stdlib-only telemetry agent for the Signal Deck fleet.

Design constraints:

- standard library only, so it runs on the system Python of every fleet host
  (Ubuntu/Linux, macOS and Windows) without a venv or a package install;
- a background sampler owns all measurement, so an HTTP request never blocks on
  a 1s CPU interval and the fleet keeps a fresh sample even with no reader;
- anything the host cannot actually measure is reported as ``None`` together
  with a reason. The agent never estimates a value it did not read.

Run:

    fleetprobe.py --once                    # one JSON sample on stdout
    fleetprobe.py --serve --host-id minipc  # HTTP agent on :9631
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import platform
import shutil
import socket
import subprocess as subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

# Standalone entry point: sibling stdlib-only collectors ship with each installer.
_probe_dir = os.path.dirname(os.path.abspath(__file__))
if _probe_dir not in sys.path:
    sys.path.insert(0, _probe_dir)
from fleetprobe_support import _run, _read_text, _percent
from fleetprobe_support import _powershell as _powershell, _parse_ps_table as _parse_ps_table, Unmeasured as Unmeasured
from fleetprobe_linux import LinuxCollector
from fleetprobe_darwin import DarwinCollector, parse_darwin_iostat_cpu as parse_darwin_iostat_cpu
from fleetprobe_windows import WindowsCollector

AGENT_VERSION = "2.1.4"
SCHEMA_VERSION = 2
DEFAULT_PORT = 9631
DEFAULT_INTERVAL = 5.0
HISTORY_POINTS = 60  # 5 minutes; durable history lives in MiniPC SQLite.


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _disk_usage() -> List[Dict[str, Any]]:
    system = platform.system().lower()
    if os.name == "nt":
        mounts = ["C:\\"]
    elif system == "darwin" and os.path.isdir("/System/Volumes/Data"):
        mounts = ["/System/Volumes/Data"]
    else:
        mounts = ["/"]
    out: List[Dict[str, Any]] = []
    for mount in mounts:
        try:
            stat = os.statvfs(mount) if hasattr(os, "statvfs") else None
        except OSError:
            stat = None
        if stat is not None:
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            entry: Dict[str, Any] = {
                "mount": mount,
                "total_bytes": total,
                "free_bytes": free,
                "used_bytes": total - free,
                "percent": _percent(total - free, total),
                "free_percent": _percent(free, total),
                "scope": "apfs-container" if system == "darwin" else "filesystem",
                "source": "statvfs",
            }
            if system == "darwin":
                capacity = _macos_available_capacity(mount)
                if capacity is not None:
                    available = capacity.get("available_bytes")
                    opportunistic = capacity.get("opportunistic_bytes")
                    if isinstance(available, int) and available >= 0:
                        entry["available_bytes"] = available
                        entry["available_percent"] = _percent(available, total)
                        entry["reclaimable_estimate_bytes"] = max(0, available - free)
                        entry["available_basis"] = (
                            "important-usage-cached"
                            if capacity.get("refresh_stale") is True
                            else "important-usage"
                        )
                        age = capacity.get("observed_age_s")
                        if isinstance(age, (int, float)):
                            entry["available_age_s"] = round(max(0.0, float(age)), 1)
                        entry["available_stale"] = capacity.get("refresh_stale") is True
                    if isinstance(opportunistic, int) and opportunistic >= 0:
                        entry["opportunistic_bytes"] = opportunistic
                    entry["source"] = (
                        "statvfs + Foundation URLResourceValues (cached after refresh failure)"
                        if capacity.get("refresh_stale") is True
                        else "statvfs + Foundation URLResourceValues"
                    )
            out.append(entry)
    return out


_MACOS_CAPACITY_CACHE: Dict[str, Tuple[float, Dict[str, int]]] = {}
_MACOS_CAPACITY_TTL_S = 60.0
_MACOS_CAPACITY_MAX_STALE_S = 120.0
_MACOS_CAPACITY_RETRIES = 2


def _macos_available_capacity(mount: str) -> Optional[Dict[str, Any]]:
    """Return APFS capacity macOS considers available for important usage.

    Prefer Foundation through JXA/osascript. ``swift -e`` compiles a temporary
    program and can be heavily throttled by a background LaunchAgent, making a
    cheap capacity lookup exceed the sampler timeout. Swift remains a fallback
    for hosts where JXA is unavailable. Any last-known-good fallback is
    explicitly marked with its observation age so callers never project stale
    evidence as a fresh measurement.
    """
    cached = _MACOS_CAPACITY_CACHE.get(mount)
    now = time.time()
    if cached and now - cached[0] < _MACOS_CAPACITY_TTL_S:
        return {
            **cached[1],
            "observed_age_s": round(max(0.0, now - cached[0]), 1),
            "refresh_stale": False,
        }
    osascript = shutil.which("osascript")
    swift = shutil.which("swift")
    if not osascript and not swift:
        if cached and now - cached[0] <= _MACOS_CAPACITY_MAX_STALE_S:
            return {
                **cached[1],
                "observed_age_s": round(max(0.0, now - cached[0]), 1),
                "refresh_stale": True,
            }
        return None
    commands: List[Tuple[List[str], float]] = []
    if osascript:
        jxa_mount = json.dumps(mount)
        jxa_script = f'''ObjC.import("Foundation");
var u = $.NSURL.fileURLWithPath({jxa_mount});
var important = Ref(), opportunistic = Ref(), error = Ref();
if (!u.getResourceValueForKeyError(important, "NSURLVolumeAvailableCapacityForImportantUsageKey", error)) {{
  throw Error("important-usage capacity unavailable");
}}
if (!u.getResourceValueForKeyError(opportunistic, "NSURLVolumeAvailableCapacityForOpportunisticUsageKey", error)) {{
  throw Error("opportunistic capacity unavailable");
}}
ObjC.unwrap(important[0]).toString() + "\\t" + ObjC.unwrap(opportunistic[0]).toString();
'''
        commands.append(([osascript, "-l", "JavaScript", "-e", jxa_script], 3.0))

    escaped = mount.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''import Foundation
let u = URL(fileURLWithPath: "{escaped}")
let keys: Set<URLResourceKey> = [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityForOpportunisticUsageKey]
let values = try u.resourceValues(forKeys: keys)
print("\\(values.volumeAvailableCapacityForImportantUsage ?? -1)\\t\\(values.volumeAvailableCapacityForOpportunisticUsage ?? -1)")
'''
    if swift:
        commands.extend(
            ([swift, "-e", script], 8.0)
            for _attempt in range(_MACOS_CAPACITY_RETRIES)
        )

    for command, timeout in commands:
        ok, output = _run(command, timeout=timeout)
        if not ok:
            continue
        fields = output.strip().split("\t")
        if len(fields) != 2:
            continue
        try:
            result = {
                "available_bytes": int(fields[0]),
                "opportunistic_bytes": int(fields[1]),
            }
        except ValueError:
            continue
        observed_at = time.time()
        _MACOS_CAPACITY_CACHE[mount] = (observed_at, result)
        return {**result, "observed_age_s": 0.0, "refresh_stale": False}
    if cached and now - cached[0] <= _MACOS_CAPACITY_MAX_STALE_S:
        return {
            **cached[1],
            "observed_age_s": round(max(0.0, now - cached[0]), 1),
            "refresh_stale": True,
        }
    return None


def build_collector() -> Any:
    system = platform.system().lower()
    if system == "linux":
        return LinuxCollector()
    if system == "darwin":
        return DarwinCollector()
    if system == "windows":
        return WindowsCollector()
    raise SystemExit(f"fleetprobe does not support platform {system!r}")


class Sampler:
    """Owns measurement in a background thread and keeps a rolling history."""

    def __init__(self, host_id: str, interval: float = DEFAULT_INTERVAL) -> None:
        self.host_id = host_id
        self.interval = interval
        self.collector = build_collector()
        self._lock = threading.Lock()
        self._latest: Optional[Dict[str, Any]] = None
        self._history: List[Dict[str, Any]] = []
        self._stop = threading.Event()

    def sample(self) -> Dict[str, Any]:
        collector = self.collector
        if hasattr(collector, "refresh"):
            collector.refresh()
        disks = _disk_usage()
        if platform.system().lower() == "windows":
            total = collector._get("disk_total")  # noqa: SLF001 - same module contract
            free = collector._get("disk_free")
            if total and free:
                disks = [
                    {
                        "mount": "C:",
                        "total_bytes": int(total),
                        "free_bytes": int(free),
                        "used_bytes": int(total) - int(free),
                        "percent": _percent(int(total) - int(free), int(total)),
                        "free_percent": _percent(int(free), int(total)),
                        "scope": "filesystem",
                        "source": "Win32_LogicalDisk",
                    }
                ]
        payload: Dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "agent_version": AGENT_VERSION,
            "host_id": self.host_id,
            "hostname": socket.gethostname(),
            "os": collector.os_name,
            "os_version": collector.os_version(),
            "arch": platform.machine(),
            "sampled_at": _utc_now(),
            "sampled_monotonic": time.monotonic(),
            "sample_interval_s": self.interval,
            "uptime_s": collector.uptime_s(),
            "cpu": collector.cpu(),
            "memory": collector.memory(),
            "disk": disks,
            "gpu": collector.gpu(),
            "thermal": collector.thermal(),
            "battery": collector.battery(),
            "network": collector.network(),
            "processes": collector.processes(),
        }
        return payload

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                payload = self.sample()
            except Exception as exc:  # noqa: BLE001 - collector bugs must not kill the agent
                payload = {
                    "schema": SCHEMA_VERSION,
                    "agent_version": AGENT_VERSION,
                    "host_id": self.host_id,
                    "sampled_at": _utc_now(),
                    "sampled_monotonic": time.monotonic(),
                    "sample_interval_s": self.interval,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            with self._lock:
                self._latest = payload
                self._history.append(
                    {
                        "t": payload.get("sampled_at"),
                        "cpu": (payload.get("cpu") or {}).get("percent"),
                        "mem": (payload.get("memory") or {}).get("percent"),
                        "temp": (payload.get("thermal") or {}).get("cpu_c"),
                    }
                )
                del self._history[:-HISTORY_POINTS]
            elapsed = time.time() - started
            self._stop.wait(max(0.5, self.interval - elapsed))

    def start(self) -> None:
        thread = threading.Thread(target=self._loop, name="fleetprobe-sampler", daemon=True)
        thread.start()
        # #571: the already-running fleet pulse also observes assigned CoS
        # sessions. This separate thread cannot delay telemetry samples or HTTP.
        if self.host_id == "mac-studio" and platform.system() == "Darwin":
            threading.Thread(target=self._cos_observer_loop,
                             name="fleetprobe-cos-notices", daemon=True).start()

    def _cos_observer_loop(self) -> None:
        from cos_orca_observer import DEFAULT_CONFIG, main as observe_once

        while not self._stop.is_set():
            if DEFAULT_CONFIG.is_file():
                try:
                    observe_once([])
                except Exception as exc:  # noqa: BLE001 - observer must not kill fleetprobe
                    print(f"cos observer: {type(exc).__name__}", flush=True)
            self._stop.wait(180)

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self, with_history: bool = False) -> Dict[str, Any]:
        with self._lock:
            latest = dict(self._latest) if self._latest else None
            history = list(self._history) if with_history else None
        if latest is None:
            return {"host_id": self.host_id, "error": "no sample collected yet"}
        sampled_monotonic = latest.pop("sampled_monotonic", None)
        latest["sample_age_s"] = (
            round(max(0.0, time.monotonic() - float(sampled_monotonic)), 2)
            if isinstance(sampled_monotonic, (int, float))
            else 0.0
        )
        if history is not None:
            latest["history"] = history
        return latest


class Handler(BaseHTTPRequestHandler):
    server_version = f"fleetprobe/{AGENT_VERSION}"
    sampler: Sampler
    token: str

    def log_message(self, fmt: str, *args: Any) -> None:  # keep journald quiet
        return

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Pollers can cancel an in-flight request during service reloads or
            # network transitions. This is not an agent fault and must not turn
            # into repeated traceback/log I/O on small hosts.
            return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path, _, query = self.path.partition("?")
        if path == "/healthz":
            self._send(200, {"ok": True, "agent_version": AGENT_VERSION, "host_id": self.sampler.host_id})
            return
        provided = self.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(provided, expected):
            self._send(401, {"error": "unauthorized"})
            return
        if path == "/metrics":
            self._send(200, self.sampler.snapshot(with_history="history" in query))
            return
        self._send(404, {"error": "not found"})


TAILSCALE_BINARIES = [
    "tailscale",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
]


def resolve_tailnet_ip() -> Optional[str]:
    """Return this host's IPv4 tailnet address, or None if Tailscale is down.

    The routing probe is tried first: asking the kernel which local address it
    would use to reach the Tailscale DNS anycast address needs no CLI on PATH,
    which is what a LaunchAgent or a service account usually lacks. No packet
    is sent - connect() on UDP only fixes the route.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.settimeout(2.0)
        probe.connect(("100.100.100.100", 53))
        address = probe.getsockname()[0]
        if address.startswith("100."):
            return address
    except OSError:
        pass
    finally:
        probe.close()
    for binary in TAILSCALE_BINARIES:
        ok, out = _run([binary, "ip", "-4"], timeout=6.0)
        if ok:
            for line in out.splitlines():
                candidate = line.strip()
                if candidate.startswith("100."):
                    return candidate
    return None


def serve(host_id: str, bind: str, port: int, token: str, interval: float) -> None:
    sampler = Sampler(host_id, interval=interval)
    sampler.start()
    Handler.sampler = sampler
    Handler.token = token
    httpd: Optional[ThreadingHTTPServer] = None
    while httpd is None:
        # "auto" keeps the agent tailnet-only without pinning an address that
        # would break the service if Tailscale reassigns it or starts late.
        address = resolve_tailnet_ip() if bind == "auto" else bind
        if address:
            try:
                httpd = ThreadingHTTPServer((address, port), Handler)
                bind = address
                break
            except OSError as exc:
                print(f"fleetprobe: bind {address}:{port} failed ({exc}); retrying", flush=True)
        else:
            print("fleetprobe: no tailnet address yet; retrying", flush=True)
        time.sleep(10)
    print(f"fleetprobe {AGENT_VERSION} serving {host_id} on {bind}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sampler.stop()
        httpd.server_close()


def resolve_token(args: argparse.Namespace) -> str:
    if args.token_file:
        text = _read_text(os.path.expanduser(args.token_file))
        if text and text.strip():
            return text.strip()
        raise SystemExit(f"token file {args.token_file} is missing or empty")
    token = os.environ.get("FLEETPROBE_TOKEN", "").strip()
    if token:
        return token
    raise SystemExit("no token: pass --token-file or set FLEETPROBE_TOKEN")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Signal Deck fleet telemetry agent")
    parser.add_argument("--host-id", default=socket.gethostname().split(".")[0].lower())
    parser.add_argument("--once", action="store_true", help="print one sample and exit")
    parser.add_argument("--serve", action="store_true", help="run the HTTP agent")
    parser.add_argument("--bind", default="auto", help="'auto' binds the tailnet address only")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--token-file")
    args = parser.parse_args(argv)

    if args.once:
        sampler = Sampler(args.host_id, interval=args.interval)
        first = sampler.sample()
        # A delta-based CPU number needs two reads; do the honest thing.
        time.sleep(1.0)
        second = sampler.sample()
        print(json.dumps(second if second.get("cpu", {}).get("percent") is not None else first, indent=2))
        return 0

    if args.serve:
        serve(args.host_id, args.bind, args.port, resolve_token(args), args.interval)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
