"""OmniRoute is read through the owner controller, with no browser-side authority."""

import hashlib
import importlib.util
import json
import sys
import asyncio
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.auth import (  # noqa: E402
    ALLOWED_ORIGINS_ENV,
    OWNER_LOGIN_ENV,
    TAILSCALE_LOGIN_HEADER,
    SessionStore,
    TailnetAuthMiddleware,
)
from backend import omniroute_status  # noqa: E402

ORIGIN = "https://fleet.example.test"
OWNER = "owner@example.test"


def test_overlapping_refreshes_share_one_native_probe(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []
    def observe():
        calls.append(1)
        started.set()
        assert release.wait(5)
        return {"result": "unavailable"}
    monkeypatch.setattr(omniroute_status, "_observe", observe)
    monkeypatch.setattr(omniroute_status, "_policy", lambda: {"available": True})
    if hasattr(omniroute_status, "_inflight"):
        monkeypatch.setattr(omniroute_status, "_inflight", None)
    from fastapi import Response
    async def scenario():
        first = asyncio.create_task(omniroute_status.omniroute_status(Response()))
        second = None
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(0.01)
            assert started.is_set()
            second = asyncio.create_task(omniroute_status.omniroute_status(Response()))
            await asyncio.sleep(0.1)
            count = len(calls)
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []))
        assert count == 1, "concurrent browser refreshes must not spawn multiple fleet controllers"
    asyncio.run(scenario())


@pytest.mark.parametrize("configured_home", [False, True])
def test_release_layout_reads_canonical_policy(monkeypatch, tmp_path, configured_home):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    canonical = tmp_path / "custom-controlcenter" if configured_home else home / "ControlCenter"
    if configured_home:
        monkeypatch.setenv("CONTROLCENTER_HOME", str(canonical))
    else:
        monkeypatch.delenv("CONTROLCENTER_HOME", raising=False)
    policy = canonical / "omniroute" / "fleet-default-models.json"
    policy.parent.mkdir(parents=True)
    raw = json.dumps({
        "schema_version": 2, "kind": "omniroute-fleet-policy",
        "models": ["test/canonical-model"],
        "harness_defaults": {"tasks": {"default": {"route": "test/canonical-model"}}},
    }).encode()
    policy.write_bytes(raw)
    release_module = canonical / "_local" / "dashboard-releases" / "fixture" / "backend" / "omniroute_status.py"
    release_module.parent.mkdir(parents=True)
    release_module.write_bytes(Path(omniroute_status.__file__).read_bytes())
    spec = importlib.util.spec_from_file_location("release_omniroute_status", release_module)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    observed = module._policy()
    assert observed["available"] is True
    assert observed["sha256"] == hashlib.sha256(raw).hexdigest()
    assert observed["models"] == ["test/canonical-model"]
    assert observed["defaults"]["default"]["route"] == "test/canonical-model"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv(OWNER_LOGIN_ENV, OWNER)
    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, ORIGIN)
    policy = tmp_path / "fleet-default-models.json"
    policy.write_text(json.dumps({
        "schema_version": 2,
        "kind": "omniroute-fleet-policy",
        "models": ["test/safe-model"],
        "harness_defaults": {"tasks": {"default": {"route": "test/safe-model"}}},
    }))
    monkeypatch.setattr(omniroute_status, "POLICY_PATH", policy)
    app = FastAPI()
    app.add_middleware(TailnetAuthMiddleware, store=SessionStore())

    @app.get("/")
    def home():
        return {"ok": True}

    app.include_router(omniroute_status.router)
    return TestClient(app, base_url=ORIGIN)


def fake_check(monkeypatch, result, code, cells, scope_complete=True):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {
            "returncode": code,
            "stdout": json.dumps({
                "schema_version": 2,
                "operation": "check",
                "result": result,
                "scope_complete": scope_complete,
                "checked_at": "2026-10-22T08:12:00+00:00",
                "policy_revision": "canonical-revision",
                "items": cells,
                "secret_prerequisites": {"secret_token": "NEVER_EXPOSE"},
            }),
        })()

    monkeypatch.setattr(omniroute_status.subprocess, "run", run)
    return calls


def authenticated(client):
    headers = {TAILSCALE_LOGIN_HEADER: OWNER}
    assert client.get("/", headers=headers).status_code == 200
    return headers


def test_anonymous_and_non_owner_cannot_observe_policy(client, monkeypatch):
    calls = fake_check(monkeypatch, "clean", 0, [{"host": "minipc", "component": "omniroute-core", "result": "clean"}])
    assert client.get("/api/omniroute").status_code == 401
    assert client.get("/api/omniroute", headers={TAILSCALE_LOGIN_HEADER: "other@example.test"}).status_code == 403
    assert calls == []


def test_native_exit_two_is_drift_and_private_payload_is_not_exposed(client, monkeypatch):
    calls = fake_check(monkeypatch, "changes", 2, [
        {"host": "minipc", "component": "omniroute-core", "result": "changes", "evidence": {"secret_token": "NEVER_EXPOSE"}},
    ])
    response = client.get("/api/omniroute", headers=authenticated(client))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["observation"]["result"] == "changes"
    assert data["observation"]["exit_code"] == 2
    assert data["observation"]["checked_at"] == "2026-10-22T08:12:00+00:00"
    assert data["observation"]["policy_revision"] == "canonical-revision"
    assert data["policy"]["models"] == ["test/safe-model"]
    assert data["policy"]["sha256"]
    assert "NEVER_EXPOSE" not in response.text
    assert len(calls) == 1
    assert calls[0][0] == omniroute_status.CHECK_COMMAND
    assert "omniroute-catalog" in calls[0][0][6]
    assert calls[0][1]["timeout"] == 25


@pytest.mark.parametrize("native_result,code,complete,items,display", [
    ("clean", 0, True, [{"host": "minipc", "component": "omniroute-core", "result": "clean"}], "clean"),
    ("clean", 0, False, [{"host": "minipc", "component": "omniroute-core", "result": "clean"}], "unavailable"),
    ("clean", 0, True, [], "unavailable"),
    ("clean", 0, True, [{"host": "minipc", "result": "clean"}], "unavailable"),
    ("clean", 2, True, [{"host": "minipc", "component": "omniroute-core", "result": "changes"}], "unavailable"),
    ("blocked", 3, False, [], "blocked"),
])
def test_clean_requires_complete_native_proof(client, monkeypatch, native_result, code, complete, items, display):
    fake_check(monkeypatch, native_result, code, items, scope_complete=complete)
    response = client.get("/api/omniroute", headers=authenticated(client))
    assert response.status_code == 200
    assert response.json()["observation"]["result"] == display


def test_unavailable_controller_does_not_forge_health(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("missing controlcenter")

    monkeypatch.setattr(omniroute_status.subprocess, "run", unavailable)
    response = client.get("/api/omniroute", headers=authenticated(client))
    assert response.json()["observation"]["result"] == "unavailable"
    assert response.json()["observation"]["checked_at"] is None


def test_missing_canonical_policy_cannot_show_green(client, monkeypatch):
    calls = fake_check(monkeypatch, "clean", 0, [
        {"host": "minipc", "component": "omniroute-core", "result": "clean"},
    ])
    monkeypatch.setattr(omniroute_status, "POLICY_PATH", Path("/no/such/omniroute-policy.json"))
    response = client.get("/api/omniroute", headers=authenticated(client))
    assert response.json()["policy"]["available"] is False
    assert response.json()["observation"]["result"] == "unavailable"
    assert len(calls) == 1
