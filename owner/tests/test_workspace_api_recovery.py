from workspace_api_test_support import *

def test_action_item_operation_reconciles_crashes_after_writer_mutation():
    class SimulatedCrash(BaseException):
        pass

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
            "row_id": "row-crash",
            "changes": {"Status": "Done"},
            "preimage_hash": "fresh",
            "actor": "Domenico",
            "surface": "signal-deck",
            "caller_type": "operator_ui",
            "agent_runtime": "",
            "overlay_id": None,
            "idempotency_key": f"action-crash-{uuid.uuid4().hex}",
        }
        operation = workspace.action_item_edit_identity(
            request["row_id"], request["changes"], request["idempotency_key"]
        )
        caller = workspace.action_item_edit_caller("Domenico", "signal-deck")
        packet = {
            "packet_id": "crash-packet",
            "row_id": request["row_id"],
            "data_source_id": ACTIONS_ID,
            "preimage_hash": request["preimage_hash"],
            "before": {"Status": "Not started"},
            "changes": request["changes"],
            "rollback": {"Status": "Not started"},
            "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
            "operation_id": operation["operation_id"],
            "caller": caller,
            "entity": {
                "type": "notion.action-item",
                "owner": "Domenico",
                "row_id": request["row_id"],
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
            "packet_id": packet["packet_id"],
            "confirmation": "APPLICA",
        }
        applied_row = {"id": request["row_id"], "properties": {"Status": "Done"}}
        with (
            patch.object(workspace, "preview_action_item_edit", return_value=packet),
            patch.object(
                workspace,
                "apply_notion_edit",
                return_value={
                    "ok": True,
                    "registry_id": "registry-crash",
                    "row": applied_row,
                },
            ) as writer,
            patch.object(
                app.state.store, "update_receipt_payload", side_effect=SimulatedCrash
            ),
        ):
            try:
                client.post(
                    "/api/workspace/operations/action-item.edit/apply",
                    json=apply_request,
                    headers=headers,
                )
            except BaseException:
                pass
            else:
                raise AssertionError("simulated crash did not escape the writer window")
        writer.assert_called_once()
        assert (
            app.state.store.receipt(operation["operation_id"])["outcome"] == "running"
        )
        with (
            patch.object(
                workspace,
                "verify_action_item_edit_post_fetch",
                return_value={
                    "row": applied_row,
                    "post_fetch": {
                        "row_id": request["row_id"],
                        "properties": {"Status": "Done"},
                    },
                },
            ),
            patch.object(workspace, "notion_row") as second_forward_fetch,
            patch.object(workspace, "apply_notion_edit") as replay_writer,
        ):
            replay = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "already-applied"
        assert replay.json()["mutation_count"] == 0
        replay_writer.assert_not_called()
        second_forward_fetch.assert_not_called()

        inverse = {
            "ok": True,
            "status": "rolled-back",
            "mutation_count": 1,
            "rollback_registry_id": "inverse-registry",
            "post_fetch": {
                "row_id": request["row_id"],
                "properties": {"Status": "Not started"},
            },
        }
        rollback_request = {
            "idempotency_key": request["idempotency_key"],
            "actor": request["actor"],
            "surface": request["surface"],
            "caller_type": request["caller_type"],
            "agent_runtime": request["agent_runtime"],
            "overlay_id": request["overlay_id"],
            "operation_id": operation["operation_id"],
            "confirmation": "ROLLBACK",
        }
        with (
            patch.object(workspace, "notion_row", return_value={"rows": [applied_row]}),
            patch.object(
                workspace, "rollback_notion_edit", return_value=inverse
            ) as inverse_writer,
            patch.object(
                app.state.store, "update_receipt_payload", side_effect=SimulatedCrash
            ),
        ):
            try:
                client.post(
                    "/api/workspace/operations/action-item.edit/rollback",
                    json=rollback_request,
                    headers=headers,
                )
            except BaseException:
                pass
            else:
                raise AssertionError(
                    "simulated inverse crash did not escape the writer window"
                )
        inverse_writer.assert_called_once()
        assert (
            app.state.store.receipt(operation["operation_id"])["outcome"] == "succeeded"
        )
        inverse_row = {"id": request["row_id"], "properties": {"Status": "Not started"}}
        with (
            patch.object(workspace, "notion_row", return_value={"rows": [inverse_row]}),
            patch.object(workspace, "rollback_notion_edit") as reconciled_inverse_writer,
        ):
            reconciled_inverse = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json=rollback_request,
                headers=headers,
            )
        assert reconciled_inverse.status_code == 200, reconciled_inverse.text
        assert reconciled_inverse.json()["status"] == "already-rolled-back"
        assert reconciled_inverse.json()["mutation_count"] == 0
        reconciled_inverse_writer.assert_not_called()
    finally:
        env.stop()


