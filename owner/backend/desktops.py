"""Owner-only Guacamole desktop inventory and WebSocket tunnel.

Target configuration lives in a host-local allowlist; arbitrary browser URLs,
hostnames and ports are never forwarded to guacd. Credentials stay in private
host-local files. The dashboard's existing owner session protects HTTP and WS.
"""
from __future__ import annotations

import asyncio
import codecs
import hmac
import json
import os
import re
import stat
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from backend.auth import CSRF_COOKIE, admit_websocket
from backend.desktop_guacd import GuacdProtocolError, client_instructions, instruction, start_guacd
from backend.runtime_config import xdg_home

router = APIRouter()
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")

def _secrets_root() -> Path:
    return xdg_home("XDG_CONFIG_HOME", ".config") / "controlcenter/secrets/remote-desktops"


def configured_desktops() -> dict[str, dict]:
    """Load only explicitly allowed connections; malformed config fails closed."""
    path = Path(os.environ.get("CONTROLCENTER_DESKTOP_TARGETS_FILE", str(_secrets_root() / "targets.json")))
    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(contents, dict) or len(contents) > 12:
        return {}
    targets: dict[str, dict] = {}
    for target_id, fields in contents.items():
        if not isinstance(target_id, str) or not _IDENTIFIER.fullmatch(target_id):
            return {}
        if not isinstance(fields, dict):
            return {}
        name = fields.get("name")
        protocol = fields.get("protocol")
        hostname = fields.get("hostname")
        port = fields.get("port")
        capabilities = fields.get("capabilities", {})
        if (
            not isinstance(name, str) or not 1 <= len(name) <= 80
            or protocol not in ("rdp", "vnc")
            or not isinstance(hostname, str) or not _HOST.fullmatch(hostname)
            or ".." in hostname or not isinstance(port, int) or isinstance(port, bool)
            or not 1 <= port <= 65535
            or not isinstance(capabilities, dict)
            or set(capabilities) - {"view", "input", "clipboard"}
            or any(type(value) is not bool for value in capabilities.values())
            or (capabilities.get("input") is True and capabilities.get("view") is not True)
            or (capabilities.get("clipboard") is True and capabilities.get("view") is not True)
        ):
            return {}
        targets[target_id] = {
            "name": name, "protocol": protocol, "hostname": hostname, "port": str(port),
            "capabilities": {
                name: capabilities.get(name) is True for name in ("view", "input", "clipboard")
            },
            "explicit_view": "view" in capabilities,
        }
    return targets


def _credentials(target_id: str) -> dict[str, str]:
    """No credentials in Git, API responses, query strings, or process arguments."""
    root = Path(os.environ.get("CONTROLCENTER_DESKTOP_SECRETS_DIR", str(_secrets_root())))
    file = root / (target_id + ".json")
    try:
        info = file.lstat()
    except FileNotFoundError:
        return {}
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise GuacdProtocolError("Remote credential file is not private")
    try:
        content = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GuacdProtocolError("Remote credential file is invalid") from exc
    if not isinstance(content, dict) or any(
        key not in ("username", "password", "domain") or not isinstance(value, str)
        or len(value) > 1024
        for key, value in content.items()
    ):
        raise GuacdProtocolError("Remote credential format is invalid")
    return content


def _guacd_endpoint() -> tuple[str, int]:
    hostname = os.environ.get("CONTROLCENTER_GUACD_HOST", "127.0.0.1")
    port = int(os.environ.get("CONTROLCENTER_GUACD_PORT", "4822"))
    if hostname not in ("127.0.0.1", "::1", "localhost") or not 1 <= port <= 65535:
        raise ValueError("Untrusted guacd endpoint")
    return hostname, port


async def _target_reachable(target: dict) -> bool:
    """Check an allowlisted endpoint without starting a remote desktop session."""
    hostname = target["hostname"]
    # guacd runs in Docker; its host gateway alias does not resolve on the host.
    if hostname in ("host.docker.internal", "host.containers.internal"):
        hostname = "127.0.0.1"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, int(target["port"])), timeout=1.0,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, ValueError, TimeoutError):
        return False


@router.get("/api/v1/desktops")
async def list_desktops() -> dict:
    targets = configured_desktops()
    healthy = False
    if any(target["capabilities"]["view"] for target in targets.values()):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(*_guacd_endpoint()), timeout=0.5)
            writer.close()
            await writer.wait_closed()
            healthy = True
        except (OSError, ValueError, TimeoutError):
            pass
    allowed_ids = [target_id for target_id, target in targets.items() if target["capabilities"]["view"]]
    reachable = {}
    if healthy:
        checks = await asyncio.gather(*(_target_reachable(targets[target_id]) for target_id in allowed_ids))
        reachable = dict(zip(allowed_ids, checks))
    desktops = []
    for target_id, target in sorted(targets.items()):
        granted = target["capabilities"]["view"]
        state = ("ready" if granted and reachable.get(target_id, False) else "offline" if granted else
                 "permission_denied" if target["explicit_view"] else "unconfigured")
        desktops.append({
            "id": target_id, "name": target["name"], "protocol": target["protocol"],
            "state": state,
            "capabilities": target["capabilities"] if state == "ready" else {
                "view": False, "input": False, "clipboard": False,
            },
        })
    return {"donor": "apache-guacamole-1.6.0",
            "status": "ready" if any(item["state"] == "ready" for item in desktops)
            else "offline" if allowed_ids
            else "permission_denied" if any(target["explicit_view"] for target in targets.values())
            else "unconfigured", "desktops": desktops}


