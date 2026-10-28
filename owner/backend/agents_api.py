"""Owner-authenticated, read-only adapter to the installed Orca CLI.

Orca owns the history and session lifecycle. We invoke its public CLI, never
parse or modify private session files, and keep searches out of access-log URLs.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter()
_ASSETS = Path(__file__).resolve().parent / "agents"
_HOSTS = {"minipc": None, "macbookair": "macbookair", "matebooklinux": "matebooklinux"}
_RESULT_FIELDS = ("agent", "sessionId", "title", "cwd", "updatedAt", "messageCount")


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=120, pattern=r"^\S(?:[\s\S]*\S)?$")
    host: Literal["minipc", "macbookair", "matebooklinux"] = "minipc"


# One heavy CLI search at a time, respecting the MiniPC resource guard.
_ORCA_SLOT = asyncio.Semaphore(1)
_MAX_OUTPUT = 2_000_000


async def _read_bounded(stream) -> bytes:
    data = bytearray()
    while chunk := await stream.read(65_536):
        data.extend(chunk)
        if len(data) > _MAX_OUTPUT:
            raise OverflowError("Orca output exceeded bound")
    return bytes(data)


async def _orca(*args: str) -> dict:
    binary = shutil.which("orca") or str(Path.home() / ".local" / "bin" / "orca")
    async with _ORCA_SLOT:
        try:
            process = await asyncio.create_subprocess_exec(
                binary, *args, "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (FileNotFoundError, PermissionError):
            raise HTTPException(status_code=503, detail="Orca CLI unavailable") from None
        try:
            async with asyncio.timeout(12):
                stdout = await _read_bounded(process.stdout)
                returncode = await process.wait()
        except (TimeoutError, OverflowError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                process.kill()
            # communicate() drains stdout before waiting: wait() alone may
            # deadlock while the pipe transport still holds unread bytes.
            try:
                await asyncio.wait_for(process.communicate(), timeout=3)
            except asyncio.TimeoutError:
                # The child was killed already. Never hold the global slot.
                pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HTTPException(status_code=503, detail="Orca runtime unavailable") from None
        if returncode != 0:
            raise HTTPException(status_code=503, detail="Orca runtime unavailable")
    try:
        envelope = json.loads(stdout)
        if envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
            raise ValueError("Orca error")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=503, detail="Orca response invalid") from None
    return envelope["result"]


async def search_history(query: str, host: str) -> dict:
    args = ["search", "--scope", "conversation", "--sort", "newest", "--limit", "8", "--query", query]
    if _HOSTS[host] is not None:
        args.extend(["--environment", _HOSTS[host]])
    return await _orca(*args)


@router.get("/agents", response_class=HTMLResponse)
def agents_page() -> HTMLResponse:
    return HTMLResponse(_ASSETS.joinpath("index.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@router.get("/agents/client.js")
def agents_script() -> FileResponse:
    return FileResponse(_ASSETS / "client.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v1/agents/status")
async def agents_status() -> dict:
    result = await _orca("search", "--index-status")
    counts = result.get("sessionsByAgent", {})
    if not isinstance(counts, dict):
        counts = {}
    return {"donor": "orca", "indexReady": result.get("enabled") is True,
            "phase": str(result.get("phase", "unknown")),
            "agents": {k: v for k, v in counts.items()
                       if k in {"claude", "codex", "pi", "opencode", "opencode2", "hermes"}
                       and isinstance(v, int)}, "historyOnly": True}


@router.post("/api/v1/agents/search")
async def agents_search(request: SearchRequest) -> dict:
    result = await search_history(request.query, request.host)
    raw_hits = result.get("hits", [])
    if not isinstance(raw_hits, list):
        raise HTTPException(status_code=503, detail="Orca search response invalid")
    hits = []
    for hit in raw_hits[:8]:
        if not isinstance(hit, dict):
            continue
        item = {key: hit.get(key) for key in _RESULT_FIELDS}
        evidence = hit.get("evidence", {})
        if isinstance(evidence, dict):
            item["snippet"] = str(evidence.get("snippet") or "")[:600]
        else:
            item["snippet"] = ""
        hits.append(item)
    return {"host": request.host, "hits": hits, "truncated": bool(result.get("truncated", False))}
