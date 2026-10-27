"""Owner access to the responsive Plancia workspace deep link."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_workspace_page_requires_owner_session():
    from backend.auth import OWNER_LOGIN_ENV, ALLOWED_ORIGINS_ENV, TAILSCALE_LOGIN_HEADER
    from backend import server

    owner = "owner@example.test"
    origin = "https://owner.example.test"
    with patch.dict(os.environ, {OWNER_LOGIN_ENV: owner, ALLOWED_ORIGINS_ENV: origin, "FLEETPROBE_TOKEN": "fixture"}):
        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=origin)
        assert client.get("/workspace").status_code == 401
        identity = {TAILSCALE_LOGIN_HEADER: owner}
        assert client.get("/", headers=identity).status_code == 200
        response = client.get("/workspace", headers=identity, follow_redirects=True)
        assert response.status_code == 200
        assert "Plancia" in response.text
        assert "#workspace" in str(response.url)


def test_pwa_assets_are_protected_by_owner_session(tmp_path):
    from backend.auth import OWNER_LOGIN_ENV, ALLOWED_ORIGINS_ENV, TAILSCALE_LOGIN_HEADER
    from backend import server

    (tmp_path / "index.html").write_text("<title>Plancia</title>")
    (tmp_path / "manifest.webmanifest").write_text('{"name":"Plancia"}')
    (tmp_path / "plancia-icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (tmp_path / "sw.js").write_text("self.addEventListener('install', () => {})")
    with patch.dict(os.environ, {OWNER_LOGIN_ENV: "owner@example.test", ALLOWED_ORIGINS_ENV: "https://owner.example.test", "FLEETPROBE_TOKEN": "fixture"}), patch.object(server, "WEB_DIST", tmp_path):
        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url="https://owner.example.test")
        for path in ("/manifest.webmanifest", "/plancia-icon.svg", "/sw.js"):
            assert client.get(path).status_code == 401
        headers = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
        assert client.get("/", headers=headers).status_code == 200
        for path in ("/manifest.webmanifest", "/plancia-icon.svg"):
            assert client.get(path, headers=headers).status_code == 200
        worker = client.get("/sw.js", headers=headers)
        assert worker.status_code == 200
        assert worker.headers["content-type"].startswith("application/javascript")
        assert worker.headers["cache-control"] == "no-store"
        assert worker.headers["service-worker-allowed"] == "/"
        assert worker.headers["x-content-type-options"] == "nosniff"


def test_silverbullet_proxy_rejects_writes_and_forwards_bounded_owner_reads():
    import httpx
    from backend import server, workspace_silverbullet
    from backend.auth import OWNER_LOGIN_ENV, ALLOWED_ORIGINS_ENV, TAILSCALE_LOGIN_HEADER, CSRF_HEADER, CSRF_COOKIE

    seen = []

    def upstream(request):
        seen.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(200, text="read-only fixture", headers={"content-type": "text/plain"})

    original_client = httpx.AsyncClient

    def stub_client(**_):
        return original_client(transport=httpx.MockTransport(upstream))

    with patch.dict(os.environ, {OWNER_LOGIN_ENV: "owner@example.test", ALLOWED_ORIGINS_ENV: "https://owner.example.test", "FLEETPROBE_TOKEN": "fixture"}), patch.object(workspace_silverbullet.httpx, "AsyncClient", side_effect=stub_client):
        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url="https://owner.example.test")
        assert client.get("/notes/.ping").status_code == 401
        headers = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
        assert client.get("/", headers=headers).status_code == 200
        response = client.get("/notes/.ping", headers=headers)
        assert response.status_code == 200
        assert response.text == "read-only fixture"
        assert response.headers["cache-control"] == "no-store"
        write_headers = {**headers, "Origin": "https://owner.example.test", CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}
        assert client.put("/notes/Note.md", content="write", headers=write_headers).status_code == 405
        assert len(seen) == 1
        method, url, forwarded = seen[0]
        assert method == "GET" and url == "http://127.0.0.1:3082/notes/.ping"
        assert "cookie" not in forwarded and "authorization" not in forwarded
