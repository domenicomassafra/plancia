from fleet_test_support import *
from fleet_test_support import (
    _browser, _write_profile, _write_local_state, _load_browser_profiles,
    _browser_process_environment, _close_browsers, __version__,
)

class LocalDeletionTests(unittest.TestCase):
    def test_delete_rescans_and_removes_only_exact_email_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(
                browser,
                {
                    "Profile 32": "other@icloud.com",
                    "Profile 45": "same@icloud.com",
                },
            )
            _write_profile(browser, "Profile 32", "other@icloud.com")
            _write_profile(browser, "Profile 45", "same@icloud.com")

            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
            }

            with patch("chrome_profile_launcher.fleet.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.fleet._active_browser_processes", return_value=[]
            ):
                receipt = delete_local_targets([target])

            self.assertTrue(receipt["ok"])
            self.assertFalse((browser.user_data_dir / "Profile 45").exists())
            self.assertTrue((browser.user_data_dir / "Profile 32").is_dir())
            state = json.loads((browser.user_data_dir / "Local State").read_text(encoding="utf-8"))
            self.assertNotIn("Profile 45", state["profile"]["info_cache"])
            self.assertIn("Profile 32", state["profile"]["info_cache"])

    def test_delete_aborts_if_previewed_path_no_longer_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            stale_target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 32",
                "path": str(browser.user_data_dir / "Profile 32"),
            }

            with patch("chrome_profile_launcher.fleet.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.fleet._active_browser_processes", return_value=[]
            ):
                receipt = delete_local_targets([stale_target])

            self.assertFalse(receipt["ok"])
            self.assertTrue((browser.user_data_dir / "Profile 45").is_dir())

    def test_delete_pathless_target_removes_only_exact_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(
                browser,
                {"Profile 32": "same@icloud.com", "Profile 45": "same@icloud.com"},
            )
            _write_profile(browser, "Profile 32", "same@icloud.com")
            _write_profile(browser, "Profile 45", "same@icloud.com")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
            }

            with patch(
                "chrome_profile_launcher.fleet.browser_configs", return_value=[browser]
            ), patch("chrome_profile_launcher.fleet._active_browser_processes", return_value=[]):
                receipt = delete_local_targets([target])

            self.assertTrue(receipt["ok"], receipt)
            self.assertFalse((browser.user_data_dir / "Profile 45").exists())
            self.assertTrue((browser.user_data_dir / "Profile 32").is_dir())

    def test_delete_refuses_default_and_active_browser_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Default": "same@icloud.com"})
            _write_profile(browser, "Default", "same@icloud.com")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Default",
                "path": str(browser.user_data_dir / "Default"),
            }

            with patch("chrome_profile_launcher.fleet.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.fleet._active_browser_processes", return_value=["chrome"]
            ) as activity:
                receipt = delete_local_targets([target])

            self.assertFalse(receipt["ok"])
            self.assertTrue((browser.user_data_dir / "Default").is_dir())
            activity.assert_not_called()

    def test_delete_closes_active_target_browser_before_removing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
            }

            with patch("chrome_profile_launcher.fleet.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.fleet._active_browser_processes",
                side_effect=[["chrome"], []],
            ) as activity, patch(
                "chrome_profile_launcher.fleet._close_browser_processes"
            ) as close:
                receipt = delete_local_targets([target])

            self.assertTrue(receipt["ok"], receipt)
            self.assertFalse((browser.user_data_dir / "Profile 45").exists())
            close.assert_called_once_with({"chrome"})
            self.assertEqual(activity.call_count, 2)

    def test_delete_ignores_active_other_browser_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
            }

            with patch(
                "chrome_profile_launcher.fleet.browser_configs", return_value=[browser]
            ), patch(
                "chrome_profile_launcher.fleet._active_browser_processes",
                return_value=["Microsoft Edge"],
            ), patch(
                "chrome_profile_launcher.fleet._close_browser_processes"
            ) as close:
                receipt = delete_local_targets([target])

            self.assertTrue(receipt["ok"], receipt)
            close.assert_not_called()

    def test_delete_stays_fail_closed_when_target_browser_does_not_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
            }

            with patch(
                "chrome_profile_launcher.fleet.browser_configs", return_value=[browser]
            ), patch(
                "chrome_profile_launcher.fleet._active_browser_processes",
                return_value=["chrome"],
            ), patch("chrome_profile_launcher.fleet._close_browser_processes"):
                receipt = delete_local_targets([target])

            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["error_code"], "browser_close_failed")
            self.assertTrue((browser.user_data_dir / "Profile 45").is_dir())

    def test_delete_refuses_symlinked_profile_and_preserves_outside_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            browser = _browser(root)
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            outside = root / "outside"
            outside.mkdir()
            (outside / "Preferences").write_text(
                json.dumps({"profile": {"name": "same@icloud.com"}, "account_info": [{"email": "same@icloud.com"}]}),
                encoding="utf-8",
            )
            (browser.user_data_dir / "Profile 45").symlink_to(outside, target_is_directory=True)
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
            }

            with patch("chrome_profile_launcher.fleet.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.fleet._active_browser_processes", return_value=[]
            ):
                receipt = delete_local_targets([target])

            self.assertFalse(receipt["ok"])
            self.assertTrue(outside.is_dir())

    def test_delete_restores_local_state_after_partial_remove_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(browser.user_data_dir / "Profile 45"),
            }

            with patch("chrome_profile_launcher.fleet.browser_configs", return_value=[browser]), patch(
                "chrome_profile_launcher.fleet._active_browser_processes", return_value=[]
            ), patch("chrome_profile_launcher.fleet.shutil.rmtree", side_effect=OSError("read only")):
                receipt = delete_local_targets([target])

            self.assertFalse(receipt["ok"])
            self.assertTrue((browser.user_data_dir / "Profile 45").is_dir())
            state = json.loads((browser.user_data_dir / "Local State").read_text(encoding="utf-8"))
            self.assertIn("Profile 45", state["profile"]["info_cache"])
