from fleet_test_support import *
from fleet_test_support import (
    _browser, _write_profile, _write_local_state, _load_browser_profiles,
    _browser_process_environment, _close_browsers, __version__,
)

class AuthSafetyTests(unittest.TestCase):
    """The rename/sync write path must never lose auth material.

    A torn or non-atomic ``Local State`` / ``Preferences`` write can make
    Chrome unable to decrypt Cookies/Login Data (os_crypt lost) or drop
    ``account_info``: the profile then appears logged out of Google on the
    next launch.  These tests pin the fail-closed browser gate and the
    post-write integrity guard with backup restore.
    """

    def test_rename_aborts_when_browser_still_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            _write_local_state(browser, {"Profile 45": "same@icloud.com"})
            _write_profile(browser, "Profile 45", "same@icloud.com")
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
            ), patch("chrome_profile_launcher.sync._browsers_running", return_value=["Google Chrome"]):
                receipt = rename_local_targets([target])

            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["renamed_count"], 0)
            state = json.loads((browser.user_data_dir / "Local State").read_text(encoding="utf-8"))
            self.assertEqual(state["profile"]["info_cache"]["Profile 45"]["name"], "same@icloud.com")

    def test_rename_preserves_os_crypt_and_account_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            (browser.user_data_dir).mkdir(parents=True, exist_ok=True)
            (browser.user_data_dir / "Local State").write_text(
                json.dumps(
                    {
                        "os_crypt": {"encrypted_key": "abc"},
                        "profile": {
                            "info_cache": {
                                "Profile 45": {"name": "same@icloud.com", "user_name": "same@icloud.com"}
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            profile_dir = browser.user_data_dir / "Profile 45"
            profile_dir.mkdir()
            (profile_dir / "Preferences").write_text(
                json.dumps(
                    {
                        "profile": {"name": "same@icloud.com"},
                        "account_info": [{"email": "same@icloud.com"}],
                    }
                ),
                encoding="utf-8",
            )
            target = {
                "browser": "chrome",
                "email": "same@icloud.com",
                "directory": "Profile 45",
                "path": str(profile_dir),
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
            self.assertIn("os_crypt", state)
            prefs = json.loads((profile_dir / "Preferences").read_text(encoding="utf-8"))
            self.assertEqual(prefs["profile"]["name"], "[TEST] same@icloud.com")
            self.assertEqual([a["email"] for a in prefs["account_info"]], ["same@icloud.com"])

    def test_guarded_local_state_write_restores_backup_when_os_crypt_lost(self) -> None:
        from chrome_profile_launcher.sync import _write_local_state_guarded

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "Local State"
            backup = root / "Local State.bak"
            original = {"os_crypt": {"encrypted_key": "abc"}, "profile": {}}
            path.write_text(json.dumps(original), encoding="utf-8")
            backup.write_text(json.dumps(original), encoding="utf-8")

            def buggy_atomic_write(target: Path, data: dict) -> None:
                # Simulates a writer that drops os_crypt (the failure mode that
                # makes Chrome unable to decrypt cookies on next launch).
                target.write_text(json.dumps({"profile": {}}), encoding="utf-8")

            with patch("chrome_profile_launcher.sync._write_json_atomic", side_effect=buggy_atomic_write):
                with self.assertRaises(OSError):
                    _write_local_state_guarded(path, {}, had_os_crypt=True, backup=backup)

            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("os_crypt", restored)

    def test_guarded_preferences_write_restores_backup_when_account_info_lost(self) -> None:
        from chrome_profile_launcher.sync import _write_preferences_guarded

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "Preferences"
            backup = root / "Preferences.bak"
            original = {"profile": {"name": "x"}, "account_info": [{"email": "a@b.com"}]}
            path.write_text(json.dumps(original), encoding="utf-8")
            backup.write_text(json.dumps(original), encoding="utf-8")

            def buggy_atomic_write(target: Path, data: dict) -> None:
                target.write_text(json.dumps({"profile": {"name": "x"}}), encoding="utf-8")

            with patch("chrome_profile_launcher.sync._write_json_atomic", side_effect=buggy_atomic_write):
                with self.assertRaises(OSError):
                    _write_preferences_guarded(
                        path, {}, account_emails_before=1, backup=backup
                    )

            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([a["email"] for a in restored["account_info"]], ["a@b.com"])




if __name__ == "__main__":
    unittest.main()
