"""Contract tests for the same-origin, owner-only Orca session reader."""
import os
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import agents_api, codex_sessions
from backend.auth import (
    ALLOWED_ORIGINS_ENV, CSRF_COOKIE, CSRF_HEADER, OWNER_LOGIN_ENV,
    TAILSCALE_LOGIN_HEADER, TRUSTED_PROXY_IPS_ENV, SessionStore, TailnetAuthMiddleware,
)

ORIGIN = "https://fleet.example.test"
OWNER = "owner@example.test"
HEADERS = {TAILSCALE_LOGIN_HEADER: OWNER}


def client():
    app = FastAPI()
    app.add_middleware(TailnetAuthMiddleware, store=SessionStore())
    app.include_router(agents_api.router)
    app.include_router(codex_sessions.router)

    @app.get("/")
    def home():
        return {"ok": True}

    return TestClient(app, base_url=ORIGIN)


def test_owner_auth_csrf_and_search_bounding():
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: OWNER, ALLOWED_ORIGINS_ENV: ORIGIN,
        TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8",
    }):
        web = client()
        assert web.post("/api/v1/agents/search", json={"query": "repo"}).status_code == 401
        assert web.get("/", headers=HEADERS).status_code == 200
        assert web.post("/api/v1/agents/search", headers=HEADERS, json={"query": "repo"}).status_code == 403
        nonce = web.cookies.get(CSRF_COOKIE)
        headers = {**HEADERS, "Origin": ORIGIN, CSRF_HEADER: nonce}
        fake = AsyncMock(return_value={"hits": [{"agent": "codex", "sessionId": "test",
            "title": "example", "cwd": "/example", "updatedAt": "2026-10-21T00:00:00Z",
            "messageCount": 4, "evidence": {"preview": "short"}}], "truncated": False})
        with patch.object(agents_api, "search_history", fake):
            ok = web.post("/api/v1/agents/search", headers=headers,
                json={"query": "repo", "host": "minipc"})
            assert ok.status_code == 200, ok.text
            assert ok.headers["cache-control"] == "no-store"
            assert ok.json()["hits"][0]["agent"] == "codex"
            assert "resumeCommand" not in str(ok.json())
            assert "source" not in str(ok.json())
            assert web.post("/api/v1/agents/search", headers=headers,
                json={"query": "a", "host": "minipc"}).status_code == 422
            assert web.post("/api/v1/agents/search", headers=headers,
                json={"query": "q" * 121}).status_code == 422
            assert web.post("/api/v1/agents/search", headers=headers,
                json={"query": "repo", "host": "unknown"}).status_code == 422
            assert fake.await_count == 1


def test_read_only_ui_requires_owner_session():
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: OWNER, ALLOWED_ORIGINS_ENV: ORIGIN,
        TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8",
    }):
        web = client()
        assert web.get("/agents", headers=HEADERS).status_code == 401
        assert web.get("/", headers=HEADERS).status_code == 200
        response = web.get("/agents", headers=HEADERS)
        assert response.status_code == 200
        assert "Orca" in response.text
        assert "http://" not in response.text
        assert "pairing" not in response.text.lower()


def test_cli_stdout_limit_kills_process():
    import asyncio

    class Reader:
        remaining = 2_100_000
        async def read(self, n):
            size = min(n, self.remaining)
            self.remaining -= size
            return b"x" * size

    class Process:
        stdout = Reader()
        returncode = None
        killed = False
        def kill(self):
            self.killed = True
        async def communicate(self):
            await self.wait()
            return b"", b""
        async def wait(self):
            self.returncode = -9 if self.killed else 0
            return self.returncode

    process = Process()
    with patch.object(agents_api.asyncio, "create_subprocess_exec",
                      AsyncMock(return_value=process)):
        with __import__("pytest").raises(__import__("fastapi").HTTPException) as error:
            asyncio.run(agents_api._orca("search", "bounded"))
    assert error.value.status_code == 503
    assert process.killed


