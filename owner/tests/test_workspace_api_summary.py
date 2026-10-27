from workspace_api_test_support import *

def test_summary_route_carries_cross_surface_projection_and_legacy_alias_is_gone():
    """The v3 summary route is canonical; the retired Cockpit route is absent."""
    env = patch.dict(
        os.environ,
        {
            OWNER_LOGIN_ENV: OWNER,
            ALLOWED_ORIGINS_ENV: ORIGIN,
            "FLEETPROBE_TOKEN": "test-token",
        },
    )
    env.start()
    try:
        from backend import server

        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=ORIGIN)
        identity = {TAILSCALE_LOGIN_HEADER: OWNER}
        assert client.get("/", headers=identity).status_code == 200
        propagation = {
            "available": True,
            "state": "partial",
            "reason": None,
            "entries": [
                {
                    "id": "lifeos.notion-operations",
                    "name": "Notion operations",
                    "version": 1,
                    "status": "source-only",
                    "source_valid": True,
                    "source_digest": {
                        "path": "surfaces/notion.md",
                        "expected": "a" * 64,
                        "actual": "a" * 64,
                        "match": True,
                    },
                    "rendered": True,
                    "projections": [],
                    "applied": "source-only",
                    "runtime": "partial",
                    "runtime_proven": False,
                    "drift": [],
                    "limitations": ["Provider runtime remains proof-gated."],
                }
            ],
        }
        payload = {"generated_at": "now", "manifests": {"propagation": propagation}}
        with patch("backend.fleet2.api.workspace_sources.control_summary", return_value=payload):
            response = client.get("/api/summary", headers=identity)
            compat = client.get("/api/cockpit", headers=identity)
        assert response.status_code == 200, response.text
        assert compat.status_code == 404, compat.text
        observed = response.json()["manifests"]["propagation"]
        assert observed["available"] is True
        assert observed["entries"][0]["source_valid"] is True
        assert observed["entries"][0]["applied"] == "source-only"
        assert observed["entries"][0]["runtime_proven"] is False
    finally:
        env.stop()
