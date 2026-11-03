"""Contract tests for the same-origin, owner-only Orca session reader."""
import os
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import agents_api
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


def test_legacy_agents_url_routes_to_the_single_native_agents_surface():
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: OWNER, ALLOWED_ORIGINS_ENV: ORIGIN,
        TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8",
    }):
        web = client()
        assert web.get("/agents", headers=HEADERS).status_code == 401
        assert web.get("/", headers=HEADERS).status_code == 200
        response = web.get("/agents", headers=HEADERS, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/#agents"
        assert response.headers["cache-control"] == "no-store"


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
