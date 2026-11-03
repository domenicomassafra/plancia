"""An uncertain Notion post-fetch must never prove that a remote PATCH failed."""

from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend.fleet2 import workspace
from backend.fleet2.store import Store
from backend.fleet2.workspace_mutation_api import register_workspace_mutation_routes


@pytest.mark.parametrize("postimage_verified", [True, False])
def test_ambiguous_writer_failure_can_be_reconciled_without_second_patch(tmp_path, postimage_verified):
    store = Store(tmp_path / "notion.sqlite")
    router = APIRouter(prefix="/api")
    register_workspace_mutation_routes(router, store)
    app = FastAPI()
    app.include_router(router)
    key = "notion-uncertain-fixture"
    changes = {"Status": "Done"}
    identity = workspace.action_item_edit_identity("row-fixture", changes, key)
    packet = {
        **identity,
        "row_id": "row-fixture",
        "changes": changes,
        "packet_id": "fixture-packet",
        "preimage_hash": "fixture-preimage",
        "data_source_id": "fixture-data-source",
        "schema_version": "workspace.operation.v1",
        "idempotency_key": key,
        "caller": workspace.action_item_edit_caller("Domenico", "signal-deck"),
        "entity": {"type": "notion.action-item"},
        "destination": {"owner": "Notion"},
        "provenance": {},
        "before": {"Status": "Open"},
        "rollback": {"Status": "Open"},
    }
    payload = {
        "row_id": "row-fixture", "changes": changes, "idempotency_key": key,
        "operation_id": identity["operation_id"], "packet_id": "fixture-packet",
        "preimage_hash": "fixture-preimage", "confirmation": "APPLICA",
        "actor": "Domenico", "surface": "signal-deck", "caller_type": "operator_ui",
        "agent_runtime": "", "overlay_id": None,
    }
    patch_calls = []

    def synthetic_writer(*_args):
        patch_calls.append("remote PATCH accepted")
        raise RuntimeError("synthetic post-fetch transport lost after PATCH")

    def synthetic_readback(_record):
        if not postimage_verified:
            raise RuntimeError("postimage not verifiable")
        return {"row": {"id": "row-fixture", "properties": changes},
                "post_fetch": {"row_id": "row-fixture", "properties": changes}}

    with (
        patch.object(workspace, "preview_action_item_edit", return_value=packet),
        patch.object(workspace, "apply_notion_edit", side_effect=synthetic_writer),
        patch.object(workspace, "verify_action_item_edit_post_fetch", side_effect=synthetic_readback),
    ):
        client = TestClient(app)
        first = client.post("/api/workspace/operations/action-item.edit/apply", json=payload)
        assert first.status_code == 409
        assert store.receipt(identity["operation_id"])["outcome"] == "interrupted"
        replay = client.post("/api/workspace/operations/action-item.edit/apply", json=payload)

    assert len(patch_calls) == 1, "ambiguous outcome must never trigger another PATCH"
    assert replay.status_code == (200 if postimage_verified else 409)
    assert store.receipt(identity["operation_id"])["outcome"] == (
        "succeeded" if postimage_verified else "interrupted"
    )
    if postimage_verified:
        assert replay.json()["status"] == "already-applied"
        assert replay.json()["mutation_count"] == 0
