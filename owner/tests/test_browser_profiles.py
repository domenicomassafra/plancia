from browser_profiles_test_support import *
from types import SimpleNamespace
from backend.browser_profiles.hosts import _resolve_profile_hosts as resolve_profile_hosts_from_registry

class BrowserProfileAdapterTests(unittest.TestCase):
    def test_linux_air_registry_drives_profile_runtime_and_alias(self):
        fleet = SimpleNamespace(hosts=[SimpleNamespace(
            id="macbook-air", ssh_alias="dodomarchy", os="linux", image=None,
        )])
        air = resolve_profile_hosts_from_registry(registry_loader=lambda: fleet)["macbook-air"]
        self.assertEqual(air.alias, "dodomarchy")
        self.assertEqual(air.runtime, "linux")

    def test_profile_host_resolution_fails_closed_when_registry_is_unavailable(self):
        with patch("backend.browser_profiles.load_registry", side_effect=RuntimeError("registry unavailable")):
            with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                _resolve_profile_hosts()

    def test_canonical_profile_label_uses_a_tag_and_full_email(self):
        self.assertEqual(
            canonical_profile_label("aiconic", "Owner@Example.Test"),
            "[AICONIC] owner@example.test",
        )
        with self.assertRaises(ValueError):
            canonical_profile_label("not [a tag", "owner@example.test")
        self.assertEqual(
            canonical_profile_category("old project", "owner@example.test"),
            "[OLD PROJECT]",
        )

    def test_fleet_name_sync_preserves_multiword_intentional_tag_over_test_fallback(self):
        email = "project@example.test"
        state = {
            "mac-studio": {
                "label": f"[OLD PROJECT] {email}",
                "policy_label": f"[OLD PROJECT] {email}",
                "name_policy_ok": False,
            },
            "minipc": {
                "label": f"[TEST] {email}",
                "policy_label": f"[TEST] {email}",
                "name_policy_ok": False,
            },
        }

        def transport(alias, command, **kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("inventory"):
                profiles = []
                if host_id in state:
                    profiles = [{
                        "browser": "chrome",
                        "email": email,
                        "directory": "Profile 2",
                        **state[host_id],
                    }]
                return ssh_result(inventory_payload(host_id, profiles))
            if command.endswith("capabilities"):
                return ssh_result({
                    "schema_version": PROFILE_SCHEMA_VERSION,
                    "agent_version": PROFILE_AGENT_VERSION,
                    "runtime": agent_runtime(host_id),
                    "host": {"id": host_id},
                    "capabilities": {"safe_exact_rename_v1": True},
                })
            if command.endswith("rename-preview"):
                requested = json.loads(kwargs["input_text"])["targets"]
                return ssh_result({
                    "ok": True,
                    "targets": [
                        {
                            **target,
                            "path": f"/private/{host_id}/{target['directory']}",
                            "current_label": state[host_id]["label"],
                        }
                        for target in requested
                    ],
                })
            return SSHResult(alias, 2, "", "unexpected", "nonzero_exit")

        plan = profile_name_sync_plan({}, transport=transport)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(
            {target["desired_label"] for target in plan["targets"]},
            {f"[OLD PROJECT] {email}"},
        )

    def test_fleet_name_sync_uses_signal_deck_category_as_authority_and_verifies(self):
        email = "same@example.test"
        state = {
            "minipc": {
                "label": f"[A] {email}",
                "policy_label": f"[A] {email}",
                "name_policy_ok": True,
            },
            "mac-studio": {
                "label": email,
                "policy_label": f"[TEST] {email}",
                "name_policy_ok": False,
            },
        }
        metadata = {
            (
                "mac-studio",
                f"chrome:{email}:Profile 2",
            ): {"alias": "", "category": "work", "updated_at": 1.0}
        }

        def transport(alias, command, **kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("inventory"):
                profiles = []
                if host_id in state:
                    profiles = [
                        {
                            "browser": "chrome",
                            "email": email,
                            "directory": "Profile 2",
                            **state[host_id],
                        }
                    ]
                return ssh_result(inventory_payload(host_id, profiles))
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": PROFILE_SCHEMA_VERSION,
                        "agent_version": PROFILE_AGENT_VERSION,
                        "runtime": agent_runtime(host_id),
                        "host": {"id": host_id},
                        "capabilities": {"safe_exact_rename_v1": True},
                    }
                )
            if command.endswith("rename-preview"):
                requested = json.loads(kwargs["input_text"])["targets"]
                return ssh_result(
                    {
                        "ok": True,
                        "targets": [
                            {
                                **target,
                                "path": f"/private/{host_id}/{target['directory']}",
                                "current_label": state[host_id]["label"],
                            }
                            for target in requested
                        ],
                    }
                )
            if command.endswith("rename"):
                requested = json.loads(kwargs["input_text"])["targets"]
                desired = requested[0]["desired_label"]
                state[host_id] = {
                    "label": desired,
                    "policy_label": desired,
                    "name_policy_ok": True,
                }
                return ssh_result({"ok": True, "renamed_count": len(requested)})
            return SSHResult(alias, 2, "", "unexpected", "nonzero_exit")

        plan = profile_name_sync_plan(metadata, transport=transport)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["target_count"], 2)
        self.assertEqual(
            {target["desired_label"] for target in plan["targets"]},
            {f"[WORK] {email}"},
        )
        self.assertTrue(all("path" not in target for target in plan["targets"]))

        result = sync_profile_names(
            metadata,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            transport=transport,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["renamed_count"], 2)
        self.assertEqual(state["minipc"]["label"], f"[WORK] {email}")
        self.assertEqual(state["mac-studio"]["label"], f"[WORK] {email}")

    def test_group_rename_proves_local_label_even_when_provider_name_differs(self):
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
            "label": "Old Local Label",
            "visible_label": "Nome Google (Old Local Label)",
        }
        renamed_hosts: set[str] = set()
        lock = threading.Lock()

        def transport(alias, command, **kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("inventory"):
                with lock:
                    local_label = (
                        "[TEST] same@example.test"
                        if host_id in renamed_hosts
                        else profile["label"]
                    )
                return ssh_result(
                    inventory_payload(
                        host_id,
                        [
                            {
                                **profile,
                                "label": local_label,
                                "visible_label": f"Nome Google ({local_label})",
                            }
                        ],
                    )
                )
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": {"safe_exact_rename_v1": True},
                    }
                )
            if command.endswith("rename-preview"):
                request = json.loads(kwargs["input_text"])["targets"][0]
                return ssh_result(
                    {
                        "ok": True,
                        "targets": [
                            {
                                **request,
                                "path": f"/private/{host_id}/Profile 2",
                                "current_label": profile["label"],
                            }
                        ],
                    }
                )
            if command.endswith("rename"):
                with lock:
                    renamed_hosts.add(host_id)
                return ssh_result({"ok": True, "renamed_count": 1})
            return SSHResult(alias, 2, "", "unexpected action", "nonzero_exit")

        result = rename_profile_group("chrome", "same@example.test", "test", transport=transport)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["desired_label"], "[TEST] same@example.test")
        self.assertEqual(
            set(result["host_results"]),
            {"minipc", "mac-studio", "macbook-air", "matebook"},
        )
        self.assertTrue(all("path" not in target for target in result["targets"]))

    def test_group_rename_batches_duplicate_directories_per_host(self):
        email = "same@example.test"
        state = {
            "minipc": [],
            "mac-studio": [],
            "macbook-air": [],
            "matebook": [
                {
                    "browser": "chrome",
                    "email": email,
                    "directory": "Profile 2",
                    "label": "Old 2",
                },
                {
                    "browser": "chrome",
                    "email": email,
                    "directory": "Profile 3",
                    "label": "Old 3",
                },
            ],
        }
        rename_calls: list[list[str]] = []

        def transport(alias, command, **kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("inventory"):
                return ssh_result(inventory_payload(host_id, list(state[host_id])))
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": PROFILE_SCHEMA_VERSION,
                        "agent_version": PROFILE_AGENT_VERSION,
                        "runtime": agent_runtime(host_id),
                        "host": {"id": host_id},
                        "capabilities": {"safe_exact_rename_v1": True},
                    }
                )
            if command.endswith("rename-preview"):
                requested = json.loads(kwargs["input_text"])["targets"]
                return ssh_result(
                    {
                        "ok": True,
                        "targets": [
                            {
                                **target,
                                "path": f"/private/{host_id}/{target['directory']}",
                                "current_label": next(
                                    item["label"]
                                    for item in state[host_id]
                                    if item["directory"] == target["directory"]
                                ),
                            }
                            for target in requested
                        ],
                    }
                )
            if command.endswith("rename"):
                requested = json.loads(kwargs["input_text"])["targets"]
                rename_calls.append([target["directory"] for target in requested])
                desired = requested[0]["desired_label"]
                state[host_id] = [
                    {
                        **item,
                        "label": desired,
                        "policy_label": desired,
                        "name_policy_ok": True,
                    }
                    for item in state[host_id]
                ]
                return ssh_result({"ok": True, "renamed_count": len(requested)})
            return SSHResult(alias, 2, "", "unexpected action", "nonzero_exit")

        result = rename_profile_group(
            "chrome",
            email,
            "test",
            target_hosts=["matebook"],
            transport=transport,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["renamed_count"], 2)
        self.assertEqual(rename_calls, [["Profile 2", "Profile 3"]])
        self.assertEqual(
            [item["label"] for item in state["matebook"]],
            [f"[TEST] {email}", f"[TEST] {email}"],
        )
