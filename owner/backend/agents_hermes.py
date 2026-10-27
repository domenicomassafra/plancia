"""Owner-only readback and gated native gateway input for installed Signori.

Hermes owns profiles and sessions; dodo-services owns the distribution allowlist.
No database reads or new agent/gateway processes. Input stays disabled until
the installed gateway transport and the real Plancia consumer are verified.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from backend.native_probe import SLOT as _SLOT


router = APIRouter()
SIGNORI_ROOT = Path(os.environ.get("PLANCIA_SIGNORI_ROOT") or Path.home() / ".local/opt/dodo-services/current/signori")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MAX_OUTPUT = 1_000_000
_MAX_PROFILES = 24
_MAX_SESSIONS = 20
_MAX_MESSAGES = 50
_MAX_RESPONSE_TEXT = 196_608  # Leave room for JSON metadata below the client's 256 KiB cap.
_ROLES = frozenset(("user", "assistant", "system", "tool"))
_INPUT_GATE_ENV = "CONTROLCENTER_HERMES_INPUT_ENABLED"
_NATIVE_ROOT = Path.home() / ".hermes" / "hermes-agent"


def _native_input_installed() -> bool:
    """Expose interaction only after explicit promotion of the installed native client.

    The attach/send boundary independently revalidates the gateway instance,
    authority epoch and existing session. A file presence check alone cannot
    promote a readback-only installation: the operator gate defaults to off.
    """
    return (os.getenv(_INPUT_GATE_ENV) == "1"
            and (_NATIVE_ROOT / "venv" / "bin" / "python").is_file()
            and (_NATIVE_ROOT / "hermes_cli" / "gateway_client.py").is_file()
            and Path(__file__).with_name("hermes_native_input.py").is_file())


class HermesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    authority_epoch: int = Field(ge=1)
    nonce: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
    text: str = Field(min_length=1, max_length=4096)


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Hermes native reader unavailable")


def _allowed_profiles() -> set[str]:
    """Use the installed release's canonical distribution manifests, never browser IDs."""
    try:
        if not SIGNORI_ROOT.is_dir():
            raise _unavailable()
        names = set()
        for folder in SIGNORI_ROOT.iterdir():
            if not folder.is_dir() or not _ID.fullmatch(folder.name):
                continue
            manifest = folder / "distribution.yaml"
            if not manifest.is_file():
                continue
            # Match only the canonical distribution name. Ignore secrets/config files.
            body = manifest.read_text(encoding="utf-8")[:8192]
            if re.search(r"(?m)^name:\s*" + re.escape(folder.name) + r"\s*$", body):
                names.add(folder.name)
        if len(names) > _MAX_PROFILES:
            raise _unavailable()
        return names
    except (OSError, UnicodeError):
        raise _unavailable() from None


async def _read_bounded(stream, max_bytes: int = _MAX_OUTPUT) -> bytes:
    result = bytearray()
    while chunk := await stream.read(65_536):
        result.extend(chunk)
        if len(result) > max_bytes:
            raise OverflowError
    return bytes(result)


async def _hermes(*args: str) -> str:
    """Bounded, serialized native CLI invocation. No shell or chat/gateway start."""
    binary = shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    async with _SLOT:
        try:
            process = await asyncio.create_subprocess_exec(
                binary, *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (FileNotFoundError, PermissionError):
            raise _unavailable() from None
        try:
            async with asyncio.timeout(15):
                data = await _read_bounded(process.stdout)
                status = await process.wait()
        except (TimeoutError, OverflowError, asyncio.CancelledError) as error:
            if process.returncode is None:
                process.kill()
            try:
                await asyncio.wait_for(process.communicate(), timeout=3)
            except asyncio.TimeoutError:
                pass
            if isinstance(error, asyncio.CancelledError):
                raise
            raise _unavailable() from None
        if status != 0:
            raise _unavailable()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _unavailable() from None


def _parse_profiles(text: str, allowed: set[str]) -> list[dict]:
    lines = text.splitlines()
    if not any("Profile" in row and "Gateway" in row for row in lines[:4]):
        if "No profiles found." in text:
            return []
        raise _unavailable()
    profiles = []
    native_input = _native_input_installed()
    for profile_id in sorted(allowed):
        found = []
        for row in lines[2:]:
            # The native table has variable-width model and optional display labels.
            # Identify a profile by its full canonical name before the gateway cell.
            status = re.search(r"\s+(running|stopped)\s+", row)
            if not status:
                continue
            prefix = row[:status.start()].strip().lstrip("◆").strip()
            displayed = re.match(r"^(.+?)\s+\(" + re.escape(profile_id) + r"\)(?:\s|$)", prefix)
            bare = re.match(r"^" + re.escape(profile_id) + r"(?:\s|$)", prefix)
            if displayed or bare:
                found.append((displayed.group(1) if displayed else profile_id, status.group(1)))
        if len(found) > 1:
            raise _unavailable()
        if found:
            label, gateway = found[0]
            profiles.append({
                "id": profile_id, "name": label[:120], "host": "minipc",
                "gateway": gateway, "version": None,
                "capabilities": {"readable": True,
                                 "attachable": native_input and gateway == "running",
                                 "sendable": native_input and gateway == "running"},
            })
    return profiles


async def _installed_profile(profile_id: str) -> None:
    if not _ID.fullmatch(profile_id) or profile_id not in _allowed_profiles():
        raise HTTPException(status_code=404, detail="Hermes profile not found")
    installed = _parse_profiles(await _hermes("profile", "list"), {profile_id})
    if not installed:
        raise HTTPException(status_code=404, detail="Hermes profile not found")


async def _installed_session(profile_id: str, session_id: str) -> None:
    if not _ID.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="Hermes session not found")
    await _installed_profile(profile_id)
    sessions = _parse_sessions(await _hermes(
        "--profile", profile_id, "sessions", "list", "--limit", "20"))
    if session_id not in {item["id"] for item in sessions}:
        raise HTTPException(status_code=404, detail="Hermes session not found")


