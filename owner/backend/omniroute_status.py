"""Read-only OmniRoute policy and native fleet observation for Signal Deck."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response
from backend.native_probe import SLOT as _SLOT

router = APIRouter(prefix="/api")
_inflight: asyncio.Task | None = None

# Same source-root contract as the fleet controller, independent of dashboard releases.
CONTROL_HOME = Path(os.environ.get("CONTROLCENTER_HOME", str(Path.home() / "ControlCenter"))).expanduser().absolute()
POLICY_PATH = CONTROL_HOME / "omniroute" / "fleet-default-models.json"
CHECK_COMMAND = (
    "controlcenter", "fleet", "check", "--hosts", "minipc",
    "--components", "omniroute-core,omniroute-catalog,omniroute-provider-llm,omniroute-client,omniroute-tailnet",
    "--timeout", "20",
)


def _policy() -> dict[str, Any]:
    try:
        raw = POLICY_PATH.read_bytes()
        source = json.loads(raw)
        if source.get("schema_version") != 2 or source.get("kind") != "omniroute-fleet-policy":
            raise ValueError("invalid canonical policy")
        models = source["models"]
        if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
            raise ValueError("invalid policy catalog")
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False, "source": "omniroute/fleet-default-models.json"}

    return {
        "available": True,
        "source": "omniroute/fleet-default-models.json",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "model_count": len(models),
        "models": models,
        "defaults": source.get("harness_defaults", {}).get("tasks", {}),
        "discovery_authority": source.get("discovery_authority"),
        "configuration_authority": source.get("configuration_authority"),
        "projection_rule": source.get("projection_rule"),
    }


def _observe() -> dict[str, Any]:
    observation: dict[str, Any] = {
        "source": " ".join(CHECK_COMMAND),
        "result": "unavailable",
        "exit_code": None,
        "scope_complete": False,
        "checked_at": None,
        "policy_revision": None,
        "items": [],
    }
    try:
        completed = subprocess.run(
            CHECK_COMMAND, capture_output=True, text=True, timeout=25, check=False,
        )
        envelope = json.loads(completed.stdout)
        if not isinstance(envelope, dict) or envelope.get("operation") != "check" or envelope.get("schema_version") != 2:
            return observation
        rows = envelope.get("items")
        if not isinstance(rows, list):
            return observation
        cells = [
            {"host": row.get("host"), "component": row.get("component"), "result": row.get("result")}
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("component"), str)
        ]
        result = envelope.get("result")
        exit_code = completed.returncode
        scope_complete = envelope.get("scope_complete") is True
        # A positive result requires actual cells, a complete scope, and both
        # the native exit code and envelope agreeing. Exit 2 is drift.
        clean = (
            exit_code == 0 and result == "clean" and scope_complete
            and bool(cells) and len(cells) == len(rows)
            and all(row["result"] == "clean" for row in cells)
        )
        if clean:
            display_result = "clean"
        elif exit_code == 2 and result == "changes":
            display_result = "changes"
        elif result in {"blocked", "failed"} and exit_code in {1, 3}:
            display_result = result
        else:
            display_result = "unavailable"
        observation.update(
            result=display_result,
            exit_code=exit_code,
            scope_complete=scope_complete,
            checked_at=envelope.get("checked_at") if isinstance(envelope.get("checked_at"), str) else None,
            policy_revision=envelope.get("policy_revision") if isinstance(envelope.get("policy_revision"), str) else None,
            items=cells,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return observation


@router.get("/omniroute")
async def omniroute_status(response: Response) -> dict[str, Any]:
    global _inflight
    response.headers["Cache-Control"] = "no-store"
    async def observe_once():
        async with _SLOT:
            return await asyncio.to_thread(_observe)
    if _inflight is None or _inflight.done():
        _inflight = asyncio.create_task(observe_once())
    policy, observation = await asyncio.gather(
        asyncio.to_thread(_policy), asyncio.shield(_inflight)
    )
    if not policy["available"] and observation["result"] == "clean":
        observation["result"] = "unavailable"
    return {"policy": policy, "observation": observation}
