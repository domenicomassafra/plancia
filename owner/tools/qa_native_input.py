"""#48 acceptance: attach -> submit -> readback -> reconnect on ONE native session.

Runs on the Hermes host with the installed native interpreter, the installed
Plancia release and the installed Hermes gateway. It never starts a gateway, a
second daemon or a provider call: ``--create`` mints the synthetic session on a
route that cannot be reached, so the turn fails without any inference.

Why the session must be created here, never with `hermes chat --api-key`: a CLI
launch pins a launch-only key that the authority keeps in memory only, and any
restart revokes it. Such a session answers ``session.resume`` from Plancia but
loses the turn with ``launch_credentials_unavailable``, so it can never prove
this boundary. A session created through the gateway freezes no launch key and
stays resumable.

Usage on the MiniPC (native interpreter, installed helper):

    ~/.hermes/hermes-agent/venv/bin/python owner/tools/qa_native_input.py \
        --profile signor-coding --session local-<id> [--wait 90]

    ~/.hermes/hermes-agent/venv/bin/python owner/tools/qa_native_input.py \
        --profile signor-coding --create --wait 90

Output is metadata only (session/instance/epoch, admission id and status, the
marker verdict, gateway PIDs); it never prints message text, credentials or
provider output. Exit 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

OWNER_LOGIN_ENV = "CONTROLCENTER_OWNER_LOGIN"
OWNER_LOGIN_FILE = Path.home() / ".config/controlcenter/secrets/fleet-dashboard-auth.env"
DEFAULT_HELPER = Path.home() / ".local/share/plancia/current/owner/backend/hermes_native_input.py"
NATIVE_ROOT = Path.home() / ".hermes"
# A provider that cannot answer: the turn starts (and persists the marker) with
# no request leaving the host, so the acceptance never bills a provider.
OFFLINE_MODEL = "qa48-offline"
OFFLINE_BASE_URL = "http://127.0.0.1:9/v1"


def native_root() -> Path:
    return NATIVE_ROOT / "hermes-agent"


def owner_subject() -> str:
    login = os.getenv(OWNER_LOGIN_ENV, "").strip()
    if not login and OWNER_LOGIN_FILE.is_file():
        for line in OWNER_LOGIN_FILE.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == OWNER_LOGIN_ENV:
                login = value.strip()
                break
    if not login:
        raise SystemExit(f"{OWNER_LOGIN_ENV} is unset and absent from {OWNER_LOGIN_FILE}")
    return "auth:v1:" + json.dumps(["tailscale", "controlcenter", login], separators=(",", ":"))


def gateway_pid() -> int | None:
    try:
        done = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value",
                               "hermes-gateway.service"], capture_output=True, text=True,
                              timeout=10)
        return int(done.stdout.strip()) or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def native_modules():
    install = native_root()
    sys.path.insert(0, str(install))
    from hermes_cli.gateway_client import GatewayClient, _session_ticket, gateway_ws_target
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from websockets.asyncio.client import connect
    return GatewayClient, _session_ticket, gateway_ws_target, discover_gateway_endpoint, connect


async def create(profile: str) -> dict:
    """Mint the synthetic QA session the way the gateway's own clients do."""
    GatewayClient, _session_ticket, gateway_ws_target, discover, connect = native_modules()
    home = NATIVE_ROOT / "profiles" / profile
    found = await asyncio.to_thread(discover, home, timeout=5)
    if found.state != "ready" or found.endpoint is None:
        raise SystemExit(f"gateway not ready for {profile}: {found.state}")
    endpoint = found.endpoint
    ticket = await asyncio.to_thread(_session_ticket, home, endpoint, subject=owner_subject())
    url, protocols = gateway_ws_target(endpoint, ticket)
    async with connect(url, subprotocols=protocols, proxy=None, open_timeout=10,
                       max_size=8 * 1024 * 1024) as ws:
        async with GatewayClient(ws) as gateway:
            snapshot = await gateway.rpc(
                "session.create", request_id=uuid.uuid4().hex, source="gui",
                cwd=str(Path.home()), model=OFFLINE_MODEL, provider="custom",
                base_url=OFFLINE_BASE_URL, safe_mode=True, ignore_user_config=True,
                _timeout=40)
    session_id = snapshot.get("session_id")
    if not isinstance(session_id, str):
        raise SystemExit("session.create returned no session id")
    return {"session_id": session_id, "instance_id": endpoint.instance_id,
            "authority_epoch": endpoint.authority_epoch}


