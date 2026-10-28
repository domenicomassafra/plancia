from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

from backend.fleet2.poller import (
    FleetPoller,
    _match_tailscale_peer,
    _match_tailscale_peers,
    _peer_matches,
    _validate_agent_sample,
)
from backend.fleet2.registry import FleetHost, FleetRegistry, load_registry
from backend.fleet2.store import Store


def _sample_payload(host_id: str) -> Dict[str, Any]:
    return {
        "schema": 2,
        "agent_version": "2.1.0",
        "host_id": host_id,
        "os": "linux",
        "sampled_at": "2026-09-20T00:00:00Z",
        "sample_age_s": 0.5,
        "sample_interval_s": 5.0,
        "cpu": {"percent": 15.2},
        "memory": {"percent": 42.0},
        "disk": [{"mount": "/", "percent": 55.0}],
    }


def _make_host(
    host_id: str,
    name: str = "Test Host",
    agent_host: str = "test-host",
    agent_ip: str = "100.64.0.1",
    offline: bool = False,
    os_name: str = "linux",
) -> FleetHost:
    return FleetHost(
        id=host_id,
        name=name,
        subtitle="Test subtitle",
        agent_host=agent_host,
        agent_ip=agent_ip,
        ssh_alias=host_id,
        os=os_name,
        role="workstation",
        image=None,
        mac=None,
        notes="",
        offline=offline,
    )


def test_peer_matching_by_ip_and_names():
    node = {
        "HostName": "matebook-dodo",
        "DNSName": "matebook.example.invalid.",
        "TailscaleIPs": ["100.64.1.11", "fd7a:115c:a1e0::6c34:e403"],
        "Online": False,
    }
    # Matches by IP
    assert _peer_matches("100.64.1.11", "", "other", node) is True
    # Matches by HostName
    assert _peer_matches("", "matebook-dodo", "other", node) is True
    # Matches by DNS short name
    assert _peer_matches("", "matebook", "other", node) is True
    # Matches by ID
    assert _peer_matches("", "", "matebook", node) is True
    # Non-match
    assert _peer_matches("100.99.99.99", "unrelated", "unrelated", node) is False


def test_match_tailscale_peer_against_self_and_peers():
    status = {
        "Self": {
            "HostName": "minipc-ubuntu",
            "DNSName": "minipc.example.invalid.",
            "TailscaleIPs": ["100.64.1.10"],
            "Online": True,
        },
        "Peer": {
            "p1": {
                "HostName": "matebook-dodo",
                "DNSName": "matebook.example.invalid.",
                "TailscaleIPs": ["100.64.1.11"],
                "Online": False,
            }
        },
    }
    host_minipc = _make_host("minipc", agent_host="minipc-ubuntu", agent_ip="100.64.1.10")
    matched_self = _match_tailscale_peer(host_minipc, status)
    assert matched_self is not None
    assert matched_self["Online"] is True

    host_matebook = _make_host("matebook", agent_host="matebook-dodo", agent_ip="100.64.1.11")
    matched_peer = _match_tailscale_peer(host_matebook, status)
    assert matched_peer is not None
    assert matched_peer["Online"] is False


def test_matebook_runtime_identities_do_not_share_tailnet_fallbacks():
    linux = _make_host("matebooklinux", agent_host="matebooklinux", agent_ip="100.64.1.12")
    windows = _make_host("matebook", agent_host="matebook-dodo", agent_ip="100.64.1.11")
    status = {
        "Peer": {
            "linux": {"HostName": "matebooklinux", "TailscaleIPs": ["100.64.1.12"], "Online": True},
            "windows": {"HostName": "matebook-dodo", "TailscaleIPs": ["100.64.1.11"], "Online": False},
        }
    }
    assert [peer["HostName"] for peer in _match_tailscale_peers(linux, status)] == ["matebooklinux"]
    assert [peer["HostName"] for peer in _match_tailscale_peers(windows, status)] == ["matebook-dodo"]
    assert _match_tailscale_peer(linux, status)["Online"] is True
    assert _match_tailscale_peer(windows, status)["Online"] is False


def test_registry_exposes_five_runtime_identities_without_dynamic_matebook():
    registry = load_registry()
    assert [host.id for host in registry.hosts] == [
        "minipc",
        "mac-studio",
        "macbook-air",
        "matebooklinux",
        "matebook",
    ]
    assert registry.get("matebooklinux").os == "linux"
    assert registry.get("matebook").os == "windows"
    assert registry.get("matebooklinux").offline is False
    assert registry.get("matebook").offline is True
    assert registry.get("matebooklinux").agent_hosts == ()
    assert registry.get("matebook").agent_hosts == ()


