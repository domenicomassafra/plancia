"""Owner-only Guacamole tunnel: denied sessions never reach the remote endpoint."""
import asyncio
import json
import os
import socket
import socketserver
import threading
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.auth import (
    ALLOWED_ORIGINS_ENV, CSRF_COOKIE, OWNER_LOGIN_ENV, SESSION_COOKIE,
    TAILSCALE_LOGIN_HEADER, TailnetAuthMiddleware, SessionStore,
)
from backend import desktops
from backend.desktop_guacd import GuacdProtocolError, client_instructions

ORIGIN = "https://fleet.example.test"
IDENTITY = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}


class FakeGuacd(socketserver.BaseRequestHandler):
    def handle(self):
        def receive_instruction():
            frame = bytearray()
            while not frame.endswith(b";"):
                char = self.request.recv(1)
                if not char:
                    return b""
                frame.extend(char)
            return bytes(frame)

        if receive_instruction() != b"6.select,3.vnc;":
            return  # A short-lived health probe is not a desktop session.
        self.request.sendall(b"4.args,13.VERSION_1_1_0,8.hostname,4.port,8.password;")
        first = [receive_instruction() for _ in range(6)]
        assert first[0].startswith(b"4.size,")
        assert first[-1].startswith(b"7.connect,")
        assert self.server.expected_host.encode() in first[-1]
        self.request.sendall(b"5.ready,4.test;")
        data = self.request.recv(4096)
        if data:
            self.request.sendall(data)


def test_remote_desktop_auth_denial_and_clipboard_tunnel(tmp_path, monkeypatch, request):
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeGuacd) as donor:
        donor.daemon_threads = True
        donor.expected_host = "127.0.0.1"
        thread = threading.Thread(target=donor.serve_forever, daemon=True)
        thread.start()
        request.addfinalizer(donor.shutdown)
        target_socket = socket.socket()
        target_socket.bind(("127.0.0.1", 0))
        target_socket.listen()
        request.addfinalizer(target_socket.close)
        config = tmp_path / "desktops.json"
        config.write_text(json.dumps({"test": {
            "name": "Desktop isolato", "protocol": "vnc",
            "hostname": "127.0.0.1", "port": target_socket.getsockname()[1],
            "capabilities": {"view": True, "input": True, "clipboard": True},
        }}))
        monkeypatch.setenv("CONTROLCENTER_DESKTOP_TARGETS_FILE", str(config))
        monkeypatch.setenv("CONTROLCENTER_GUACD_HOST", "127.0.0.1")
        monkeypatch.setenv("CONTROLCENTER_GUACD_PORT", str(donor.server_address[1]))
        with patch.dict(os.environ, {
            OWNER_LOGIN_ENV: IDENTITY[TAILSCALE_LOGIN_HEADER],
            ALLOWED_ORIGINS_ENV: ORIGIN,
        }):
            app = FastAPI()
            session_store = SessionStore()
            app.state.session_store = session_store
            app.add_middleware(TailnetAuthMiddleware, store=session_store)
            app.include_router(desktops.router)

            @app.get("/")
            def bootstrap():
                return {"ok": True}

            browser = TestClient(app, base_url=ORIGIN)
            assert browser.get("/api/v1/desktops").status_code == 401
            assert browser.get("/", headers=IDENTITY).status_code == 200
            result = browser.get("/api/v1/desktops", headers=IDENTITY)
            assert result.status_code == 200
            assert result.json() == {"donor": "apache-guacamole-1.6.0", "status": "ready", "desktops": [{
                "id": "test", "name": "Desktop isolato", "protocol": "vnc", "state": "ready",
                "capabilities": {"view": True, "input": True, "clipboard": True},
            }]}
            assert "no-store" in result.headers["cache-control"]
            session_headers = {
                **IDENTITY,
                "Origin": ORIGIN,
                "Cookie": (
                    f"{SESSION_COOKIE}={browser.cookies.get(SESSION_COOKIE)}; "
                    f"{CSRF_COOKIE}={browser.cookies.get(CSRF_COOKIE)}"
                ),
            }

            # Missing owner identity, origin, session, or unknown destination:
            # none may send a select instruction or open a guacd socket.
            with patch.object(desktops.asyncio, "open_connection", side_effect=AssertionError("denied dial")):
                fresh = TestClient(app, base_url=ORIGIN)
                with pytest.raises(WebSocketDisconnect):
                    with fresh.websocket_connect(
                        "wss://fleet.example.test/api/v1/desktops/test/tunnel",
                        headers={**IDENTITY, "Origin": ORIGIN},
                    ) as ws:
                        ws.receive_text()
                        ws.receive_text()
                for target, headers in [
                    ("test", {"Origin": ORIGIN, "Cookie": session_headers["Cookie"]}),
                    ("test", {**IDENTITY, "Cookie": session_headers["Cookie"]}),
                    ("test", {**session_headers, "Origin": "https://evil.example.test"}),
                    ("test", {**session_headers, "Cookie": f"{SESSION_COOKIE}={browser.cookies.get(SESSION_COOKIE)}"}),
                    ("test", {**session_headers, "Cookie": f"{SESSION_COOKIE}={browser.cookies.get(SESSION_COOKIE)}; {CSRF_COOKIE}=tampered"}),
                    ("missing", session_headers),
                ]:
                    with pytest.raises(WebSocketDisconnect):
                        with browser.websocket_connect(f"wss://fleet.example.test/api/v1/desktops/{target}/tunnel", headers=headers) as ws:
                            ws.receive_text()
                            # Auth failures send a bounded error before closing;
                            # unknown IDs close immediately. Neither may dial.
                            ws.receive_text()

            with browser.websocket_connect(
                "wss://fleet.example.test/api/v1/desktops/test/tunnel",
                headers=session_headers,
                subprotocols=["guacamole"],
            ) as ws:
                assert ws.accepted_subprotocol == "guacamole"
                assert ws.receive_text() == "0.,4.test;"
                # Tunnel keepalives belong to the WebSocket endpoint, not guacd.
                ws.send_text("0.,4.ping,3.123;")
                assert ws.receive_text() == "0.,4.ping,3.123;"
                # Relay is byte-faithful for Guacamole clipboard instructions.
                ws.send_text("9.clipboard,1.0,10.text/plain;")
                assert ws.receive_text() == "9.clipboard,1.0,10.text/plain;"
        donor.shutdown()


