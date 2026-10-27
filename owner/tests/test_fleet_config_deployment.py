"""The public Plancia build must load its private installed fleet inventory."""

import json
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