def test_action_item_operation_reconciles_after_payload_persistence_before_close():
    class SimulatedCrash(BaseException):
        pass

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
            "row_id": "row-persisted-crash",
            "changes": {"Status": "Done"},
            "preimage_hash": "fresh",
            "actor": "Domenico",
            "surface": "signal-deck",
            "caller_type": "operator_ui",
            "agent_runtime": "",
            "overlay_id": None,
            "idempotency_key": f"action-persisted-crash-{uuid.uuid4().hex}",
        }
        operation = workspace.action_item_edit_identity(
            request["row_id"], request["changes"], request["idempotency_key"]
        )
        caller = workspace.action_item_edit_caller("Domenico", "signal-deck")
        packet = {
            "packet_id": "persisted-crash-packet",
            "row_id": request["row_id"],
            "data_source_id": ACTIONS_ID,
            "preimage_hash": request["preimage_hash"],
            "before": {"Status": "Not started"},
            "changes": request["changes"],
            "rollback": {"Status": "Not started"},
            "operation_type": workspace.ACTION_ITEM_EDIT_OPERATION,
            "operation_id": operation["operation_id"],
            "caller": caller,
            "entity": {
                "type": "notion.action-item",
                "owner": "Domenico",
                "row_id": request["row_id"],
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
            "packet_id": packet["packet_id"],
            "confirmation": "APPLICA",
        }
        applied_row = {
            "id": request["row_id"],
            "title": "Crash-safe action",
            "url": None,
            "last_edited_time": None,
            "properties": {"Status": "Done"},
            "property_types": {"Status": "status"},
        }
        original_update = app.state.store.update_receipt_payload

        def persist_then_crash(receipt_id, record):
            original_update(receipt_id, record)
            raise SimulatedCrash

        with (
            patch.object(workspace, "preview_action_item_edit", return_value=packet),
            patch.object(
                workspace,
                "apply_notion_edit",
                return_value={
                    "ok": True,
                    "registry_id": "registry-persisted-crash",
                    "row": applied_row,
                },
            ) as forward_writer,
            patch.object(
                app.state.store,
                "update_receipt_payload",
                side_effect=persist_then_crash,
            ),
        ):
            try:
                client.post(
                    "/api/workspace/operations/action-item.edit/apply",
                    json=apply_request,
                    headers=headers,
                )
            except BaseException:
                pass
            else:
                raise AssertionError("simulated forward close crash did not escape")
        forward_writer.assert_called_once()
        receipt = app.state.store.receipt(operation["operation_id"])
        assert receipt["outcome"] == "running"
        assert receipt["payload"]["registry_id"] == "registry-persisted-crash"
        assert receipt["payload"]["receipt_sha"] == workspace.receipt_sha(
            {
                key: value
                for key, value in receipt["payload"].items()
                if key != "receipt_sha"
            }
        )

        app.state.store.update_receipt_payload(
            operation["operation_id"],
            {**receipt["payload"], "registry_id": "tampered"},
        )
        with patch.object(workspace, "apply_notion_edit") as tampered_forward_writer:
            tampered_forward = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert tampered_forward.status_code == 409
        tampered_forward_writer.assert_not_called()
        app.state.store.update_receipt_payload(
            operation["operation_id"], receipt["payload"]
        )

        verified_forward = {
            "row": applied_row,
            "post_fetch": {
                "row_id": request["row_id"],
                "properties": {"Status": "Done"},
            },
        }
        with (
            patch.object(
                workspace,
                "verify_action_item_edit_post_fetch",
                return_value=verified_forward,
            ),
            patch.object(workspace, "notion_row") as duplicate_forward_fetch,
            patch.object(workspace, "apply_notion_edit") as replay_writer,
        ):
            replay = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
            second_replay = client.post(
                "/api/workspace/operations/action-item.edit/apply",
                json=apply_request,
                headers=headers,
            )
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "already-applied"
        assert replay.json()["registry_id"] == "registry-persisted-crash"
        assert second_replay.status_code == 200, second_replay.text
        assert second_replay.json()["status"] == "already-applied"
        replay_writer.assert_not_called()
        duplicate_forward_fetch.assert_not_called()
        receipt = app.state.store.receipt(operation["operation_id"])
        assert receipt["outcome"] == "succeeded"
        assert receipt["payload"]["registry_id"] == "registry-persisted-crash"
        assert receipt["payload"]["receipt_sha"] == workspace.receipt_sha(
            {
                key: value
                for key, value in receipt["payload"].items()
                if key != "receipt_sha"
            }
        )

        inverse = {
            "ok": True,
            "status": "rolled-back",
            "mutation_count": 1,
            "rollback_registry_id": "rollback-registry-persisted-crash",
            "post_fetch": {
                "row_id": request["row_id"],
                "properties": {"Status": "Not started"},
            },
        }
        rollback_request = {
            "idempotency_key": request["idempotency_key"],
            "actor": request["actor"],
            "surface": request["surface"],
            "caller_type": request["caller_type"],
            "agent_runtime": request["agent_runtime"],
            "overlay_id": request["overlay_id"],
            "operation_id": operation["operation_id"],
            "confirmation": "ROLLBACK",
        }
        with (
            patch.object(workspace, "notion_row", return_value={"rows": [applied_row]}),
            patch.object(
                workspace, "rollback_notion_edit", return_value=inverse
            ) as inverse_writer,
            patch.object(
                app.state.store,
                "update_receipt_payload",
                side_effect=persist_then_crash,
            ),
        ):
            try:
                client.post(
                    "/api/workspace/operations/action-item.edit/rollback",
                    json=rollback_request,
                    headers=headers,
                )
            except BaseException:
                pass
            else:
                raise AssertionError("simulated inverse close crash did not escape")
        inverse_writer.assert_called_once()
        receipt = app.state.store.receipt(operation["operation_id"])
        assert receipt["outcome"] == "succeeded"
        assert (
            receipt["payload"]["rollback_registry_id"]
            == "rollback-registry-persisted-crash"
        )
        assert receipt["payload"]["receipt_sha"] == workspace.receipt_sha(
            {
                key: value
                for key, value in receipt["payload"].items()
                if key != "receipt_sha"
            }
        )

        app.state.store.update_receipt_payload(
            operation["operation_id"],
            {**receipt["payload"], "rollback_registry_id": "tampered"},
        )
        with patch.object(workspace, "rollback_notion_edit") as tampered_inverse_writer:
            tampered_inverse = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json=rollback_request,
                headers=headers,
            )
        assert tampered_inverse.status_code == 409
        tampered_inverse_writer.assert_not_called()
        app.state.store.update_receipt_payload(
            operation["operation_id"], receipt["payload"]
        )

        inverse_row = {**applied_row, "properties": {"Status": "Not started"}}
        with (
            patch.object(workspace, "notion_row", return_value={"rows": [inverse_row]}) as inverse_fetch,
            patch.object(workspace, "rollback_notion_edit") as reconciled_inverse_writer,
        ):
            reconciled_inverse = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json=rollback_request,
                headers=headers,
            )
            second_reconciled_inverse = client.post(
                "/api/workspace/operations/action-item.edit/rollback",
                json=rollback_request,
                headers=headers,
            )
        assert reconciled_inverse.status_code == 200, reconciled_inverse.text
        assert reconciled_inverse.json()["status"] == "already-rolled-back"
        assert (
            reconciled_inverse.json()["rollback_registry_id"]
            == "rollback-registry-persisted-crash"
        )
        assert second_reconciled_inverse.status_code == 200, second_reconciled_inverse.text
        assert second_reconciled_inverse.json()["status"] == "already-rolled-back"
        reconciled_inverse_writer.assert_not_called()
        assert inverse_fetch.call_count == 1
        receipt = app.state.store.receipt(operation["operation_id"])
        assert receipt["outcome"] == "rolled-back"
        assert (
            receipt["payload"]["rollback_registry_id"]
            == "rollback-registry-persisted-crash"
        )
        assert receipt["payload"]["receipt_sha"] == workspace.receipt_sha(
            {
                key: value
                for key, value in receipt["payload"].items()
                if key != "receipt_sha"
            }
        )
    finally:
        env.stop()