@pytest.mark.parametrize("hostname", ["127.0.0.1", "host.docker.internal"])
def test_remote_inventory_does_not_grant_offline_target_when_guacd_is_healthy(tmp_path, monkeypatch, hostname):
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeGuacd) as donor:
        with socket.socket() as unused_target:
            unused_target.bind(("127.0.0.1", 0))
            closed_port = unused_target.getsockname()[1]
        config = tmp_path / "targets.json"
        config.write_text(json.dumps({"test": {
            "name": "Desktop spento", "protocol": "vnc", "hostname": hostname,
            "port": closed_port,
            "capabilities": {"view": True, "input": True, "clipboard": True},
        }}))
        monkeypatch.setenv("CONTROLCENTER_DESKTOP_TARGETS_FILE", str(config))
        monkeypatch.setenv("CONTROLCENTER_GUACD_HOST", "127.0.0.1")
        monkeypatch.setenv("CONTROLCENTER_GUACD_PORT", str(donor.server_address[1]))
        inventory = asyncio.run(desktops.list_desktops())
        assert inventory["status"] == "offline"
        assert inventory["desktops"][0]["state"] == "offline"
        assert inventory["desktops"][0]["capabilities"] == {
            "view": False, "input": False, "clipboard": False,
        }


def test_remote_inventory_resolves_docker_host_alias_for_live_target(tmp_path, monkeypatch):
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeGuacd) as donor:
        with socket.socket() as reachable_target:
            reachable_target.bind(("127.0.0.1", 0))
            reachable_target.listen()
            config = tmp_path / "targets.json"
            config.write_text(json.dumps({"test": {
                "name": "Desktop isolato", "protocol": "vnc", "hostname": "host.docker.internal",
                "port": reachable_target.getsockname()[1],
                "capabilities": {"view": True, "input": False, "clipboard": False},
            }}))
            monkeypatch.setenv("CONTROLCENTER_DESKTOP_TARGETS_FILE", str(config))
            monkeypatch.setenv("CONTROLCENTER_GUACD_HOST", "127.0.0.1")
            monkeypatch.setenv("CONTROLCENTER_GUACD_PORT", str(donor.server_address[1]))
            inventory = asyncio.run(desktops.list_desktops())
            assert inventory["status"] == "ready"
            assert inventory["desktops"][0]["state"] == "ready"
            assert inventory["desktops"][0]["capabilities"] == {
                "view": True, "input": False, "clipboard": False,
            }


