"""SilverBullet read-only transport compatibility and authorization contract."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import server, workspace_silverbullet
from backend.auth import (
    ALLOWED_ORIGINS_ENV, CSRF_COOKIE, CSRF_HEADER,
    OWNER_LOGIN_ENV, TAILSCALE_LOGIN_HEADER,
)


def test_silverbullet_sync_metadata_and_native_search_survive_same_origin_proxy():
    observed = []

    def upstream(request):
        observed.append(request)
        return httpx.Response(
            200,
            text="# Allowed\n[[Linked note]]",
            headers={
                "content-type": "text/markdown",
                "etag": '"sha256:fixture"',
                "x-permission": "ro",
                "x-last-modified": "1720000000000",
                "x-created": "1710000000000",
                "x-content-length": "25",
            },
        )

    real_client = httpx.AsyncClient

    def mock_client(**_kwargs):
        return real_client(transport=httpx.MockTransport(upstream))

    origin = "https://owner.example.test"
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: "owner@example.test",
        ALLOWED_ORIGINS_ENV: origin,
        "FLEETPROBE_TOKEN": "fixture",
    }), patch.object(workspace_silverbullet.httpx, "AsyncClient", side_effect=mock_client):
        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=origin)
        identity = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
        assert client.get("/", headers=identity).status_code == 200

        note = client.get("/notes/.fs/Allowed.md", headers={
            **identity,
            "X-Sync-Mode": "true",
            "X-Get-Meta": "true",
        })
        assert note.status_code == 200
        assert note.text == "# Allowed\n[[Linked note]]"
        assert note.headers["x-permission"] == "ro"
        assert note.headers["x-last-modified"] == "1720000000000"
        assert note.headers["x-created"] == "1710000000000"
        assert note.headers["x-content-length"] == "25"
        assert note.headers["etag"] == '"sha256:fixture"'
        assert observed[0].headers["x-sync-mode"] == "true"
        assert observed[0].headers["x-get-meta"] == "true"

        search = client.post(
            "/notes/.search/query",
            json={"query": "Allowed", "mode": "words"},
            headers={**identity, "Origin": origin, "X-Sync-Mode": "true"},
        )
        assert search.status_code == 200
        assert observed[-1].url.path == "/notes/.search/query"
        assert observed[-1].method == "POST"
        assert client.post(
            "/notes/.search/query", json={"query": "Allowed"},
            headers={**identity, "Origin": "https://other.example.test"},
        ).status_code == 403
        assert len(observed) == 2


def test_silverbullet_proxy_rejects_privileged_requests_and_hidden_files():
    observed = []

    def upstream(request):
        observed.append(request)
        return httpx.Response(200, text="should not be returned")

    real_client = httpx.AsyncClient

    origin = "https://owner.example.test"
    with patch.dict(os.environ, {
        OWNER_LOGIN_ENV: "owner@example.test",
        ALLOWED_ORIGINS_ENV: origin,
        "FLEETPROBE_TOKEN": "fixture",
    }), patch.object(
        workspace_silverbullet.httpx, "AsyncClient",
        side_effect=lambda **_: real_client(transport=httpx.MockTransport(upstream)),
    ):
        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=origin)
        identity = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
        assert client.get("/", headers=identity).status_code == 200
        for path in ("/notes/.fs/.obsidian/config", "/notes/.fs/tmp/hidden.md",
                     "/notes/.fs/.git/config"):
            assert client.get(path, headers=identity).status_code == 404
        headers = {**identity, "Origin": origin, CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}
        for path in ("/notes/.shell", "/notes/.auth", "/notes/.revisions/",
                     "/notes/.fs/Allowed.md"):
            assert client.post(path, headers=headers).status_code == 405
        assert observed == []