def helper(helper_path: Path, request: dict) -> dict:
    """Call the installed adapter out of process, exactly as the backend does."""
    interpreter = native_root() / "venv" / "bin" / "python"
    if not interpreter.is_file() or not helper_path.is_file():
        raise SystemExit("installed native interpreter or Plancia helper is missing")
    data = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    done = subprocess.run([str(interpreter), "-I", str(helper_path)], input=data,
                          capture_output=True, timeout=60,
                          env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    try:
        answer = json.loads(done.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise SystemExit("the installed helper answered nothing usable")
    if done.returncode != 0 or not isinstance(answer, dict) or "result" not in answer:
        raise SystemExit(f"the installed helper refused: {answer.get('error') if isinstance(answer, dict) else '?'}")
    return answer["result"]


def marker_present(profile: str, session_id: str, marker: str) -> bool:
    binary = shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    done = subprocess.run([binary, "--profile", profile, "sessions", "export", "--session-id",
                           session_id, "--format", "jsonl", "--redact", "-"],
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        return False
    try:
        payload = json.loads(done.stdout.strip())
    except ValueError:
        return False
    for message in payload.get("messages") or []:
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if message.get("role") == "user" and marker in text:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--session", help="pre-existing synthetic session id")
    parser.add_argument("--create", action="store_true", help="mint a durable synthetic session")
    parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    parser.add_argument("--wait", type=int, default=90, help="seconds to wait for the readback")
    parser.add_argument("--nonce", default="qa48_" + uuid.uuid4().hex[:12])
    args = parser.parse_args()
    if bool(args.session) == bool(args.create):
        parser.error("pass exactly one of --session or --create")

    pid_before = gateway_pid()
    if args.create:
        args.session = asyncio.run(create(args.profile))["session_id"]
    base = {"profile_id": args.profile, "session_id": args.session, "subject": owner_subject()}

    attached = helper(args.helper, {**base, "action": "attach"})
    sent = helper(args.helper, {**base, "action": "send", "nonce": args.nonce,
                                "text": f"Plancia #48 QA marker {args.nonce}",
                                "expected": {"instance_id": attached["instance_id"],
                                             "authority_epoch": attached["authority_epoch"]}})
    deadline = time.monotonic() + max(args.wait, 0)
    while True:
        found = marker_present(args.profile, args.session, args.nonce)
        if found or time.monotonic() >= deadline:
            break
        time.sleep(5)
    reattached = helper(args.helper, {**base, "action": "attach"})
    pid_after = gateway_pid()

    identity = [(attached.get("instance_id"), attached.get("authority_epoch"), attached.get("session_id")),
                (sent.get("instance_id"), sent.get("authority_epoch"), sent.get("session_id")),
                (reattached.get("instance_id"), reattached.get("authority_epoch"),
                 reattached.get("session_id"))]
    checks = {
        "attach": attached.get("session_id") == args.session,
        "send_admission": bool(sent.get("admission_id")) and sent.get("status") in
                          {"queued", "started", "unknown", "terminal"},
        "readback_marker": found,
        "reconnect_same_session": reattached.get("session_id") == args.session,
        "same_instance_and_epoch": len(set(identity)) == 1,
        "same_gateway_process": pid_before is not None and pid_before == pid_after,
    }
    print(json.dumps({"ok": all(checks.values()), "profile_id": args.profile,
                      "session_id": args.session, "instance_id": attached.get("instance_id"),
                      "authority_epoch": attached.get("authority_epoch"),
                      "admission_id": sent.get("admission_id"), "status": sent.get("status"),
                      "marker_present": found, "gateway_pid_before": pid_before,
                      "gateway_pid_after": pid_after, "checks": checks,
                      "nonce": args.nonce}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
