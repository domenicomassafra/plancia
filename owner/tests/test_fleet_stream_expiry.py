"""A live fleet stream must stop at the exact owner-session boundary."""
import asyncio
from contextlib import nullcontext
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.auth import (
    ALLOWED_ORIGINS_ENV, OWNER_LOGIN_ENV, SESSION_COOKIE,
    TAILSCALE_LOGIN_HEADER, Principal, SessionStore,
)
from backend.fleet2.api import build_router
from backend.fleet2.registry import load_registry
from backend.fleet2.store import Store


@pytest.mark.parametrize("cause", [
    "expired", "revoked", "idle_timeout", "revoked_during_snapshot",
    "revoked_during_encoding", "clock_corrected",
])
def test_existing_fleet_websocket_never_sends_after_session_expiry_or_revocation(tmp_path, cause):
    now = [0.0]
    sessions = SessionStore(clock=time.monotonic if cause == "idle_timeout" else lambda: now[0])
    token, _session = sessions.create(
        Principal("owner@example.test"),
        ttl_seconds=1.5 if cause == "idle_timeout" else 0.05 if cause == "clock_corrected" else 60,
    )

    class Poller:
        queue = None
        subscribed = False

        def snapshot(self):
            if cause == "revoked_during_snapshot":
                sessions.clear()
            return {"seq": 0}

        def subscribe(self):
            self.queue = asyncio.Queue()
            self.subscribed = True
            return self.queue

        def unsubscribe(self, queue):
            assert queue is self.queue
            self.subscribed = False

    poller = Poller()
    app = FastAPI()
    app.include_router(build_router(registry=load_registry(), poller=poller,
                                    store=Store(tmp_path / "receipts.sqlite3"),
                                    engine=object(), session_store=sessions))

    @app.get("/fixture-tick")
    async def tick():
        poller.queue.put_nowait({"seq": 1})
        return {"ok": True}

    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: "owner@example.test",
        ALLOWED_ORIGINS_ENV: "https://fleet.example.test",
    }):
        def revoke_during_encoding(*args, **kwargs):
            sessions.clear()
            return json.dumps(*args, **kwargs)

        encoding_patch = (
            patch("backend.fleet2.api.encode_json", side_effect=revoke_during_encoding)
            if cause == "revoked_during_encoding" else nullcontext()
        )
        with TestClient(app, base_url="https://fleet.example.test") as client:
            headers = {
                TAILSCALE_LOGIN_HEADER: "owner@example.test",
                "Origin": "https://fleet.example.test",
                "Cookie": f"{SESSION_COOKIE}={token}",
            }
            with encoding_patch:
                with client.websocket_connect("/api/stream", headers=headers) as websocket:
                    if cause in ("revoked_during_snapshot", "revoked_during_encoding"):
                        with pytest.raises(WebSocketDisconnect) as exc:
                            websocket.receive_json()
                        assert exc.value.code == 1008
                    else:
                        assert websocket.receive_json()["data"]["seq"] == 0
                        if cause == "clock_corrected":
                            # The store's authoritative clock has moved backwards. A
                            # monotonic wait timeout must not falsely expire this session.
                            time.sleep(0.12)
                            assert client.get("/fixture-tick").status_code == 200
                            assert websocket.receive_json()["data"]["seq"] == 1
                            now[0] = 61
                        if cause == "expired":
                            now[0] = 61
                        elif cause == "revoked":
                            sessions.clear()
                        if cause != "idle_timeout":
                            assert client.get("/fixture-tick").status_code == 200
                        with pytest.raises(WebSocketDisconnect) as exc:
                            websocket.receive_json()
                        assert exc.value.code == 1008
            assert poller.subscribed is False