def test_cancelled_cli_search_kills_process():
    import asyncio

    class Reader:
        async def read(self, n):
            await asyncio.Event().wait()

    class Process:
        stdout = Reader()
        returncode = None
        killed = False
        def kill(self):
            self.killed = True
        async def communicate(self):
            await self.wait()
            return b"", b""
        async def wait(self):
            self.returncode = -9
            return self.returncode

    process = Process()

    async def scenario():
        with patch.object(agents_api.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=process)):
            task = asyncio.create_task(agents_api._orca("search", "cancel"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            with __import__("pytest").raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert process.killed


def test_large_real_subprocess_does_not_deadlock():
    """A real pipe with unread stdout must not keep the Orca slot occupied."""
    import asyncio
    import sys
    import pytest

    async def exercise():
        spawn = asyncio.create_subprocess_exec

        async def child(*_args, **kwargs):
            return await spawn(
                sys.executable, "-c",
                "import sys;sys.stdout.buffer.write(b'x'*4000000);sys.stdout.flush()",
                **kwargs,
            )

        with patch.object(agents_api.asyncio, "create_subprocess_exec", side_effect=child):
            with pytest.raises(__import__("fastapi").HTTPException) as exc:
                await asyncio.wait_for(agents_api._orca("search", "bounded"), timeout=7)
            assert exc.value.status_code == 503

    asyncio.run(exercise())


def test_cancel_real_subprocess_releases_slot():
    import asyncio
    import sys
    import pytest

    async def exercise():
        spawn = asyncio.create_subprocess_exec
        started = asyncio.Event()

        async def child(*_args, **kwargs):
            process = await spawn(sys.executable, "-c", "import time;time.sleep(30)", **kwargs)
            started.set()
            return process

        with patch.object(agents_api.asyncio, "create_subprocess_exec", side_effect=child):
            task = asyncio.create_task(agents_api._orca("search", "cancel"))
            await asyncio.wait_for(started.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

    asyncio.run(exercise())

def test_codex_account_selection_uses_isolated_home_and_idempotent_native_launch(tmp_path):
    """The HTTP contract must bind account and listed model without exposing credentials."""
    import json
    import uuid
    from backend import codex_sessions

    system = tmp_path / ".codex"
    managed = tmp_path / ".config/orca/codex-accounts/11111111-1111-4111-8111-111111111111/home"
    for home, model in ((system, "gpt-system"), (managed, "gpt-managed")):
        home.mkdir(parents=True)
        (home / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": model, "visibility": "list", "supported_in_api": True},
            {"slug": "hidden-model", "visibility": "hide", "supported_in_api": True},
        ]}))
    native = {"codex": {
        "systemDefault": {"hasAuth": True},
        "accounts": [{"id": "11111111-1111-4111-8111-111111111111",
                      "managedHomeRuntime": "host", "workspaceLabel": "Lavoro"}],
    }}
    terminal = {"terminals": [{"handle": "term_codex-test", "connected": True,
                               "orphaned": False}]}
    calls = []

    async def cli(*args):
        calls.append(args)
        if args[:2] == ("account", "list"):
            return native
        if args[:2] == ("terminal", "create"):
            return {"terminal": {"handle": "term_codex-test"}}
        if args[:2] == ("terminal", "list"):
            return terminal
        raise AssertionError(args)

    with patch.dict(os.environ, {
        "HOME": str(tmp_path), OWNER_LOGIN_ENV: OWNER, ALLOWED_ORIGINS_ENV: ORIGIN,
        TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8",
    }), patch.object(codex_sessions, "_orca", AsyncMock(side_effect=cli)):
        web = client()
        assert web.get("/", headers=HEADERS).status_code == 200
        accounts = web.get("/api/v1/agents/codex/accounts", headers=HEADERS)
        assert accounts.status_code == 200, accounts.text
        rows = accounts.json()["accounts"]
        assert {(row["id"], tuple(row["models"])) for row in rows} == {
            ("system", ("gpt-system",)),
            ("11111111-1111-4111-8111-111111111111", ("gpt-managed",)),
        }
        assert str(tmp_path) not in accounts.text
        assert "hidden-model" not in accounts.text
        h = {**HEADERS, "Origin": ORIGIN, CSRF_HEADER: web.cookies.get(CSRF_COOKIE)}
        endpoint = "/api/v1/agents/codex/sessions"
        invalid = {"accountId": "system", "model": "gpt-managed", "operationId": str(uuid.uuid4())}
        assert web.post(endpoint, headers=h, json=invalid).status_code == 422
        assert web.post(endpoint, headers=HEADERS, json=invalid).status_code == 403
        request = {"accountId": rows[1]["id"], "model": "gpt-managed", "operationId": str(uuid.uuid4())}
        new = web.post(endpoint, headers=h, json=request)
        assert new.status_code == 200, new.text
        assert new.json()["accountId"] == request["accountId"]
        assert new.json()["model"] == request["model"]
        assert new.json()["handle"] == "term_codex-test"
        assert new.json()["replayed"] is False
        replay = web.post(endpoint, headers=h, json=request)
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert len([c for c in calls if c[:2] == ("terminal", "create")]) == 1
        launch = next(c for c in calls if c[:2] == ("terminal", "create"))
        command = launch[launch.index("--command") + 1]
        assert str(managed) not in command
        assert "CODEX_HOME=" not in command
        assert request["accountId"] in command
        readback = web.get(endpoint, headers=HEADERS)
        assert readback.status_code == 200
        assert readback.json()["sessions"][0]["accountId"] == request["accountId"]
        assert str(tmp_path) not in readback.text


def test_codex_receipts_follow_dashboard_xdg_state(tmp_path, monkeypatch):
    import stat
    monkeypatch.setenv("CONTROLCENTER_DASHBOARD_STATE_DIR", str(tmp_path / "custom-dashboard-state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    destination = codex_sessions._receipts_dir()
    assert destination == tmp_path / "custom-dashboard-state" / "codex-session-operations"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert not (tmp_path / "ControlCenter" / "_local").exists()


def test_codex_runner_execs_only_selected_home_and_model(tmp_path):
    from backend import codex_run
    import json
    import sys

    system = tmp_path / ".codex"
    managed = tmp_path / ".config/orca/codex-accounts/11111111-1111-4111-8111-111111111111/home"
    for home, model in ((system, "gpt-system"), (managed, "gpt-managed")):
        home.mkdir(parents=True)
        (home / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": model, "visibility": "list", "supported_in_api": True}
        ]}))
    native = {"codex": {"systemDefault": {"hasAuth": True}, "accounts": [
        {"id": "11111111-1111-4111-8111-111111111111", "managedHomeRuntime": "host"}
    ]}}
    with patch.dict(os.environ, {"HOME": str(tmp_path)}), \
            patch.object(codex_run, "native_inventory", return_value=native), \
            patch.object(codex_run.os, "execvpe") as execve:
        for account_id, model, expected_home in (
            ("system", "gpt-system", system),
            ("11111111-1111-4111-8111-111111111111", "gpt-managed", managed),
        ):
            with patch.object(sys, "argv", ["codex_run.py", account_id, model]):
                assert codex_run.main() == 0
            program, argv, environment = execve.call_args.args
            assert program == "codex"
            assert argv == ["codex", "--model", model]
            assert environment["CODEX_HOME"] == str(expected_home)
        assert execve.call_count == 2
        with patch.object(sys, "argv", ["codex_run.py", "system", "gpt-managed"]):
            assert codex_run.main() == 69
        assert execve.call_count == 2  # Never launch with another account's model.
