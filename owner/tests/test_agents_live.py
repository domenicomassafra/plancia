"""The live agent route must enforce owner auth, CSRF and current PTY ownership."""
import os
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import agents_live
from backend.auth import (
    ALLOWED_ORIGINS_ENV, CSRF_COOKIE, CSRF_HEADER, OWNER_LOGIN_ENV,
    TAILSCALE_LOGIN_HEADER, TRUSTED_PROXY_IPS_ENV, SessionStore, TailnetAuthMiddleware,
)

ORIGIN = "https://fleet.example.test"
HEADERS = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
TERMINAL = "term_test-1234"
INVENTORY = {"terminals": [{
    "handle": TERMINAL, "agentIdentity": "opencode", "title": "Example",
    "connected": True, "writable": True, "orphaned": False,
    "lastOutputAt": 123456,
}]}
SCREEN = {"terminal": {"source": "screen", "tail": ["hello", "<b>reply</b>"], "status": "running"}}


def test_native_live_screen_send_and_session_boundary():
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: HEADERS[TAILSCALE_LOGIN_HEADER],
        ALLOWED_ORIGINS_ENV: ORIGIN, TRUSTED_PROXY_IPS_ENV: "127.0.0.1/8",
    }):
        app = FastAPI()
        app.add_middleware(TailnetAuthMiddleware, store=SessionStore())
        app.include_router(agents_live.router)

        @app.get("/")
        def bootstrap():
            return {"ok": True}

        browser = TestClient(app, base_url=ORIGIN)
        base = f"/api/v1/agents/terminals/{TERMINAL}"
        assert browser.get("/api/v1/agents/terminals").status_code == 401
        assert browser.get("/", headers=HEADERS).status_code == 200
        assert browser.post(base + "/send", headers=HEADERS, json={"text": "hello"}).status_code == 403
        headers = {**HEADERS, "Origin": ORIGIN, CSRF_HEADER: browser.cookies.get(CSRF_COOKIE)}
        async def fake_cli(*args):
            if args[:2] == ("terminal", "list"):
                return INVENTORY
            if args[:2] == ("terminal", "read"):
                return SCREEN
            if args[:2] == ("terminal", "send"):
                return {"submissionObserved": True}
            raise AssertionError(args)

        fake = AsyncMock(side_effect=fake_cli)
        with patch.object(agents_live, "_orca", fake):
            inventory = browser.get("/api/v1/agents/terminals", headers=HEADERS)
            assert inventory.status_code == 200
            assert inventory.json()["terminals"][0]["agent"] == "opencode"
            assert inventory.headers["cache-control"] == "no-store"
            screen = browser.get(base + "/screen", headers=HEADERS)
            assert screen.status_code == 200
            assert "<b>reply</b>" in screen.json()["text"]  # plain text, JS uses textContent
            send = browser.post(base + "/send", headers=headers, json={"text": "hello"})
            assert send.status_code == 200, send.text
            assert send.json()["accepted"] is True
            assert any("--enter" in args for args, _ in fake.await_args_list)
            assert browser.post(base + "/send", headers=headers, json={"text": "\u001b[0m"}).status_code == 422
            assert browser.post(base + "/send", headers=headers, json={"text": "x" * 2001}).status_code == 422
            assert browser.post("/api/v1/agents/terminals/term_fake/send", headers=headers,
                                json={"text": "hello"}).status_code == 404
            assert browser.get("/api/v1/agents/terminals/../../../etc/screen",
                               headers=HEADERS).status_code != 200
            assert fake.await_count == 7


def test_readonly_or_disconnected_cannot_be_sent_to():
    import asyncio
    from fastapi import HTTPException
    for entry in [
        {**INVENTORY["terminals"][0], "writable": False},
        {**INVENTORY["terminals"][0], "connected": False},
        {**INVENTORY["terminals"][0], "orphaned": True},
    ]:
        with patch.object(agents_live, "_orca", AsyncMock(return_value={"terminals": [entry]})):
            try:
                asyncio.run(agents_live._terminal(TERMINAL, writable=True))
                assert False, "Must reject unwritable PTY"
            except HTTPException as exc:
                assert exc.status_code in (404, 409)