def test_agent_sample_validation_rejects_wrong_identity_and_schema():
    host = _make_host("minipc")
    assert _validate_agent_sample(host, _sample_payload("minipc"))["host_id"] == "minipc"
    wrong_host = _sample_payload("mac-studio")
    with pytest.raises(ValueError, match="identity mismatch"):
        _validate_agent_sample(host, wrong_host)
    wrong_schema = _sample_payload("minipc")
    wrong_schema["schema"] = 99
    with pytest.raises(ValueError, match="unsupported agent schema"):
        _validate_agent_sample(host, wrong_schema)


def test_agent_sample_validation_rejects_missing_age():
    host = _make_host("minipc")
    payload = _sample_payload("minipc")
    payload.pop("sample_age_s")
    with pytest.raises(ValueError, match="sample age"):
        _validate_agent_sample(host, payload)


@pytest.mark.anyio
async def test_poller_suppresses_known_offline_peer_tailscale(tmp_path: Path):
    store = Store(tmp_path / "fleet.db")
    host_online = _make_host("minipc", agent_host="minipc-ubuntu", agent_ip="100.64.1.10")
    host_offline = _make_host("matebook", agent_host="matebook-dodo", agent_ip="100.64.1.11")
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[host_online, host_offline])

    ts_status = {
        "Self": {
            "HostName": "minipc-ubuntu",
            "TailscaleIPs": ["100.64.1.10"],
            "Online": True,
        },
        "Peer": {
            "p1": {
                "HostName": "matebook-dodo",
                "TailscaleIPs": ["100.64.1.11"],
                "Online": False,
            }
        },
    }

    probed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        probed_urls.append(str(request.url))
        if "minipc" in str(request.url):
            return httpx.Response(200, json=_sample_payload("minipc"))
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    poller = FleetPoller(
        registry,
        store,
        token="secret",
        tailscale_status_fn=lambda: ts_status,
        max_backoff_s=60.0,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        await poller._poll_host(client, host_online)
        await poller._poll_host(client, host_offline)

    # Online host was probed
    assert any("minipc" in url for url in probed_urls)
    assert poller.states["minipc"].reachable is True
    assert poller.states["minipc"].error is None

    # Offline host was SUPPRESSED: no HTTP request attempted at all!
    assert not any("matebook" in url or "100.64.1.11" in url for url in probed_urls)
    assert poller.states["matebook"].reachable is False
    assert poller.states["matebook"].error == "peer offline on tailnet"
    assert poller.states["matebook"].consecutive_failures == 1
    assert poller.states["matebook"].next_poll > time.time()


@pytest.mark.anyio
async def test_poller_exponential_backoff_on_connection_failure(tmp_path: Path):
    store = Store(tmp_path / "fleet.db")
    host = _make_host("failing-node", agent_host="failing-node", agent_ip="100.64.0.99")
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[host])

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ConnectTimeout("connection timed out")

    transport = httpx.MockTransport(handler)
    poller = FleetPoller(
        registry,
        store,
        token="secret",
        tailscale_status_fn=lambda: None,  # no tailscale status available
        max_backoff_s=60.0,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        # First attempt: should execute and fail
        await poller._poll_host(client, host)
        assert request_count == 2  # magicdns + ip fallback tried
        assert poller.states["failing-node"].consecutive_failures == 1
        first_next_poll = poller.states["failing-node"].next_poll
        assert first_next_poll >= time.time() + 9.0

        # Immediate second poll within backoff window: MUST be skipped without HTTP attempts
        await poller._poll_host(client, host)
        assert request_count == 2  # No new requests sent!

        # Advance past first backoff window
        poller.states["failing-node"].next_poll = time.time() - 0.1
        await poller._poll_host(client, host)
        assert request_count == 4  # retried
        assert poller.states["failing-node"].consecutive_failures == 2
        # Backoff now 5.0 * 2^2 = 20.0s
        assert poller.states["failing-node"].next_poll > time.time() + 15.0

        # Simulate 5 consecutive failures to hit cap
        poller.states["failing-node"].consecutive_failures = 4
        poller.states["failing-node"].next_poll = time.time() - 0.1
        await poller._poll_host(client, host)
        assert poller.states["failing-node"].consecutive_failures == 5
        # Capped at max_backoff_s = 60.0
        assert poller.states["failing-node"].next_poll <= time.time() + 60.1


@pytest.mark.anyio
async def test_poller_resumes_when_peer_comes_online(tmp_path: Path):
    store = Store(tmp_path / "fleet.db")
    host = _make_host("matebook", agent_host="matebook-dodo", agent_ip="100.64.1.11")
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[host])

    is_online = False

    def get_ts_status():
        return {
            "Peer": {
                "p1": {
                    "HostName": "matebook-dodo",
                    "TailscaleIPs": ["100.64.1.11"],
                    "Online": is_online,
                }
            }
        }

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_sample_payload("matebook"))

    transport = httpx.MockTransport(handler)
    poller = FleetPoller(
        registry,
        store,
        token="secret",
        tailscale_status_fn=get_ts_status,
        max_backoff_s=60.0,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        # Phase 1: offline in Tailscale -> suppressed
        await poller._poll_host(client, host)
        assert request_count == 0
        assert poller.states["matebook"].reachable is False
        assert poller.states["matebook"].error == "peer offline on tailnet"

        # Phase 2: host comes online in Tailscale
        is_online = True
        poller.reset_backoff("matebook")  # operator refresh or backoff elapsed
        poller._ts_status_cache = None  # invalidate status cache

        await poller._poll_host(client, host)
        assert request_count > 0
        assert poller.states["matebook"].reachable is True
        assert poller.states["matebook"].error is None
        assert poller.states["matebook"].consecutive_failures == 0
        assert poller.states["matebook"].next_poll == 0.0


@pytest.mark.anyio
async def test_poller_suppresses_configured_offline_host(tmp_path: Path):
    store = Store(tmp_path / "fleet.db")
    host = _make_host("matebook", agent_host="matebook-dodo", agent_ip="100.64.1.11", offline=True)
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[host])

    probed = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal probed
        probed = True
        return httpx.Response(200, json=_sample_payload("matebook"))

    transport = httpx.MockTransport(handler)
    poller = FleetPoller(registry, store, token="secret")

    async with httpx.AsyncClient(transport=transport) as client:
        await poller._poll_host(client, host)

    assert probed is False
    assert poller.states["matebook"].reachable is False
    assert poller.states["matebook"].error == "runtime pending/offline in fleet config"
    assert poller.states["matebook"].consecutive_failures == 0
    assert poller.states["matebook"].next_poll > time.time()
    assert store.history("matebook", minutes=60) == []
    snapshot = poller.snapshot()
    assert snapshot["hosts"][0]["lifecycle"] == "pending"
    assert snapshot["fleet"]["total"] == 1
    assert snapshot["fleet"]["managed_total"] == 0
    assert snapshot["fleet"]["pending"] == 1
    assert snapshot["fleet"]["reachable"] == 0
    assert snapshot["fleet"]["worst_severity"] == "unknown"


