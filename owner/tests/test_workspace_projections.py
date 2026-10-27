from workspace_test_support import (
    WorkspaceTestBase, ACTIONS_ID, ast, json, os, sys, tempfile,
    unittest, Path, SimpleNamespace, patch, workspace, workspace_sources,
)

class WorkspaceProjectionTests(WorkspaceTestBase):
    def test_external_context_projection_uses_fresh_sources_and_owner_manifests(self):
        source = {
            "state": "healthy",
            "available": True,
            "reason": None,
            "freshness_s": 5,
            "stale_after_s": 120,
        }
        manifests = {
            "available": True,
            "agent_profiles": [
                {
                    "id": "hermes-signor",
                    "name": "Hermes · Signor",
                    "published": True,
                    "digest": "b" * 64,
                    "rendered": True,
                    "runtime": "runtime-proven",
                    "runtime_proven": True,
                }
            ],
            "provider_gate": {
                "state": "manual",
                "reason": "refresh owner richiesto",
                "observed_at": "2026-07-26",
            },
        }
        projection = workspace.external_context_projection(
            {"source": source},
            {"source": source},
            manifests,
            now=1_000,
        )

        states = {entry["id"]: entry["state"] for entry in projection["sources"]}
        self.assertEqual(states["notion"], "proven")
        self.assertEqual(states["obsidian"], "proven")
        self.assertEqual(states["ordinary-chatgpt-project"], "manual")
        self.assertEqual(projection["state"], "manual")
        self.assertEqual(
            projection["agents_projects"]["agents"][0]["runtime"]["state"],
            "runtime-proven",
        )

    def test_external_context_projection_fails_closed_on_stale_sources_or_missing_manifest(self):
        fresh_source = {
            "state": "healthy",
            "available": True,
            "reason": None,
            "freshness_s": 5,
            "stale_after_s": 120,
        }
        stale_source = {**fresh_source, "freshness_s": 999}
        stale = workspace.external_context_projection(
            {"source": stale_source},
            {"source": fresh_source},
            {"available": True, "provider_gate": {"state": "manual"}},
            now=1_000,
        )
        self.assertEqual(stale["state"], "unproven")
        self.assertEqual(
            next(entry for entry in stale["sources"] if entry["id"] == "notion")["state"],
            "unproven",
        )

        missing_manifest = workspace.external_context_projection(
            {"source": fresh_source},
            {"source": fresh_source},
            {"available": False, "reason": "manifest receipt missing"},
            now=1_000,
        )
        self.assertEqual(missing_manifest["state"], "unproven")
        self.assertFalse(missing_manifest["available"])
        self.assertTrue(
            all(entry["state"] == "unproven" for entry in missing_manifest["sources"])
        )

    def test_agents_projects_projection_proves_fixture_and_gates_inflight_project(self):
        projection = workspace.agents_projects_projection(
            {
                "available": True,
                "agent_profiles": [
                    {
                        "id": "hermes-signor",
                        "name": "Hermes · Signor",
                        "published": True,
                        "digest": "b" * 64,
                        "rendered": True,
                        "runtime": "deferred",
                        "runtime_proven": False,
                    }
                ],
                "propagation": {
                    "available": True,
                    "state": "partial",
                    "entries": [
                        {
                            "id": "ordinary-chatgpt-project",
                            "name": "ChatGPT Project · Life OS",
                            "version": "1",
                            "status": "published",
                            "source_valid": True,
                            "source_digest": {"actual": "a" * 64, "match": True},
                            "rendered": True,
                            "applied": "source-only",
                            "runtime": "manual",
                            "runtime_proven": False,
                            "drift": [],
                            "limitations": ["provider receipt in-flight"],
                        }
                    ],
                },
            },
            now=1_000,
        )

        hermes = projection["agents"][0]
        project = projection["projects"][0]
        self.assertEqual(hermes["source"]["state"], "source-proven")
        self.assertEqual(hermes["generated"]["state"], "generated")
        self.assertEqual(hermes["runtime"]["state"], "deferred")
        self.assertEqual(project["source"]["state"], "source-proven")
        self.assertEqual(project["generated"]["state"], "generated")
        self.assertEqual(project["runtime"]["state"], "manual")
        self.assertEqual(project["digest"], "a" * 64)
        self.assertEqual(projection["state"], "manual")

        incomplete = workspace.agents_projects_projection(
            {"available": True},
            now=1_000,
        )
        self.assertEqual(incomplete["state"], "deferred")

    def test_obsidian_explorer_is_allowlisted_and_returns_links_without_body(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "05-knowledge").mkdir()
            (root / "06-life").mkdir()
            (root / "05-knowledge" / "Index.md").write_text(
                "---\ntitle: Index\nstatus: active\nsecret: no\n---\nSee [[Second]].\n",
                encoding="utf-8",
            )
            (root / "05-knowledge" / "Second.md").write_text(
                "# Second\n", encoding="utf-8"
            )
            (root / "06-life" / "Private.md").write_text("private", encoding="utf-8")
            with patch.dict(os.environ, {"SIGNALDECK_OBSIDIAN_ROOT": str(root)}):
                payload = workspace.obsidian_explorer(note="05-knowledge/Index.md")

        self.assertEqual(payload["source"]["state"], "healthy")
        paths = {node["path"] for node in payload["nodes"]}
        self.assertEqual(paths, {"05-knowledge/Index.md", "05-knowledge/Second.md"})
        self.assertNotIn("secret", payload["selected"]["metadata"])
        self.assertEqual(
            payload["selected"]["links"][0]["path"], "05-knowledge/Second.md"
        )
        self.assertNotIn("body", json.dumps(payload))

    def test_notion_writes_are_confined_to_the_registry_transaction(self):
        """The dashboard reads Notion; only the Registry transaction may mutate it.

        The projection/read surface is read-only by contract, and the single
        authorised Notion mutation path is the typed, preview-first,
        Registry-gated Actions transaction. Two ways a read helper could
        silently start mutating Notion are guarded at source level, because
        review alone does not survive refactors:

        * an explicit write verb (``method="POST|PATCH|PUT|DELETE"``);
        * the implicit ``ntn api`` default, which is a POST whenever a payload
          is passed, so any payload-bearing ``_run_ntn`` call that is neither
          inside the transaction nor a read-only ``/query`` endpoint
          (``GET`` is only used for payload-less paths).
        """
        source_path = (
            Path(__file__).resolve().parents[1] / "backend" / "fleet2" / "workspace.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        enclosing: dict[ast.AST, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    enclosing.setdefault(child, node.name)
        write_verbs = {"POST", "PATCH", "PUT", "DELETE"}
        # The whole Registry transaction: open (create the ledger row) ->
        # patch the target -> close (finalise the ledger row).
        allowed_functions = {
            "_open_notion_registry",
            "_close_notion_registry",
            "_apply_notion_edit",
        }
        offenders: list[tuple[str, str, int]] = []
        write_issuing: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                name = getattr(node.func, "id", "")
            if name != "_run_ntn":
                continue
            function = enclosing.get(node, "<module>")
            method = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "method"),
                None,
            )
            explicit = (
                method.value.upper()
                if isinstance(method, ast.Constant) and isinstance(method.value, str)
                else None
            )
            path_arg = node.args[0] if node.args else None
            if isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str):
                path = path_arg.value
            elif isinstance(path_arg, ast.JoinedStr):
                # f-string: keep the literal parts so a read-only suffix such as
                # "/query" is still recognised across an interpolated segment.
                path = "".join(
                    part.value
                    for part in path_arg.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
            else:
                path = ""
            payload_supplied = len(node.args) >= 2 or any(
                keyword.arg == "payload" for keyword in node.keywords
            )
            mutating = explicit in write_verbs or (
                explicit is None and payload_supplied and not path.endswith("/query")
            )
            if not mutating:
                continue
            write_issuing.add(function)
            if function not in allowed_functions:
                offenders.append((function, explicit or "implicit-POST", node.lineno))
        self.assertEqual(offenders, [])
        # Non-vacuous: the guard must actually be looking at the real writers.
        self.assertEqual(write_issuing, allowed_functions)


class ActionsBindingTests(unittest.TestCase):
    """V08: canonical role binding for the transactional Actions path.

    The transactional read/preview/apply/receipt/rollback flow must resolve
    ``primary.operational_data_sources.actions`` once, fail closed on a missing
    or divergent binding before any fetch, and never let a superseded receipt
    silently target a different surface after a rebind.
    """

    def _role(self, actions: str | None) -> dict:
        role = {
            "workspace_id": "primary-id",
            "registry_data_source_id": "registry-id",
        }
        if actions is not None:
            role["operational_data_sources"] = {"actions": actions}
        return role

    def test_transactional_read_resolves_the_canonical_binding_once(self):
        source = {
            "id": "rebound-actions-id",
            "title": [{"plain_text": "Actions"}],
            "properties": {},
        }
        with (
            patch.object(
                workspace_sources, "primary_notion_role",
                return_value=self._role("rebound-actions-id"),
            ),
            patch.object(
                workspace, "_verify_workspace",
                return_value={"workspace": "Domenico", "workspace_id": "primary-id"},
            ),
            patch.object(
                workspace.workspace_sources,
                "_run_ntn",
                side_effect=[source, {"results": [], "has_more": False}],
            ) as run_ntn,
        ):
            payload = workspace.notion_database(force=True)

        self.assertTrue(payload["source"]["available"])
        self.assertEqual(payload["source"]["data_source_id"], "rebound-actions-id")
        self.assertEqual(
            [call.args[0] for call in run_ntn.call_args_list],
            [
                "v1/data_sources/rebound-actions-id",
                "v1/data_sources/rebound-actions-id/query",
            ],
        )

    def test_missing_or_divergent_binding_fails_closed_before_any_fetch(self):
        for actions, override in ((None, ""), ("canonical-actions", "stale-actions")):
            with self.subTest(actions=actions, override=override), patch.dict(
                os.environ, {"SIGNALDECK_ACTIONS_DATA_SOURCE_ID": override}
            ), patch.object(
                workspace_sources, "primary_notion_role",
                return_value=self._role(actions),
            ), patch.object(
                workspace, "_verify_workspace", return_value={"workspace": "Domenico"}
            ), patch.object(workspace.workspace_sources, "_run_ntn") as run_ntn:
                payload = workspace.notion_database(force=True)
                with self.assertRaises(RuntimeError):
                    workspace.notion_row("row-1")

            self.assertFalse(payload["source"]["available"])
            run_ntn.assert_not_called()

    def test_apply_refuses_a_packet_from_a_superseded_actions_binding(self):
        with (
            patch.object(
                workspace_sources, "primary_notion_role",
                return_value=self._role("rebound-actions-id"),
            ),
            patch.object(workspace, "_apply_notion_edit") as apply_edit,
        ):
            with self.assertRaisesRegex(RuntimeError, "rebind"):
                workspace.apply_notion_edit(
                    "row-1", {"Status": "Done"}, "preimage", "packet", ACTIONS_ID
                )
        apply_edit.assert_not_called()

    def test_apply_pins_the_resolved_canonical_source_for_a_matching_packet(self):
        with (
            patch.object(
                workspace_sources, "primary_notion_role",
                return_value=self._role(ACTIONS_ID),
            ),
            patch.object(
                workspace, "_apply_notion_edit", return_value={"ok": True}
            ) as apply_edit,
        ):
            workspace.apply_notion_edit(
                "row-1", {"Status": "Done"}, "preimage", "packet", ACTIONS_ID
            )
        self.assertEqual(apply_edit.call_args.args[0].data_source_id, ACTIONS_ID)
        self.assertEqual(apply_edit.call_args.args[1], "row-1")

    def test_rollback_refuses_a_receipt_from_a_superseded_actions_binding(self):
        record = {
            "source_key": "actions",
            "data_source_id": ACTIONS_ID,
            "row_id": "row-1",
            "before": {"Status": "Not started"},
            "after": {"Status": "Done"},
        }
        with (
            patch.object(
                workspace_sources, "primary_notion_role",
                return_value=self._role("rebound-actions-id"),
            ),
            patch.object(workspace, "_notion_row") as fetch_row,
        ):
            with self.assertRaisesRegex(ValueError, "non appartiene"):
                workspace.rollback_notion_edit(record)
        fetch_row.assert_not_called()

    def test_post_fetch_reconcile_refuses_a_superseded_receipt(self):
        record = {
            "source_key": "actions",
            "data_source_id": ACTIONS_ID,
            "row_id": "row-1",
            "changes": {"Status": "Done"},
        }
        with (
            patch.object(
                workspace_sources, "primary_notion_role",
                return_value=self._role("rebound-actions-id"),
            ),
            patch.object(workspace, "_notion_row") as fetch_row,
        ):
            with self.assertRaisesRegex(RuntimeError, "canonico"):
                workspace.verify_action_item_edit_post_fetch(record)
        fetch_row.assert_not_called()
