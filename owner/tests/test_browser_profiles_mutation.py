from browser_profiles_test_support import *

class BrowserProfileMutationTests(unittest.TestCase):
    def test_delete_is_idempotent_only_when_exact_directory_is_absent(self):
        calls = []

        def transport(alias, command, **kwargs):
            calls.append(command)
            return ssh_result(inventory_payload("matebook", []))

        result = delete_profile(
            "matebook", "chrome", "dodo@example.com", "Profile 2", transport=transport
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already_absent")
        self.assertEqual(
            calls,
            ["chrome-profiles agent inventory"],
        )

    def test_delete_rejects_stale_cross_identity_before_mutation(self):
        calls = []

        def transport(alias, command, **kwargs):
            calls.append((alias, command))
            return ssh_result(
                inventory_payload(
                    "matebook",
                    [
                        {
                            "browser": "chrome",
                            "email": "other@example.com",
                            "directory": "Profile 2",
                        }
                    ],
                )
            )

        result = delete_profile(
            "matebook", "chrome", "dodo@example.com", "Profile 2", transport=transport
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "stale_inventory")
        self.assertEqual(
            calls,
            [("matebooklinux", "chrome-profiles agent inventory")],
        )

    def test_delete_rejects_same_identity_at_a_different_directory(self):
        calls = []
        moved = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 3",
            "label": "Moved profile",
        }

        def transport(alias, command, **_kwargs):
            calls.append((alias, command))
            return ssh_result(inventory_payload("matebook", [moved]))

        result = delete_profile(
            "matebook",
            "chrome",
            "same@example.test",
            "Profile 2",
            transport=transport,
        )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "stale_inventory")
        self.assertEqual(
            result["current_targets"],
            [
                {
                    "browser": "chrome",
                    "email": "same@example.test",
                    "directory": "Profile 3",
                    "label": "Moved profile",
                }
            ],
        )
        self.assertEqual(
            calls,
            [("matebooklinux", "chrome-profiles agent inventory")],
        )

    def test_delete_rejects_default_profile_before_inventory(self):
        with self.assertRaises(ValueError):
            delete_profile(
                "matebook",
                "chrome",
                "dodo@example.com",
                "Default",
                transport=lambda *_args, **_kwargs: self.fail("transport called"),
            )

    def test_delete_rejects_unproven_agent_result(self):
        profile = {
            "browser": "chrome",
            "email": "dodo@example.com",
            "directory": "Profile 2",
        }
        inventories = [inventory_payload("matebook", [profile])]

        def transport(_alias, command, **_kwargs):
            if command.endswith("inventory"):
                return ssh_result(inventories.pop(0))
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": "matebook"},
                        "capabilities": {"safe_exact_delete_v2": True},
                    }
                )
            return ssh_result(
                {
                    "ok": True,
                    "deleted_count": 1,
                    "deleted": [{**profile, "directory": "Profile 3"}],
                    "safety": {
                        "browser_inactive": True,
                        "single_target": True,
                        "path_verified": True,
                    },
                }
            )

        result = delete_profile(
            "matebook", "chrome", "dodo@example.com", "Profile 2", transport=transport
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "unproven_delete_result")

    def test_delete_requires_new_safe_agent_before_mutation(self):
        profile = {
            "browser": "chrome",
            "email": "dodo@example.com",
            "directory": "Profile 2",
        }

        def transport(_alias, command, **_kwargs):
            if command.endswith("inventory"):
                return ssh_result(inventory_payload("matebook", [profile]))
            return SSHResult("matebook", 2, "", "unknown action", "nonzero_exit")

        result = delete_profile(
            "matebook", "chrome", "dodo@example.com", "Profile 2", transport=transport
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "safe_delete_agent_required")

    def test_delete_reports_browser_active_from_structured_agent_rejection(self):
        profile = {
            "browser": "chrome",
            "email": "dodo@example.com",
            "directory": "Profile 2",
        }

        def transport(_alias, command, **_kwargs):
            if command.endswith("inventory"):
                return ssh_result(inventory_payload("matebook", [profile]))
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": "matebook"},
                        "capabilities": {"safe_exact_delete_v2": True},
                    }
                )
            return SSHResult(
                "matebook",
                2,
                json.dumps({"ok": False, "error_code": "browser_active"}),
                "",
                "nonzero_exit",
            )

        result = delete_profile(
            "matebook", "chrome", "dodo@example.com", "Profile 2", transport=transport
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "browser_active")

    def test_delete_rejects_unsafe_directory_before_transport(self):
        with self.assertRaises(ValueError):
            delete_profile(
                "matebook",
                "chrome",
                "dodo@example.com",
                "../../Default",
                transport=lambda *_args, **_kwargs: self.fail("transport called"),
            )

    def test_invalid_target_never_reaches_transport(self):
        with self.assertRaises(ValueError):
            open_profile(
                "matebook",
                "edge; shutdown",
                "dodo@example.com",
                "Default",
                transport=lambda *_args, **_kwargs: self.fail("transport called"),
            )

    def test_scan_probes_all_hosts_in_parallel(self):
        calls = []
        lock = threading.Lock()
        barrier_released = threading.Event()
        barrier = threading.Barrier(
            len(PROFILE_HOSTS), action=barrier_released.set
        )

        def transport(alias, _command, **_kwargs):
            with lock:
                calls.append(alias)
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError as exc:
                raise AssertionError(
                    "inventory probes did not all reach the synchronization barrier"
                ) from exc
            host_id = ALIAS_TO_HOST[alias]
            return ssh_result(inventory_payload(host_id, []))

        fleet = scan_profile_fleet(transport=transport)

        self.assertTrue(barrier_released.is_set())
        self.assertEqual(len(fleet["inventories"]), 4)
        self.assertEqual(
            set(calls), {"minipc", "macstudio", "macbookair", "matebooklinux"}
        )

    def test_group_delete_preflight_capability_probes_are_parallel(self):
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }
        capability_calls = []
        lock = threading.Lock()
        barrier_released = threading.Event()
        barrier = threading.Barrier(
            len(PROFILE_HOSTS), action=barrier_released.set
        )

        def transport(alias, command, **_kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("capabilities"):
                with lock:
                    capability_calls.append(alias)
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError as exc:
                    raise AssertionError(
                        "capability probes did not all reach the synchronization barrier"
                    ) from exc
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": {"safe_exact_delete_v2": True},
                    }
                )
            return ssh_result(inventory_payload(host_id, [profile]))

        plan = profile_group_delete_plan(
            "chrome", "same@example.test", transport=transport
        )

        self.assertTrue(plan["ok"], plan)
        self.assertTrue(barrier_released.is_set())
        self.assertEqual(len(plan["targets"]), 4)
        self.assertEqual(
            set(capability_calls),
            {"minipc", "macstudio", "macbookair", "matebooklinux"},
        )
