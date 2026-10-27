"""Hermes #48: input crosses the owner HTTP and already-running native gateway boundaries."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import agents_hermes, hermes_native_input
from backend.auth import (ALLOWED_ORIGINS_ENV, CSRF_COOKIE, CSRF_HEADER,
                          OWNER_LOGIN_ENV, TAILSCALE_LOGIN_HEADER,
                          TRUSTED_PROXY_IPS_ENV, SessionStore, TailnetAuthMiddleware)


ORIGIN = "https://fleet.example.test"
HEADERS = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
PROFILES = """Profile  Model  Gateway  Alias  Distribution
────────  ─────  ───────  ─────  ────────────
signor-coding model-x running alias Coding@1.0
signor-other model-x running alias Other@1.0
"""
SESSIONS = """Title        Preview      Last Active   ID
─────────────────────────────────────────────
Example      test         3m            sess_123
"""
BASE = "/api/v1/hermes/profiles/signor-coding/sessions/sess_123"
INSTANCE = "native-instance-001"
EPOCH = 12


def test_native_ticket_subject_is_namespaced_for_authenticated_owner():
    # Hermes 0.22.5 control socket refuses bare forwarded identities, even
    # when the caller is the verified same-user Unix peer.
    subject = hermes_native_input._owner_subject("owner@example.test")
    assert subject.startswith("auth:v1:")
    assert json.loads(subject.removeprefix("auth:v1:")) == [
        "tailscale", "controlcenter", "owner@example.test"]
    assert hermes_native_input._owner_subject("owner@example.test") == subject


def test_native_ticket_subject_rejects_untrusted_login():
    for bad in ("", "  ", "bad\nlogin", "a" * 240, "a" * 257, None):
        with pytest.raises(hermes_native_input.Refused):
            hermes_native_input._owner_subject(bad)


@pytest.fixture
def client(tmp_path):
    for name in ("signor-coding", "signor-other"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "distribution.yaml").write_text(f"name: {name}\n")
    app = FastAPI()
    app.add_middleware(TailnetAuthMiddleware, store=SessionStore())
    app.include_router(agents_hermes.router)

    @app.get("/")
    def home():
        return {"ok": True}

    with patch.dict(os.environ, {OWNER_LOGIN_ENV: HEADERS[TAILSCALE_LOGIN_HEADER],
                                  ALLOWED_ORIGINS_ENV: ORIGIN,
                                  TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8",
                                  "CONTROLCENTER_HERMES_INPUT_ENABLED": "1"}), \
         patch.object(agents_hermes, "SIGNORI_ROOT", tmp_path):
        with TestClient(app, base_url=ORIGIN) as test_client:
            yield test_client


def authorized(client):
    assert client.get("/", headers=HEADERS).status_code == 200
    csrf = client.cookies.get(CSRF_COOKIE)
    assert csrf
    return {**HEADERS, "Origin": ORIGIN, CSRF_HEADER: csrf}


def native(*args):
    if args == ("profile", "list"):
        return PROFILES
    if args == ("--profile", "signor-coding", "sessions", "list", "--limit", "20"):
        return SESSIONS
    raise AssertionError(f"Unexpected CLI: {args}")


def test_attach_requires_existing_owner_session_origin_csrf_and_known_session(client):
    gateway = AsyncMock(return_value={"profile_id": "signor-coding", "session_id": "sess_123",
                                      "instance_id": INSTANCE, "authority_epoch": EPOCH})
    with patch.object(agents_hermes, "_hermes", side_effect=native), \
         patch.object(agents_hermes, "_native_gateway", gateway):
        assert client.post(BASE + "/attach", headers=HEADERS).status_code == 401
        valid = authorized(client)
        assert client.post(BASE + "/attach", headers=HEADERS).status_code == 403
        assert client.post(BASE + "/attach", headers={**valid, "Origin": "https://evil.test"}).status_code == 403
        assert client.post(BASE + "/attach", headers={**valid, CSRF_HEADER: "wrong"}).status_code == 403
        assert client.post(BASE.replace("sess_123", "missing") + "/attach", headers=valid).status_code == 404
        assert client.post(BASE.replace("signor-coding", "intruder") + "/attach", headers=valid).status_code == 404
        response = client.post(BASE + "/attach", headers=valid)
        assert response.status_code == 200, response.text
        assert response.json() == {"source": "hermes-native", "profile_id": "signor-coding",
                                   "session_id": "sess_123", "instance_id": INSTANCE,
                                   "authority_epoch": EPOCH}
        assert response.headers["cache-control"] == "no-store"
        gateway.assert_awaited_once_with("attach", "signor-coding", "sess_123",
                                         HEADERS[TAILSCALE_LOGIN_HEADER], None)


def test_send_reuses_exact_native_instance_session_and_nonce(client):
    calls = []

    async def gateway(action, profile_id, session_id, subject, payload):
        calls.append((action, profile_id, session_id, subject, payload))
        if action == "attach":
            return {"profile_id": profile_id, "session_id": session_id,
                    "instance_id": INSTANCE, "authority_epoch": EPOCH}
        assert payload == {"instance_id": INSTANCE, "authority_epoch": EPOCH,
                           "nonce": "nonce_12345678", "text": "synthetic test"}
        return {"profile_id": profile_id, "session_id": session_id,
                "instance_id": INSTANCE, "authority_epoch": EPOCH,
                "admission_id": "admit_123", "status": "queued"}

    with patch.object(agents_hermes, "_hermes", side_effect=native), \
         patch.object(agents_hermes, "_native_gateway", side_effect=gateway):
        valid = authorized(client)
        attach = client.post(BASE + "/attach", headers=valid)
        assert attach.status_code == 200
        payload = {"instance_id": attach.json()["instance_id"],
                   "authority_epoch": attach.json()["authority_epoch"],
                   "nonce": "nonce_12345678", "text": "synthetic test"}
        first = client.post(BASE + "/send", headers=valid, json=payload)
        replay = client.post(BASE + "/send", headers=valid, json=payload)
        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json() == {
            "source": "hermes-native", "profile_id": "signor-coding", "session_id": "sess_123",
            "instance_id": INSTANCE, "authority_epoch": EPOCH,
            "admission_id": "admit_123", "status": "queued"}
        assert [item[0] for item in calls] == ["attach", "send", "send"]
        assert calls[1] == calls[2]
        assert client.post(BASE + "/send", headers=valid,
                           json={**payload, "nonce": "x"}).status_code == 422
        assert client.post(BASE + "/send", headers=valid,
                           json={**payload, "text": ""}).status_code == 422


def test_send_is_denied_by_origin_and_csrf_before_native_dispatch(client):
    body = {"instance_id": INSTANCE, "authority_epoch": EPOCH,
            "nonce": "nonce_12345678", "text": "synthetic test"}
    native_call = AsyncMock()
    with patch.object(agents_hermes, "_native_gateway", native_call):
        valid = authorized(client)
        assert client.post(BASE + "/send", headers={**valid, "Origin": "https://evil.test"},
                           json=body).status_code == 403
        assert client.post(BASE + "/send", headers={**valid, CSRF_HEADER: "invalid"},
                           json=body).status_code == 403
        native_call.assert_not_awaited()


def test_missing_native_client_and_mismatched_gateway_fail_closed(client, tmp_path):
    valid = authorized(client)
    with patch.object(agents_hermes, "_hermes", side_effect=native), \
         patch.object(agents_hermes, "_NATIVE_ROOT", tmp_path / "missing"):
        assert client.post(BASE + "/attach", headers=valid).status_code == 503
    with patch.object(agents_hermes, "_hermes", side_effect=native), \
         patch.object(agents_hermes, "_native_gateway", AsyncMock(return_value={
             "profile_id": "signor-other", "session_id": "sess_123",
             "instance_id": INSTANCE, "authority_epoch": EPOCH})):
        assert client.post(BASE + "/attach", headers=valid).status_code == 503


def test_no_input_admitted_without_verified_native_transport(client, tmp_path):
    valid = authorized(client)
    # A test-enabled POST path does not imply an end-user capability grant.
    with patch.object(agents_hermes, "_hermes", return_value=PROFILES), \
         patch.object(agents_hermes, "_NATIVE_ROOT", tmp_path / "missing-native"):
        inventory = client.get("/api/v1/hermes/profiles", headers=HEADERS)
        assert inventory.status_code == 200
        assert all(not p["capabilities"]["attachable"] and not p["capabilities"]["sendable"]
                   for p in inventory.json()["profiles"])
    with patch.dict(os.environ, {"CONTROLCENTER_HERMES_INPUT_ENABLED": "0"}):
        assert client.post(BASE + "/attach", headers=valid).status_code == 503
        assert client.post(BASE + "/send", headers=valid, json={
            "instance_id": INSTANCE, "authority_epoch": EPOCH,
            "nonce": "nonce_12345678", "text": "synthetic test"}).status_code == 503


def test_advertised_input_requires_original_native_client_and_explicit_operator_gate(client, tmp_path):
    # Inventory can promote grants only when the installed native client is present
    # and the operator has enabled input after an acceptance run.
    valid = authorized(client)
    with patch.object(agents_hermes, "_hermes", return_value=PROFILES), \
         patch.object(agents_hermes, "_NATIVE_ROOT", tmp_path):
        before = client.get("/api/v1/hermes/profiles", headers=valid).json()["profiles"]
        assert all(not row["capabilities"]["sendable"] for row in before)
        (tmp_path / "venv" / "bin").mkdir(parents=True)
        (tmp_path / "venv" / "bin" / "python").touch()
        (tmp_path / "hermes_cli").mkdir()
        (tmp_path / "hermes_cli" / "gateway_client.py").touch()
        after = client.get("/api/v1/hermes/profiles", headers=valid).json()["profiles"]
        assert all(row["capabilities"]["attachable"] and row["capabilities"]["sendable"]
                   for row in after)
        with patch.dict(os.environ, {"CONTROLCENTER_HERMES_INPUT_ENABLED": "0"}):
            disabled = client.get("/api/v1/hermes/profiles", headers=valid).json()["profiles"]
            assert all(not row["capabilities"]["attachable"] and not row["capabilities"]["sendable"]
                       for row in disabled)


def test_native_input_dispatches_one_official_client_without_starting_another_gateway(tmp_path):
    native_python = tmp_path / "venv" / "bin" / "python"
    native_python.parent.mkdir(parents=True)
    native_python.touch()
    native_client = tmp_path / "hermes_cli" / "gateway_client.py"
    native_client.parent.mkdir()
    native_client.touch()
    expected = {"profile_id": "signor-coding", "session_id": "sess_123",
                "instance_id": INSTANCE, "authority_epoch": EPOCH}
    stdin = Mock()
    stdin.drain = AsyncMock()
    stdout = SimpleNamespace(read=AsyncMock(side_effect=[
        json.dumps({"result": expected}).encode(),
        b"",
    ]))
    client_process = SimpleNamespace(stdin=stdin, stdout=stdout, returncode=0,
                                     wait=AsyncMock(return_value=0))
    launcher = AsyncMock(return_value=client_process)
    with patch.object(agents_hermes, "_NATIVE_ROOT", tmp_path), \
         patch.object(agents_hermes.asyncio, "create_subprocess_exec", launcher):
        result = asyncio.run(agents_hermes._native_gateway_client(
            "attach", "signor-coding", "sess_123", "owner@example.test", None))

    assert result == expected
    launcher.assert_awaited_once()
    argv = launcher.await_args.args
    assert argv == (str(native_python), "-I", str(agents_hermes.Path(
        agents_hermes.__file__).with_name("hermes_native_input.py")))
    assert set(launcher.await_args.kwargs["env"]) == {"HOME", "PATH", "LANG"}
    assert b'"action":"attach"' in stdin.write.call_args.args[0]
