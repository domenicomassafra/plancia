from browser_profiles_test_support import *
from backend.fleet2 import profiles_api as api_module

class BrowserProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                OWNER_LOGIN_ENV: OWNER,
                ALLOWED_ORIGINS_ENV: ORIGIN,
                "FLEETPROBE_TOKEN": "test-token",
            },
        )
        self.env.start()
        from backend import server as server_module

        self.app = server_module.create_app()
        self.app.state.session_store.clear()
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.identity = {TAILSCALE_LOGIN_HEADER: OWNER}
        self.assertEqual(self.client.get("/", headers=self.identity).status_code, 200)
        self.headers = {
            **self.identity,
            "Origin": ORIGIN,
            CSRF_HEADER: self.client.cookies.get(CSRF_COOKIE),
        }

    def tearDown(self):
        self.app.state.session_store.clear()
        self.env.stop()

    def test_inventory_requires_session_and_returns_typed_payload(self):
        anonymous = TestClient(self.app, base_url=ORIGIN).get("/api/profiles")
        self.assertEqual(anonymous.status_code, 401, anonymous.text)

        with patch.object(
            api_module,
            "profile_inventory_fleet",
            return_value={"schema_version": 1, "inventories": [], "accounts": []},
        ):
            response = self.client.get("/api/profiles", headers=self.identity)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["schema_version"], 1)

    def test_inventory_includes_persisted_alias_and_category(self):
        profile = {
            "browser": "chrome",
            "email": "owner@example.test",
            "label": "Owner",
            "directory": "Profile 2",
            "profile_key": "chrome:owner@example.test:Profile 2",
        }
        self.app.state.store.set_profile_metadata(
            "mac-studio",
            profile["profile_key"],
            "Gmail principale",
            "Personale",
        )
        inventory = {
            "host": {"id": "mac-studio", "label": "Mac Studio"},
            "online": True,
            "profiles": [profile.copy()],
        }
        account = {
            "email": profile["email"],
            "label": profile["label"],
            "browsers": ["chrome"],
            "instances": [{**profile, "host": inventory["host"]}],
        }
        with patch.object(
            api_module,
            "profile_inventory_fleet",
            return_value={
                "schema_version": 1,
                "inventories": [inventory],
                "accounts": [account],
            },
        ):
            response = self.client.get("/api/profiles", headers=self.identity)

        self.assertEqual(response.status_code, 200, response.text)
        decorated = response.json()["inventories"][0]["profiles"][0]
        self.assertEqual(decorated["alias"], "Gmail principale")
        self.assertEqual(decorated["category"], "[PERSONALE]")
        self.assertEqual(decorated["policy_label"], "[PERSONALE] owner@example.test")
        self.assertFalse(decorated["name_policy_ok"])

    def test_inventory_passes_refresh_to_bounded_cache(self):
        with patch.object(
            api_module,
            "profile_inventory_fleet",
            return_value={"schema_version": 1, "inventories": [], "accounts": []},
        ) as inventory:
            response = self.client.get(
                "/api/profiles?refresh=true", headers=self.identity
            )

        self.assertEqual(response.status_code, 200, response.text)
        inventory.assert_called_once_with(refresh=True)

    def test_name_sync_preview_is_read_only_and_redacts_private_plan(self):
        with patch.object(
            api_module,
            "profile_name_sync_plan",
            return_value={
                "ok": True,
                "status": "ready",
                "target_count": 1,
                "plan_fingerprint": "a" * 64,
                "targets": [{
                    "host_id": "mac-studio",
                    "host_label": "Mac Studio",
                    "browser": "chrome",
                    "email": "owner@example.test",
                    "directory": "Profile 3",
                    "label": "owner@example.test",
                    "desired_label": "[TEST] owner@example.test",
                }],
                "category_updates": [{
                    "host_id": "mac-studio",
                    "profile_key": "chrome:owner@example.test:Profile 3",
                    "category": "[TEST]",
                }],
                "_private_previews": {"mac-studio": [{"path": "/private/Profile 3"}]},
                "unavailable_hosts": [],
                "complete": False,
            },
        ) as planned:
            response = self.client.get("/api/profiles/names/preview", headers=self.identity)

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["target_count"], 1)
        self.assertNotIn("category_updates", data)
        self.assertNotIn("_private_previews", data)
        self.assertNotIn("/private/", response.text)
        planned.assert_called_once()

    def test_name_sync_requires_csrf_confirmation_and_persists_category_authority(self):
        payload = {"confirmed": True, "plan_fingerprint": "b" * 64}
        with patch.object(
            api_module,
            "sync_profile_names",
            return_value={
                "ok": True,
                "status": "synced",
                "renamed_count": 1,
                "plan_fingerprint": "b" * 64,
                "targets": [{
                    "host_id": "mac-studio",
                    "browser": "chrome",
                    "email": "owner@example.test",
                    "directory": "Profile 3",
                }],
                "category_updates": [{
                    "host_id": "mac-studio",
                    "profile_key": "chrome:owner@example.test:Profile 3",
                    "category": "[AICONIC]",
                }],
                "unavailable_hosts": [],
                "recovery_class": "host_local_rename_backup",
            },
        ) as synced:
            denied = self.client.post(
                "/api/profiles/names/sync", json=payload, headers=self.identity
            )
            self.assertEqual(denied.status_code, 403, denied.text)
            synced.assert_not_called()

            not_confirmed = self.client.post(
                "/api/profiles/names/sync",
                json={**payload, "confirmed": False},
                headers=self.headers,
            )
            self.assertEqual(not_confirmed.status_code, 400, not_confirmed.text)
            synced.assert_not_called()

            allowed = self.client.post(
                "/api/profiles/names/sync", json=payload, headers=self.headers
            )

        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertTrue(allowed.json()["receipt_id"])
        synced.assert_called_once()
        metadata = self.app.state.store.profile_metadata()[
            ("mac-studio", "chrome:owner@example.test:Profile 3")
        ]
        self.assertEqual(metadata["category"], "[AICONIC]")

    def test_open_requires_csrf_and_calls_typed_adapter(self):
        payload = {
            "host_id": "mac-studio",
            "browser": "chrome",
            "email": "owner@example.test",
            "directory": "Profile 3",
        }
        with patch("backend.fleet2.api.open_profile", return_value={"ok": True}) as opened:
            denied = self.client.post(
                "/api/profiles/open", json=payload, headers=self.identity
            )
            self.assertEqual(denied.status_code, 403, denied.text)
            opened.assert_not_called()

            allowed = self.client.post(
                "/api/profiles/open", json=payload, headers=self.headers
            )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        opened.assert_called_once_with(
            "mac-studio", "chrome", "owner@example.test", "Profile 3"
        )

    def test_single_delete_requires_explicit_second_step(self):
        payload = {
            "host_id": "mac-studio",
            "browser": "chrome",
            "email": "",
            "directory": "Profile 3",
            "confirmed": False,
        }
        with patch.object(api_module, "delete_profile") as deleted:
            denied = self.client.post(
                "/api/profiles/delete", json=payload, headers=self.headers
            )
            self.assertEqual(denied.status_code, 400, denied.text)
            deleted.assert_not_called()

            deleted.return_value = {"ok": True, "deleted_count": 1}
            payload["confirmed"] = True
            response = self.client.post(
                "/api/profiles/delete", json=payload, headers=self.headers
            )

        self.assertEqual(response.status_code, 200, response.text)
        deleted.assert_called_once_with("mac-studio", "chrome", "", "Profile 3")

    def test_delete_does_not_require_retyping_the_email(self):
        payload = {
            "host_id": "mac-studio",
            "browser": "chrome",
            "email": "owner@example.test",
            "directory": "Profile 3",
            "confirmed": True,
        }
        with patch.object(
            api_module, "delete_profile", return_value={"ok": True, "deleted_count": 1}
        ) as deleted:
            right = self.client.post(
                "/api/profiles/delete", json=payload, headers=self.headers
            )
        self.assertEqual(right.status_code, 200, right.text)
        self.assertTrue(right.json()["receipt_id"])
        deleted.assert_called_once_with(
            "mac-studio", "chrome", "owner@example.test", "Profile 3"
        )

    def test_group_delete_requires_explicit_second_step_and_uses_server_resolution(self):
        payload = {
            "browser": "chrome",
            "email": "owner@example.test",
            "confirmed": False,
            "plan_fingerprint": "a" * 64,
        }
        with patch.object(
            api_module,
            "delete_profile_group",
            return_value={"ok": True, "status": "deleted", "deleted_count": 3},
        ) as deleted:
            denied = self.client.post(
                "/api/profiles/delete-group", json=payload, headers=self.headers
            )
            self.assertEqual(denied.status_code, 400, denied.text)
            deleted.assert_not_called()

            payload["confirmed"] = True
            allowed = self.client.post(
                "/api/profiles/delete-group", json=payload, headers=self.headers
            )

        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertTrue(allowed.json()["receipt_id"])
        deleted.assert_called_once_with(
            "chrome",
            "owner@example.test",
            expected_plan_fingerprint="a" * 64,
        )

    def test_delete_refuses_default_profile_before_adapter_call(self):
        payload = {
            "host_id": "mac-studio",
            "browser": "chrome",
            "email": "owner@example.test",
            "directory": "Default",
            "confirmed": True,
        }
        with patch.object(api_module, "delete_profile") as deleted:
            response = self.client.post(
                "/api/profiles/delete", json=payload, headers=self.headers
            )

        # The adapter owns the Default guard; this request must surface its
        # fail-closed error instead of turning it into a generic delete.
        self.assertEqual(response.status_code, 400, response.text)
        deleted.assert_not_called()

    def test_profile_metadata_update_is_csrf_protected_and_persistent(self):
        payload = {
            "host_id": "mac-studio",
            "browser": "chrome",
            "email": "owner@example.test",
            "directory": "Profile 3",
            "alias": "Amministrazione",
            "category": "Lavoro",
        }
        denied = self.client.put(
            "/api/profiles/metadata",
            json=payload,
            headers=self.identity,
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        allowed = self.client.put(
            "/api/profiles/metadata",
            json=payload,
            headers=self.headers,
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertTrue(allowed.json()["receipt_id"])
        metadata = self.app.state.store.profile_metadata()[
            ("mac-studio", "chrome:owner@example.test:Profile 3")
        ]
        self.assertEqual(metadata["alias"], "Amministrazione")
        self.assertEqual(metadata["category"], "Lavoro")

    @patch("backend.fleet2.api.open_profile")
    def test_open_profile_emits_redacted_attributable_receipt(self, opened):
        opened.return_value = {
            "ok": True,
            "host_id": "mac-studio",
            "opened_count": 1,
            "browser": "chrome",
            "email": "owner@example.test",
        }
        payload = {
            "host_id": "mac-studio",
            "browser": "chrome",
            "email": "owner@example.test",
            "directory": "Profile 3",
        }
        response = self.client.post(
            "/api/profiles/open",
            json=payload,
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data.get("receipt_id"))

        receipts = self.app.state.store.receipts()
        match = next((r for r in receipts if r["id"] == data["receipt_id"]), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["action"], "profile-open")
        self.assertEqual(match["host_id"], "mac-studio")
        self.assertEqual(match["outcome"], "succeeded")
        self.assertNotIn("owner@example.test", json.dumps(match["payload"]))
        self.assertNotIn("/Users/", json.dumps(match["payload"]))
        self.assertTrue(match["payload"].get("account_hash"))


if __name__ == "__main__":
    unittest.main()
