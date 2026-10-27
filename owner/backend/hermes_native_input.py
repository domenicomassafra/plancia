"""One-shot client for Hermes' *existing* native gateway, never a gateway launcher.

The helper uses the installed Hermes' own gateway_runtime and gateway_client.
Only the canonical ticketed local WebSocket can submit a turn. The subprocess is
a short-lived CLIENT in Hermes' pinned Python venv; the owner/agent stays in the
original gateway process and Hermes' durable admission ledger owns replay.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
import sys


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _valid(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise Refused("invalid_identifier")
    return value


def _owner_subject(login: object) -> str:
    """Hermes control-socket v0.22.5 requires a namespaced forwarded login.

    The Unix peer still authenticates the request. The subject must use
    Hermes' auth:v1 [provider, issuer, user] contract so it cannot collide
    with a native UID or another gateway principal.
    """
    if (not isinstance(login, str) or not login.strip() or len(login) > 256
            or any(ord(c) < 33 or ord(c) == 127 for c in login)):
        raise Refused("invalid_subject")
    subject = "auth:v1:" + json.dumps(["tailscale", "controlcenter", login],
                                      ensure_ascii=True, separators=(",", ":"))
    if len(subject) > 256:
        raise Refused("invalid_subject")
    return subject


async def operation(request: dict) -> dict:
    action = request.get("action")
    if action not in {"attach", "send"}:
        raise Refused("invalid_action")
    profile_id = _valid(request.get("profile_id"))
    session_id = _valid(request.get("session_id"))
    subject = _owner_subject(request.get("subject"))

    root = Path.home() / ".hermes"
    install = root / "hermes-agent"
    profile_home = root / "profiles" / profile_id
    if not profile_home.is_dir():
        raise Refused("native_unavailable")
    # Load the official, installed MIT implementation. Never copy or patch it.
    sys.path.insert(0, str(install))
    try:
        from hermes_cli.gateway_client import (GATEWAY_WS_PROTOCOL, GatewayClient,
                                                GatewayRPCError, _session_ticket,
                                                gateway_ws_target)
        from hermes_cli.gateway_runtime import discover_gateway_endpoint
        from websockets.asyncio.client import connect
    except (ImportError, SyntaxError) as exc:
        raise Refused("native_unavailable") from exc

    discovered = await asyncio.to_thread(discover_gateway_endpoint, profile_home, timeout=2)
    # Fail closed on absent/stale/starting/draining owners. Never ensure/start one.
    endpoint = discovered.endpoint
    if discovered.state != "ready" or endpoint is None:
        raise Refused("native_unavailable")
    if not endpoint.code_sha:
        raise Refused("native_unverified")
    installed = await asyncio.to_thread(
        subprocess.run, ["git", "-C", str(install), "rev-parse", "HEAD"],
        capture_output=True, timeout=3, check=False)
    if installed.returncode != 0 or installed.stdout.decode("ascii", "replace").strip() != endpoint.code_sha:
        raise Refused("native_version_mismatch")
    expected = request.get("expected")
    if action == "send" and (not isinstance(expected, dict)
                             or expected.get("instance_id") != endpoint.instance_id
                             or type(expected.get("authority_epoch")) is not int
                             or expected["authority_epoch"] != endpoint.authority_epoch):
        raise Refused("gateway_instance_changed")

    ticket = await asyncio.to_thread(_session_ticket, profile_home, endpoint, subject=subject)
    url, protocols = gateway_ws_target(endpoint, ticket)
    try:
        async with connect(url, subprotocols=protocols, proxy=None, open_timeout=5,
                           max_size=8 * 1024 * 1024) as ws:
            if ws.subprotocol != GATEWAY_WS_PROTOCOL:
                raise Refused("native_protocol_mismatch")
            async with GatewayClient(ws) as gateway:
                snapshot = await gateway.rpc("session.resume", session_id=session_id,
                                             omit_messages=True, _timeout=15)
                if (not isinstance(snapshot, dict)
                        or snapshot.get("session_id") != session_id
                        or snapshot.get("authority_epoch") != endpoint.authority_epoch):
                    raise Refused("session_mismatch")
                result = {
                    "profile_id": profile_id, "session_id": session_id,
                    "instance_id": endpoint.instance_id,
                    "authority_epoch": endpoint.authority_epoch,
                }
                if action == "send":
                    nonce = _valid(request.get("nonce"))
                    content = request.get("text")
                    if not isinstance(content, str) or not content or len(content) > 4096:
                        raise Refused("invalid_payload")
                    # Hermes stores (authenticated subject, session ID, submission_id)
                    # and checks the payload digest on replay. This is its idempotency
                    # mechanism; the client must resend the SAME nonce on uncertainty.
                    receipt = await gateway.rpc("prompt.submit", session_id=session_id,
                                                text=content, submission_id=nonce, _timeout=20)
                    if (not isinstance(receipt, dict)
                            or receipt.get("ref", {}).get("profile_id") != endpoint.profile_id
                            or receipt.get("ref", {}).get("session_id") != session_id
                            or not isinstance(receipt.get("admission_id"), str)
                            or receipt.get("status") not in {"queued", "started", "unknown", "terminal"}):
                        raise Refused("receipt_mismatch")
                    result.update(admission_id=receipt["admission_id"], status=receipt["status"])
                subscription_id = snapshot.get("subscription_id")
                if isinstance(subscription_id, str):
                    try:
                        await gateway.rpc("session.detach", session_id=session_id,
                                          subscription_id=subscription_id, _timeout=5)
                    except Exception:
                        # Socket close detaches this viewer; the native admission is
                        # already durable, so a failed detach must never cause resend.
                        pass
                return result
    except GatewayRPCError as exc:
        raise Refused("native_refused") from exc


async def main() -> None:
    try:
        raw = await asyncio.to_thread(sys.stdin.buffer.readline, 12_288)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise Refused("invalid_request")
        result = await asyncio.wait_for(operation(data), timeout=45)
        print(json.dumps({"result": result}, separators=(",", ":")))
    except Refused as exc:
        print(json.dumps({"error": exc.reason}))
    except Exception:
        # Gateway/session diagnostics and auth material must never reach Plancia.
        print('{"error":"native_unavailable"}')


if __name__ == "__main__":
    asyncio.run(main())
