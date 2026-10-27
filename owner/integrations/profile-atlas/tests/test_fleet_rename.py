from fleet_test_support import *
from fleet_test_support import (
    _browser, _write_profile, _write_local_state, _load_browser_profiles,
    _browser_process_environment, _close_browsers, __version__,
)

class RenameTests(unittest.TestCase):
    def test_linux_browser_close_never_matches_chrome_profiles_command_line(self) -> None:
        with patch("chrome_profile_launcher.sync.sys.platform", "linux"), patch(
            "chrome_profile_launcher.sync.subprocess.run"
        ) as run, patch("chrome_profile_launcher.sync.time.sleep"), patch(
            "chrome_profile_launcher.sync._browsers_running", return_value=[]
        ):
            _close_browsers()

        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][0:2], ["pkill", "-x"])

    def test_edge_rename_does_not_close_or_block_on_unrelated_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp), "edge")
            _write_local_state(browser, {"Profile 1": "same@outlook.test"})
            _write_profile(browser, "Profile 1", "same@outlook.test")
            target = {
                "browser": "edge",
                "email": "same@outlook.test",
                "directory": "Profile 1",
                "path": str(browser.user_data_dir / "Profile 1"),
                "current_label": "same@outlook.test",
                "desired_label": "[TEST] same@outlook.test",
            }

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.sync._close_browsers", return_value=[]
            ) as close_browsers, patch(
                "chrome_profile_launcher.sync._browsers_running", return_value=[]
            ) as browsers_running:
                receipt = rename_local_targets([target])

            self.assertTrue(receipt["ok"])
            close_browsers.assert_called_once_with({"edge"})
            browsers_running.assert_called_once_with({"edge"})


    def test_rename_updates_exact_email_profile_and_keeps_other_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 32": "other@icloud.com", "Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 32", "other@icloud.com")
            _write_profile(browser, "Profile 45", "same@icloud.com")
            local_state_path = browser.user_data_dir / "Local State"
            seeded_state = json.loads(local_state_path.read_text(encoding="utf-8"))
            seeded_entry = seeded_state["profile"]["info_cache"]["Profile 45"]
            seeded_entry.update(
                {
                    "gaia_name": "Server Person",
                    "gaia_given_name": "Server",
                    "is_using_default_name": False,
                }
            )
            local_state_path.write_text(json.dumps(seeded_state), encoding="utf-8")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
                "current_label": "same@icloud.com",
                "desired_label": "[TEST] same@icloud.com",
            }

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.sync._close_browsers", return_value=[]
            ), patch("chrome_profile_launcher.sync._browsers_running", return_value=[]):
                receipt = rename_local_targets([target])

            self.assertTrue(receipt["ok"])
            state = json.loads((browser.user_data_dir / "Local State").read_text(encoding="utf-8"))
            self.assertEqual(state["profile"]["info_cache"]["Profile 45"]["name"], "[TEST] same@icloud.com")
            self.assertEqual(state["profile"]["info_cache"]["Profile 45"]["shortcut_name"], "[TEST] same@icloud.com")
            self.assertEqual(state["profile"]["info_cache"]["Profile 45"]["gaia_name"], "Server Person")
            self.assertEqual(state["profile"]["info_cache"]["Profile 45"]["gaia_given_name"], "Server")
            self.assertFalse(state["profile"]["info_cache"]["Profile 45"]["is_using_default_name"])
            self.assertEqual(state["profile"]["info_cache"]["Profile 32"]["name"], "other@icloud.com")
            prefs = json.loads((browser.user_data_dir / "Profile 45" / "Preferences").read_text(encoding="utf-8"))
            self.assertEqual(prefs["profile"]["name"], "[TEST] same@icloud.com")

            reloaded = next(
                profile
                for profile in _load_browser_profiles(browser)
                if profile.directory == "Profile 45"
            )
            self.assertEqual(reloaded.label, "[TEST] same@icloud.com")
            self.assertEqual(reloaded.visible_label, "Server ([TEST] same@icloud.com)")
            self.assertTrue(reloaded.name_policy_ok)

    def test_provider_name_refresh_does_not_create_local_name_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            desired = "[TEST] same@icloud.com"

            local_state_path = browser.user_data_dir / "Local State"
            state = json.loads(local_state_path.read_text(encoding="utf-8"))
            entry = state["profile"]["info_cache"]["Profile 45"]
            entry.update(
                {
                    "name": desired,
                    "shortcut_name": desired,
                    "gaia_name": "Provider Full Name",
                    "gaia_given_name": "Provider",
                    "is_using_default_name": False,
                }
            )
            local_state_path.write_text(json.dumps(state), encoding="utf-8")
            prefs_path = browser.user_data_dir / "Profile 45" / "Preferences"
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            prefs["profile"]["name"] = desired
            prefs_path.write_text(json.dumps(prefs), encoding="utf-8")

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[browser]):
                preview = preview_local_rename_targets(
                    [
                        {
                            "browser": "chrome",
                            "email": "same@icloud.com",
                            "directory": "Profile 45",
                            "desired_label": desired,
                        }
                    ]
                )

            self.assertTrue(preview["ok"])
            self.assertEqual(preview["rename_count"], 0)
            reloaded = next(
                profile
                for profile in _load_browser_profiles(browser)
                if profile.directory == "Profile 45"
            )
            self.assertEqual(reloaded.label, desired)
            self.assertEqual(reloaded.visible_label, f"Provider ({desired})")
            self.assertTrue(reloaded.name_policy_ok)

    def test_rename_scope_is_browser_plus_email_not_email_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chrome = _browser(root, "chrome")
            edge = _browser(root, "edge")
            _write_local_state(chrome, {"Profile 2": "same@example.com"})
            _write_profile(chrome, "Profile 2", "same@example.com")
            _write_local_state(edge, {"Profile 7": "same@example.com"})
            _write_profile(edge, "Profile 7", "same@example.com")

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[chrome, edge]), patch(
                "chrome_profile_launcher.sync._close_browsers", return_value=[]
            ), patch("chrome_profile_launcher.sync._browsers_running", return_value=[]):
                preview = preview_local_rename_targets(
                    [{
                        "browser": "chrome",
                        "email": "same@example.com",
                        "directory": "Profile 2",
                        "desired_label": "[TEST] same@example.com",
                    }]
                )
                self.assertTrue(preview["ok"], preview)
                receipt = rename_local_targets(preview["targets"])

            self.assertTrue(receipt["ok"], receipt)
            chrome_state = json.loads((chrome.user_data_dir / "Local State").read_text(encoding="utf-8"))
            edge_state = json.loads((edge.user_data_dir / "Local State").read_text(encoding="utf-8"))
            self.assertEqual(
                chrome_state["profile"]["info_cache"]["Profile 2"]["name"],
                "[TEST] same@example.com",
            )
            self.assertEqual(
                edge_state["profile"]["info_cache"]["Profile 7"]["name"],
                "same@example.com",
            )

    def test_rename_preview_keeps_private_path_local_until_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[browser]):
                preview = preview_local_rename_targets(
                    [
                        {
                            "browser": "chrome",
                            "email": "same@icloud.com",
                            "directory": "Profile 45",
                            "desired_label": "[TEST] same@icloud.com",
                        }
                    ]
                )

            self.assertTrue(preview["ok"])
            self.assertEqual(preview["rename_count"], 1)
            self.assertEqual(preview["targets"][0]["desired_label"], "[TEST] same@icloud.com")
            self.assertIn("path", preview["targets"][0])

    def test_rename_preview_accepts_multiword_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[browser]):
                preview = preview_local_rename_targets(
                    [
                        {
                            "browser": "chrome",
                            "email": "same@icloud.com",
                            "directory": "Profile 45",
                            "desired_label": "[OLD PROJECT] same@icloud.com",
                        }
                    ]
                )

            self.assertTrue(preview["ok"], preview)
            self.assertEqual(preview["rename_count"], 1)
            self.assertEqual(
                preview["targets"][0]["desired_label"],
                "[OLD PROJECT] same@icloud.com",
            )

    def test_rename_preview_rejects_tag_the_reader_cannot_keep(self) -> None:
        """Apply must agree with the read-side canonical rule.

        A lowercase or over-long tag would be written as-is but rejected or
        uppercased by ``Profile.policy_label``, so the profile could never
        reach ``name_policy_ok`` and would be re-targeted on every sync.
        """

        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            long_tag = "L" * 33

            with patch("chrome_profile_launcher.sync.browser_configs", return_value=[browser]):
                for desired in (
                    "[test] same@icloud.com",
                    f"[{long_tag}] same@icloud.com",
                ):
                    with self.subTest(desired=desired):
                        preview = preview_local_rename_targets(
                            [
                                {
                                    "browser": "chrome",
                                    "email": "same@icloud.com",
                                    "directory": "Profile 45",
                                    "desired_label": desired,
                                }
                            ]
                        )
                        self.assertFalse(preview["ok"])
                        self.assertEqual(preview["targets"], [])

                canonical = preview_local_rename_targets(
                    [
                        {
                            "browser": "chrome",
                            "email": "same@icloud.com",
                            "directory": "Profile 45",
                            "desired_label": "[TEST] same@icloud.com",
                        }
                    ]
                )

            self.assertTrue(canonical["ok"])
            self.assertEqual(canonical["rename_count"], 1)
