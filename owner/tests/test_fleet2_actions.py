"""An action is offered only when the host proved it can run it unattended."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.fleet2 import actions as actions_module
from backend.fleet2.actions import ActionEngine
from backend.fleet2.api import ActionRequest
from backend.fleet2.registry import load_registry
from backend.fleet2.store import Store


class FakeResult:
    def __init__(self, stdout="", ok=True, exit_code=0, failure=None, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.ok = ok
        self.exit_code = exit_code
        self.failure = failure


def engine_for(tmp_path, monkeypatch, probe_stdout="rc=0", ok=True):
    calls = []

    def fake_ssh(alias, command, **kwargs):
        calls.append((alias, command))
        if "sudo -n -l" in command or "IsInRole" in command:
            return FakeResult(probe_stdout, ok=ok)
        if (
            "systemctl --user cat fleetprobe.service" in command
            or "launchctl print" in command
            or "Get-ScheduledTask" in command
        ):
            return FakeResult("rc=0", ok=True)
        return FakeResult("done")

    monkeypatch.setattr(actions_module, "run_ssh", fake_ssh)
    registry = load_registry()
    store = Store(tmp_path / "fleet2.sqlite3")
    return ActionEngine(registry, store), store, registry, calls


def test_privileged_host_can_reboot(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)
    capability = engine.capabilities(registry.get("mac-studio"))
    assert capability["privileged"] is True
    assert capability["actions"]["reboot"]["available"] is True


def test_action_request_rejects_untyped_extra_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ActionRequest(
            confirmation="",
            idempotency_key="typed-request-key",
            command="rm -rf /",
        )


def test_action_probes_bypass_sandboxed_system_ssh_config(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)
    seen = []

    def fake_ssh(alias, command, **kwargs):
        seen.append(kwargs.get("config_path"))
        if "sudo -n -l" in command:
            return FakeResult("rc=0")
        if "launchctl print" in command:
            return FakeResult("rc=0")
        return FakeResult("done")

    monkeypatch.setattr(actions_module, "run_ssh", fake_ssh)
    engine.capabilities(registry.get("mac-studio"), refresh=True)
    assert seen
    assert all(path == actions_module.SSH_CONFIG_PATH for path in seen)
    assert actions_module.SSH_CONFIG_PATH.endswith("/.ssh/config")


def test_silent_transport_failure_is_reported_without_attribute_error(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)

    def silent_failure(alias, command, **kwargs):
        return FakeResult(ok=False, exit_code=255, failure="unreachable")

    monkeypatch.setattr(actions_module, "run_ssh", silent_failure)
    capability = engine.capabilities(registry.get("mac-studio"), refresh=True)
    assert capability["transport_ok"] is False
    assert capability["probe_detail"] == "ssh exit 255"
    assert capability["actions"]["restart-agent"]["available"] is False


def test_password_prompt_blocks_destructive_actions_with_a_fix(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(
        tmp_path, monkeypatch, probe_stdout="sudo: a password is required\nrc=1"
    )
    capability = engine.capabilities(registry.get("macbook-air"))
    assert capability["privileged"] is False
    reboot = capability["actions"]["reboot"]
    assert reboot["available"] is False
    assert "privilegio unattended non verificato" in reboot["reason"]
    assert "visudo" in reboot["fix"]
    # The safe action stays usable.
    assert capability["actions"]["restart-agent"]["available"] is True


def test_control_plane_cannot_be_powered_off_from_its_own_dashboard(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)
    capability = engine.capabilities(registry.get("minipc"))
    for key in ("reboot", "shutdown"):
        assert capability["actions"][key]["available"] is False
        assert "control plane" in capability["actions"][key]["reason"]


def test_policy_block_never_advertises_a_privilege_fix(tmp_path, monkeypatch):
    """A recovery hint must match the real blocker, not just the platform.

    The control plane is fully privileged, so the sudoers remediation is not
    the way to enable its power actions: showing it would tell the operator to
    "fix" a host that is intentionally blocked by policy.
    """
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)
    capability = engine.capabilities(registry.get("minipc"))
    assert capability["privileged"] is True
    for key in ("reboot", "shutdown"):
        entry = capability["actions"][key]
        assert entry["available"] is False
        assert "control plane" in entry["reason"]
        assert entry["fix"] is None


def test_destructive_dispatch_requires_the_typed_word(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    with pytest.raises(ValueError):
        engine.dispatch(host, "reboot", confirmation="ok")
    with pytest.raises(ValueError):
        engine.dispatch(host, "reboot", confirmation="")


def test_unavailable_action_is_refused_before_any_command_runs(tmp_path, monkeypatch):
    engine, _, registry, calls = engine_for(
        tmp_path, monkeypatch, probe_stdout="sudo: a password is required\nrc=1"
    )
    with pytest.raises(PermissionError):
        engine.dispatch(registry.get("macbook-air"), "shutdown", confirmation="CONFERMA")
    assert all(command != "sudo -n shutdown -h now" for _, command in calls)


def test_dispatch_opens_a_receipt(tmp_path, monkeypatch):
    engine, store, registry, _ = engine_for(tmp_path, monkeypatch)
    result = engine.dispatch(registry.get("mac-studio"), "restart-agent", confirmation="")
    assert result["receipt_id"]
    assert result["replayed"] is False
    receipts = store.receipts()
    assert receipts[0]["action"] == "restart-agent"
    assert receipts[0]["host_id"] == "mac-studio"


def test_dispatch_idempotency_suppresses_duplicate_command(tmp_path, monkeypatch):
    engine, store, registry, calls = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    key = "host-action-request-123"

    first = engine.dispatch(
        host,
        "restart-agent",
        confirmation="",
        idempotency_key=key,
    )
    second = engine.dispatch(
        host,
        "restart-agent",
        confirmation="",
        idempotency_key=key,
    )
    assert first["receipt_id"] == second["receipt_id"]
    assert first["replayed"] is False
    assert second["replayed"] is True

    import time

    for _ in range(50):
        receipt = store.receipt(first["receipt_id"])
        if receipt and receipt["outcome"] != "running":
            break
        time.sleep(0.01)
    restart_commands = [command for _, command in calls if "launchctl kickstart" in command]
    assert len(restart_commands) == 1
    assert receipt["payload"].get("idempotency_digest")
    assert key not in str(receipt["payload"])


def test_idempotent_retry_returns_receipt_even_if_runtime_became_unreachable(tmp_path, monkeypatch):
    engine, store, registry, _ = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    key = "host-action-retry-after-drop"
    first = engine.dispatch(host, "restart-agent", "", idempotency_key=key)

    import time

    for _ in range(50):
        receipt = store.receipt(first["receipt_id"])
        if receipt and receipt["outcome"] != "running":
            break
        time.sleep(0.01)

    monkeypatch.setattr(
        actions_module,
        "run_ssh",
        lambda *_a, **_k: FakeResult(ok=False, exit_code=255, failure="unreachable"),
    )
    replay = engine.dispatch(host, "restart-agent", "", idempotency_key=key)
    assert replay["replayed"] is True
    assert replay["receipt_id"] == first["receipt_id"]
    assert replay["outcome"] == receipt["outcome"]


def test_dispatch_rejects_invalid_idempotency_key(tmp_path, monkeypatch):
    engine, _, registry, _ = engine_for(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="idempotency_key"):
        engine.dispatch(
            registry.get("mac-studio"),
            "restart-agent",
            confirmation="",
            idempotency_key="short",
        )


def test_power_action_does_not_claim_dispatch_on_remote_command_failure(tmp_path, monkeypatch):
    engine, store, registry, _ = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    # Prime a valid capability result before replacing the actual dispatch.
    engine.capabilities(host)
    def command_failure(alias, command, **kwargs):
        if "sudo -n -l" in command or "launchctl print" in command:
            return FakeResult("rc=0", ok=True)
        return FakeResult(
            ok=False,
            exit_code=1,
            failure="agent_failed",
            stderr="sudo: permission denied",
        )

    monkeypatch.setattr(actions_module, "run_ssh", command_failure)
    receipt_id = engine.dispatch(host, "reboot", confirmation="CONFERMA")["receipt_id"]
    for _ in range(50):
        receipt = store.receipt(receipt_id)
        if receipt and receipt["outcome"] != "running":
            break
        import time

        time.sleep(0.01)
    assert receipt["outcome"] == "failed"
    assert "permission denied" in (receipt["detail"] or "")


def test_power_action_transport_drop_is_recorded_as_dispatched_not_succeeded(tmp_path, monkeypatch):
    engine, store, registry, _ = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    engine.capabilities(host)
    def transport_drop(alias, command, **kwargs):
        if "sudo -n -l" in command or "launchctl print" in command:
            return FakeResult("rc=0", ok=True)
        return FakeResult(
            ok=False,
            exit_code=255,
            failure="unreachable",
            stderr="connection closed",
        )

    monkeypatch.setattr(actions_module, "run_ssh", transport_drop)
    receipt_id = engine.dispatch(host, "shutdown", confirmation="CONFERMA")["receipt_id"]
    for _ in range(50):
        receipt = store.receipt(receipt_id)
        if receipt and receipt["outcome"] != "running":
            break
        import time

        time.sleep(0.01)
    assert receipt["outcome"] == "dispatched"


def test_capability_probe_is_cached_per_host(tmp_path, monkeypatch):
    engine, _, registry, calls = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    engine.capabilities(host)
    engine.capabilities(host)
    probes = [command for _, command in calls if "sudo -n -l" in command]
    assert len(probes) == 1


def test_dispatch_revalidates_capability_instead_of_trusting_cache(tmp_path, monkeypatch):
    engine, _, registry, calls = engine_for(tmp_path, monkeypatch)
    host = registry.get("mac-studio")
    assert engine.capabilities(host)["actions"]["restart-agent"]["available"] is True

    def unavailable(alias, command, **kwargs):
        calls.append((alias, command))
        if "sudo -n -l" in command:
            return FakeResult("rc=1", ok=True)
        if "launchctl print" in command:
            return FakeResult("rc=1", ok=True)
        return FakeResult("must-not-run", ok=True)

    monkeypatch.setattr(actions_module, "run_ssh", unavailable)
    with pytest.raises(PermissionError, match="FleetProbe"):
        engine.dispatch(host, "restart-agent", confirmation="")
    assert all("kickstart" not in command for _, command in calls)


def test_matebook_runtime_identities_never_substitute_windows_with_linux(tmp_path, monkeypatch):
    engine, _, registry, calls = engine_for(tmp_path, monkeypatch)
    linux = registry.get("matebooklinux")
    windows = registry.get("matebook")

    linux_capabilities = engine.capabilities(linux)
    windows_capabilities = engine.capabilities(windows)

    assert linux_capabilities["host_id"] == "matebooklinux"
    assert windows_capabilities["host_id"] == "matebook"
    assert any(alias == "matebooklinux" and "sudo -n -l" in command for alias, command in calls)
    assert not any(alias == "matebook" for alias, _ in calls)
    assert linux_capabilities["actions"]["restart-agent"]["available"] is True
    assert all(not entry["available"] for entry in windows_capabilities["actions"].values())
    assert all("pending/offline" in entry["reason"] for entry in windows_capabilities["actions"].values())


def test_host_preview_requires_owner_csrf_and_writes_only_redacted_dry_run(tmp_path, monkeypatch):
    import os
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from backend import server
    from backend.auth import (
        ALLOWED_ORIGINS_ENV, CSRF_COOKIE, CSRF_HEADER,
        OWNER_LOGIN_ENV, TAILSCALE_LOGIN_HEADER,
    )
    from backend.runtime_config import STATE_DIR_ENV

    origin = "https://fleet.example.test"
    owner = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
    commands = []

    def fake_ssh(alias, command, **kwargs):
        commands.append(command)
        return FakeResult("rc=0")

    monkeypatch.setattr(actions_module, "run_ssh", fake_ssh)
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: "owner@example.test",
        ALLOWED_ORIGINS_ENV: origin,
        STATE_DIR_ENV: str(tmp_path),
        "FLEETPROBE_TOKEN": "fixture-only-token",
    }):
        app = server.create_app()
        client = TestClient(app, base_url=origin)
        path = "/api/actions/mac-studio/restart-agent/preview"
        assert client.post(path).status_code == 401
        assert client.get("/api/v1/session", headers=owner).status_code == 200
        assert client.post(path, headers={**owner, "Origin": origin}).status_code == 403

        csrf = {**owner, "Origin": origin, CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}
        response = client.post(path, headers=csrf)
        assert response.status_code == 200, response.text
        assert response.json()["preview"] == {
            "status": "dry-run", "mutation_count": 0, "requires_confirmation": False,
        }
        receipt_id = response.json()["receipt_id"]
        audit = client.get("/api/receipts", headers=owner)
        assert audit.status_code == 200
        row = next(row for row in audit.json()["receipts"] if row["id"] == receipt_id)
        assert row["action"] == "restart-agent-preview"
        assert row["outcome"] == "dry-run"
        assert row["detail"] is None
        assert row["payload"] == {}

        blocked = client.post("/api/actions/minipc/shutdown/preview", headers=csrf)
        assert blocked.status_code == 409
        assert "control plane" in blocked.json()["detail"]
        assert len(client.get("/api/receipts", headers=owner).json()["receipts"]) == 1
        assert commands
        assert all("restart fleetprobe" not in command and "poweroff" not in command.split(" ")[:3]
                   and "shutdown -" not in command for command in commands)
