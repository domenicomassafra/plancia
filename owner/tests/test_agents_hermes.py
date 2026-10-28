"""Hermes native CLI adapter: owner-only access, no new runtimes or session writes."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import (ALLOWED_ORIGINS_ENV, OWNER_LOGIN_ENV,
                          TAILSCALE_LOGIN_HEADER, TRUSTED_PROXY_IPS_ENV,
                          SessionStore, TailnetAuthMiddleware)


ORIGIN = "https://fleet.example.test"
HEADERS = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
PROFILES = """ Profile          Model                        Gateway      Alias        Distribution
 ───────────────    ───────────────────────────    ───────────    ───────────    ────────────────────
 ◆Signor Coding (signor-coding) model-x                      running      signor-coding  Coding@1.0
   signor-studio   model-y                      stopped      —            Studio@1.0
   intruder        model-z                      running      —            Other@1.0
"""
SESSIONS = """Title                            Preview                                  Last Active   ID
─────────────────────────────────────────────────────────────────────────────────────────────
Welcome                          Hello                                    3m            sess_123
Second                           More                                     1h            sess_456
"""


@pytest.fixture
def client(tmp_path):
    from backend import agents_hermes

    for name in ("signor-coding", "signor-studio"):
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
                                  TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8"}), \
         patch.object(agents_hermes, "SIGNORI_ROOT", tmp_path):
        yield TestClient(app, base_url=ORIGIN)


def test_owner_session_and_allowlisted_profiles(client):
    from backend import agents_hermes

    runner = AsyncMock(return_value=PROFILES)
    with patch.object(agents_hermes, "_hermes", runner):
        assert client.get("/api/v1/hermes/profiles", headers=HEADERS).status_code == 401
        assert client.get("/api/v1/hermes/profiles").status_code == 401
        # Only an already-authenticated owner can inspect profiles.
        assert client.get("/", headers=HEADERS).status_code == 200
        response = client.get("/api/v1/hermes/profiles", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"source": "hermes-native", "profiles": [
            {"id": "signor-coding", "name": "Signor Coding", "host": "minipc",
             "gateway": "running", "version": None,
             "capabilities": {"readable": True, "attachable": False, "sendable": False}},
            {"id": "signor-studio", "name": "signor-studio", "host": "minipc",
             "gateway": "stopped", "version": None,
             "capabilities": {"readable": True, "attachable": False, "sendable": False}},
        ]}
        runner.assert_awaited_once_with("profile", "list")


def test_sessions_readback_and_cross_profile_denied(client):
    from backend import agents_hermes

    record = {"id": "sess_123", "messages": [
        {"role": "user", "content": "testo redatto", "timestamp": "2026-10-23T09:00:00Z"},
        {"role": "assistant", "content": "risposta", "timestamp": None},
    ]}

    async def native(*args):
        if args == ("profile", "list"):
            return PROFILES
        if args == ("--profile", "signor-coding", "sessions", "list", "--limit", "20"):
            return SESSIONS
        if args == ("--profile", "signor-coding", "sessions", "export", "--session-id",
                    "sess_123", "--format", "jsonl", "--redact", "-"):
            return json.dumps(record) + "\n"
        raise AssertionError(args)

    with patch.object(agents_hermes, "_hermes", side_effect=native) as runner:
        # Bootstrap is not available through the API itself.
        assert client.get("/api/v1/hermes/profiles/signor-coding/sessions", headers=HEADERS).status_code == 401
        client.get("/", headers=HEADERS)
        url = "/api/v1/hermes/profiles/signor-coding/sessions"
        sessions = client.get(url, headers=HEADERS)
        assert sessions.status_code == 200
        assert [s["id"] for s in sessions.json()["sessions"]] == ["sess_123", "sess_456"]
        messages = client.get(url + "/sess_123/messages", headers=HEADERS)
        assert messages.status_code == 200, messages.text
        assert messages.json()["redacted"] is True
        assert messages.json()["messages"][0]["text"] == "testo redatto"
        assert messages.json()["session_id"] == "sess_123"
        assert client.get(url + "/other_999/messages", headers=HEADERS).status_code == 404
        assert client.get("/api/v1/hermes/profiles/intruder/sessions", headers=HEADERS).status_code == 404
        assert client.get("/api/v1/hermes/profiles/signor-coding/sessions?limit=21", headers=HEADERS).status_code == 422
        assert client.get("/api/v1/hermes/profiles/%2e%2e/sessions", headers=HEADERS).status_code in (404, 422)
        assert not any("other_999" in args for args in (call.args for call in runner.call_args_list))
        assert not any("intruder" in args for args in (call.args for call in runner.call_args_list))


def test_malformed_output_fails_closed(client):
    from backend import agents_hermes

    client.get("/", headers=HEADERS)
    with patch.object(agents_hermes, "_hermes", AsyncMock(return_value="unrecognised CLI format")):
        assert client.get("/api/v1/hermes/profiles", headers=HEADERS).status_code == 503
    with patch.object(agents_hermes, "_hermes", AsyncMock(side_effect=[PROFILES, "broken table"])):
        assert client.get("/api/v1/hermes/profiles/signor-coding/sessions", headers=HEADERS).status_code == 503
    with patch.object(agents_hermes, "_hermes", AsyncMock(side_effect=[PROFILES, SESSIONS, "{broken}"])):
        assert client.get("/api/v1/hermes/profiles/signor-coding/sessions/sess_123/messages", headers=HEADERS).status_code == 503
    wrong = json.dumps({"id": "other_222", "messages": []})
    with patch.object(agents_hermes, "_hermes", AsyncMock(side_effect=[PROFILES, SESSIONS, wrong])):
        assert client.get("/api/v1/hermes/profiles/signor-coding/sessions/sess_123/messages", headers=HEADERS).status_code == 503


def test_message_payload_stays_within_browser_limit():
    from backend import agents_hermes

    exported = json.dumps({"id": "sess_123", "messages": [
        {"role": "assistant", "content": "a" * 8192} for _ in range(50)]})
    result = agents_hermes._parse_export(exported, "sess_123")
    assert len(result) == 50
    assert len(json.dumps({"source": "hermes-native", "profile_id": "signor-coding",
                           "session_id": "sess_123", "redacted": True, "messages": result})) < 262_144


def test_cli_bounds_timeout_and_no_runtime_launch():
    from backend import agents_hermes

    class Reader:
        async def read(self, n):
            return b"x" * n

    class Process:
        stdout = Reader()
        returncode = None
        killed = False

        def kill(self):
            self.killed = True

        async def communicate(self):
            self.returncode = -9
            return b"", b""

    process = Process()
    with patch.object(agents_hermes.shutil, "which", return_value="/usr/bin/hermes"), \
         patch.object(agents_hermes.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
        with pytest.raises(agents_hermes.HTTPException) as error:
            asyncio.run(agents_hermes._hermes("profile", "list"))
        assert error.value.status_code == 503
        assert process.killed
        assert spawn.call_args.args[:3] == ("/usr/bin/hermes", "profile", "list")
