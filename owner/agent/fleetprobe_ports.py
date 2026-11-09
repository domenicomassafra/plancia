"""Bounded native TCP inventory; only Tailscale Serve can certify a web link."""
from __future__ import annotations

import http.client
import ipaddress
import json
import platform
import re
import shutil
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from fleetprobe_support import _run, _powershell

MAX_PORTS = 256
MAX_OBSERVED = 4096
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
    address, marker, scope = value.partition("%")
    if marker and not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", scope):
        return False
    try:
        ipaddress.ip_address(address)
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
    elif system == "darwin":
        # macOS netstat -an -p tcp can successfully return zero TCP rows in the
        # installed execution context. lsof -Fn has one n<bind>:<port> per socket.
        sockets = []
        for line in output.splitlines():
            if not line.startswith("n") or ":" not in line:
                continue
            bind, raw_port = line[1:].rsplit(":", 1)
            sockets.append((bind.strip("[]"), raw_port))
    else:
        sockets = []
        for line in output.splitlines():
            parts = line.split()
            if system == "linux" and len(parts) >= 4 and parts[0] == "LISTEN":
                endpoint = parts[3]
                separator = ":"
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
        if len(rows) >= MAX_OBSERVED:
            break
    # Rank the bounded observed set before the display cap, so OS enumeration
    # order cannot arbitrarily hide a low-numbered listening port.
    return sorted(rows, key=lambda entry: (entry["port"], entry["bind"]))[:MAX_PORTS + 1]


def _confirmed_http(port: int, host: str = "127.0.0.1", scheme: str = "http") -> bool:
    try:
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=0.25, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, port, timeout=0.25)
        try:
            conn.request("HEAD", "/", headers={"Host": host})
            return 100 <= conn.getresponse().status < 600
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return False


def serve_web_links(
    config: dict[str, Any], status: dict[str, Any] | None,
    *, check_http: Callable[[int, str, str], bool] | None = None,
) -> dict[tuple[str, int], list[str]]:
    """Trust only private HTTPS root proxies to confirmed local HTTP endpoints."""
    if not isinstance(status, dict) or status.get("BackendState") != "Running":
        return {}
    if check_http is None:
        check_http = _confirmed_http
    if not isinstance(status.get("Self"), dict) or status["Self"].get("Online") is not True:
        return {}
    web = config.get("Web") if isinstance(config, dict) else None
    if not isinstance(web, dict):
        return {}
    result: dict[tuple[str, int], list[str]] = {}
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
        if proxy.scheme not in {"http", "https"} or proxy.hostname not in {"localhost", "127.0.0.1", "::1"}:
            continue
        try:
            target_port = proxy.port
        except ValueError:
            continue
        if not target_port or proxy.username or proxy.password or proxy.query or proxy.fragment or proxy.path not in {"", "/"}:
            continue
        host_ip = "::1" if proxy.hostname == "::1" else "127.0.0.1"
        if not check_http(target_port, host_ip, proxy.scheme):
            continue
        suffix = "" if int(raw_port) == 443 else f":{int(raw_port)}"
        url = f"https://{host.lower()}{suffix}"
        result.setdefault((host_ip, target_port), []).append(url)
    return result


def _loopback_listener(bind: str, target: str) -> bool:
    """An HTTP proof at loopback may annotate only that socket's bind."""
    if bind == target:
        return True
    return (target == "127.0.0.1" and bind in {"0.0.0.0", "*"}) or (
        target == "::1" and bind in {"::", "*"}
    )


def _local_scope(bind: str) -> str | None:
    if bind in {"127.0.0.1", "::1"}:
        return "server-local"
    try:
        address = ipaddress.ip_address(bind.split("%", 1)[0])
    except ValueError:
        return None
    if address.is_private and not address.is_loopback and not address.is_link_local and not address.is_unspecified:
        return "lan"
    return None


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
        success, output = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fn"], timeout=2.0)
    elif system == "windows":
        success, output = _powershell(
            "Get-NetTCPConnection -State Listen -ErrorAction Stop | "
            "Select-Object LocalAddress,LocalPort | ConvertTo-Json -Compress", timeout=2.5,
        )
    else:
        success, output = False, "unsupported OS"
    observed = parse_tcp_listeners(output, system) if success else []
    truncated = len(observed) > MAX_PORTS
    ports = observed[:MAX_PORTS]
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
                    urls = []
                    for (loopback, port), served_urls in links.items():
                        # An identical port on a different interface does not
                        # prove that the other listener is this HTTP service.
                        if row["port"] == port and _loopback_listener(row["bind"], loopback):
                            urls.extend(served_urls)
                    if urls:
                        row["web"] = True
                        row["url"] = urls[0]
                        row["links"] = [{"scope": "tailnet", "url": url} for url in urls]
    # Only probe sockets actually observed on this host; never scan addresses
    # or promote a wildcard bind into a client-reachable URL.
    deadline = time.monotonic() + 2.5
    for row in ports:
        if time.monotonic() >= deadline:
            break
        scope = _local_scope(row["bind"])
        if scope is None:
            continue
        bind = row["bind"].split("%", 1)[0]
        for scheme in ("http", "https"):
            if time.monotonic() >= deadline:
                break
            if _confirmed_http(row["port"], bind, scheme):
                bracketed = f"[{bind}]" if ":" in bind else bind
                row["links"].append({"scope": scope, "url": f"{scheme}://{bracketed}:{row['port']}"})
                row["web"] = True
                break
    return {"sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ports": ports, "tailnet": tailnet, "truncated": truncated,
            "reason": None if success else "native TCP listener inventory unavailable"}