@pytest.mark.anyio
async def test_http_reachable_but_stale_agent_sample_is_not_counted_as_live(tmp_path: Path):
    store = Store(tmp_path / "fleet.db")
    host = _make_host("minipc")
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[host])
    payload = _sample_payload("minipc")
    payload["sample_age_s"] = 120.0

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    poller = FleetPoller(registry, store, token="secret", tailscale_status_fn=lambda: None)
    async with httpx.AsyncClient(transport=transport) as client:
        await poller._poll_host(client, host)

    snapshot = poller.snapshot()
    observed = snapshot["hosts"][0]
    assert observed["reachable"] is True
    assert observed["stale"] is True
    assert "sample stale" in observed["error"]
    assert observed["health"]["severity"] == "unknown"
    assert snapshot["fleet"]["reachable"] == 0


@pytest.mark.anyio
async def test_slow_governed_sampler_uses_its_declared_freshness_budget(tmp_path: Path):
    store = Store(tmp_path / "fleet.db")
    host = _make_host("matebook", os_name="windows")
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[host])
    payload = _sample_payload("matebook")
    payload["os"] = "windows"
    payload["sample_interval_s"] = 60.0
    payload["sample_age_s"] = 45.0
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    poller = FleetPoller(registry, store, token="secret", tailscale_status_fn=lambda: None)
    async with httpx.AsyncClient(transport=transport) as client:
        await poller._poll_host(client, host)

    snapshot = poller.snapshot()
    assert snapshot["hosts"][0]["stale"] is False
    assert snapshot["fleet"]["reachable"] == 1


def test_pending_runtime_does_not_degrade_managed_fleet_health(tmp_path: Path):
    managed = _make_host("minipc")
    pending = _make_host("matebook", os_name="windows", offline=True)
    registry = FleetRegistry(agent_port=9631, poll_interval_s=5.0, hosts=[managed, pending])
    poller = FleetPoller(registry, Store(tmp_path / "fleet.db"), token="secret")
    poller.states["minipc"].sample = _sample_payload("minipc")
    poller.states["minipc"].reachable = True
    poller.states["minipc"].last_success = time.time()
    poller.states["matebook"].reachable = False
    poller.states["matebook"].error = "runtime pending/offline in fleet config"

    snapshot = poller.snapshot()
    assert snapshot["fleet"] == {
        "total": 2,
        "managed_total": 1,
        "pending": 1,
        "reachable": 1,
        "worst_severity": "ok",
    }
