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
    "incarnationId": "process-123", "lastOutputAt": 123456,
}]}
SCREEN = {"terminal": {"source": "screen", "tail": ["hello", "<b>reply</b>"], "status": "running"}}


def native_receipt(retry_id="req_12345678", incarnation="process-123", *, replayed=False,
                   stages=None):
    return {"send": {"accepted": True, "handle": TERMINAL, "prompt": {
        "requestId": retry_id, "processIncarnation": incarnation,
        "stages": stages if stages is not None else ["input_accepted"]}},
        "mutation": {"requestId": retry_id, "replayed": replayed}}


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
                return native_receipt(stages=["input_accepted", "submission_observed"])
            raise AssertionError(args)

        fake = AsyncMock(side_effect=fake_cli)
        with patch.object(agents_live, "_orca", fake):
            inventory = browser.get("/api/v1/agents/terminals", headers=HEADERS)
            assert inventory.status_code == 200
            assert inventory.json()["terminals"][0]["agent"] == "opencode"
            assert inventory.json()["terminals"][0]["incarnationId"] == "process-123"
            assert inventory.headers["cache-control"] == "no-store"
            screen = browser.get(base + "/screen", headers=HEADERS)
            assert screen.status_code == 200
            assert "<b>reply</b>" in screen.json()["text"]  # plain text, JS uses textContent
            send = browser.post(base + "/send", headers=headers, json={
                "text": "hello", "incarnation_id": "process-123", "retry_request": "req_12345678"})
            assert send.status_code == 200, send.text
            assert send.json()["accepted"] is True
            assert send.json()["status"] == "observed"
            assert any("--enter" in args for args, _ in fake.await_args_list)
            assert any("--retry-request" in args and "req_12345678" in args
                       for args, _ in fake.await_args_list)
            assert browser.post(base + "/send", headers=headers, json={"text": "\u001b[0m"}).status_code == 422
            assert browser.post(base + "/send", headers=headers, json={"text": "x" * 2001}).status_code == 422
            assert browser.post("/api/v1/agents/terminals/term_fake/send", headers=headers,
                                json={"text": "hello", "incarnation_id": "process-123",
                                      "retry_request": "req_12345678"}).status_code == 404
            assert browser.get("/api/v1/agents/terminals/../../../etc/screen",
                               headers=HEADERS).status_code != 200
            assert fake.await_count == 6


def test_uncertain_transport_replays_same_native_request_without_second_admission():
    import asyncio
    from fastapi import HTTPException

    admissions = set()
    cli_calls = []

    async def fake_cli(*args):
        if args[:2] == ("terminal", "list"):
            return INVENTORY
        if args[:2] == ("terminal", "send"):
            retry_id = args[args.index("--retry-request") + 1]
            cli_calls.append((args[args.index("--text") + 1], retry_id))
            admissions.add((TERMINAL, "process-123", retry_id))
            if len(cli_calls) == 1:
                raise HTTPException(503, "transport lost AFTER native input accepted")
            return native_receipt(retry_id, replayed=True,
                                  stages=["input_accepted", "submission_observed"])
        raise AssertionError(args)

    payload = agents_live.TerminalInput(
        text="fixture text only", incarnation_id="process-123", retry_request="req_same_1234")
    with patch.object(agents_live, "_orca", AsyncMock(side_effect=fake_cli)):
        try:
            asyncio.run(agents_live.send_live_input(payload, TERMINAL))
            assert False, "Must surface uncertain admission"
        except HTTPException as exc:
            assert exc.status_code == 503
        accepted = asyncio.run(agents_live.send_live_input(payload, TERMINAL))
    assert accepted["submissionObserved"] is True
    assert accepted["replayed"] is True
    assert cli_calls == [("fixture text only", "req_same_1234")] * 2
    assert len(admissions) == 1, "The same native request admits once"


def test_native_process_changed_or_missing_cannot_accept_retry():
    import asyncio
    from fastapi import HTTPException

    payload = agents_live.TerminalInput(
        text="fixture", incarnation_id="previous-process", retry_request="req_same_1234")
    fake = AsyncMock(return_value=INVENTORY)
    with patch.object(agents_live, "_orca", fake):
        try:
            asyncio.run(agents_live.send_live_input(payload, TERMINAL))
            assert False, "A reused terminal handle must not admit an old retry"
        except HTTPException as exc:
            assert exc.status_code == 409
    assert fake.await_count == 1, "No send command after incarnation mismatch"
    for missing_id in (None, ""):
        entry = {**INVENTORY["terminals"][0], "incarnationId": missing_id}
        fake = AsyncMock(return_value={"terminals": [entry]})
        with patch.object(agents_live, "_orca", fake):
            try:
                asyncio.run(agents_live.send_live_input(payload, TERMINAL))
                assert False, "Legacy terminal cannot accept retry"
            except HTTPException as exc:
                assert exc.status_code == 409
        assert fake.await_count == 1


def test_cli_success_without_bound_native_receipt_cannot_be_reported_as_accepted():
    import asyncio
    from fastapi import HTTPException

    payload = agents_live.TerminalInput(
        text="fixture", incarnation_id="process-123", retry_request="req_12345678")
    for malformed in [
        {"submissionObserved": True},
        native_receipt(retry_id="different_12345"),
        native_receipt(incarnation="replacement-process"),
        {"send": {"accepted": True}},
    ]:
        async def fake_cli(*args):
            return INVENTORY if args[:2] == ("terminal", "list") else malformed

        with patch.object(agents_live, "_orca", AsyncMock(side_effect=fake_cli)):
            try:
                asyncio.run(agents_live.send_live_input(payload, TERMINAL))
                assert False, "Unbound native receipt is not an ack"
            except HTTPException as exc:
                assert exc.status_code == 503


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
