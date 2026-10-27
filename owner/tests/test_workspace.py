from workspace_test_support import (
    WorkspaceTestBase, ACTIONS_ID, ast, json, os, sys, tempfile,
    unittest, Path, SimpleNamespace, patch, workspace, workspace_sources,
)

class WorkspaceAdapterTests(WorkspaceTestBase):
    def test_workspace_snapshot_contains_only_active_notion_and_obsidian_sources(self):
        notion = {"source": {"available": True}, "rows": []}
        obsidian = {"source": {"available": True}, "results": []}
        with (
            patch.object(workspace, "notion_database", return_value=notion),
            patch.object(workspace, "obsidian_explorer", return_value=obsidian),
        ):
            snapshot = workspace.workspace_snapshot(force=True)
        self.assertEqual(snapshot["notion"], notion)
        self.assertEqual(snapshot["obsidian"], obsidian)
        self.assertEqual(set(snapshot), {"generated_at", "notion", "obsidian"})






    def test_ntn_get_omits_body_flag(self):
        with (
            tempfile.NamedTemporaryFile() as cli,
            patch.object(workspace_sources, "NOTION_CLI", Path(cli.name)),
            patch.object(
                workspace_sources.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="{}", stderr=""),
            ) as run,
        ):
            workspace_sources._run_ntn("v1/data_sources/row")

        self.assertEqual(
            run.call_args.args[0], [cli.name, "api", "v1/data_sources/row"]
        )

    def test_ntn_identity_parses_workspace_without_credentials(self):
        output = "bot-id\tNotion CLI\tbot\towner@example.test\tworkspace-id\tDomenico\towner-id\tPersonal\n"
        with (
            tempfile.NamedTemporaryFile() as cli,
            patch.object(workspace_sources, "NOTION_CLI", Path(cli.name)),
            patch.object(
                workspace_sources.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=output, stderr=""),
            ) as run,
        ):
            identity = workspace_sources._run_ntn_identity()

        self.assertEqual(identity["workspace"], "Domenico")
        self.assertEqual(identity["plan"], "Personal")
        self.assertEqual(run.call_args.args[0], [cli.name, "whoami"])

    def test_verified_primary_notion_identity_rejects_workspace_mismatch(self):
        with patch.object(
            workspace_sources,
            "_run_ntn_identity",
            return_value={"workspace_id": "other-id", "workspace": "Domenico"},
        ):
            with self.assertRaisesRegex(RuntimeError, "non-primary workspace"):
                workspace_sources.verified_primary_notion_identity()

    def test_primary_notion_environment_ignores_inherited_direct_tokens(self):
        role = {
            **self.PRIMARY_ROLE,
            "profile_env": {"NOTION_PROFILE": "primary"},
        }
        with patch.dict(
            os.environ,
            {
                "NOTION_API_TOKEN": "stale-token",
                "NOTION_TOKEN": "stale-token",
                "NOTION_API_KEY": "stale-token",
            },
            clear=False,
        ), patch.object(workspace_sources, "primary_notion_role", return_value=role):
            env = workspace_sources._primary_notion_env()

        self.assertNotIn("NOTION_API_TOKEN", env)
        self.assertNotIn("NOTION_TOKEN", env)
        self.assertNotIn("NOTION_API_KEY", env)
        self.assertEqual(env["NOTION_PROFILE"], "primary")

    def test_notion_projection_normalizes_typed_schema_and_rows(self):
        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "properties": {
                "Action Item": {"id": "title", "type": "title", "title": {}},
                "Status": {
                    "id": "status",
                    "type": "status",
                    "status": {"options": [{"name": "Done"}]},
                },
                "Do Date": {"id": "date", "type": "date", "date": {}},
                "Related": {"id": "relation", "type": "relation", "relation": {}},
            },
        }
        with patch.object(
            workspace,
            "_verify_workspace",
            return_value={"workspace": "Domenico", "workspace_id": "primary-id"},
        ), patch.object(
            workspace.workspace_sources, "_run_ntn",
            side_effect=[
                source,
                {
                    "results": [
                        {
                            "id": "row-1",
                            "url": "https://notion.test/row-1",
                            "last_edited_time": "2026-07-26T08:00:00Z",
                            "properties": {
                                "Action Item": {
                                    "type": "title",
                                    "title": [{"plain_text": "Write brief"}],
                                },
                                "Status": {
                                    "type": "status",
                                    "status": {"name": "In progress"},
                                },
                                "Do Date": {
                                    "type": "date",
                                    "date": {"start": "2026-07-26"},
                                },
                                "Related": {
                                    "type": "relation",
                                    "relation": [{"id": "hidden"}],
                                },
                            },
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            ],
        ):
            payload = workspace.notion_database(force=True)

        self.assertEqual(payload["source"]["state"], "healthy")
        self.assertEqual(payload["source"]["workspace"], "Domenico")
        self.assertEqual(payload["rows"][0]["title"], "Write brief")
        self.assertEqual(payload["rows"][0]["properties"]["Related"], {"count": 1})
        self.assertFalse(
            next(column for column in payload["schema"] if column["name"] == "Related")[
                "editable"
            ]
        )

    def test_notion_body_fields_are_full_length_but_fail_closed_for_generic_edits(self):
        body = "A" * 420 + "B" * 260
        source = {
            "properties": {
                "Body": {
                    "id": "body",
                    "type": "rich_text",
                    "rich_text": {},
                },
                "Description": {
                    "id": "description",
                    "type": "rich_text",
                    "rich_text": {},
                },
                "Status": {"id": "status", "type": "status", "status": {}},
            }
        }
        schema = workspace._schema(source)
        row = workspace._row(
            {
                "id": "row-body",
                "last_edited_time": "before",
                "properties": {
                    "Body": {
                        "type": "rich_text",
                        "rich_text": [
                            {"plain_text": body[:420]},
                            {"plain_text": body[420:]},
                        ],
                    },
                    "Description": {
                        "type": "rich_text",
                        "rich_text": [
                            {"plain_text": body[:420]},
                            {"plain_text": body[420:]},
                        ],
                    },
                    "Status": {"type": "status", "status": {"name": "Open"}},
                },
            },
            schema,
            truncate_strings=False,
        )
        database = {"schema": schema, "rows": [row], "fetched_at": 1}

        for name in ("Body", "Description"):
            with self.subTest(name=name):
                self.assertEqual(row["properties"][name], body)
                self.assertEqual(len(row["properties"][name]), 680)
                self.assertFalse(next(c for c in schema if c["name"] == name)["editable"])
                with self.assertRaisesRegex(ValueError, "non modificabile"):
                    workspace.preview_notion_edit(
                        database, "row-body", {name: "replacement"}
                    )
                with self.assertRaisesRegex(ValueError, "non modificabile"):
                    workspace._typed_change(
                        {"name": name, "type": "rich_text", "editable": True},
                        "replacement",
                    )

    def test_notion_apply_rejects_body_and_description_before_registry_or_write(self):
        source = {
            "properties": {
                "Body": {"id": "body", "type": "rich_text", "rich_text": {}},
                "Description": {
                    "id": "description",
                    "type": "rich_text",
                    "rich_text": {},
                },
            }
        }
        schema = workspace._schema(source)
        database = {
            "fetched_at": 1,
            "schema": schema,
            "rows": [
                {
                    "id": "row-body",
                    "last_edited_time": "before",
                    "properties": {"Body": "owned body", "Description": "owned description"},
                }
            ],
        }

        with patch.object(
            workspace,
            "_verify_workspace",
            return_value={"workspace": "Domenico", "workspace_id": "primary-id"},
        ), patch.object(
            workspace,
            "_notion_row",
            return_value=database,
        ), patch.object(workspace.workspace_sources, "_run_ntn") as run_ntn:
            for name in ("Body", "Description"):
                with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, "non modificabile"
                ):
                    workspace._apply_notion_edit(
                        workspace.actions_source(),
                        "row-body",
                        {name: "replacement"},
                        "",
                        "unreachable-packet",
                    )

        run_ntn.assert_not_called()

    def test_full_rich_text_preimage_hash_detects_suffix_only_conflict(self):
        schema = [
            {"name": "Summary", "type": "rich_text", "editable": True},
            {"name": "Status", "type": "status", "editable": True, "options": ["Done"]},
        ]
        prefix = "x" * 500
        first = {
            "fetched_at": 1,
            "schema": schema,
            "rows": [
                {
                    "id": "row-1",
                    "last_edited_time": "same",
                    "properties": {"Summary": prefix + "A", "Status": "Open"},
                }
            ],
        }
        second = {
            **first,
            "rows": [
                {
                    **first["rows"][0],
                    "properties": {"Summary": prefix + "B", "Status": "Open"},
                }
            ],
        }
        packet = workspace.preview_notion_edit(first, "row-1", {"Status": "Done"})

        with self.assertRaisesRegex(RuntimeError, "preimage Notion obsoleta"):
            workspace.preview_notion_edit(
                second,
                "row-1",
                {"Status": "Done"},
                packet["preimage_hash"],
            )

    def test_notion_database_preimage_digest_covers_values_past_the_display_truncation(self):
        """The CAS preimage must not inherit `_row`'s 500-char display truncation.

        Regression for the suffix-only drift: a rich_text value that changes
        only past character 500 with an unchanged `last_edited_time` must still
        be detected as a stale preimage. The previous shape derived the digest
        from the truncated list projection, so both states hashed identically
        and the stale packet was accepted.
        """
        schema = [
            {"name": "Action Item", "type": "title", "editable": True},
            {"name": "Summary", "type": "rich_text", "editable": True},
            {"name": "Status", "type": "status", "editable": True, "options": ["Done"]},
        ]
        prefix = "x" * 500

        def raw(suffix: str) -> dict:
            return {
                "id": "row-1",
                "last_edited_time": "same",
                "properties": {
                    "Action Item": {
                        "type": "title",
                        "title": [{"plain_text": "Write brief"}],
                    },
                    "Summary": {
                        "type": "rich_text",
                        "rich_text": [{"plain_text": prefix + suffix}],
                    },
                    "Status": {"type": "status", "status": {"name": "Open"}},
                },
            }

        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "properties": {
                "Action Item": {"id": "title", "type": "title", "title": {}},
                "Summary": {"id": "summary", "type": "rich_text", "rich_text": {}},
                "Status": {"id": "status", "type": "status", "status": {}},
            },
        }

        def database(item: dict) -> dict:
            columns = workspace._schema(source)
            rows = [workspace._row(item, columns)]
            return {
                "fetched_at": 1,
                "schema": columns,
                "rows": rows,
                "preimage_by_row": {
                    str(item["id"]): workspace._preimage_digest(item, columns)
                },
            }

        first, second = database(raw("A")), database(raw("B"))
        # The display projection is still bounded, so the two rows look equal.
        self.assertEqual(
            first["rows"][0]["properties"]["Summary"],
            second["rows"][0]["properties"]["Summary"],
        )
        self.assertEqual(len(first["rows"][0]["properties"]["Summary"]), 500)
        # The preimage digests must not be, otherwise the CAS is unsound.
        self.assertNotEqual(
            first["preimage_by_row"]["row-1"],
            second["preimage_by_row"]["row-1"],
        )

        packet = workspace.preview_notion_edit(first, "row-1", {"Status": "Done"})
        with self.assertRaisesRegex(RuntimeError, "preimage Notion obsoleta"):
            workspace.preview_notion_edit(
                second,
                "row-1",
                {"Status": "Done"},
                packet["preimage_hash"],
            )

    def test_notion_database_publishes_a_full_value_digest_for_external_callers(self):
        """The list projection must advertise a digest that cannot collide.

        Reachable surface: a client reads a row digest from the workspace
        listing and echoes it back as the CAS `preimage_hash`. Deriving that
        digest from the 500-char display projection let a suffix-only change
        with an unchanged `last_edited_time` collide, so the row could be
        overwritten over a stale preimage. The digest must cover the whole
        value while the display stays truncated.
        """
        source = {
            "id": ACTIONS_ID,
            "title": [{"plain_text": "Actions"}],
            "properties": {
                "Action Item": {"id": "title", "type": "title", "title": {}},
                "Summary": {"id": "summary", "type": "rich_text", "rich_text": {}},
            },
        }
        prefix = "x" * 500

        def listing(suffix: str) -> dict:
            return {
                "results": [
                    {
                        "id": "row-1",
                        "last_edited_time": "same",
                        "properties": {
                            "Action Item": {
                                "type": "title",
                                "title": [{"plain_text": "Write brief"}],
                            },
                            "Summary": {
                                "type": "rich_text",
                                "rich_text": [{"plain_text": prefix + suffix}],
                            },
                        },
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            }

        def snapshot(suffix: str) -> dict:
            with patch.object(
                workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
            ), patch.object(
                workspace.workspace_sources,
                "_run_ntn",
                side_effect=[source, listing(suffix)],
            ):
                return workspace._notion_database(workspace.actions_source())

        first, second = snapshot("A"), snapshot("B")
        self.assertEqual(
            first["rows"][0]["properties"]["Summary"],
            second["rows"][0]["properties"]["Summary"],
        )
        self.assertNotEqual(
            first["preimage_by_row"]["row-1"], second["preimage_by_row"]["row-1"]
        )

    def test_long_rich_text_rollback_payload_preserves_complete_preimage(self):
        before = "before-" + "a" * 700
        after = "after-" + "b" * 700
        database = {
            "fetched_at": 1,
            "schema": [{"name": "Summary", "type": "rich_text", "editable": True}],
            "rows": [
                {
                    "id": "row-long",
                    "last_edited_time": "before",
                    "properties": {"Summary": before},
                }
            ],
        }
        packet = workspace.preview_notion_edit(
            database, "row-long", {"Summary": after}
        )

        self.assertEqual(packet["before"]["Summary"], before)
        self.assertEqual(packet["rollback"]["Summary"], before)
        self.assertEqual(
            workspace._typed_change(database["schema"][0], before)["rich_text"][0]["text"]["content"],
            before,
        )
