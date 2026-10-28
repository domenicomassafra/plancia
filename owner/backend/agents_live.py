"""Owner-only live screen and input for *existing* Orca terminals.

Orca is the only owner of the PTY and agent lifecycle. This adapter uses
its public CLI; it neither starts agent runtimes nor reads private files.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from backend.agents_api import _orca

router = APIRouter()
_HANDLE = r"^term_[a-zA-Z0-9-]{1,80}$"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class TerminalInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


async def _terminals() -> list[dict]:
    result = await _orca("terminal", "list")
    entries = result.get("terminals")
    if not isinstance(entries, list):
        raise HTTPException(503, "Orca terminal inventory unavailable")
    return [entry for entry in entries[:50]
            if isinstance(entry, dict) and isinstance(entry.get("handle"), str)
            and re.fullmatch(_HANDLE, entry["handle"])]


async def _terminal(handle: str, *, writable: bool = False) -> dict:
    for entry in await _terminals():
        if entry["handle"] == handle and entry.get("connected") is True and not entry.get("orphaned"):
            if writable and entry.get("writable") is not True:
                raise HTTPException(409, "Terminal is read-only")
            return entry
    raise HTTPException(404, "Live terminal not found")


@router.get("/api/v1/agents/terminals")
async def list_live_terminals() -> dict:
    entries = await _terminals()
    return {"donor": "orca", "terminals": [
        {"handle": entry["handle"],
         "title": str(entry.get("title") or "Sessione")[:120],
         "agent": str(entry.get("agentIdentity") or "unknown")[:40],
         "connected": entry.get("connected") is True and not entry.get("orphaned"),
         "writable": entry.get("writable") is True and not entry.get("orphaned"),
         "lastOutputAt": entry.get("lastOutputAt")}
        for entry in entries
    ]}


@router.get("/api/v1/agents/terminals/{handle}/screen")
async def live_screen(handle: str = Path(pattern=_HANDLE)) -> dict:
    await _terminal(handle)
    result = await _orca("terminal", "read", "--terminal", handle, "--screen")
    terminal = result.get("terminal")
    if not isinstance(terminal, dict):
        raise HTTPException(503, "Orca terminal screen unavailable")
    # The CLI removes ANSI escape sequences in screen mode. The browser uses
    # textContent rather than HTML. Never send the raw CLI envelope to clients.
    tail = terminal.get("tail")
    if not isinstance(tail, list) or terminal.get("source") != "screen":
        raise HTTPException(503, "Orca rendered screen unavailable")
    lines = [line[:400] for line in tail[-100:] if isinstance(line, str)]
    return {"handle": handle, "source": "screen", "text": "\n".join(lines)[-16000:],
            "status": str(terminal.get("status") or "unknown")[:30]}


@router.post("/api/v1/agents/terminals/{handle}/send")
async def send_live_input(payload: TerminalInput, handle: str = Path(pattern=_HANDLE)) -> dict:
    await _terminal(handle, writable=True)
    if not payload.text.strip() or _CONTROL.search(payload.text):
        raise HTTPException(422, "Invalid terminal input")
    result = await _orca("terminal", "send", "--terminal", handle,
                         "--text", payload.text, "--enter", "--wait-submit", "2")
    # A successful CLI receipt proves command acceptance, not a model reply.
    # Never retry automatically after a transport error; inspect screen first.
    return {"accepted": True, "handle": handle,
            "submissionObserved": result.get("submissionObserved") is True}
