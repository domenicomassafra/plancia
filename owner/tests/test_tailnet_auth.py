import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.auth import (
    ALLOWED_ORIGINS_ENV,
    CSRF_COOKIE,
    CSRF_HEADER,
    OWNER_LOGIN_ENV,
    SESSION_COOKIE,
    SESSION_TTL_ENV,
    TAILSCALE_LOGIN_HEADER,
    TRUSTED_PROXY_IPS_ENV,
    SessionStore,
    TailnetAuthMiddleware,
    admit_websocket,
)

OWNER = "owner@example.test"
ORIGIN = "https://fleet.example.test"
IDENTITY_HEADERS = {TAILSCALE_LOGIN_HEADER: OWNER}


def build_test_app(store: SessionStore) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TailnetAuthMiddleware, store=store)

    @app.get("/")
    def index():
        return {"ok": True}

    @app.get("/index.html")
    def index_html():
        return {"ok": True}

    @app.get("/read")
    def read():
        return {"ok": True}

    @app.get("/api/v1/session")
    def session(request: Request):
        return {
            "authenticated": True,
            "expires_at": request.state.owner_session.expires_at,
        }

    @app.post("/change")
    def change():
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if not await admit_websocket(websocket, store):
            return
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    return app


class TestTailnetAuth(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                OWNER_LOGIN_ENV: OWNER,
                ALLOWED_ORIGINS_ENV: ORIGIN,
                SESSION_TTL_ENV: "900",
                TRUSTED_PROXY_IPS_ENV: "100.64.0.0/10,fd7a:115c:a1e0::/48",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def client(self, store=None, client_host="testclient"):
        store = store or SessionStore()
        app = build_test_app(store)
        return TestClient(
            app,
            base_url=ORIGIN,
            client=(client_host, 50000),
        )

    def bootstrap(self, client):
        response = client.get("/", headers=IDENTITY_HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def websocket_headers(self, client, origin=ORIGIN):
        session_token = client.cookies.get(SESSION_COOKIE)
        csrf_nonce = client.cookies.get(CSRF_COOKIE)
        return {
            **IDENTITY_HEADERS,
            "Origin": origin,
            "Cookie": (
                f"{SESSION_COOKIE}={session_token}; "
                f"{CSRF_COOKIE}={csrf_nonce}"
            ),
        }

    def assert_denial(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["detail"]["code"], code)
        self.assertNotIn(OWNER, response.text)

    def test_direct_tailnet_request_with_spoofed_header_fails_closed(self):
        client = self.client(client_host="203.0.113.19")
        response = client.get("/", headers=IDENTITY_HEADERS)
        self.assert_denial(response, 403, "trusted_proxy_required")

    def test_configured_local_tailscale_serve_proxy_is_trusted(self):
        client = self.client(client_host="100.64.1.10")
        self.assertEqual(client.get("/", headers=IDENTITY_HEADERS).status_code, 200)

    def test_missing_and_non_owner_identity_fail_closed(self):
        client = self.client()
        missing = client.get("/")
        self.assert_denial(missing, 401, "identity_required")
        self.assertEqual(missing.headers["cache-control"], "no-store")
        self.assertEqual(missing.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", missing.headers["content-security-policy"])
        response = client.get(
            "/",
            headers={TAILSCALE_LOGIN_HEADER: "someone-else@example.test"},
        )
        self.assert_denial(response, 403, "owner_required")

    def test_missing_configuration_fails_closed_without_echoing_values(self):
        client = self.client()
        with patch.dict(os.environ, {OWNER_LOGIN_ENV: ""}):
            response = client.get("/", headers=IDENTITY_HEADERS)
        self.assert_denial(response, 503, "auth_not_configured")

    def test_owner_bootstrap_sets_short_lived_secure_cookies(self):
        client = self.client()
        response = self.bootstrap(client)
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(value for value in cookies if value.startswith(SESSION_COOKIE))
        csrf_cookie = next(value for value in cookies if value.startswith(CSRF_COOKIE))

        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("Secure", session_cookie)
        self.assertIn("SameSite=strict", session_cookie)
        self.assertIn("Max-Age=900", session_cookie)
        self.assertNotIn("Domain=", session_cookie)
        self.assertNotIn("HttpOnly", csrf_cookie)
        self.assertIn("Secure", csrf_cookie)
        self.assertIn("SameSite=strict", csrf_cookie)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn("wss://fleet.example.test", response.headers["content-security-policy"])
        self.assertNotIn("connect-src 'self' wss:;", response.headers["content-security-policy"])

    def test_spa_entry_and_legacy_paths_share_one_bootstrap_contract(self):
        # The SPA is reachable at both entries and bootstraps a session.
        for path in ("/", "/index.html"):
            client = self.client()
            response = client.get(path, headers=IDENTITY_HEADERS)
            self.assertEqual(response.status_code, 200, f"{path}: {response.text}")
            self.assertIn(SESSION_COOKIE, client.cookies, path)

        # Pre-v3 surfaces no longer exist and must not mint an owner session.
        for path in ("/app.js", "/styles.css", "/hifi.css", "/actions.css"):
            client = self.client()
            response = client.get(path, headers=IDENTITY_HEADERS)
            self.assert_denial(response, 401, "session_required")
            self.assertNotIn(SESSION_COOKIE, client.cookies, path)

    def test_origin_and_csrf_are_both_required_for_state_change(self):
        client = self.client()
        self.bootstrap(client)
        csrf_nonce = client.cookies.get(CSRF_COOKIE)

        self.assert_denial(
            client.post("/change", headers=IDENTITY_HEADERS),
            403,
            "origin_denied",
        )
        self.assert_denial(
            client.post(
                "/change",
                headers={**IDENTITY_HEADERS, "Origin": "https://wrong.example.test"},
            ),
            403,
            "origin_denied",
        )
        self.assert_denial(
            client.post(
                "/change",
                headers={**IDENTITY_HEADERS, "Origin": ORIGIN},
            ),
            403,
            "csrf_failed",
        )
        accepted = client.post(
            "/change",
            headers={
                **IDENTITY_HEADERS,
                "Origin": ORIGIN,
                CSRF_HEADER: csrf_nonce,
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_expired_session_is_rejected_until_bootstrap(self):
        now = [1000.0]
        store = SessionStore(clock=lambda: now[0])
        client = self.client(store=store)
        self.bootstrap(client)
        now[0] += 901

        response = client.get("/read", headers=IDENTITY_HEADERS)
        self.assert_denial(response, 401, "session_expired")

        renewed = client.get("/", headers=IDENTITY_HEADERS)
        self.assertEqual(renewed.status_code, 200, renewed.text)

    def test_session_endpoint_rebootstraps_after_expiry(self):
        now = [1000.0]
        store = SessionStore(clock=lambda: now[0])
        client = self.client(store=store)
        self.bootstrap(client)
        first_cookie = client.cookies.get(SESSION_COOKIE)
        now[0] += 901

        response = client.get("/api/v1/session", headers=IDENTITY_HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["authenticated"])
        self.assertNotEqual(client.cookies.get(SESSION_COOKIE), first_cookie)
        self.assertEqual(client.get("/read", headers=IDENTITY_HEADERS).status_code, 200)

    def test_query_token_never_substitutes_for_identity_or_session(self):
        client = self.client()
        response = client.get("/?token=not-an-auth-path")
        self.assert_denial(response, 401, "identity_required")

    def test_websocket_requires_owner_session_and_allowed_origin(self):
        client = self.client()
        with client.websocket_connect(
            "/ws",
            headers={**IDENTITY_HEADERS, "Origin": ORIGIN},
        ) as websocket:
            denial = websocket.receive_json()
            self.assertEqual(denial["code"], "session_required")
            with self.assertRaises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            self.assertEqual(closed.exception.code, 1008)

        self.bootstrap(client)
        with client.websocket_connect(
            "/ws",
            headers=self.websocket_headers(client, "https://wrong.example.test"),
        ) as websocket:
            self.assertEqual(websocket.receive_json()["code"], "origin_denied")

        with client.websocket_connect(
            "/ws",
            headers=self.websocket_headers(client),
        ) as websocket:
            self.assertEqual(websocket.receive_json(), {"ok": True})

    def test_websocket_rejects_direct_spoof_and_non_owner_identity(self):
        direct_client = self.client(client_host="203.0.113.19")
        with direct_client.websocket_connect(
            "/ws",
            headers={**IDENTITY_HEADERS, "Origin": ORIGIN},
        ) as websocket:
            self.assertEqual(websocket.receive_json()["code"], "trusted_proxy_required")

        non_owner_client = self.client()
        with non_owner_client.websocket_connect(
            "/ws?token=not-an-auth-path",
            headers={
                TAILSCALE_LOGIN_HEADER: "someone-else@example.test",
                "Origin": ORIGIN,
            },
        ) as websocket:
            self.assertEqual(websocket.receive_json()["code"], "owner_required")

    def test_real_app_shares_the_same_boundary_for_api_and_websocket(self):
        os.environ[TRUSTED_PROXY_IPS_ENV] = "testclient"
        os.environ["FLEETPROBE_TOKEN"] = "test-token"
        from backend import server as server_module

        app = server_module.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=ORIGIN)

        anonymous = client.get("/api/fleet")
        self.assertEqual(anonymous.status_code, 401, anonymous.text)

        self.bootstrap(client)
        fleet = client.get("/api/fleet", headers=IDENTITY_HEADERS)
        self.assertEqual(fleet.status_code, 200, fleet.text)
        self.assertIn("hosts", fleet.json())

        with client.websocket_connect(
            "/api/stream", headers=self.websocket_headers(client)
        ) as websocket:
            self.assertEqual(websocket.receive_json()["type"], "snapshot")


if __name__ == "__main__":
    unittest.main()
