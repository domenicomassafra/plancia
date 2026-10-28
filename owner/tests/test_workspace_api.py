from workspace_api_test_support import *
import pytest
from backend.fleet2 import workspace_sources


@pytest.fixture(autouse=True)
def synthetic_primary_notion_role(monkeypatch):
    """Operation regression tests never depend on a personal vault role file."""
    monkeypatch.setattr(workspace_sources, "primary_notion_role", lambda: {
        "workspace_id": "primary-id",
        "registry_data_source_id": "registry-id",
        "expected_names": ["Domenico"],
        "profile_env": {"NOTION_PROFILE": "primary"},
        "operational_data_sources": {"actions": ACTIONS_ID},
    })

def test_public_receipt_projection_never_exposes_operation_bodies():
    projected = _public_receipt(
        {
            "id": "receipt-1",
            "host_id": "control-plane",
            "action": "action-item.edit",
            "started_at": 1.0,
            "finished_at": 2.0,
            "outcome": "failed",
            "detail": "preimage cambiata dopo la preview",
            "payload": {
                "operation_id": "op-1",
                "registry_id": "registry-1",
                "before": {"Journal": "private"},
                "after": {"Journal": "still private"},
                "callers": [{"agent_runtime": "hermes"}],
            },
        }
    )
    assert projected["payload"] == {
        "operation_id": "op-1",
        "registry_id": "registry-1",
    }
    assert projected["detail"] == "Conflitto o drift rilevato"
    assert "before" not in projected["payload"]


def test_action_item_operation_rejects_an_unbound_agent_before_mutation():
    """The shared operation bridge must not expose Agents/Projects as writers."""
    env = patch.dict(
        os.environ,
        {
            OWNER_LOGIN_ENV: OWNER,
            ALLOWED_ORIGINS_ENV: ORIGIN,
            "FLEETPROBE_TOKEN": "test-token",
        },
    )
    env.start()
    try:
        from backend import server

        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=ORIGIN)
        identity = {TAILSCALE_LOGIN_HEADER: OWNER}
        assert client.get("/", headers=identity).status_code == 200
        headers = {
            **identity,
            "Origin": ORIGIN,
            CSRF_HEADER: client.cookies.get(CSRF_COOKIE),
        }
        unauthenticated = client.post(
            "/api/workspace/operations/action-item.edit/preview",
            json={"row_id": "row-1", "changes": {"Status": "Done"}},
            headers={"Origin": ORIGIN},
        )
        assert unauthenticated.status_code == 401
        receipts_before = app.state.store.receipts()
        response = client.post(
            "/api/workspace/operations/action-item.edit/preview",
            json={
                "row_id": "row-1",
                "changes": {"Status": "Done"},
                "actor": "ChatGPT",
                "surface": "ordinary-chatgpt-project",
                "caller_type": "agent",
                "agent_runtime": "chatgpt",
                "idempotency_key": "test-action-item-unbound-agent",
            },
            headers=headers,
        )
        assert response.status_code == 400
        assert app.state.store.receipts() == receipts_before
    finally:
        env.stop()


def test_action_item_operation_rejects_empty_and_semantic_noop_patches_before_receipt():
    env = patch.dict(
        os.environ,
        {
            OWNER_LOGIN_ENV: OWNER,
            ALLOWED_ORIGINS_ENV: ORIGIN,
            "FLEETPROBE_TOKEN": "test-token",
        },
    )
    env.start()
    try:
        from backend import server

        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=ORIGIN)
        identity_headers = {TAILSCALE_LOGIN_HEADER: OWNER}
        assert client.get("/", headers=identity_headers).status_code == 200
        headers = {
            **identity_headers,
            "Origin": ORIGIN,
            CSRF_HEADER: client.cookies.get(CSRF_COOKIE),
        }
        request = {
            "row_id": "row-noop",
            "changes": {"Status": "Not started"},
            "preimage_hash": "",
            "actor": "Domenico",
            "surface": "signal-deck",
            "caller_type": "operator_ui",
            "agent_runtime": "",
            "overlay_id": None,
            "idempotency_key": f"action-noop-{uuid.uuid4().hex}",
        }
        operation = workspace.action_item_edit_identity(
            request["row_id"], request["changes"], request["idempotency_key"]
        )
        database = {
            "schema": [
                {
                    "name": "Status",
                    "type": "status",
                    "editable": True,
                    "options": ["Not started", "Done"],
                }
            ],
            "rows": [
                {
                    "id": request["row_id"],
                    "last_edited_time": None,
                    "properties": {"Status": "Not started"},
                }
            ],
        }
        receipts_before = app.state.store.receipts()
        with (
            patch.object(workspace, "_notion_row", return_value=database),
            patch.object(workspace, "apply_notion_edit") as writer,
        ):
            empty = client.post(
                "/api/workspace/operations/action-item.edit/preview",
                json={**request, "changes": {}},
                headers=headers,
            )
            semantic_noop = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json={
                    **request,
                    "operation_id": operation["operation_id"],
                    "packet_id": "not-used-for-noop",
                    "confirmation": "APPLICA",
                },
                headers=headers,
            )
        assert empty.status_code == 422
        assert semantic_noop.status_code == 400
        assert app.state.store.receipts() == receipts_before
        writer.assert_not_called()
    finally:
        env.stop()