async def _native_gateway(action: str, profile_id: str, session_id: str,
                          subject: str, payload: dict | None) -> dict:
    """Talk to the official native gateway through its pinned venv client.

    This child process ONLY attaches as a viewer. It does not execute the Hermes
    CLI or bootstrap/ensure/start a gateway. The gateway owns the admission
    ledger, so the caller's nonce survives retries and this HTTP worker.
    """
    async with _SLOT:
        return await _native_gateway_client(action, profile_id, session_id, subject, payload)


async def _native_gateway_client(action: str, profile_id: str, session_id: str,
                                 subject: str, payload: dict | None) -> dict:
    interpreter = _NATIVE_ROOT / "venv" / "bin" / "python"
    helper = Path(__file__).with_name("hermes_native_input.py")
    if not (interpreter.is_file() and helper.is_file()
            and (_NATIVE_ROOT / "hermes_cli" / "gateway_client.py").is_file()):
        raise _unavailable()
    request = {"action": action, "profile_id": profile_id, "session_id": session_id,
               "subject": subject}
    if payload is not None:
        request.update(expected={key: payload[key] for key in
                                 ("instance_id", "authority_epoch")},
                       nonce=payload["nonce"], text=payload["text"])
    data = (json.dumps(request, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    if len(data) > 12_000:
        raise HTTPException(status_code=422, detail="Hermes input too large")
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    try:
        process = await asyncio.create_subprocess_exec(
            str(interpreter), "-I", str(helper), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
    except (OSError, ValueError):
        raise _unavailable() from None
    try:
        async with asyncio.timeout(55):
            process.stdin.write(data)
            await process.stdin.drain()
            process.stdin.close()
            output = await _read_bounded(process.stdout, 4096)
            await process.wait()
    except (TimeoutError, OverflowError, OSError, asyncio.CancelledError) as error:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(error, asyncio.CancelledError):
            raise
        raise _unavailable() from None
    if process.returncode != 0:
        raise _unavailable()
    try:
        answer = json.loads(output)
    except (ValueError, UnicodeDecodeError):
        raise _unavailable() from None
    if not isinstance(answer, dict):
        raise _unavailable()
    if answer.get("error") in {"gateway_instance_changed", "native_refused"}:
        raise HTTPException(status_code=409, detail="Hermes native admission refused")
    if answer.get("error") or not isinstance(answer.get("result"), dict):
        raise _unavailable()
    return answer["result"]


async def _gateway_input(action: str, profile_id: str, session_id: str,
                         request: Request, payload: dict | None = None) -> dict:
    # Explicit operational gate: only after a verified native version, same
    # gateway-process acceptance and real browser consumer have been proven.
    if os.getenv(_INPUT_GATE_ENV) != "1":
        raise _unavailable()
    await _installed_session(profile_id, session_id)
    result = await _native_gateway(action, profile_id, session_id,
                                   request.state.principal.login, payload)
    if (result.get("profile_id") != profile_id or result.get("session_id") != session_id
            or not isinstance(result.get("instance_id"), str)
            or not _ID.fullmatch(result["instance_id"])
            or type(result.get("authority_epoch")) is not int
            or result["authority_epoch"] <= 0):
        raise _unavailable()
    if action == "send":
        if (payload is None or result["instance_id"] != payload["instance_id"]
                or result["authority_epoch"] != payload["authority_epoch"]
                or not isinstance(result.get("admission_id"), str)
                or not _ID.fullmatch(result["admission_id"])
                or result.get("status") not in {"queued", "started", "unknown", "terminal"}):
            raise _unavailable()
    fields = ("profile_id", "session_id", "instance_id", "authority_epoch")
    if action == "send":
        fields += ("admission_id", "status")
    return {"source": "hermes-native", **{key: result[key] for key in fields}}


def _parse_sessions(output: str) -> list[dict]:
    lines = output.splitlines()
    if "No sessions found." in output and len(lines) == 1:
        return []
    if not lines or not (lines[0].startswith("Title") or lines[0].startswith("Preview")) or not lines[0].rstrip().endswith("ID"):
        raise _unavailable()
    sessions = []
    seen = set()
    for row in lines[2:]:
        match = re.search(r"\s+([A-Za-z0-9][A-Za-z0-9_-]{0,127})\s*$", row)
        if not match:
            continue  # Native truncated-count hint; no session ID in that line.
        session_id = match.group(1)
        if session_id in seen:
            raise _unavailable()
        seen.add(session_id)
        sessions.append({"id": session_id, "title": None, "updated_at": None})
        if len(sessions) > _MAX_SESSIONS:
            raise _unavailable()
    return sessions


def _text(content) -> str:
    if isinstance(content, str):
        return content[:8192]
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)[:8192]
    return ""


def _timestamp(value) -> str | None:
    if isinstance(value, str) and len(value) <= 60:
        return value
    if type(value) in (float, int):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return None


def _parse_export(output: str, session_id: str) -> list[dict]:
    try:
        rows = output.strip().splitlines()
        if len(rows) != 1:
            raise ValueError
        payload = json.loads(rows[0])
        if not isinstance(payload, dict) or payload.get("id") != session_id:
            raise ValueError
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            raise ValueError
        messages = []
        remaining = _MAX_RESPONSE_TEXT
        for item in raw_messages[-_MAX_MESSAGES:]:
            if not isinstance(item, dict) or item.get("role") not in _ROLES:
                raise ValueError
            content = _text(item.get("content"))[:remaining]
            remaining -= len(content)
            messages.append({"role": item["role"], "text": content,
                             "timestamp": _timestamp(item.get("timestamp"))})
        return messages
    except (ValueError, TypeError, KeyError):
        raise _unavailable() from None


@router.get("/api/v1/hermes/profiles")
async def hermes_profiles() -> dict:
    allowed = _allowed_profiles()
    if not allowed:
        return {"source": "hermes-native", "profiles": []}
    return {"source": "hermes-native",
            "profiles": _parse_profiles(await _hermes("profile", "list"), allowed)}


@router.get("/api/v1/hermes/profiles/{profile_id}/sessions")
async def hermes_sessions(profile_id: str, limit: int = Query(default=20, ge=1, le=20)) -> dict:
    await _installed_profile(profile_id)
    output = await _hermes("--profile", profile_id, "sessions", "list", "--limit", str(limit))
    return {"source": "hermes-native", "profile_id": profile_id,
            "sessions": _parse_sessions(output)[:limit]}


@router.get("/api/v1/hermes/profiles/{profile_id}/sessions/{session_id}/messages")
async def hermes_messages(profile_id: str, session_id: str) -> dict:
    if not _ID.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="Hermes session not found")
    await _installed_profile(profile_id)
    sessions = _parse_sessions(await _hermes(
        "--profile", profile_id, "sessions", "list", "--limit", "20"))
    if session_id not in {item["id"] for item in sessions}:
        raise HTTPException(status_code=404, detail="Hermes session not found")
    output = await _hermes("--profile", profile_id, "sessions", "export", "--session-id",
                           session_id, "--format", "jsonl", "--redact", "-")
    return {"source": "hermes-native", "profile_id": profile_id, "session_id": session_id,
            "redacted": True, "messages": _parse_export(output, session_id)}


@router.post("/api/v1/hermes/profiles/{profile_id}/sessions/{session_id}/attach")
async def hermes_attach(profile_id: str, session_id: str, request: Request) -> dict:
    return await _gateway_input("attach", profile_id, session_id, request)


@router.post("/api/v1/hermes/profiles/{profile_id}/sessions/{session_id}/send")
async def hermes_send(profile_id: str, session_id: str, request: Request,
                      body: HermesInput) -> dict:
    return await _gateway_input("send", profile_id, session_id, request,
                                body.model_dump())
