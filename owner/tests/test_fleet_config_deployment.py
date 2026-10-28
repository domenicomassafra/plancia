"""The public Plancia build must load its private installed fleet inventory."""

import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from backend.fleet2.registry import load_registry


def test_installed_fleet_inventory_uses_explicit_private_file(monkeypatch, tmp_path):
    inventory = tmp_path / "fleet.json"
    inventory.write_text(json.dumps({
        "agent_port": 9777,
        "hosts": [{
            "id": "private-server", "name": "Private server",
            "agent_host": "server.example.invalid", "agent_ip": "192.0.2.10",
            "ssh_alias": "example", "os": "linux", "role": "primary",
        }],
    }))
    monkeypatch.setenv("PLANCIA_FLEET_CONFIG", str(inventory))
    load_registry.cache_clear()
    try:
        fleet = load_registry()
        assert fleet.agent_port == 9777
        assert [host.id for host in fleet.hosts] == ["private-server"]
    finally:
        load_registry.cache_clear()


def test_installed_server_refuses_public_sample_without_private_config(monkeypatch):
    monkeypatch.delenv("PLANCIA_FLEET_CONFIG", raising=False)
    monkeypatch.setenv("PLANCIA_REQUIRE_PRIVATE_CONFIG", "1")
    load_registry.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="explicit private fleet inventory"):
            load_registry()
    finally:
        load_registry.cache_clear()


def test_real_fleetctl_cached_status_reads_private_endpoint_fixture(tmp_path):
    """Same public host IDs, different private endpoints and names: use installed config."""
    owner = Path(__file__).resolve().parents[1]
    sample = json.loads((owner / "fleet.json").read_text())
    installed = tmp_path / ".config/plancia/fleet.json"
    installed.parent.mkdir(parents=True)
    private = {**sample, "hosts": [
        {**host, "name": "Installed private fixture", "agent_host": "private.example.test"}
        for host in sample["hosts"]
    ]}
    installed.write_text(json.dumps(private))
    token = tmp_path / "synthetic-fleetprobe-token"
    token.write_text("test-only-token")
    env = {**os.environ,
           "HOME": str(tmp_path),
           "CONTROLCENTER_DASHBOARD_STATE_DIR": str(tmp_path / "state"),
           "FLEETPROBE_TOKEN_FILE": str(token),
           "PLANCIA_FLEET_CONFIG": str(installed),
           "PLANCIA_REQUIRE_PRIVATE_CONFIG": "1"}
    result = subprocess.run(
        [sys.executable, str(owner / "fleetctl.py"), "status", "--cached"],
        cwd=owner, env=env, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    result_hosts = json.loads(result.stdout)["hosts"]
    assert [h["id"] for h in result_hosts] == [h["id"] for h in sample["hosts"]]
    assert result_hosts[0]["name"] == "Installed private fixture"
    assert result_hosts[0]["name"] != sample["hosts"][0]["name"]
