"""Bounded native TCP inventory; only Tailscale Serve can certify a web link."""
from __future__ import annotations

import http.client
import ipaddress
import json
import platform
import re
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from fleetprobe_support import _run, _powershell

MAX_PORTS = 128
_TAILSCALE_BINS = (
    "tailscale", "/usr/bin/tailscale", "/usr/local/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
)


def _port_row(bind: str, port: int) -> dict[str, Any]:
    return {"protocol": "tcp", "bind": bind, "port": port, "process": None,
            "service": None, "web": False, "url": None, "links": []}


def _valid_bind(value: str) -> bool:
    if value == "*":
        return True
    try:
        ipaddress.ip_address(value.split("%", 1)[0])
        return True
    except ValueError:
        return False


def parse_tcp_listeners(output: str, system: str) -> list[dict[str, Any]]:
    """Read LISTEN sockets, never infer HTTP or a process from a TCP socket."""
    if system == "windows":
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            return []
        entries = decoded if isinstance(decoded, list) else [decoded]
        sockets = [(str(entry.get("LocalAddress", "")), entry.get("LocalPort"))
                   for entry in entries if isinstance(entry, dict)]
    else:
        sockets = []
        for line in output.splitlines():
            parts = line.split()
            if system == "linux" and len(parts) >= 4 and parts[0] == "LISTEN":
                endpoint = parts[3]
                separator = ":"
            elif system == "darwin" and len(parts) >= 6 and parts[0] in {"tcp4", "tcp6", "tcp"} and parts[-1] == "LISTEN":
                endpoint = parts[3]
                separator = "."
            else:
                continue
            if separator not in endpoint:
                continue
            bind, raw_port = endpoint.rsplit(separator, 1)
            sockets.append((bind.strip("[]"), raw_port))
    rows = []
    seen = set()
    for bind, raw_port in sockets:
        try:
            port = int(raw_port)
        except (ValueError, TypeError):
            continue
        if not (0 < port <= 65535) or not _valid_bind(bind) or (bind, port) in seen:
            continue
        rows.append(_port_row(bind, port))
        seen.add((bind, port))
        if len(rows) >= MAX_PORTS:
            break
    return sorted(rows, key=lambda entry: (entry["port"], entry["bind"]))


def _confirmed_http(port: int) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.4)
        try:
            conn.request("HEAD", "/", headers={"Host": "localhost"})
            return 100 <= conn.getresponse().status < 600
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def serve_web_links(
    config: dict[str, Any], status: dict[str, Any] | None,
    *, check_http: Callable[[int], bool] = _confirmed_http,
) -> dict[int, list[str]]:
    """Trust only private HTTPS root proxies to confirmed local HTTP endpoints."""
    if not isinstance(status, dict) or status.get("BackendState") != "Running":
        return {}
    if not isinstance(status.get("Self"), dict) or status["Self"].get("Online") is not True:
        return {}
    web = config.get("Web") if isinstance(config, dict) else None
    if not isinstance(web, dict):
        return {}
    result: dict[int, list[str]] = {}
    funnel = config.get("AllowFunnel")
    deadline = time.monotonic() + 2.5
    for hostname, site in list(web.items())[:64]:
        if time.monotonic() >= deadline:
            break
        if not isinstance(hostname, str) or not isinstance(site, dict):
            continue
        host, sep, raw_port = hostname.rpartition(":")
        if not sep or not re.fullmatch(r"[a-zA-Z0-9.-]+\.ts\.net", host):
            continue
        if not raw_port.isdigit() or not (0 < int(raw_port) <= 65535):
            continue
        if isinstance(funnel, dict) and funnel.get(hostname):
            continue  # Funnel is public and never qualifies as a private link.
        root = (site.get("Handlers") or {}).get("/")
        if not isinstance(root, dict) or not isinstance(root.get("Proxy"), str):
            continue
        proxy = urlsplit(root["Proxy"])
        if proxy.scheme != "http" or proxy.hostname not in {"localhost", "127.0.0.1", "::1"}:
            continue
        try:
            target_port = proxy.port
        except ValueError:
            continue
        if not target_port or proxy.username or proxy.password or proxy.query or proxy.fragment:
            continue
        if not check_http(target_port):
            continue
        suffix = "" if int(raw_port) == 443 else f":{int(raw_port)}"
        url = f"https://{host.lower()}{suffix}"
        result.setdefault(target_port, []).append(url)
    return result


def collect_ports(
    *, system: str | None = None,
    run: Callable[..., tuple[bool, str]] = _run,
    tailscale_binary: str | None = None,
) -> dict[str, Any]:
    """On-demand read; no scanner, daemon, or exposure change."""
    system = (system or platform.system()).lower()
    if system == "linux":
        success, output = run(["ss", "-H", "-ltn"], timeout=2.0)
    elif system == "darwin":
        success, output = run(["netstat", "-an", "-p", "tcp"], timeout=2.0)
    elif system == "windows":
        success, output = _powershell(
            "Get-NetTCPConnection -State Listen -ErrorAction Stop | "
            "Select-Object LocalAddress,LocalPort | ConvertTo-Json -Compress", timeout=2.5,
        )
    else:
        success, output = False, "unsupported OS"
    ports = parse_tcp_listeners(output, system) if success else []
    tailnet = "not verified"
    binary = tailscale_binary or next((name for name in _TAILSCALE_BINS if shutil.which(name)), None)
    if binary:
        ts_ok, raw_status = run([binary, "status", "--json"], timeout=1.5)
        try:
            status = json.loads(raw_status) if ts_ok else None
        except json.JSONDecodeError:
            status = None
        if isinstance(status, dict) and status.get("BackendState") != "Running":
            tailnet = "off"
        elif isinstance(status, dict) and isinstance(status.get("Self"), dict) and status["Self"].get("Online") is True:
            tailnet = "on"
            serve_ok, raw_serve = run([binary, "serve", "status", "--json"], timeout=1.5)
            if serve_ok:
                try:
                    links = serve_web_links(json.loads(raw_serve), status)
                except (ValueError, TypeError):
                    links = {}
                for row in ports:
                    urls = links.get(row["port"], [])
                    if urls:
                        row["web"] = True
                        row["url"] = urls[0]
                        row["links"] = [{"scope": "tailnet", "url": url} for url in urls]
    return {"sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ports": ports, "tailnet": tailnet,
            "reason": None if success else "native TCP listener inventory unavailable"}