def test_action_item_operation_replays_and_rolls_back_once():
    # Keep the test bound to its synthetic Actions fixture, even on a host
    # with a real private Notion primary-role file installed.
    source = workspace.NotionSource(
        workspace.ACTIONS_SOURCE_KEY,
        ACTIONS_ID,
        workspace.ACTIONS_SOURCE_TITLE,
        workspace.ACTIONS_SOURCE_TITLE_PROPERTY,
        workspace.ACTIONS_SOURCE_EDIT_SURFACE,
    )
    source_patch = patch.object(workspace, "actions_source", return_value=source)
    source_patch.start()
    env = patch.dict(
        os.environ,
        {
            OWNER_LOGIN_ENV: OWNER,
            ALLOWED_ORIGINS_ENV: ORIGIN,
            "FLEETPROBE_TOKEN": "test-token",
        },
    )
    env.start()
    try:
        from backend import server

        app = server.create_app()
        app.state.session_store.clear()
        client = TestClient(app, base_url=ORIGIN)
        identity_headers = {TAILSCALE_LOGIN_HEADER: OWNER}
        assert client.get("/", headers=identity_headers).status_code == 200
        headers = {
            **identity_headers,
            "Origin": ORIGIN,
            CSRF_HEADER: client.cookies.get(CSRF_COOKIE),
        }
        request = {
            "row_id": "row-1",
            "changes": {"Status": "Done"},
            "preimage_hash": "fresh",
            "actor": "Domenico",
            "surface": "signal-deck",
            "caller_type": "operator_ui",
            "agent_runtime": "",
            "overlay_id": None,
            "idempotency_key": f"action-item-{uuid.uuid4().hex}",
        }
        operation = workspace.action_item_edit_identity(
            request["row_id"], request["changes"], request["idempotency_key"]
        )
        caller = workspace.action_item_edit_caller("Domenico", "signal-deck")
        packet = {
            "packet_id": "action-packet",
            "row_id": "row-1",
            "data_source_id": ACTIONS_ID,
            "preimage_hash": "fresh",
            "before": {"Status": "Not started"},
            "changes": {"Status": "Done"},
            "rollback": {"Status": "Not started"},
            "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
            "operation_id": operation["operation_id"],
            "caller": caller,
            "entity": {
                "type": "notion.action-item",
                "owner": "Domenico",
                "row_id": "row-1",
            },
            "destination": {
                "owner": "Notion",
                "workspace": "Domenico",
                "data_source_id": ACTIONS_ID,
            },
            "schema_version": "workspace.operation.v1",
            "idempotency_key": request["idempotency_key"],
            "request_digest": operation["request_digest"],
            "provenance": {},
        }
        apply_request = {
            **request,
            "operation_id": operation["operation_id"],
            "packet_id": "action-packet",
            "confirmation": "APPLICA",
        }
        row = {"id": "row-1", "properties": {"Status": "Done"}}
        with (
            patch.object(workspace, "preview_action_item_edit", return_value=packet),
            patch.object(
                workspace,
                "apply_notion_edit",
                return_value={"ok": True, "registry_id": "registry-1", "row": row},
            ) as writer,
        ):
            applied = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert applied.status_code == 200, applied.text
        assert applied.json()["mutation_count"] == 1
        with (
            patch.object(
                workspace,
                "verify_action_item_edit_post_fetch",
                return_value={
                    "row": row,
                    "post_fetch": {
                        "row_id": "row-1", "properties": {"Status": "Done"}
                    },
                },
            ),
            patch.object(workspace, "apply_notion_edit") as writer,
        ):
            replay = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "already-applied"
        assert replay.json()["mutation_count"] == 0
        writer.assert_not_called()
        changed = client.post(
            "/api/workspace/operations/action-item.edit/apply",
            json={**apply_request, "changes": {"Status": "Blocked"}},
            headers=headers,
        )
        assert changed.status_code == 409
        receipt = app.state.store.receipt(operation["operation_id"])
        # The durable receipt must carry the exact binding the packet was read
        # from, never a static module constant that a rebind could supersede.
        assert receipt["payload"]["data_source_id"] == packet["data_source_id"]
        app.state.store.update_receipt_payload(
            operation["operation_id"],
            {**receipt["payload"], "registry_id": "tampered"},
        )
        with patch.object(workspace, "apply_notion_edit") as tampered_replay_writer:
            tampered_replay = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert tampered_replay.status_code == 409
        tampered_replay_writer.assert_not_called()
        app.state.store.update_receipt_payload(
            operation["operation_id"], receipt["payload"]
        )
        rollback_database = {
            "schema": [
                {
                    "name": "Status",
                    "type": "status",
                    "editable": True,
                    "options": ["Done"],
                }
            ],
            "rows": [row],
        }
        restored = {
            "ok": True,
            "registry_id": "rollback-1",
            "row": {"id": "row-1", "properties": {"Status": "Not started"}},
        }
        with (
            patch.object(workspace, "_verify_workspace"),
            patch.object(workspace, "_notion_row", return_value=rollback_database),
            patch.object(
                workspace, "_apply_notion_edit", return_value=restored
            ) as rollback_writer,
        ):
            rolled_back = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json={
                    "idempotency_key": request["idempotency_key"],
                    "actor": request["actor"],
                    "surface": request["surface"],
                    "caller_type": request["caller_type"],
                    "agent_runtime": request["agent_runtime"],
                    "overlay_id": request["overlay_id"],
                    "operation_id": operation["operation_id"],
                    "confirmation": "ROLLBACK",
                },
                headers=headers,
            )
            replayed_rollback = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json={
                    "idempotency_key": request["idempotency_key"],
                    "actor": request["actor"],
                    "surface": request["surface"],
                    "caller_type": request["caller_type"],
                    "agent_runtime": request["agent_runtime"],
                    "overlay_id": request["overlay_id"],
                    "operation_id": operation["operation_id"],
                    "confirmation": "ROLLBACK",
                },
                headers=headers,
            )
        assert rolled_back.status_code == 200, rolled_back.text
        assert replayed_rollback.json()["status"] == "already-rolled-back"
        rollback_writer.assert_called_once()
        with patch.object(workspace, "apply_notion_edit") as replay_after_rollback_writer:
            replay_after_rollback = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert replay_after_rollback.status_code == 200, replay_after_rollback.text
        assert replay_after_rollback.json()["status"] == "already-rolled-back"
        assert replay_after_rollback.json()["mutation_count"] == 0
        assert "row" not in replay_after_rollback.json()
        replay_after_rollback_writer.assert_not_called()
        receipt = app.state.store.receipt(operation["operation_id"])
        app.state.store.update_receipt_payload(
            operation["operation_id"],
            {**receipt["payload"], "rollback_registry_id": "tampered"},
        )
        with patch.object(workspace, "_apply_notion_edit") as tampered_writer:
            tampered = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json={
                    "idempotency_key": request["idempotency_key"],
                    "actor": request["actor"],
                    "surface": request["surface"],
                    "caller_type": request["caller_type"],
                    "agent_runtime": request["agent_runtime"],
                    "overlay_id": request["overlay_id"],
                    "operation_id": operation["operation_id"],
                    "confirmation": "ROLLBACK",
                },
                headers=headers,
            )
        assert tampered.status_code == 409
        tampered_writer.assert_not_called()
    finally:
        env.stop()
        source_patch.stop()
