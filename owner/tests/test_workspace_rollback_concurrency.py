"""Rollback must admit only one remote writer across server processes."""

import asyncio
import threading
from unittest.mock import patch

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from workspace_api_test_support import workspace
from backend.fleet2.store import Store
from backend.fleet2.workspace_mutation_api import register_workspace_mutation_routes


def test_simultaneous_rollback_only_invokes_one_remote_writer(tmp_path):
    path = tmp_path / "receipts.sqlite"
    stores = [Store(path), Store(path)]  # separate server instances, one durable DB
    apps = []
    for store in stores:
        router = APIRouter(prefix="/api")
        register_workspace_mutation_routes(router, store)
        app = FastAPI()
        app.include_router(router)
        apps.append(app)

    key = "same-rollback-key"
    operation_id = workspace.action_item_edit_identity("row-1", {"Status": "Done"}, key)["operation_id"]
    record = {
        "operation_id": operation_id,
        "idempotency_key": key,
        "row_id": "row-1",
        "before": {"Status": "Not started"},
        "after": {"Status": "Done"},
    }
    record["receipt_sha"] = workspace.receipt_sha(record)
    assert stores[0].claim_receipt(operation_id, "control-plane", workspace.ACTION_ITEM_EDIT_OPERATION, record)
    stores[0].close_receipt(operation_id, "succeeded", "applied")

    entered, release = threading.Event(), threading.Event()
    writer_calls = 0
    writer_lock = threading.Lock()

    def writer(_record):
        nonlocal writer_calls
        with writer_lock:
            writer_calls += 1
            call_number = writer_calls
        if call_number == 1:
            entered.set()
            assert release.wait(5), "first writer was never released"
        return {
            "ok": True, "status": "rolled-back", "mutation_count": 1,
            "rollback_registry_id": "registry-inverse",
            "post_fetch": {"row_id": "row-1", "properties": {"Status": "Not started"}},
        }

    payload = {
        "operation_id": operation_id, "idempotency_key": key,
        "actor": "Domenico", "surface": "signal-deck", "confirmation": "ROLLBACK",
    }

    async def run_requests():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[0]), base_url="http://test") as a,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1]), base_url="http://test") as b,
        ):
            first = asyncio.create_task(a.post("/api/workspace/operations/action-item.edit/rollback", json=payload))
            try:
                assert await asyncio.to_thread(entered.wait, 3), "first writer did not start"
                competing = await b.post("/api/workspace/operations/action-item.edit/rollback", json=payload)
            finally:
                release.set()
            original = await first
            replay = await b.post("/api/workspace/operations/action-item.edit/rollback", json=payload)
            return original, competing, replay

    with (
        patch.object(workspace, "notion_row", return_value={"rows": [{"properties": {"Status": "Done"}}]}),
        patch.object(workspace, "rollback_notion_edit", side_effect=writer),
    ):
        first, concurrent, replay = asyncio.run(run_requests())

    assert first.status_code == 200, first.text
    assert concurrent.status_code == 409, concurrent.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "already-rolled-back"
    assert writer_calls == 1
    assert stores[1].receipt(operation_id)["outcome"] == "rolled-back"


@pytest.mark.parametrize("remote_status", ["Done", "Blocked"])
def test_restart_reconciles_uncertain_rollback_without_repeating_remote_write(tmp_path, remote_status):
    store = Store(tmp_path / "receipts.sqlite")
    key = "uncertain-rollback-key"
    operation_id = workspace.action_item_edit_identity("row-1", {"Status": "Done"}, key)["operation_id"]
    record = {
        "idempotency_key": key, "row_id": "row-1",
        "before": {"Status": "Not started"}, "after": {"Status": "Done"},
    }
    record["receipt_sha"] = workspace.receipt_sha(record)
    assert store.claim_receipt(operation_id, "control-plane", workspace.ACTION_ITEM_EDIT_OPERATION, record)
    store.close_receipt(operation_id, "succeeded", "applied")
    assert store.begin_receipt_rollback(operation_id, workspace.ACTION_ITEM_EDIT_OPERATION)
    assert store.interrupt_abandoned_server_receipts() == 1
    assert store.receipt(operation_id)["outcome"] == "rollback-interrupted"

    router = APIRouter(prefix="/api")
    register_workspace_mutation_routes(router, Store(store.path))
    app = FastAPI()
    app.include_router(router)

    async def retry():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/workspace/operations/action-item.edit/rollback", json={
                "operation_id": operation_id, "idempotency_key": key,
                "actor": "Domenico", "surface": "signal-deck", "confirmation": "ROLLBACK",
            })

    with (
        patch.object(workspace, "notion_row", return_value={"rows": [{"properties": {"Status": remote_status}}]}),
        patch.object(workspace, "rollback_notion_edit") as writer,
    ):
        response = asyncio.run(retry())
    assert response.status_code == 409, response.text
    writer.assert_not_called()
    assert store.receipt(operation_id)["outcome"] == "rollback-interrupted"
