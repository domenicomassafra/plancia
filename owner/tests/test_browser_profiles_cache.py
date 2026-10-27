from browser_profiles_test_support import *

class BrowserProfileCacheTests(unittest.TestCase):
    def test_inventory_cache_marks_cached_then_stale_without_rescanning(self):
        ticks = iter([100.0, 100.0, 105.0, 130.0])
        cache = ProfileInventoryCache(ttl_seconds=20, clock=lambda: next(ticks))
        scans = []

        def scanner():
            scans.append("scan")
            return {"schema_version": 1, "inventories": [], "accounts": []}

        fresh = cache.get(refresh=False, scanner=scanner)
        cached = cache.get(refresh=False, scanner=scanner)
        self.assertEqual(fresh["freshness"], "fresh")
        self.assertEqual(cached["freshness"], "cached")
        self.assertEqual(scans, ["scan"])

        stale = cache.get(
            refresh=False, scanner=lambda: (_ for _ in ()).throw(OSError("offline"))
        )
        self.assertEqual(stale["freshness"], "stale")

    def test_inventory_cache_coalesces_concurrent_scans(self):
        cache = ProfileInventoryCache(ttl_seconds=20)
        entered = threading.Event()
        release = threading.Event()
        scans = []
        results = []

        def scanner():
            scans.append("scan")
            entered.set()
            self.assertTrue(release.wait(timeout=1))
            return {"schema_version": 1, "inventories": [], "accounts": []}

        first = threading.Thread(
            target=lambda: results.append(cache.get(refresh=False, scanner=scanner))
        )
        second = threading.Thread(
            target=lambda: results.append(cache.get(refresh=False, scanner=scanner))
        )
        first.start()
        self.assertTrue(entered.wait(timeout=1))
        second.start()
        release.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(scans, ["scan"])
        self.assertEqual(len(results), 2)

    def test_mac_studio_host_id_resolves_through_canonical_registry_to_macstudio(self):
        from backend.fleet2.registry import load_registry

        reg = load_registry()
        reg_mac = reg.get("mac-studio")
        self.assertIsNotNone(reg_mac)
        self.assertEqual(reg_mac.ssh_alias, "macstudio")
        self.assertEqual(PROFILE_HOSTS["mac-studio"].alias, "macstudio")
        self.assertEqual(PROFILE_HOSTS["mac-studio"].alias, reg_mac.ssh_alias)

    def test_inventory_host_distinguishes_failure_states(self):
        r_missing = inventory_host(
            "mac-studio",
            transport=lambda *a, **k: SSHResult("macstudio", 127, "", "command not found", "missing_installation"),
        )
        self.assertFalse(r_missing["online"])
        self.assertEqual(r_missing["error_code"], "missing_installation")

        r_unreach = inventory_host(
            "mac-studio",
            transport=lambda *a, **k: SSHResult("macstudio", 255, "", "Connection timed out", "unreachable"),
        )
        self.assertFalse(r_unreach["online"])
        self.assertEqual(r_unreach["error_code"], "unreachable")

        r_dns = inventory_host(
            "mac-studio",
            transport=lambda *a, **k: SSHResult("macstudio", 255, "", "Could not resolve hostname", "dns_failure"),
        )
        self.assertFalse(r_dns["online"])
        self.assertEqual(r_dns["error_code"], "dns_failure")

        r_invalid = inventory_host(
            "mac-studio",
            transport=lambda *a, **k: SSHResult("macstudio", 0, "not json", "", None),
        )
        self.assertFalse(r_invalid["online"])
        self.assertEqual(r_invalid["error_code"], "invalid_agent_response")

        r_zero = inventory_host(
            "mac-studio",
            transport=lambda *a, **k: ssh_result(inventory_payload("mac-studio", [])),
        )
        self.assertTrue(r_zero["online"])
        self.assertEqual(len(r_zero["profiles"]), 0)

    def test_unrelated_unavailable_host_does_not_block_targeted_rename(self):
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
            "visible_label": "Old Label",
        }
        renamed: set[str] = set()

        def transport(alias, command, **kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if alias == "matebooklinux":
                return SSHResult(alias, 255, "", "Connection timed out", "unreachable")
            if command.endswith("inventory"):
                label = "[WORK] same@example.test" if host_id in renamed else profile["visible_label"]
                return ssh_result(inventory_payload(host_id, [{**profile, "visible_label": label}]))
            if command.endswith("capabilities"):
                return ssh_result({
                    "schema_version": 1,
                    "host": {"id": host_id},
                    "capabilities": {"safe_exact_rename_v1": True},
                })
            if command.endswith("rename-preview"):
                return ssh_result({
                    "ok": True,
                    "targets": [{
                        "browser": "chrome",
                        "email": "same@example.test",
                        "directory": "Profile 2",
                        "path": f"/private/{host_id}/Profile 2",
                        "current_label": profile["visible_label"],
                        "desired_label": "[WORK] same@example.test",
                    }],
                })
            if command.endswith("rename"):
                renamed.add(host_id)
                return ssh_result({"ok": True, "renamed_count": 1})
            return SSHResult(alias, 2, "", "unexpected", "nonzero_exit")

        result = rename_profile_group(
            "chrome",
            "same@example.test",
            "work",
            target_hosts=["minipc", "mac-studio"],
            transport=transport,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["desired_label"], "[WORK] same@example.test")
        self.assertEqual(set(result["host_results"]), {"minipc", "mac-studio"})

    def test_targeted_unavailable_host_blocks_rename_fail_closed(self):
        def transport(alias, command, **kwargs):
            if alias == "matebooklinux":
                return SSHResult(alias, 255, "", "Connection timed out", "unreachable")
            host_id = ALIAS_TO_HOST[alias]
            return ssh_result(inventory_payload(host_id, []))

        result = rename_profile_group(
            "chrome",
            "same@example.test",
            "work",
            target_hosts=["minipc", "matebook"],
            transport=transport,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "fleet_inventory_unavailable")
        self.assertEqual(result["unavailable_hosts"], ["matebook"])

    def test_rename_preview_returns_fingerprint_and_apply_rejects_drift(self):
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
            "visible_label": "Old Label",
        }
        def transport(alias, command, **kwargs):
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("inventory"):
                return ssh_result(inventory_payload(host_id, [{**profile}]))
            if command.endswith("capabilities"):
                return ssh_result({
                    "schema_version": 1,
                    "host": {"id": host_id},
                    "capabilities": {"safe_exact_rename_v1": True},
                })
            if command.endswith("rename-preview"):
                return ssh_result({
                    "ok": True,
                    "targets": [{
                        "browser": "chrome",
                        "email": "same@example.test",
                        "directory": "Profile 2",
                        "path": f"/private/{host_id}/Profile 2",
                        "current_label": profile["visible_label"],
                        "desired_label": "[TEST] same@example.test",
                    }],
                })
            return ssh_result({"ok": True, "renamed_count": 1})

        plan = profile_group_rename_plan(
            "chrome",
            "same@example.test",
            "test",
            target_hosts=["minipc"],
            transport=transport,
        )
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["plan_fingerprint"])
        self.assertEqual(len(plan["targets"]), 1)

        tampered = rename_profile_group(
            "chrome",
            "same@example.test",
            "test",
            target_hosts=["minipc"],
            expected_plan_fingerprint="0" * 64,
            transport=transport,
        )
        self.assertFalse(tampered["ok"])
        self.assertEqual(tampered["error_code"], "group_plan_changed")