def test_no_config_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROLCENTER_DESKTOP_TARGETS_FILE", str(tmp_path / "absent.json"))
    assert desktops.configured_desktops() == {}


def test_desktop_secrets_resolve_from_xdg_config_home(tmp_path, monkeypatch):
    directory = tmp_path / "host-config" / "controlcenter" / "secrets" / "remote-desktops"
    directory.mkdir(parents=True)
    (directory / "targets.json").write_text(json.dumps({"test": {
        "name": "Host", "protocol": "vnc", "hostname": "localhost", "port": 5900,
        "capabilities": {"view": True},
    }}))
    credential = directory / "test.json"
    credential.write_text(json.dumps({"username": "test"}))
    credential.chmod(0o600)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "host-config"))
    monkeypatch.delenv("CONTROLCENTER_DESKTOP_TARGETS_FILE", raising=False)
    monkeypatch.delenv("CONTROLCENTER_DESKTOP_SECRETS_DIR", raising=False)
    assert "test" in desktops.configured_desktops()
    assert desktops._credentials("test") == {"username": "test"}


def test_denied_grants_never_dial_guacd(tmp_path, monkeypatch):
    config = tmp_path / "targets.json"
    config.write_text(json.dumps({"test": {
        "name": "Readback", "protocol": "vnc", "hostname": "localhost", "port": 5900,
    }}))
    monkeypatch.setenv("CONTROLCENTER_DESKTOP_TARGETS_FILE", str(config))
    assert desktops.configured_desktops()["test"]["capabilities"] == {
        "view": False, "input": False, "clipboard": False,
    }
    from asyncio import run
    with patch.object(desktops.asyncio, "open_connection", side_effect=AssertionError("denied dial")):
        result = run(desktops.list_desktops())
    assert result["status"] == "unconfigured"
    assert result["desktops"][0]["capabilities"]["input"] is False
    config.write_text(json.dumps({"test": {
        "name": "Readback", "protocol": "vnc", "hostname": "localhost", "port": 5900,
        "capabilities": {"input": True},
    }}))
    assert desktops.configured_desktops() == {}


def test_untrusted_guacamole_control_frames_are_rejected():
    for payload in (
        "6.select,3.vnc;", "7.connect,4.host;", "3.key,1.1,1.1;", "4.blob,1.0,4.test;",
    ):
        parsed = client_instructions(payload)
        assert len(parsed) == 1
    for payload in ("3.key,1.1", "3.key,1.1?", "9999999.foo;", "4.blob,10.no;"):
        with pytest.raises(GuacdProtocolError):
            client_instructions(payload)


def test_capability_rules_enforced_per_instruction():
    grants = {"view": True, "input": False, "clipboard": False}
    for opcode, args in (("mouse", ["0", "0", "1"]), ("key", ["1", "1"]),
                         ("clipboard", ["0", "text/plain"]), ("select", ["vnc"]),
                         ("blob", ["0", "data"])):
        with pytest.raises(GuacdProtocolError) as error:
            desktops.verify_client_control(opcode, args, grants, {})
        assert error.value.reason == "access_denied"
    desktops.verify_client_control("sync", ["1"], grants, {})
    desktops.verify_client_control("ack", ["2", "0", "0"], grants, {})
    grants["input"] = True
    desktops.verify_client_control("mouse", ["0", "0", "1"], grants, {})
    grants["clipboard"] = True
    streams = {}
    desktops.verify_client_control("clipboard", ["7", "text/plain"], grants, streams)
    desktops.verify_client_control("blob", ["7", "dGVzdA=="], grants, streams)
    desktops.verify_client_control("end", ["7"], grants, streams)
    assert streams == {}
    with pytest.raises(GuacdProtocolError):
        desktops.verify_client_control("blob", ["7", "dGVzdA=="], grants, streams)
