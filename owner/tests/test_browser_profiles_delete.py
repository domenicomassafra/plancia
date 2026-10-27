from browser_profiles_test_support import *

class BrowserProfileDeleteTests(unittest.TestCase):
    def test_group_delete_resolves_every_host_before_exact_deletes(self):
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }
        lock = threading.Lock()
        queues = {
            "minipc": [
                inventory_payload("minipc", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("minipc", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("minipc", []),
                inventory_payload("minipc", []),
            ],
            "mac-studio": [
                inventory_payload("mac-studio", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("mac-studio", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("mac-studio", []),
                inventory_payload("mac-studio", []),
            ],
            "matebook": [
                inventory_payload("matebook", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("matebook", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("matebook", []),
                inventory_payload("matebook", []),
            ],
            "macbook-air": [
                inventory_payload("macbook-air", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("macbook-air", [profile]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("macbook-air", []),
                inventory_payload("macbook-air", []),
            ],
        }

        def transport(alias, command, **_kwargs):
            host_id = ALIAS_TO_HOST[alias]
            with lock:
                next_payload = queues[host_id].pop(0)
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": next_payload["capabilities"],
                    }
                )
            if command.endswith("delete"):
                return ssh_result(
                    {
                        "ok": True,
                        "deleted_count": 1,
                        "deleted": [profile],
                        "safety": {
                            "browser_inactive": True,
                            "single_target": True,
                            "path_verified": True,
                        },
                    }
                )
            return ssh_result(next_payload)

        result = delete_profile_group(
            "chrome", "same@example.test", transport=transport
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["deleted_count"], 4)
        self.assertEqual(
            set(result["host_results"]),
            {"minipc", "mac-studio", "macbook-air", "matebook"},
        )
        self.assertTrue(all(not queue for queue in queues.values()))

    def test_group_delete_fails_when_identity_moves_directory_after_shared_plan(self):
        planned = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }
        moved = {**planned, "directory": "Profile 3", "label": "Moved profile"}
        lock = threading.Lock()
        queues = {
            "minipc": [
                inventory_payload("minipc", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("minipc", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("minipc", []),
                inventory_payload("minipc", []),
            ],
            "mac-studio": [
                inventory_payload("mac-studio", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("mac-studio", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("mac-studio", []),
                inventory_payload("mac-studio", []),
            ],
            "matebook": [
                inventory_payload("matebook", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("matebook", [moved]),
                inventory_payload("matebook", [moved]),
            ],
            "macbook-air": [
                inventory_payload("macbook-air", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload("macbook-air", [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload("macbook-air", []),
                inventory_payload("macbook-air", []),
            ],
        }

        def transport(alias, command, **_kwargs):
            host_id = ALIAS_TO_HOST[alias]
            with lock:
                payload = queues[host_id].pop(0)
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": payload["capabilities"],
                    }
                )
            if command.endswith("delete"):
                return ssh_result(
                    {
                        "ok": True,
                        "deleted_count": 1,
                        "deleted": [planned],
                        "safety": {
                            "browser_inactive": True,
                            "single_target": True,
                            "path_verified": True,
                        },
                    }
                )
            return ssh_result(payload)

        result = delete_profile_group(
            "chrome", "same@example.test", transport=transport
        )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "group_delete_unproven")
        self.assertEqual(
            result["host_results"]["matebook"]["error_code"], "stale_inventory"
        )
        self.assertEqual(
            result["host_results"]["matebook"]["current_targets"],
            [
                {
                    "browser": "chrome",
                    "email": "same@example.test",
                    "directory": "Profile 3",
                    "label": "Moved profile",
                }
            ],
        )
        self.assertTrue(
            result["host_results"]["minipc"]["final_read"]["absence_proven"]
        )
        self.assertTrue(
            result["host_results"]["mac-studio"]["final_read"]["absence_proven"]
        )
        self.assertTrue(all(not queue for queue in queues.values()))

    def test_group_final_scan_rejects_identity_that_reappears_after_host_checks(self):
        planned = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }
        moved = {**planned, "directory": "Profile 3", "label": "Moved profile"}
        lock = threading.Lock()
        queues = {
            host_id: [
                inventory_payload(host_id, [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload(host_id, [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload(host_id, []),
                inventory_payload(
                    host_id, [moved] if host_id == "matebook" else []
                ),
            ]
            for host_id in ("minipc", "mac-studio", "macbook-air", "matebook")
        }

        def transport(alias, command, **_kwargs):
            host_id = ALIAS_TO_HOST[alias]
            with lock:
                payload = queues[host_id].pop(0)
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": payload["capabilities"],
                    }
                )
            if command.endswith("delete"):
                return ssh_result(
                    {
                        "ok": True,
                        "deleted_count": 1,
                        "deleted": [planned],
                        "safety": {
                            "browser_inactive": True,
                            "single_target": True,
                            "path_verified": True,
                        },
                    }
                )
            return ssh_result(payload)

        result = delete_profile_group(
            "chrome", "same@example.test", transport=transport
        )

        self.assertFalse(result["ok"], result)
        matebook = result["host_results"]["matebook"]
        self.assertEqual(matebook["error_code"], "post_delete_identity_present")
        self.assertEqual(matebook["status"], "unproven")
        self.assertFalse(matebook["final_read"]["absence_proven"])
        self.assertEqual(matebook["current_targets"][0]["directory"], "Profile 3")
        self.assertTrue(all(not queue for queue in queues.values()))

    def test_group_final_scan_checks_host_that_was_not_an_original_target(self):
        planned = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }
        appeared = {**planned, "directory": "Profile 3", "label": "New profile"}
        lock = threading.Lock()
        queues = {
            host_id: [
                inventory_payload(host_id, [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                inventory_payload(host_id, [planned]),
                {"capabilities": {"safe_exact_delete_v2": True}},
                {"delete": True},
                inventory_payload(host_id, []),
                inventory_payload(host_id, []),
            ]
            for host_id in ("minipc", "mac-studio")
        }
        queues["matebook"] = [
            inventory_payload("matebook", []),
            inventory_payload("matebook", [appeared]),
        ]
        queues["macbook-air"] = [
            inventory_payload("macbook-air", []),
            inventory_payload("macbook-air", []),
        ]

        def transport(alias, command, **_kwargs):
            host_id = ALIAS_TO_HOST[alias]
            with lock:
                payload = queues[host_id].pop(0)
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": payload["capabilities"],
                    }
                )
            if command.endswith("delete"):
                return ssh_result(
                    {
                        "ok": True,
                        "deleted_count": 1,
                        "deleted": [planned],
                        "safety": {
                            "browser_inactive": True,
                            "single_target": True,
                            "path_verified": True,
                        },
                    }
                )
            return ssh_result(payload)

        result = delete_profile_group(
            "chrome", "same@example.test", transport=transport
        )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "group_delete_unproven")
        matebook = result["host_results"]["matebook"]
        self.assertFalse(matebook["ok"])
        self.assertEqual(matebook["status"], "unproven")
        self.assertEqual(matebook["error_code"], "post_delete_identity_present")
        self.assertFalse(matebook["planned_target"])
        self.assertEqual(matebook["current_targets"][0]["directory"], "Profile 3")
        self.assertTrue(
            all("path" not in target for target in matebook["current_targets"])
        )
        self.assertTrue(all(not queue for queue in queues.values()))

    def test_group_delete_blocks_everything_when_any_host_is_unavailable(self):
        calls = []

        def transport(alias, command, **_kwargs):
            calls.append((alias, command))
            if alias == "matebooklinux":
                return SSHResult(alias, 124, "", "", "timeout")
            host_id = ALIAS_TO_HOST[alias]
            return ssh_result(inventory_payload(host_id, []))

        result = delete_profile_group(
            "chrome", "same@example.test", transport=transport
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "fleet_inventory_unavailable")
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(command.endswith("inventory") for _alias, command in calls))
