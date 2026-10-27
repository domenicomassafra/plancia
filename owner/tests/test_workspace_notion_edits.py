from workspace_test_support import (
    WorkspaceTestBase, ACTIONS_ID, ast, json, os, sys, tempfile,
    unittest, Path, SimpleNamespace, patch, workspace, workspace_sources,
)

class WorkspaceNotionEditTests(WorkspaceTestBase):
    def test_notion_projection_uses_ntn_cli_path_without_leading_slash(self):
        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "properties": {},
        }
        with patch.object(
            workspace,
            "_verify_workspace",
            return_value={"workspace": "Domenico", "workspace_id": "primary-id"},
        ), patch.object(
            workspace,
            "_registry_data_source_id",
            return_value="registry-id",
        ), patch.object(
            workspace.workspace_sources, "_run_ntn", side_effect=[source, {"results": []}]
        ) as run_ntn:
            workspace.notion_database(force=True)

        self.assertEqual(
            [
                (entry.args[0], entry.args[1] if len(entry.args) > 1 else None)
                for entry in run_ntn.call_args_list
            ],
            [
                (f"v1/data_sources/{ACTIONS_ID}", None),
                (
                    f"v1/data_sources/{ACTIONS_ID}/query",
                    {"page_size": workspace.MAX_ROWS},
                ),
            ],
        )

    def test_notion_projection_passes_opaque_cursor(self):
        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "properties": {},
        }
        with patch.object(
            workspace,
            "_verify_workspace",
            return_value={"workspace": "Domenico", "workspace_id": "primary-id"},
        ), patch.object(
            workspace.workspace_sources, "_run_ntn", side_effect=[source, {"results": []}]
        ) as run_ntn:
            workspace.notion_database(cursor="cursor-2")

        self.assertEqual(
            run_ntn.call_args_list[1].args[1],
            {"page_size": workspace.MAX_ROWS, "start_cursor": "cursor-2"},
        )

    def test_missing_owner_role_never_falls_back_to_a_registry_id(self):
        with patch.object(
            workspace.workspace_sources,
            "primary_notion_role",
            side_effect=FileNotFoundError("role file missing"),
        ):
            payload = workspace.notion_database(force=True)

        self.assertFalse(payload["source"]["available"])
        self.assertEqual(payload["registry"]["data_source_id"], "")
        self.assertIn("owner role contract", payload["source"]["reason"])

    def test_notion_projection_fails_closed_before_fetch_when_identity_mismatches(self):
        with patch.object(
            workspace,
            "_registry_data_source_id",
            return_value="registry-id",
        ), patch.object(
            workspace,
            "_verify_workspace",
            side_effect=RuntimeError("non-primary workspace"),
        ), patch.object(workspace.workspace_sources, "_run_ntn") as run_ntn:
            payload = workspace.notion_database(force=True)

        self.assertFalse(payload["source"]["available"])
        self.assertIn("non-primary workspace", payload["source"]["reason"])
        run_ntn.assert_not_called()

    def test_notion_projection_replaces_stale_source_url(self):
        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "url": "https://app.notion.com/p/720230a613cd8322bb6501a04f2eb57a",
            "properties": {},
        }
        with (
            patch.object(
                workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
            ),
            patch.object(
                workspace.workspace_sources, "_run_ntn", side_effect=[source, {"results": []}]
            ),
        ):
            payload = workspace._notion_database(workspace.actions_source())

        self.assertEqual(
            payload["source"]["url"],
            f"https://app.notion.com/p/{ACTIONS_ID.replace('-', '')}",
        )

    def test_notion_row_requires_allowlisted_parent(self):
        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "properties": {
                "Action Item": {"id": "title", "type": "title", "title": {}}
            },
        }
        raw = {
            "id": "row-1",
            "parent": {"data_source_id": "other-source"},
            "properties": {},
        }
        with patch.object(
            workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
        ), patch.object(workspace.workspace_sources, "_run_ntn", side_effect=[source, raw]):
            with self.assertRaisesRegex(ValueError, "allowlisted"):
                workspace.notion_row("row-1")

    def test_notion_row_fails_before_source_fetch_when_identity_mismatches(self):
        with patch.object(
            workspace,
            "_verify_workspace",
            side_effect=RuntimeError("non-primary workspace"),
        ), patch.object(workspace.workspace_sources, "_run_ntn") as run_ntn:
            with self.assertRaisesRegex(RuntimeError, "non-primary workspace"):
                workspace.notion_row("row-1")

        run_ntn.assert_not_called()

    def test_preview_builds_typed_patch_and_refuses_stale_preimage(self):
        database = {
            "fetched_at": 10,
            "schema": [
                {"name": "Action Item", "type": "title", "editable": True},
                {
                    "name": "Status",
                    "type": "status",
                    "editable": True,
                    "options": ["In progress"],
                },
            ],
            "rows": [
                {
                    "id": "row-1",
                    "last_edited_time": "2026-07-26T08:00:00Z",
                    "properties": {
                        "Action Item": "Write brief",
                        "Status": "Not started",
                    },
                }
            ],
        }
        packet = workspace.preview_notion_edit(
            database, "row-1", {"Status": "In progress"}
        )
        self.assertEqual(packet["status"], "dry-run")
        self.assertTrue(packet["apply_allowed"])
        self.assertEqual(
            packet["properties_patch"], {"Status": {"status": {"name": "In progress"}}}
        )
        with self.assertRaises(RuntimeError):
            workspace.preview_notion_edit(database, "row-1", {"Status": "Done"}, "wrong")

    def test_action_item_operation_keeps_fresh_preimage_and_supported_fields_only(self):
        database = {
            "fetched_at": 10,
            "schema": [
                {
                    "name": "Status",
                    "type": "status",
                    "editable": True,
                    "options": ["Done"],
                }
            ],
            "rows": [
                {
                    "id": "row-1",
                    "last_edited_time": "before",
                    "properties": {"Status": "Not started"},
                }
            ],
        }
        kwargs = {
            "actor": "Domenico",
            "surface": "signal-deck",
            "idempotency_key": "action-item-test",
        }
        with patch.object(workspace, "_notion_row", return_value=database):
            with self.assertRaisesRegex(RuntimeError, "preimage Notion obsoleta"):
                workspace.preview_action_item_edit(
                    "row-1", {"Status": "Done"}, preimage_hash="stale", **kwargs
                )
            with self.assertRaisesRegex(ValueError, "proprietà sconosciuta"):
                workspace.preview_action_item_edit("row-1", {"Agent": "unsafe"}, **kwargs)

    def test_notion_apply_creates_registry_then_patches_and_post_fetches(self):
        database = {
            "fetched_at": 10,
            "schema": [
                {
                    "name": "Status",
                    "type": "status",
                    "editable": True,
                    "options": ["Done"],
                }
            ],
            "rows": [
                {
                    "id": "row-1",
                    "last_edited_time": "before",
                    "properties": {"Status": "Not started"},
                }
            ],
        }
        packet = workspace.preview_notion_edit(database, "row-1", {"Status": "Done"})
        post = {
            **database,
            "rows": [
                {
                    **database["rows"][0],
                    "last_edited_time": "after",
                    "properties": {"Status": "Done"},
                }
            ],
        }
        registry = {
            "id": workspace._registry_data_source_id(),
            "title": [{"plain_text": "Registry / Mutation Log"}],
        }
        with (
            patch.object(
                workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
            ),
            patch.object(
                workspace,
                "_notion_row",
                side_effect=[database, post],
            ),
            patch.object(
                workspace.workspace_sources,
                "_run_ntn",
                side_effect=[registry, {"id": "registry-row"}, {}, {}],
            ) as run_ntn,
        ):
            result = workspace.apply_notion_edit(
                "row-1",
                {"Status": "Done"},
                packet["preimage_hash"],
                packet["packet_id"],
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["registry_id"], "registry-row")
        self.assertEqual(run_ntn.call_args_list[2].kwargs["method"], "PATCH")

    def test_notion_apply_never_blindly_rolls_back_a_concurrent_postimage(self):
        database = {
            "fetched_at": 10,
            "schema": [
                {"name": "Status", "type": "status", "editable": True, "options": ["Done"]}
            ],
            "rows": [
                {
                    "id": "row-1",
                    "last_edited_time": "before",
                    "properties": {"Status": "Not started"},
                }
            ],
        }
        packet = workspace.preview_notion_edit(database, "row-1", {"Status": "Done"})
        drifted = {
            **database,
            "rows": [
                {
                    **database["rows"][0],
                    "last_edited_time": "concurrent",
                    "properties": {"Status": "Blocked"},
                }
            ],
        }
        registry = {
            "id": workspace._registry_data_source_id(),
            "title": [{"plain_text": "Registry / Mutation Log"}],
        }
        with (
            patch.object(workspace, "_verify_workspace", return_value={"workspace": "Domenico"}),
            patch.object(workspace, "_notion_row", side_effect=[database, drifted]),
            patch.object(
                workspace.workspace_sources,
                "_run_ntn",
                side_effect=[
                    registry,
                    {"id": "registry-row"},
                    {},
                ],
            ) as run_ntn,
        ):
            with self.assertRaisesRegex(RuntimeError, "post-fetch Notion"):
                workspace.apply_notion_edit(
                    "row-1",
                    {"Status": "Done"},
                    packet["preimage_hash"],
                    packet["packet_id"],
                )
        page_patches = [
            call
            for call in run_ntn.call_args_list
            if call.args and call.args[0] == "v1/pages/row-1"
        ]
        self.assertEqual(len(page_patches), 1)
        self.assertEqual(page_patches[0].args[1], {"properties": packet["properties_patch"]})

    def test_notion_rollback_requires_unchanged_postimage_and_uses_registry_inverse(
        self,
    ):
        database = {
            "fetched_at": 11,
            "schema": [
                {
                    "name": "Status",
                    "type": "status",
                    "editable": True,
                    "options": ["Done"],
                }
            ],
            "rows": [
                {
                    "id": "row-1",
                    "last_edited_time": "after",
                    "properties": {"Status": "Done"},
                }
            ],
        }
        record = {
            "data_source_id": ACTIONS_ID,
            "source_key": "actions",
            "row_id": "row-1",
            "before": {"Status": "Not started"},
            "after": {"Status": "Done"},
        }
        restored = {
            "ok": True,
            "registry_id": "rollback-registry",
            "row": {**database["rows"][0], "properties": {"Status": "Not started"}},
        }
        with (
            patch.object(
                workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
            ),
            patch.object(workspace, "_notion_row", return_value=database),
            patch.object(
                workspace,
                "_apply_notion_edit",
                return_value=restored,
            ) as apply_edit,
        ):
            result = workspace.rollback_notion_edit(record)
        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(result["rollback_registry_id"], "rollback-registry")
        self.assertEqual(result["post_fetch"]["properties"], {"Status": "Not started"})
        self.assertEqual(
            apply_edit.call_args.args[:3],
            (workspace.actions_source(), "row-1", {"Status": "Not started"}),
        )
        self.assertEqual(apply_edit.call_args.kwargs["registry_action"], "rollback")

        drifted = {
            **database,
            "rows": [{**database["rows"][0], "properties": {"Status": "In progress"}}],
        }
        with (
            patch.object(
                workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
            ),
            patch.object(workspace, "_notion_row", return_value=drifted),
            patch.object(
                workspace,
                "_apply_notion_edit",
            ) as apply_edit,
        ):
            with self.assertRaisesRegex(RuntimeError, "cambiata dopo"):
                workspace.rollback_notion_edit(record)
        apply_edit.assert_not_called()

    def test_notion_explicit_rollback_keeps_complete_long_rich_text(self):
        before = "before-" + "a" * 700
        after = "after-" + "b" * 700
        database = {
            "fetched_at": 11,
            "schema": [
                {"name": "Summary", "type": "rich_text", "editable": True}
            ],
            "rows": [
                {
                    "id": "row-long",
                    "last_edited_time": "after",
                    "properties": {"Summary": after},
                }
            ],
        }
        record = {
            "data_source_id": ACTIONS_ID,
            "source_key": "actions",
            "row_id": "row-long",
            "before": {"Summary": before},
            "after": {"Summary": after},
        }
        restored = {
            "ok": True,
            "registry_id": "rollback-long",
            "row": {"id": "row-long", "properties": {"Summary": before}},
        }
        with (
            patch.object(workspace, "_verify_workspace", return_value={"workspace": "Domenico"}),
            patch.object(workspace, "_notion_row", return_value=database),
            patch.object(workspace, "_apply_notion_edit", return_value=restored) as apply_edit,
        ):
            result = workspace.rollback_notion_edit(record)

        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(apply_edit.call_args.args[2]["Summary"], before)
        self.assertEqual(len(apply_edit.call_args.args[2]["Summary"]), len(before))
