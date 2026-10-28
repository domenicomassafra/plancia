"""Authenticated Signal Deck API: fleet stream, actions and bounded audit."""
from __future__ import annotations
import asyncio
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import Field
from backend.auth import admit_websocket
from backend.browser_profiles import open_profile
from . import workspace_sources
from .audit import public_receipt as _public_receipt
from .actions import ACTIONS, ActionEngine
from .poller import FleetPoller
from .profiles_api import register_profile_routes
from .registry import FleetRegistry
from .request_models import StrictRequest
from .store import Store
from .workspace_api import register_workspace_routes

class ActionRequest(StrictRequest):
    confirmation: str = Field(default="", max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=200)


def build_router(
    *,
    registry: FleetRegistry,
    poller: FleetPoller,
    store: Store,
    engine: ActionEngine,
    session_store: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    # Origin and CSRF for every non-safe method are already enforced by
    # TailnetAuthMiddleware before a route runs.

    def _host_or_404(host_id: str):
        host = registry.get(host_id)
        if host is None:
            raise HTTPException(status_code=404, detail="unknown host")
        return host

    # ------------------------------------------------------------------ fleet

    @router.get("/fleet")
    async def fleet() -> Dict[str, Any]:
        return poller.snapshot()

    @router.get("/fleet/{host_id}/history")
    async def history(
        host_id: str, minutes: int = Query(60, ge=5, le=20160)
    ) -> Dict[str, Any]:
        _host_or_404(host_id)
        points = await asyncio.to_thread(store.history, host_id, minutes)
        return {"host_id": host_id, "minutes": minutes, "points": points}
    @router.websocket("/stream")
    async def stream(websocket: WebSocket) -> None:
        if not await admit_websocket(websocket, session_store):
            return
        # admit_websocket only accepts the socket when it rejects it, so the
        # successful path has to complete the handshake itself.
        await websocket.accept()
        queue = poller.subscribe()
        try:
            await websocket.send_json({"type": "snapshot", "data": poller.snapshot()})
            while True:
                payload = await queue.get()
                await websocket.send_json({"type": "snapshot", "data": payload})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            poller.unsubscribe(queue)

    # --------------------------------------------------------------- profiles

    register_profile_routes(router, store, lambda *args: open_profile(*args))

    # ---------------------------------------------------------------- actions

    @router.get("/actions/{host_id}")
    async def action_capabilities(
        host_id: str, refresh: bool = False
    ) -> Dict[str, Any]:
        host = _host_or_404(host_id)
        return await asyncio.to_thread(engine.capabilities, host, refresh)

    @router.post("/actions/{host_id}/{action_key}/preview")
    async def action_preview(host_id: str, action_key: str) -> Dict[str, Any]:
        host = _host_or_404(host_id)
        if action_key not in ACTIONS:
            raise HTTPException(status_code=404, detail="unknown action")
        try:
            return await asyncio.to_thread(engine.preview, host, action_key)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/actions/{host_id}/{action_key}")
    async def action_dispatch(
        host_id: str, action_key: str, payload: ActionRequest
    ) -> Dict[str, Any]:
        host = _host_or_404(host_id)
        if action_key not in ACTIONS:
            raise HTTPException(status_code=404, detail="unknown action")
        try:
            return await asyncio.to_thread(
                engine.dispatch,
                host,
                action_key,
                payload.confirmation,
                idempotency_key=payload.idempotency_key,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/receipts")
    async def receipts(limit: int = Query(30, ge=1, le=200)) -> Dict[str, Any]:
        rows = await asyncio.to_thread(store.receipts, limit)
        return {"receipts": [_public_receipt(receipt) for receipt in rows]}

    @router.get("/receipts/{receipt_id}")
    async def receipt(receipt_id: str) -> Dict[str, Any]:
        if not receipt_id or len(receipt_id) > 100:
            raise HTTPException(status_code=400, detail="invalid receipt id")
        row = await asyncio.to_thread(store.receipt, receipt_id)
        if not row:
            raise HTTPException(status_code=404, detail="receipt not found")
        return {"receipt": _public_receipt(row)}

    # ---------------------------------------------------------- control summary

    @router.get("/summary")
    async def control_summary(refresh: bool = False) -> Dict[str, Any]:
        return await asyncio.to_thread(workspace_sources.control_summary, refresh)

    # -------------------------------------------------------------- Workspace

    register_workspace_routes(router, store)

    return router
