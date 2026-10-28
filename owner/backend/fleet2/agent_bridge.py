"""Fixed-profile local bridge for Signore dei Signori -> ControlCenter.

The bridge is intentionally stdio-only: it does not expose a second HTTP auth
surface, does not replay Signal Deck browser cookies/CSRF state and never
accepts arbitrary shell commands, paths or URLs. It reuses the canonical
registry, FleetPoller, Store and ActionEngine so fleet truth stays single-owned.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from backend.runtime_config import get_runtime_config

from .actions import ACTIONS, ActionEngine
from .poller import FleetPoller
from .registry import FleetRegistry, load_registry
from .store import Store

logger = logging.getLogger("fleet2.agent_bridge")

PROFILE = "signore-dei-signori"
PREVIEW_TTL_S = 120
FLEETPROBE_TOKEN_FILE_ENV = "FLEETPROBE_TOKEN_FILE"
DEFAULT_FLEETPROBE_TOKEN_FILE = (
    Path.home() / ".config" / "controlcenter" / "secrets" / "fleetprobe-token"
)

METHODS = frozenset(
    {
        "controlcenter_fleet_status",
        "controlcenter_host_diagnostics",
        "controlcenter_telemetry_history",
        "controlcenter_recent_receipts",
        "controlcenter_action_capabilities",
        "controlcenter_action_preview",
        "controlcenter_action_apply",
    }
)


class BridgeError(ValueError):
    pass


def _only(args: dict[str, Any], allowed: set[str]) -> None:
    extra = set(args) - allowed
    if extra:
        raise BridgeError(f"unsupported argument(s): {', '.join(sorted(extra))}")


def _bounded_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    payload = receipt.get("payload") or {}
    safe_payload = {
        key: payload[key]
        for key in (
            "operation_id",
            "operation_type",
            "packet_id",
            "request_digest",
            "preview_fingerprint",
            "dispatch_receipt_id",
        )
        if key in payload and isinstance(payload[key], (str, int, float, bool))
    }
    return {
        "id": receipt.get("id"),
        "host_id": receipt.get("host_id"),
        "action": receipt.get("action"),
        "started_at": receipt.get("started_at"),
        "finished_at": receipt.get("finished_at"),
        "outcome": receipt.get("outcome"),
        "payload": safe_payload,
    }


class ControlCenterAgentBridge:
    def __init__(
        self,
        registry: FleetRegistry,
        poller: FleetPoller,
        store: Store,
        engine: ActionEngine,
        *,
        clock=time.time,
    ) -> None:
        self.registry = registry
        self.poller = poller
        self.store = store
        self.engine = engine
        self.clock = clock

    def _host(self, host_id: str):
        host = self.registry.get(host_id)
        if host is None:
            raise BridgeError("unknown host")
        return host

    async def call(self, method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in METHODS:
            raise BridgeError("unknown method")
        params = args or {}
        if not isinstance(params, dict):
            raise BridgeError("args must be an object")

        if method == "controlcenter_fleet_status":
            _only(params, {"refresh"})
            refresh = bool(params.get("refresh", True))
            return await self.poller.poll_once(force=refresh) if refresh else self.poller.snapshot()

        if method == "controlcenter_host_diagnostics":
            _only(params, {"host_id", "refresh"})
            host_id = str(params.get("host_id", ""))
            self._host(host_id)
            snapshot = (
                await self.poller.poll_once(host_id, force=bool(params.get("refresh", True)))
                if bool(params.get("refresh", True))
                else self.poller.snapshot()
            )
            host = next(entry for entry in snapshot["hosts"] if entry["id"] == host_id)
            return {"host": host}

        if method == "controlcenter_telemetry_history":
            _only(params, {"host_id", "minutes"})
            host_id = str(params.get("host_id", ""))
            self._host(host_id)
            minutes = int(params.get("minutes", 60))
            if not 5 <= minutes <= 20160:
                raise BridgeError("minutes must be between 5 and 20160")
            return {
                "host_id": host_id,
                "minutes": minutes,
                "points": await asyncio.to_thread(self.store.history, host_id, minutes),
            }

        if method == "controlcenter_recent_receipts":
            _only(params, {"limit"})
            limit = int(params.get("limit", 20))
            if not 1 <= limit <= 50:
                raise BridgeError("limit must be between 1 and 50")
            rows = await asyncio.to_thread(self.store.receipts, limit)
            return {"receipts": [_bounded_receipt(row) for row in rows]}

        if method == "controlcenter_action_capabilities":
            _only(params, {"host_id", "refresh"})
            host = self._host(str(params.get("host_id", "")))
            return await asyncio.to_thread(
                self.engine.capabilities, host, bool(params.get("refresh", False))
            )

        if method == "controlcenter_action_preview":
            _only(params, {"host_id", "action_key"})
            host = self._host(str(params.get("host_id", "")))
            action_key = str(params.get("action_key", ""))
            if action_key not in ACTIONS:
                raise BridgeError("unknown action")
            capability = await asyncio.to_thread(self.engine.capabilities, host, False)
            action = capability["actions"][action_key]
            preview_id = f"agent-preview-{uuid.uuid4().hex[:16]}"
            expires_at = self.clock() + PREVIEW_TTL_S
            apply_allowed = bool(action["available"] and not ACTIONS[action_key].destructive)
            canonical = json.dumps(
                {
                    "profile": PROFILE,
                    "preview_id": preview_id,
                    "host_id": host.id,
                    "action_key": action_key,
                    "expires_at": expires_at,
                    "apply_allowed": apply_allowed,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            payload = {
                "caller_profile": PROFILE,
                "operation_type": "controlcenter.action.preview",
                "preview_fingerprint": fingerprint,
                "expires_at": expires_at,
                "action_key": action_key,
                "apply_allowed": apply_allowed,
            }
            self.store.open_receipt(preview_id, host.id, "agent-action-preview", payload)
            self.store.close_receipt(
                preview_id,
                "dry-run",
                "safe action preview" if apply_allowed else "apply blocked by capability or owner gate",
            )
            return {
                "preview_id": preview_id,
                "preview_fingerprint": fingerprint,
                "expires_at": expires_at,
                "host_id": host.id,
                "action_key": action_key,
                "capability": action,
                "apply_allowed": apply_allowed,
                "block_reason": (
                    None
                    if apply_allowed
                    else (
                        "destructive actions require a separate owner confirmation gate"
                        if ACTIONS[action_key].destructive
                        else action.get("reason") or "action unavailable"
                    )
                ),
            }

        if method == "controlcenter_action_apply":
            _only(
                params,
                {"host_id", "action_key", "preview_id", "preview_fingerprint", "idempotency_key"},
            )
            host = self._host(str(params.get("host_id", "")))
            action_key = str(params.get("action_key", ""))
            if action_key != "restart-agent":
                raise BridgeError("agent bridge apply permits only restart-agent")
            preview_id = str(params.get("preview_id", ""))
            fingerprint = str(params.get("preview_fingerprint", ""))
            idempotency_key = str(params.get("idempotency_key", "")).strip()
            if not idempotency_key or len(idempotency_key) > 200:
                raise BridgeError("invalid idempotency_key")
            preview = self.store.receipt(preview_id)
            if (
                not preview
                or preview.get("host_id") != host.id
                or preview.get("action") != "agent-action-preview"
                or (preview.get("payload") or {}).get("action_key") != action_key
                or (preview.get("payload") or {}).get("preview_fingerprint") != fingerprint
                or not (preview.get("payload") or {}).get("apply_allowed")
            ):
                raise BridgeError("preview does not match requested action")
            if float((preview.get("payload") or {}).get("expires_at") or 0) < self.clock():
                raise BridgeError("preview expired")

            digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            operation_id = f"agent-apply-{digest[:20]}"
            payload = {
                "caller_profile": PROFILE,
                "operation_id": operation_id,
                "operation_type": "controlcenter.action.apply",
                "preview_fingerprint": fingerprint,
                "request_digest": digest,
                "action_key": action_key,
            }
            if not self.store.claim_receipt(operation_id, host.id, "agent-action-apply", payload):
                existing = self.store.receipt(operation_id)
                existing_payload = (existing or {}).get("payload") or {}
                if (
                    not existing
                    or existing.get("host_id") != host.id
                    or existing_payload.get("preview_fingerprint") != fingerprint
                    or existing_payload.get("action_key") != action_key
                ):
                    raise BridgeError("idempotency key already used for a different request")
                return {
                    "operation_id": operation_id,
                    "host_id": host.id,
                    "action_key": action_key,
                    "replayed": True,
                    "dispatch_receipt_id": existing_payload.get("dispatch_receipt_id"),
                    "outcome": existing.get("outcome"),
                }

            try:
                dispatched = await asyncio.to_thread(
                    self.engine.dispatch, host, action_key, ""
                )
                payload["dispatch_receipt_id"] = dispatched["receipt_id"]
                self.store.update_receipt_payload(operation_id, payload)
                self.store.close_receipt(operation_id, "dispatched", "typed safe action dispatched")
            except Exception:
                self.store.close_receipt(operation_id, "failed", "typed safe action dispatch failed")
                raise
            return {
                "operation_id": operation_id,
                "host_id": host.id,
                "action_key": action_key,
                "replayed": False,
                "dispatch_receipt_id": dispatched["receipt_id"],
                "outcome": "dispatched",
            }

        raise BridgeError("unknown method")


def _read_fleetprobe_token() -> str:
    path = Path(
        os.getenv(FLEETPROBE_TOKEN_FILE_ENV, str(DEFAULT_FLEETPROBE_TOKEN_FILE))
    ).expanduser()
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("FleetProbe token file is empty")
    return token


def build_default_bridge() -> ControlCenterAgentBridge:
    registry = load_registry()
    runtime = get_runtime_config()
    store = Store(
        runtime.database_path,
        sample_retention_days=runtime.sample_retention_days,
        receipt_retention_days=runtime.receipt_retention_days,
    )
    poller = FleetPoller(registry, store, _read_fleetprobe_token())
    engine = ActionEngine(registry, store)
    return ControlCenterAgentBridge(registry, poller, store, engine)


async def serve_stdio(bridge: ControlCenterAgentBridge) -> None:
    """One JSON request per line; one bounded JSON response per line."""
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise BridgeError("request must be an object")
            if set(request) - {"method", "args"}:
                raise BridgeError("request contains unsupported fields")
            result = await bridge.call(str(request.get("method", "")), request.get("args"))
            response = {"ok": True, "result": result}
        except (BridgeError, ValueError, TypeError, KeyError) as exc:
            response = {"ok": False, "error": str(exc)[:300]}
        except Exception as exc:
            logger.exception("unexpected agent bridge request failure")
            response = {"ok": False, "error": f"{type(exc).__name__}: request failed"}
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main() -> int:
    bridge = build_default_bridge()
    asyncio.run(serve_stdio(bridge))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
