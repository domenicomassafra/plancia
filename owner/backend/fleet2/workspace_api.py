"""Dashboard read-only workspace projections and route registration."""
from __future__ import annotations
import asyncio
from typing import Any, Dict
from fastapi import APIRouter, Query
from . import workspace, workspace_sources
from .audit import public_receipt as _public_receipt
from .store import Store
from .workspace_mutation_api import register_workspace_mutation_routes


def register_workspace_routes(router: APIRouter, store: Store) -> None:
    @router.get("/workspace")
    async def workspace_view(refresh: bool = False) -> Dict[str, Any]:
        payload = await asyncio.to_thread(workspace.workspace_snapshot, refresh)
        receipts = await asyncio.to_thread(store.receipts, 20)
        manifests = await asyncio.to_thread(workspace_sources.canonical_manifests, refresh)
        payload["receipts"] = [_public_receipt(receipt) for receipt in receipts]
        payload["external_context"] = await asyncio.to_thread(
            workspace.external_context_projection,
            payload["notion"],
            payload["obsidian"],
            manifests,
        )
        return payload

    @router.get("/workspace/notion")
    async def workspace_notion(
        refresh: bool = False,
        cursor: str = Query("", max_length=200),
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(workspace.notion_database, refresh, cursor)

    # Action Items use the authenticated, idempotent v3 workspace envelope.
    # Caller evidence, durable idempotency, and explicit ROLLBACK are mandatory.
    register_workspace_mutation_routes(router, store)

    @router.get("/workspace/obsidian")
    async def workspace_obsidian(
        q: str = Query("", max_length=120),
        folder: str = Query("", max_length=160),
        note: str = Query("", max_length=240),
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(workspace.obsidian_explorer, q, folder, note)
