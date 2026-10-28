"""Guarded, receipt-backed Notion Action Item mutation endpoints."""
from __future__ import annotations
import asyncio
import uuid
from typing import Any, Dict
from fastapi import APIRouter, HTTPException
from . import workspace
from .store import Store
from .workspace_models import WorkspaceActionItemEditPreviewRequest, WorkspaceActionItemEditApplyRequest, WorkspaceActionItemEditRollbackRequest

def register_workspace_mutation_routes(router: APIRouter, store: Store) -> None:
    @router.post("/workspace/operations/action-item.edit/preview")
    async def action_item_edit_preview(
        payload: WorkspaceActionItemEditPreviewRequest,
    ) -> Dict[str, Any]:
        try:
            packet = await asyncio.to_thread(
                workspace.preview_action_item_edit,
                payload.row_id,
                payload.changes,
                actor=payload.actor,
                surface=payload.surface,
                caller_type=payload.caller_type,
                agent_runtime=payload.agent_runtime,
                overlay_id=payload.overlay_id,
                idempotency_key=payload.idempotency_key,
                provenance=payload.provenance,
                preimage_hash=payload.preimage_hash,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        receipt_id = uuid.uuid4().hex[:12]
        store.open_receipt(
            receipt_id,
            "control-plane",
            "action-item-edit-preview",
            {
                "operation_id": packet["operation_id"],
                "idempotency_key": packet["idempotency_key"],
                "caller": packet["caller"],
                "packet_id": packet["packet_id"],
            },
        )
        store.close_receipt(
            receipt_id, "dry-run", "action-item.edit preparato; mutation_count=0"
        )
        return {
            **packet,
            "receipt_id": receipt_id,
            "receipt": {"id": receipt_id, "outcome": "dry-run"},
        }

    @router.post("/workspace/operations/action-item.edit/apply")
    async def action_item_edit_apply(
        payload: WorkspaceActionItemEditApplyRequest,
    ) -> Dict[str, Any]:
        if payload.confirmation != "APPLICA":
            raise HTTPException(status_code=400, detail="digita APPLICA per confermare")
        try:
            caller = workspace.action_item_edit_caller(
                payload.actor,
                payload.surface,
                payload.caller_type,
                payload.agent_runtime,
                payload.overlay_id,
            )
            identity = workspace.action_item_edit_identity(
                payload.row_id, payload.changes, payload.idempotency_key
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if identity["operation_id"] != payload.operation_id:
            raise HTTPException(
                status_code=409,
                detail="operation_id non corrisponde alla idempotency_key",
            )
        receipt_id = identity["operation_id"]
        existing = await asyncio.to_thread(store.receipt, receipt_id)
        if existing:
            record = existing["payload"]
            if (
                existing["action"] != workspace.ACTION_ITEM_EDIT_OPERATION
                or record.get("request_digest") != identity["request_digest"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="idempotency_key già usata con un payload diverso",
                )
            if existing["outcome"] != "running" or "receipt_sha" in record:
                expected_sha = workspace.receipt_sha(
                    {
                        key: value
                        for key, value in record.items()
                        if key != "receipt_sha"
                    }
                )
                if record.get("receipt_sha") != expected_sha:
                    raise HTTPException(
                        status_code=409, detail="ricevuta action-item.edit non integra"
                    )
            if existing["outcome"] == "running":
                try:
                    verified = await asyncio.to_thread(
                        workspace.verify_action_item_edit_post_fetch, record
                    )
                    row = verified["row"]
                    post_fetch = verified["post_fetch"]
                except (RuntimeError, ValueError, IndexError) as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="action-item.edit running senza postimage riconciliabile",
                    ) from exc
                record.update(
                    {
                        "registry_id": record.get("registry_id"),
                        "row": row,
                        "after": post_fetch["properties"],
                        "post_fetch": post_fetch,
                        "reconciled": "postimage",
                    }
                )
                record["receipt_sha"] = workspace.receipt_sha(
                    {
                        key: value
                        for key, value in record.items()
                        if key != "receipt_sha"
                    }
                )
                await asyncio.to_thread(
                    store.update_receipt_payload, receipt_id, record
                )
                store.close_receipt(
                    receipt_id,
                    "succeeded",
                    "action-item.edit riconciliato dal post-fetch; mutation_count=0",
                )
                return {
                    "ok": True,
                    "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
                    "operation_id": receipt_id,
                    "status": "already-applied",
                    "mutation_count": 0,
                    "registry_id": record.get("registry_id"),
                    "row": row,
                    "post_fetch": post_fetch,
                    "callers": record["callers"],
                    "receipt_id": receipt_id,
                    "receipt_sha": record["receipt_sha"],
                }
            if existing["outcome"] == "rolled-back":
                return {
                    "ok": True,
                    "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
                    "operation_id": receipt_id,
                    "status": "already-rolled-back",
                    "mutation_count": 0,
                    "receipt_id": receipt_id,
                    "receipt_sha": record.get("receipt_sha"),
                }
            if existing["outcome"] != "succeeded":
                raise HTTPException(
                    status_code=409,
                    detail=f"action-item.edit esistente in stato {existing['outcome']}",
                )
            try:
                post_fetch = await asyncio.to_thread(
                    workspace.verify_action_item_edit_post_fetch, record
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            callers = [
                entry
                for entry in (record.get("callers") or [record.get("caller")])
                if isinstance(entry, dict)
            ]
            if caller not in callers:
                callers.append(caller)
                record["callers"] = callers
                record["receipt_sha"] = workspace.receipt_sha(
                    {
                        key: value
                        for key, value in record.items()
                        if key != "receipt_sha"
                    }
                )
                await asyncio.to_thread(
                    store.update_receipt_payload, receipt_id, record
                )
            return {
                "ok": True,
                "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
                "operation_id": receipt_id,
                "status": "already-applied",
                "mutation_count": 0,
                "registry_id": record.get("registry_id"),
                "row": record["row"],
                "post_fetch": post_fetch,
                "callers": callers,
                "receipt_id": receipt_id,
                "receipt_sha": record["receipt_sha"],
            }
        try:
            packet = await asyncio.to_thread(
                workspace.preview_action_item_edit,
                payload.row_id,
                payload.changes,
                actor=payload.actor,
                surface=payload.surface,
                caller_type=payload.caller_type,
                agent_runtime=payload.agent_runtime,
                overlay_id=payload.overlay_id,
                idempotency_key=payload.idempotency_key,
                provenance=payload.provenance,
                preimage_hash=payload.preimage_hash,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if packet["packet_id"] != payload.packet_id:
            raise HTTPException(
                status_code=409,
                detail="packet action-item.edit non corrisponde alla preview",
            )
        record = {
            "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
            "operation_id": receipt_id,
            "caller": packet["caller"],
            "callers": [packet["caller"]],
            "entity": packet["entity"],
            "destination": packet["destination"],
            "schema_version": packet["schema_version"],
            "idempotency_key": packet["idempotency_key"],
            "request_digest": packet["request_digest"],
            "provenance": packet["provenance"],
            "packet_id": packet["packet_id"],
            "preimage_hash": packet["preimage_hash"],
            "source_key": workspace.ACTIONS_SOURCE_KEY,
            "data_source_id": packet["data_source_id"],
            "row_id": packet["row_id"],
            "before": packet["before"],
            "changes": packet["changes"],
            "rollback": packet["rollback"],
        }
        if not store.claim_receipt(
            receipt_id, "control-plane", workspace.ACTION_ITEM_EDIT_OPERATION, record
        ):
            raise HTTPException(
                status_code=409,
                detail="action-item.edit già acquisito da una richiesta concorrente",
            )
        try:
            result = await asyncio.to_thread(
                workspace.apply_notion_edit,
                payload.row_id,
                payload.changes,
                packet["preimage_hash"],
                packet["packet_id"],
                packet["data_source_id"],
            )
            record.update(
                {
                    "registry_id": result["registry_id"],
                    "row": result["row"],
                    "after": {
                        name: result["row"]["properties"].get(name)
                        for name in packet["changes"]
                    },
                    "post_fetch": {
                        "row_id": payload.row_id,
                        "properties": {
                            name: result["row"]["properties"].get(name)
                            for name in packet["changes"]
                        },
                    },
                }
            )
            record["receipt_sha"] = workspace.receipt_sha(
                {key: value for key, value in record.items() if key != "receipt_sha"}
            )
            await asyncio.to_thread(store.update_receipt_payload, receipt_id, record)
            store.close_receipt(
                receipt_id,
                "succeeded",
                f"action-item.edit applicato · Registry {result['registry_id']} · post-fetch verificato",
            )
        except RuntimeError as exc:
            store.close_receipt(receipt_id, "failed", str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            store.close_receipt(receipt_id, "failed", str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            store.close_receipt(
                receipt_id,
                "failed",
                "action-item.edit fallito; rollback del writer tentato",
            )
            raise HTTPException(
                status_code=502,
                detail=f"action-item.edit fallito: {type(exc).__name__}",
            ) from exc
        return {
            "ok": True,
            "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
            "operation_id": receipt_id,
            "status": "applied",
            "mutation_count": 1,
            "registry_id": result["registry_id"],
            "row": result["row"],
            "post_fetch": record["post_fetch"],
            "callers": record["callers"],
            "receipt_id": receipt_id,
            "receipt_sha": record["receipt_sha"],
        }

    @router.post("/workspace/operations/action-item.edit/rollback")
    async def action_item_edit_rollback(
        payload: WorkspaceActionItemEditRollbackRequest,
    ) -> Dict[str, Any]:
        if payload.confirmation != "ROLLBACK":
            raise HTTPException(
                status_code=400, detail="digita ROLLBACK per confermare"
            )
        try:
            rollback_caller = workspace.action_item_edit_caller(
                payload.actor,
                payload.surface,
                payload.caller_type,
                payload.agent_runtime,
                payload.overlay_id,
            )
            identity = workspace.action_item_edit_identity(
                "rollback", {}, payload.idempotency_key
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if identity["operation_id"] != payload.operation_id:
            raise HTTPException(
                status_code=409,
                detail="operation_id non corrisponde alla idempotency_key",
            )
        receipt = await asyncio.to_thread(store.receipt, payload.operation_id)
        if not receipt or receipt["action"] != workspace.ACTION_ITEM_EDIT_OPERATION:
            raise HTTPException(status_code=404, detail="action-item.edit non trovato")
        record = receipt["payload"]
        expected_sha = workspace.receipt_sha(
            {key: value for key, value in record.items() if key != "receipt_sha"}
        )
        if record.get("receipt_sha") != expected_sha:
            raise HTTPException(
                status_code=409, detail="ricevuta action-item.edit non integra"
            )
        if receipt["outcome"] == "rolled-back":
            return {
                "ok": True,
                "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
                "operation_id": payload.operation_id,
                "status": "already-rolled-back",
                "mutation_count": 0,
                "rollback_registry_id": record.get("rollback_registry_id"),
                "receipt_id": payload.operation_id,
                "receipt_sha": record.get("receipt_sha"),
            }
        if receipt["outcome"] != "succeeded":
            raise HTTPException(
                status_code=409,
                detail=f"action-item.edit non ripristinabile in stato {receipt['outcome']}",
            )
        if record.get("idempotency_key") != payload.idempotency_key:
            raise HTTPException(
                status_code=409,
                detail="receipt non appartiene alla idempotency_key richiesta",
            )
        try:
            current = await asyncio.to_thread(workspace.notion_row, record["row_id"])
            current = current["rows"][0]
        except (RuntimeError, ValueError, IndexError) as exc:
            raise HTTPException(
                status_code=409,
                detail="action-item.edit senza post-fetch rollback riconciliabile",
            ) from exc
        before = record.get("before") or {}
        if before and all(
            current["properties"].get(name) == value for name, value in before.items()
        ):
            record.update(
                {
                    "rollback_registry_id": record.get("rollback_registry_id"),
                    "rollback_post_fetch": {
                        "row_id": record["row_id"],
                        "properties": {
                            name: current["properties"].get(name) for name in before
                        },
                    },
                    "reconciled_rollback": "inverse-postimage",
                }
            )
            record["receipt_sha"] = workspace.receipt_sha(
                {key: value for key, value in record.items() if key != "receipt_sha"}
            )
            await asyncio.to_thread(
                store.update_receipt_payload, payload.operation_id, record
            )
            store.close_receipt(
                payload.operation_id,
                "rolled-back",
                "action-item.edit rollback riconciliato dal post-fetch; mutation_count=0",
            )
            return {
                "ok": True,
                "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
                "operation_id": payload.operation_id,
                "status": "already-rolled-back",
                "mutation_count": 0,
                "rollback_registry_id": record.get("rollback_registry_id"),
                "receipt_id": payload.operation_id,
                "receipt_sha": record["receipt_sha"],
            }
        try:
            result = await asyncio.to_thread(workspace.rollback_notion_edit, record)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record.update(
            {
                "rollback_caller": rollback_caller,
                "rollback_registry_id": result["rollback_registry_id"],
                "rollback_post_fetch": result["post_fetch"],
            }
        )
        record["receipt_sha"] = workspace.receipt_sha(
            {key: value for key, value in record.items() if key != "receipt_sha"}
        )
        await asyncio.to_thread(
            store.update_receipt_payload, payload.operation_id, record
        )
        store.close_receipt(
            payload.operation_id,
            "rolled-back",
            f"action-item.edit ripristinato · Registry {result['rollback_registry_id']} · post-fetch verificato",
        )
        return {
            **result,
            "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
            "operation_id": payload.operation_id,
            "receipt_id": payload.operation_id,
            "receipt_sha": record["receipt_sha"],
        }
