from __future__ import annotations

import time

import pytest

from backend.fleet2.agent_bridge import BridgeError, ControlCenterAgentBridge
from backend.fleet2.registry import load_registry
from backend.fleet2.store import Store


class FakePoller:
    def __init__(self, registry):
        self.registry = registry
        self.calls = []

    def snapshot(self):
        return {
            "generated_at": 1.0,
            "fleet": {"total": len(self.registry.hosts), "reachable": 1, "worst_severity": "unknown"},
            "hosts": [
                {
                    "id": host.id,
                    "reachable": host.id == "minipc",
                    "stale": False,
                    "health": {"severity": "ok" if host.id == "minipc" else "unknown"},
                    "sample": None,
                }
                for host in self.registry.hosts
            ],
        }

    async def poll_once(self, host_id=None, *, force=False):
        self.calls.append((host_id, force))
        return self.snapshot()


class FakeEngine:
    def __init__(self):
        self.dispatched = []

    def capabilities(self, host, refresh=False):
        return {
            "host_id": host.id,
            "actions": {
                "restart-agent": {"available": True, "destructive": False, "reason": None},
                "reboot": {"available": True, "destructive": True, "reason": None},
                "shutdown": {"available": True, "destructive": True, "reason": None},
            },
        }

    def dispatch(self, host, action_key, confirmation):
        self.dispatched.append((host.id, action_key, confirmation))
        return {"receipt_id": "dispatch-1", "host_id": host.id, "action": action_key}


def make_bridge(tmp_path, *, clock=time.time):
    registry = load_registry()
    store = Store(tmp_path / "fleet2.sqlite3")
    poller = FakePoller(registry)
    engine = FakeEngine()
    return ControlCenterAgentBridge(registry, poller, store, engine, clock=clock), store, poller, engine


@pytest.mark.anyio
async def test_bridge_exposes_only_fixed_typed_methods(tmp_path):
    bridge, _, _, _ = make_bridge(tmp_path)
    with pytest.raises(BridgeError, match="unknown method"):
        await bridge.call("shell", {"command": "hostname"})
    with pytest.raises(BridgeError, match="unsupported argument"):
        await bridge.call("controlcenter_fleet_status", {"command": "hostname"})
    with pytest.raises(BridgeError, match="unsupported argument"):
        await bridge.call("controlcenter_host_diagnostics", {"host_id": "minipc", "path": "/etc"})


@pytest.mark.anyio
async def test_bridge_fleet_status_preserves_five_runtime_identities(tmp_path):
    bridge, _, poller, _ = make_bridge(tmp_path)
    result = await bridge.call("controlcenter_fleet_status", {"refresh": True})
    assert result["fleet"]["total"] == 5
    assert poller.calls == [(None, True)]


@pytest.mark.anyio
async def test_bridge_history_and_receipts_are_bounded(tmp_path):
    bridge, store, _, _ = make_bridge(tmp_path)
    store.record_sample("matebooklinux", None, reachable=False)
    history = await bridge.call(
        "controlcenter_telemetry_history", {"host_id": "matebooklinux", "minutes": 60}
    )
    assert history["host_id"] == "matebooklinux"
    assert history["points"][0]["reachable"] is False
    two_weeks = await bridge.call(
        "controlcenter_telemetry_history", {"host_id": "matebooklinux", "minutes": 20160}
    )
    assert two_weeks["minutes"] == 20160
    with pytest.raises(BridgeError, match="minutes"):
        await bridge.call("controlcenter_telemetry_history", {"host_id": "matebook", "minutes": 1})
    with pytest.raises(BridgeError, match="minutes"):
        await bridge.call("controlcenter_telemetry_history", {"host_id": "matebook", "minutes": 20161})


@pytest.mark.anyio
async def test_bridge_blocks_destructive_apply_without_separate_owner_gate(tmp_path):
    bridge, _, _, engine = make_bridge(tmp_path)
    preview = await bridge.call(
        "controlcenter_action_preview", {"host_id": "mac-studio", "action_key": "reboot"}
    )
    assert preview["apply_allowed"] is False
    assert "owner confirmation" in preview["block_reason"]
    with pytest.raises(BridgeError, match="only restart-agent"):
        await bridge.call(
            "controlcenter_action_apply",
            {
                "host_id": "mac-studio",
                "action_key": "reboot",
                "preview_id": preview["preview_id"],
                "preview_fingerprint": preview["preview_fingerprint"],
                "idempotency_key": "reboot-1",
            },
        )
    assert engine.dispatched == []


@pytest.mark.anyio
async def test_bridge_safe_action_is_preview_bound_and_idempotent(tmp_path):
    bridge, _, _, engine = make_bridge(tmp_path)
    preview = await bridge.call(
        "controlcenter_action_preview",
        {"host_id": "matebooklinux", "action_key": "restart-agent"},
    )
    assert preview["apply_allowed"] is True
    request = {
        "host_id": "matebooklinux",
        "action_key": "restart-agent",
        "preview_id": preview["preview_id"],
        "preview_fingerprint": preview["preview_fingerprint"],
        "idempotency_key": "safe-restart-1",
    }
    first = await bridge.call("controlcenter_action_apply", request)
    second = await bridge.call("controlcenter_action_apply", request)
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert engine.dispatched == [("matebooklinux", "restart-agent", "")]


@pytest.mark.anyio
async def test_bridge_rejects_expired_or_cross_host_preview(tmp_path):
    now = [1000.0]
    bridge, _, _, _ = make_bridge(tmp_path, clock=lambda: now[0])
    preview = await bridge.call(
        "controlcenter_action_preview", {"host_id": "mac-studio", "action_key": "restart-agent"}
    )
    now[0] += 121
    with pytest.raises(BridgeError, match="expired"):
        await bridge.call(
            "controlcenter_action_apply",
            {
                "host_id": "mac-studio",
                "action_key": "restart-agent",
                "preview_id": preview["preview_id"],
                "preview_fingerprint": preview["preview_fingerprint"],
                "idempotency_key": "expired-1",
            },
        )
    with pytest.raises(BridgeError, match="preview does not match"):
        await bridge.call(
            "controlcenter_action_apply",
            {
                "host_id": "macbook-air",
                "action_key": "restart-agent",
                "preview_id": preview["preview_id"],
                "preview_fingerprint": preview["preview_fingerprint"],
                "idempotency_key": "wrong-host-1",
            },
        )
