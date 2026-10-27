import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.fleet2 import workspace, workspace_sources


class CanonicalManifestTests(unittest.TestCase):
    def test_notion_actions_uses_current_canonical_binding(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            workspace_sources, "primary_notion_role",
            return_value={"operational_data_sources": {"actions": "rebound-actions"}},
        ), patch.object(
            workspace_sources, "verified_primary_notion_identity",
            return_value={"workspace_id": "primary-id", "workspace": "Primary"},
        ), patch.object(workspace_sources, "_run_ntn", side_effect=[
            {"title": [{"plain_text": "Actions"}]},
            {"results": [], "has_more": False},
        ]) as run_ntn:
            payload = workspace_sources.notion_actions(force=True)

        self.assertTrue(payload["available"])
        self.assertEqual(payload["source"]["data_source_id"], "rebound-actions")
        self.assertEqual(run_ntn.call_args_list[0].args[0], "/v1/data_sources/rebound-actions")
        self.assertEqual(run_ntn.call_args_list[1].args[0], "/v1/data_sources/rebound-actions/query")

    def test_notion_actions_rejects_missing_or_divergent_binding_before_fetch(self):
        for binding, override in ((None, ""), ("canonical-actions", "stale-actions")):
            with self.subTest(binding=binding, override=override), patch.dict(
                os.environ, {"SIGNALDECK_ACTIONS_DATA_SOURCE_ID": override},
            ), patch.object(
                workspace_sources, "primary_notion_role",
                return_value={"operational_data_sources": {"actions": binding}},
            ), patch.object(
                workspace_sources, "verified_primary_notion_identity", return_value={},
            ), patch.object(workspace_sources, "_run_ntn") as run_ntn:
                payload = workspace_sources.notion_actions(force=True)

            self.assertFalse(payload["available"])
            run_ntn.assert_not_called()

    def test_notion_actions_fails_closed_before_fetch_when_identity_mismatches(self):
        with patch.object(
            workspace_sources,
            "_notion_cache",
            workspace_sources._Cache(),
        ), patch.object(
            workspace_sources,
            "verified_primary_notion_identity",
            side_effect=RuntimeError("non-primary workspace"),
        ), patch.object(workspace_sources, "_run_ntn") as run_ntn:
            payload = workspace_sources.notion_actions(force=True)

        self.assertFalse(payload["available"])
        self.assertIn("non-primary workspace", payload["reason"])
        run_ntn.assert_not_called()

    def test_obsidian_state_enforces_adapter_root_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in ("05-knowledge/Allowed.md", "06-life/Private.md"):
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text("allowed" if relative.startswith("05-") else "private")

            payload = workspace_sources.obsidian_state(force=True, vault=root)

        self.assertTrue(payload["available"])
        self.assertEqual([item["path"] for item in payload["recent"]], ["05-knowledge/Allowed.md"])
        self.assertEqual(payload["allowlisted_roots"], list(workspace_sources.OBSIDIAN_ALLOWED_ROOTS))

    def test_authorized_note_body_is_read_only_and_excluded_paths_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowed = root / "05-knowledge" / "Note.md"
            private = root / "06-life" / "Private.md"
            allowed.parent.mkdir(parents=True)
            private.parent.mkdir(parents=True)
            allowed.write_text("# Canonical note\nRead-only fixture\n", encoding="utf-8")
            private.write_text("not for workspace", encoding="utf-8")
            (allowed.parent / "Escape.md").symlink_to(private)
            with patch.dict(os.environ, {"SIGNALDECK_OBSIDIAN_ROOT": str(root)}):
                selected = workspace.obsidian_explorer(note="05-knowledge/Note.md")
                excluded = workspace.obsidian_explorer(note="06-life/Private.md")
                escape = workspace.obsidian_explorer(note="05-knowledge/Escape.md")
                traversal = workspace.obsidian_explorer(note="05-knowledge/../06-life/Private.md")

            self.assertEqual(selected["selected"]["content"], "# Canonical note\nRead-only fixture\n")
            self.assertFalse(selected["selected"]["content_truncated"])
            self.assertIsNone(excluded["selected"])
            self.assertIsNone(escape["selected"])
            self.assertIsNone(traversal["selected"])
            self.assertEqual(allowed.read_text(encoding="utf-8"), "# Canonical note\nRead-only fixture\n")
            self.assertEqual(private.read_text(encoding="utf-8"), "not for workspace")

    def test_old_allowlisted_note_remains_openable_beyond_recent_index_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "05-knowledge"
            folder.mkdir()
            for i in range(workspace.MAX_NODES + 5):
                note = folder / f"{i:03}.md"
                note.write_text(f"# Page {i}\n", encoding="utf-8")
                os.utime(note, (i + 1, i + 1))
            with patch.dict(os.environ, {"SIGNALDECK_OBSIDIAN_ROOT": str(root)}):
                payload = workspace.obsidian_explorer(note="05-knowledge/000.md")

        self.assertLessEqual(len(payload["nodes"]), workspace.MAX_NODES)
        self.assertIsNotNone(payload["selected"])
        self.assertEqual(payload["selected"]["path"], "05-knowledge/000.md")
        self.assertEqual(payload["selected"]["content"], "# Page 0\n")

    def test_projection_is_bounded_and_exposes_provider_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            services_root = root / "services-root"
            files = {
                "factory_registry": root / "registry/targets.json",
                "obsidian_app": root / "surfaces/chatgpt/apps/obsidian/app.json",
                "app_catalog": root / "registry/catalog/chatgpt-app-servers/catalog.json",
                "capability_catalog": root / "registry/catalog/capabilities/catalog.json",
                "obsidian_source": services_root / "services/obsidian-mcp/SOURCE.json",
                "council_source": services_root / "services/dodo-council/SOURCE.json",
            }
            values = {
                files["factory_registry"]: {"schema_version": 1, "targets": {
                    "obsidian-chatgpt-app": {"kind": "chatgpt-app", "operation": "update", "bundle": "apps/obsidian"},
                    "too-many": {"kind": "ignored", "operation": "create", "bundle": "x"},
                }},
                files["obsidian_app"]: {"app": {"expected_tool_count": 15}, "update_workflow": {"action": "Aggiorna", "owner_host": "mac-studio"}},
                files["app_catalog"]: {"servers": {"obsidian": {"status": "active", "runtime": {"service": "obsidian-mcp.service"}}}},
                files["obsidian_source"]: {"legacy_commit": "abc123"},
                files["council_source"]: {"source_commit": "def456", "runtime_promoted": True},
            }
            for path, value in values.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

            values[files["capability_catalog"]] = {
                "propagation": {
                    "entries": [{
                        "id": "lifeos.obsidian-context-app",
                        "name": "Obsidian context app",
                        "version": 1,
                        "status": "partial",
                        "canonical_contract": {
                            "path": "surfaces/chatgpt/apps/obsidian/app.json",
                            "contract_version": 1,
                            "sha256": hashlib.sha256(files["obsidian_app"].read_bytes()).hexdigest(),
                        },
                        "projections": [{
                            "id": "chatgpt-app-server-catalog",
                            "path": "registry/catalog/chatgpt-app-servers/catalog.json",
                            "status": "aligned",
                            "contract_version": 1,
                            "sha256": hashlib.sha256(files["app_catalog"].read_bytes()).hexdigest(),
                        }],
                        "consumers": ["signal-deck"],
                        "liveness_proof": {"status": "partial"},
                        "last_propagation_receipt": {"status": "source-only"},
                        "limitations": ["provider proof pending"],
                    }]
                }
            }
            files["capability_catalog"].parent.mkdir(parents=True, exist_ok=True)
            files["capability_catalog"].write_text(
                json.dumps(values[files["capability_catalog"]], sort_keys=True), encoding="utf-8"
            )
            with patch.object(workspace_sources, "FACTORY_ROOT", root), patch.object(
                workspace_sources, "SERVICES_ROOT", services_root
            ):
                workspace_sources._manifest_cache = workspace_sources._Cache()
                payload = workspace_sources.canonical_manifests(force=True)

        self.assertTrue(payload["available"])
        self.assertEqual(payload["factory"]["targets"][0]["id"], "obsidian-chatgpt-app")
        self.assertEqual(payload["provider_gate"]["expected_tool_count"], 15)
        self.assertEqual(payload["provider_gate"]["observed_tool_count"], 19)
        self.assertEqual(payload["news"]["state"], "manual")
        self.assertEqual(payload["services"][1]["status"], "promoted")
        propagation = payload["propagation"]
        self.assertTrue(propagation["available"])
        entry = propagation["entries"][0]
        self.assertTrue(entry["source_valid"])
        self.assertTrue(entry["rendered"])
        self.assertEqual(entry["applied"], "source-only")
        self.assertFalse(entry["runtime_proven"])
        self.assertEqual(entry["drift"], [])

    def test_propagation_marks_digest_mismatch_as_drift(self):
        entry = {
            "id": "lifeos.obsidian-context-app",
            "name": "Obsidian context app",
            "version": 1,
            "status": "partial",
            "canonical_contract": {
                "path": "surfaces/chatgpt/apps/obsidian/app.json",
                "contract_version": 1,
                "sha256": "0" * 64,
            },
            "projections": [],
            "consumers": ["signal-deck"],
            "liveness_proof": {"status": "unproven"},
            "last_propagation_receipt": {"status": "source-only"},
            "limitations": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "surfaces/chatgpt/apps/obsidian/app.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}", encoding="utf-8")
            with patch.object(workspace_sources, "FACTORY_ROOT", root):
                projected = workspace_sources._project_propagation({"propagation": {"entries": [entry]}})

        self.assertFalse(projected["entries"][0]["source_valid"])
        self.assertIn("source-digest", projected["entries"][0]["drift"])

    def test_malformed_projection_shape_fails_as_a_bounded_contract_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "surfaces/chatgpt/apps/obsidian/app.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}", encoding="utf-8")
            entry = {
                "id": "lifeos.obsidian-context-app",
                "canonical_contract": {
                    "path": "surfaces/chatgpt/apps/obsidian/app.json",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
                "projections": {"unexpected": "object"},
                "consumers": ["signal-deck"],
            }
            with patch.object(workspace_sources, "FACTORY_ROOT", root), self.assertRaisesRegex(
                ValueError, "proiezioni"
            ):
                workspace_sources._project_propagation({"propagation": {"entries": [entry]}})

    def test_missing_capability_catalog_is_an_explicit_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            services_root = root / "services-root"
            fixtures = {
                root / "registry/targets.json": {"targets": {}},
                root / "surfaces/chatgpt/apps/obsidian/app.json": {"app": {"expected_tool_count": 15}},
                root / "registry/catalog/chatgpt-app-servers/catalog.json": {"servers": {"obsidian": {}}},
                services_root / "services/obsidian-mcp/SOURCE.json": {},
                services_root / "services/dodo-council/SOURCE.json": {},
            }
            for path, value in fixtures.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(workspace_sources, "FACTORY_ROOT", root), patch.object(
                workspace_sources, "SERVICES_ROOT", services_root
            ):
                workspace_sources._manifest_cache = workspace_sources._Cache()
                payload = workspace_sources.canonical_manifests(force=True)

        self.assertTrue(payload["available"])
        self.assertFalse(payload["propagation"]["available"])
        self.assertEqual(payload["propagation"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