def verify_client_control(opcode: str, args: list[str], grants: dict[str, bool],
                          streams: dict[str, int]) -> None:
    """Reject input and clipboard instructions unless the server granted them."""
    if opcode in ("mouse", "key") and grants["input"]:
        return
    if opcode in ("sync", "nop", "ack", "disconnect"):
        return
    if opcode == "size" and len(args) == 3 and all(
        part.isdigit() and 1 <= int(part) <= 4096 for part in args
    ):
        return
    if grants["clipboard"]:
        if opcode == "clipboard" and len(args) == 2 and args[0].isdigit() and args[1] == "text/plain":
            streams[args[0]] = 0
            return
        if opcode == "blob" and len(args) == 2 and args[0] in streams:
            streams[args[0]] += len(args[1])
            if streams[args[0]] <= 65536:
                return
        if opcode == "end" and len(args) == 1 and args[0] in streams:
            del streams[args[0]]
            return
    raise GuacdProtocolError("Remote operation not allowed", reason="access_denied")


@router.websocket("/api/v1/desktops/{target_id}/tunnel")
async def desktop_tunnel(websocket: WebSocket, target_id: str) -> None:
    # WebSocket upgrades bypass Starlette's HTTP middleware. Authenticate the
    # existing Tailnet principal + short-lived owner cookie + exact Origin here.
    if not await admit_websocket(websocket, websocket.app.state.session_store):
        return
    csrf = websocket.cookies.get(CSRF_COOKIE, "")
    if not csrf or not hmac.compare_digest(csrf, websocket.state.owner_session.csrf_nonce):
        await websocket.accept()
        await websocket.close(code=1008, reason="csrf_failed")
        return
    target = configured_desktops().get(target_id)
    if target is None:
        await websocket.accept()
        await websocket.close(code=1008, reason="unknown_desktop")
        return
    if not target["capabilities"]["view"]:
        await websocket.accept()
        await websocket.close(code=1008, reason="permission_denied")
        return
    try:
        width = int(websocket.query_params.get("width", "1024"))
        height = int(websocket.query_params.get("height", "768"))
    except ValueError:
        await websocket.accept()
        await websocket.close(code=1008, reason="invalid_size")
        return
    if not (320 <= width <= 4096 and 240 <= height <= 2160):
        await websocket.accept()
        await websocket.close(code=1008, reason="invalid_size")
        return
    # The official guacd Docker image is bound to loopback only; never turn
    # this into a public TCP proxy even if the host-local environment drifts.
    try:
        guacd_host, guacd_port = _guacd_endpoint()
    except ValueError:
        await websocket.accept()
        await websocket.close(code=1008, reason="invalid_guacd_host")
        return
    # Upgrade only an authenticated, admitted target. Successful upgrade can
    # complete before slow remote desktop handshakes finish.
    await websocket.accept(subprotocol=(
        "guacamole" if "guacamole" in websocket.scope.get("subprotocols", []) else None
    ))
    try:
        # Credentials are resolved after authorization and target admission.
        connection = {**target, **_credentials(target_id),
                      "input": "true" if target["capabilities"]["input"] else "false",
                      "clipboard": "true" if target["capabilities"]["clipboard"] else "false"}
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(guacd_host, guacd_port), timeout=5
        )
    except (GuacdProtocolError, OSError, ValueError, TimeoutError):
        await websocket.close(code=1011, reason="desktop_unavailable")
        return
    try:
        ready = await start_guacd(reader, writer, connection, (width, height))
        await websocket.send_text(ready.decode("ascii"))

        async def remote_to_browser() -> None:
            decoder = codecs.getincrementaldecoder("utf-8")()
            while data := await reader.read(65536):
                text = decoder.decode(data)
                if text:
                    await websocket.send_text(text)
            remainder = decoder.decode(b"", final=True)
            if remainder:
                await websocket.send_text(remainder)

        async def browser_to_remote() -> None:
            clipboard_streams: dict[str, int] = {}
            second = time.monotonic()
            count = 0
            while True:
                payload = await websocket.receive_text()
                if len(payload) > 131072:
                    raise GuacdProtocolError("Guacamole frame too large")
                now = time.monotonic()
                if now - second >= 1:
                    second, count = now, 0
                instructions = client_instructions(payload)
                count += len(instructions)
                if count > 180:
                    raise GuacdProtocolError("Input rate limit", reason="access_denied")
                forwarded = []
                for opcode, args in instructions:
                    # Native WebSocketTunnel keepalives terminate here, not at guacd.
                    if (opcode == "" and len(args) == 2 and args[0] == "ping"
                            and args[1].isdigit() and len(args[1]) <= 20):
                        await websocket.send_text(instruction("", *args).decode("ascii"))
                        continue
                    verify_client_control(opcode, args, target["capabilities"], clipboard_streams)
                    forwarded.append(instruction(opcode, *args))
                if forwarded:
                    writer.writelines(forwarded)
                    await writer.drain()

        tasks = [
            asyncio.create_task(remote_to_browser()),
            asyncio.create_task(browser_to_remote()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except GuacdProtocolError as exc:
        # Preserve a meaningful denied-vs-offline result for the native
        # Guacamole client's onerror handler without exposing guacd messages.
        await websocket.send_text(
            instruction("error",
                        "Accesso remoto negato" if exc.reason == "access_denied"
                        else "Desktop non disponibile",
                        "0x0202" if exc.reason == "access_denied" else "0x0203").decode("utf-8")
        )
        await websocket.close(code=4003 if exc.reason == "access_denied" else 1011, reason=exc.reason)
    except (OSError, TimeoutError, WebSocketDisconnect,
            UnicodeError, asyncio.IncompleteReadError):
        # No remote content or private connection settings enter logs.
        pass
    finally:
        writer.close()
        await writer.wait_closed()
        try:
            await websocket.close()
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
