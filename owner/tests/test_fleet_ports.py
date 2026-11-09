"""The authenticated ports directory must never invent a reachable web URL."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import fleetprobe_ports
from fleetprobe_ports import collect_ports, parse_tcp_listeners, serve_web_links

from backend.fleet2.poller import FleetPoller
from backend.fleet2.registry import FleetHost, FleetRegistry
from backend.fleet2.store import Store


def test_native_listeners_normalize_bind_and_never_guess_web_protocol():
    linux = "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\nLISTEN 0 128 *:5555 *:*\n"
    mac = "p123\nf4\nn127.0.0.1:8443\nf5\nn[::1]:9090\n"
    assert [(p["bind"], p["port"], p["web"], p["url"]) for p in parse_tcp_listeners(linux, "linux")] == [
        ("*", 5555, False, None), ("127.0.0.1", 8000, False, None),
    ]
    assert [(p["bind"], p["port"]) for p in parse_tcp_listeners(mac, "darwin")] == [
        ("127.0.0.1", 8443), ("::1", 9090),
    ]


def test_tailnet_links_require_running_private_serve_and_real_http():
    config = {"Web": {
        "host.fixture.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}},
        "host.fixture.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9443"}}},
    }, "AllowFunnel": {"host.fixture.ts.net:8443": True}}
    status = {"BackendState": "Running", "Self": {"Online": True}}
    assert serve_web_links(config, status, check_http=lambda port, host, scheme: port == 8000) == {
        ("127.0.0.1", 8000): ["https://host.fixture.ts.net"]
    }
    assert serve_web_links(config, {"BackendState": "Stopped", "Self": {"Online": False}}, check_http=lambda *_: True) == {}
    assert serve_web_links(config, None, check_http=lambda *_: True) == {}


def test_ipv6_and_https_upstream_proofs_are_bound_to_the_socket():
    config = {"Web": {"host.fixture.ts.net:8443": {
        "Handlers": {"/": {"Proxy": "https://[::1]:9443"}}
    }}}
    probes = []
    status = {"BackendState": "Running", "Self": {"Online": True}}
    result = serve_web_links(config, status, check_http=lambda port, host, scheme: (
        probes.append((port, host, scheme)) or True
    ))
    assert probes == [(9443, "::1", "https")]
    assert result == {("::1", 9443): ["https://host.fixture.ts.net:8443"]}


def test_bounds_and_ipv6_scopes_preserve_more_than_128_observations():
    linux = "\n".join(f"LISTEN 0 10 127.0.0.1:{port} 0.0.0.0:*" for port in range(7000, 7140))
    linux += "\nLISTEN 0 10 [fe80::1%en0]:9999 [::]:*\n"
    result = parse_tcp_listeners(linux, "linux")
    assert len(result) == 141
    assert any(row["bind"] == "fe80::1%en0" and row["port"] == 9999 for row in result)


def test_exceeded_listener_cap_is_explicit_and_sorted_before_selection(monkeypatch):
    monkeypatch.setattr(fleetprobe_ports, "_confirmed_http", lambda *args: False)
    native = "\n".join(f"LISTEN 0 10 127.0.0.1:{port} 0.0.0.0:*" for port in range(9300, 8999, -1))

    def fake_run(argv, timeout=2.0):
        return (True, native) if argv[0] == "ss" else (False, "tailscale unavailable")

    result = collect_ports(system="linux", run=fake_run, tailscale_binary="tailscale")
    assert result["truncated"] is True
    assert len(result["ports"]) == 256
    assert result["ports"][0]["port"] == 9000
    assert result["ports"][-1]["port"] == 9255


def test_collection_fails_closed_when_tailnet_off_or_native_probe_fails(monkeypatch):
    monkeypatch.setattr(fleetprobe_ports, "_confirmed_http", lambda *args: False)
    def fake_run(argv, timeout=2.0):
        if argv[0] == "ss":
            return True, "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\nLISTEN 0 128 *:5555 *:*\n"
        if argv[1:3] == ["status", "--json"]:
            return True, json.dumps({"BackendState": "Stopped", "Self": {"Online": False}})
        return False, "no tailnet"

    result = collect_ports(system="linux", run=fake_run, tailscale_binary="tailscale")
    assert result["tailnet"] == "off"
    assert len(result["ports"]) == 2
    assert all(not row["web"] and row["url"] is None for row in result["ports"])


def test_link_annotations_never_attach_to_other_interface_on_same_port(monkeypatch):
    def fake_run(argv, timeout=2.0):
        if argv[0] == "ss":
            return True, ("LISTEN 0 128 127.0.0.1:8000 0.0.0.0:*\n"
                          "LISTEN 0 128 192.0.2.10:8000 0.0.0.0:*\n")
        if argv[1:3] == ["status", "--json"]:
            return True, json.dumps({"BackendState": "Running", "Self": {"Online": True}})
        if argv[1:3] == ["serve", "status"]:
            return True, json.dumps({"Web": {"host.fixture.ts.net:8443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}
            }}})
        return False, "unavailable"

    monkeypatch.setattr(fleetprobe_ports, "_confirmed_http", lambda port, bind="127.0.0.1", scheme="http": bind == "127.0.0.1")
    result = collect_ports(system="linux", run=fake_run, tailscale_binary="tailscale")
    ports = {row["bind"]: row for row in result["ports"]}
    assert ports["127.0.0.1"]["url"] == "https://host.fixture.ts.net:8443"
    assert ports["192.0.2.10"]["url"] is None
    assert ports["127.0.0.1"]["links"][-1]["scope"] == "server-local"


def _host(offline=False):
    return FleetHost(
        id="fixture", name="Fixture", subtitle="", agent_host="fixture", agent_ip="100.64.0.2",
        ssh_alias="fixture", os="linux", role="workstation", image=None, mac=None,
        notes="", offline=offline,
    )


@pytest.mark.anyio
async def test_ports_api_read_side_uses_verified_agent_and_marks_offline(tmp_path, monkeypatch):
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5, hosts=[_host()])
    poller = FleetPoller(registry, Store(tmp_path / "fleet.sqlite"), token="fixture")
    poller.states["fixture"].reachable = True
    poller.states["fixture"].url = "http://100.64.0.2:9631"
    poller.states["fixture"].last_success = __import__("time").time()

    def serve(request):
        assert request.headers.get("Authorization") == "Bearer fixture"
        return httpx.Response(200, json={"host_id": "fixture", "sampled_at": "2026-11-09T12:00:00Z", "tailnet": "on", "ports": [
            {"port": 8000, "bind": "127.0.0.1", "protocol": "tcp", "web": True, "url": "https://host.fixture.ts.net"},
            {"port": 5555, "bind": "*", "protocol": "tcp", "web": False, "url": "https://malicious.example"},
        ]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(serve), **kwargs))
    result = await poller.ports_snapshot()
    assert len(result) == 2
    assert result[0]["url"] == "https://host.fixture.ts.net"
    assert result[1]["url"] is None
    assert result[1]["web"] is False

    registry.hosts[0] = _host(offline=True)
    assert (await poller.ports_snapshot())[0]["status"] == "pending"
    assert (await poller.ports_snapshot())[0]["url"] is None
