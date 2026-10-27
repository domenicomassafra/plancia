"""Owner-only API for choosing a Codex identity on a NEW Orca terminal."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.agents_api import _orca
from backend.codex_run import safe_accounts
from backend.runtime_config import get_runtime_config, ensure_private_directory

router = APIRouter()


class NewCodexSession(BaseModel):
    accountId: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=80)
    operationId: UUID


async def _accounts() -> list[dict]:
    return safe_accounts(await _orca("account", "list"))


def _receipts_dir() -> Path:
    root = get_runtime_config().state_root / "codex-session-operations"
    ensure_private_directory(root)
    return root


def _save(receipt: Path, payload: dict) -> None:
    temp = receipt.with_suffix(".next")
    with temp.open("x", encoding="utf-8") as stream:
        os.chmod(temp, 0o600)
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, receipt)


def _result(payload: dict, replayed: bool = False) -> dict:
    return {"handle": payload["handle"], "accountId": payload["accountId"],
            "model": payload["model"], "replayed": replayed}


@router.get("/api/v1/agents/codex/accounts")
async def list_codex_accounts() -> dict:
    return {"accounts": await _accounts()}


@router.get("/api/v1/agents/codex/sessions")
async def codex_session_readback() -> dict:
    live = await _orca("terminal", "list")
    handles = {entry["handle"] for entry in live.get("terminals", [])
               if isinstance(entry, dict) and isinstance(entry.get("handle"), str)
               and entry.get("connected") is True and not entry.get("orphaned")}
    sessions = []
    for receipt in _receipts_dir().glob("*.json"):
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if payload.get("state") == "done" and payload.get("handle") in handles:
                sessions.append(_result(payload))
        except (OSError, ValueError, KeyError):
            continue
    return {"sessions": sessions[-50:]}


@router.post("/api/v1/agents/codex/sessions")
async def create_codex_session(request: NewCodexSession) -> dict:
    selected = next((account for account in await _accounts()
                     if account["id"] == request.accountId), None)
    if selected is None or request.model not in selected["models"]:
        raise HTTPException(422, "Account or enabled model unavailable")
    receipt = _receipts_dir() / (str(request.operationId) + ".json")
    request_meta = {"accountId": request.accountId, "model": request.model}
    try:
        fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            prior = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(409, "Launch receipt unreadable") from None
        if any(prior.get(key) != value for key, value in request_meta.items()):
            raise HTTPException(409, "Operation already used for a different selection")
        if prior.get("state") != "done":
            raise HTTPException(409, "Launch status uncertain; inspect original terminals")
        return _result(prior, replayed=True)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({**request_meta, "state": "pending"}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # argv contains only fixed code location, validated opaque account ID and
    # cached model identifier. The credential home stays inside codex_run.py.
    launcher = Path(__file__).resolve().with_name("codex_run.py")
    command = " ".join(shlex.quote(arg) for arg in (
        "python3", str(launcher), request.accountId, request.model
    ))
    workspace = Path.home()
    native = await _orca(
        "terminal", "create", "--worktree", f"path:{workspace}",
        "--title", "Codex · " + selected["label"][:70],
        "--command", command,
    )
    terminal = native.get("terminal", {})
    handle = terminal.get("handle") if isinstance(terminal, dict) else None
    if not isinstance(handle, str) or not handle.startswith("term_"):
        raise HTTPException(503, "Orca creation not confirmed; inspect terminals")
    done = {**request_meta, "state": "done", "handle": handle}
    try:
        _save(receipt, done)
    except OSError:
        raise HTTPException(503, "Orca created terminal; readback unavailable") from None
    return _result(done)
