from fleet_test_support import *
from fleet_test_support import (
    _browser, _write_profile, _write_local_state, _load_browser_profiles,
    _browser_process_environment, _close_browsers, __version__,
)

class FleetIdentityTests(unittest.TestCase):
    def test_agent_inventory_reports_protocol_version_and_runtime_identity(self) -> None:
        with (
            patch("chrome_profile_launcher.fleet.current_host_id", return_value="mac-studio"),
            patch("chrome_profile_launcher.fleet.browser_configs", return_value=[]),
        ):
            payload = local_inventory()

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["schema_version"], AGENT_PROTOCOL_VERSION)
        self.assertEqual(payload["agent_version"], "3.2.7")
        self.assertEqual(payload["agent_version"], __version__)
        self.assertEqual(payload["runtime"], sys.platform)
        self.assertEqual(payload["host"]["id"], "mac-studio")

    def test_agent_capabilities_reports_same_protocol_identity(self) -> None:
        with (
            patch("chrome_profile_launcher.fleet.current_host_id", return_value="mac-studio"),
            patch("builtins.print") as printer,
        ):
            rc = cli_main(["agent", "capabilities"])

        self.assertEqual(rc, 0)
        payload = json.loads(printer.call_args.args[0])
        self.assertEqual(payload["schema_version"], AGENT_PROTOCOL_VERSION)
        self.assertEqual(payload["agent_version"], __version__)
        self.assertEqual(payload["runtime"], sys.platform)
        self.assertEqual(payload["host"]["id"], "mac-studio")

    def test_macbook_air_hostname_resolves_to_distinct_fleet_host(self) -> None:
        with (
            patch.dict(os.environ, {"CHROME_PROFILES_HOST_ID": ""}),
            patch(
                "chrome_profile_launcher.fleet.socket.gethostname",
                return_value="macbook-air-dodo",
            ),
        ):
            self.assertEqual(current_host_id(), "macbook-air")

    def test_dodomarchy_linux_hostname_resolves_to_air_fleet_id(self) -> None:
        with (
            patch.dict(os.environ, {"CHROME_PROFILES_HOST_ID": ""}),
            patch("chrome_profile_launcher.fleet.socket.gethostname", return_value="dodomarchy"),
        ):
            self.assertEqual(current_host_id(), "macbook-air")

    def test_email_identity_is_case_insensitive(self) -> None:
        self.assertEqual(normalize_email("  Dodo@iCloud.com "), "dodo@icloud.com")

    def test_inventory_uses_account_email_when_chromium_display_names_vary(self) -> None:
        """A sanitized three-host fixture must retain one canonical identity.

        Chromium may put the account in ``user_name`` on one host, nest it in
        a human display label on another, or omit it from the visible name.
        Those presentation differences must never turn a known account into
        an email-less installation.
        """

        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            browser.user_data_dir.mkdir(parents=True)
            profile_dir = browser.user_data_dir / "Profile 7"
            profile_dir.mkdir()
            (browser.user_data_dir / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {
                                "Profile 7": {
                                    "name": "Domenico (same@example.com)",
                                    "user_name": "Domenico (same@example.com)",
                                    "gaia_name": "Domenico",
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (profile_dir / "Preferences").write_text(
                json.dumps(
                    {
                        "profile": {"name": "Domenico"},
                        "account_info": [{"email": "SAME@example.com"}],
                    }
                ),
                encoding="utf-8",
            )

            profiles = _load_browser_profiles(browser)

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].email, "same@example.com")

    def test_inventory_leaves_conflicting_identity_metadata_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp))
            browser.user_data_dir.mkdir(parents=True)
            profile_dir = browser.user_data_dir / "Profile 8"
            profile_dir.mkdir()
            (browser.user_data_dir / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {
                                "Profile 8": {
                                    "user_name": "other@example.com",
                                    "name": "Domenico (other@example.com)",
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (profile_dir / "Preferences").write_text(
                json.dumps({"account_info": [{"email": "same@example.com"}]}),
                encoding="utf-8",
            )

            profiles = _load_browser_profiles(browser)

        self.assertEqual(profiles[0].email, "")

    def test_multi_account_profile_uses_profile_level_identity(self) -> None:
        """A signed-in profile with a secondary account must stay identified.

        Chromium/Edge store every signed-in account in ``account_info`` and the
        first entry is not necessarily the profile's own account.  Reading only
        that entry used to disagree with the profile-level identity in
        ``Local State``, so the profile silently became email-less and its
        name/shortcut tag drift could never be reconciled.
        """

        with tempfile.TemporaryDirectory() as tmp:
            browser = _browser(Path(tmp), "edge")
            browser.user_data_dir.mkdir(parents=True)
            profile_dir = browser.user_data_dir / "Default"
            profile_dir.mkdir()
            (browser.user_data_dir / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {
                                "Default": {
                                    "name": "[A] primary@example.com",
                                    "shortcut_name": "[TEST] primary@example.com",
                                    "user_name": "primary@example.com",
                                    "gaia_name": "Primary Person",
                                    "gaia_given_name": "[TEST] primary@example.com",
                                    "is_using_default_name": False,
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (profile_dir / "Preferences").write_text(
                json.dumps(
                    {
                        "profile": {"name": "[A] primary@example.com"},
                        "account_info": [
                            {"email": "secondary@example.com"},
                            {"email": "primary@example.com"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            profiles = _load_browser_profiles(browser)

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].email, "primary@example.com")
        # The secondary account must not leak into the primary identity.
        self.assertEqual(profiles[0].policy_label, "[A] primary@example.com")
        # Tag drift between name and shortcut is now visible instead of hidden.
        self.assertFalse(profiles[0].name_policy_ok)

    def test_inventory_never_infers_browser_account_from_saved_login_data(self) -> None:
        """A credential stored in the profile is not evidence of browser sign-in.

        Chrome and Edge profiles can contain saved website usernames even when
        the browser profile itself has no signed-in account.  Treating a lone
        saved email as the profile identity can merge unrelated profiles and
        route rename/delete actions to the wrong logical account.
        """

        for browser_key in ("chrome", "edge"):
            with self.subTest(browser=browser_key), tempfile.TemporaryDirectory() as tmp:
                browser = _browser(Path(tmp), browser_key)
                browser.user_data_dir.mkdir(parents=True)
                profile_dir = browser.user_data_dir / "Profile 3"
                profile_dir.mkdir()
                (browser.user_data_dir / "Local State").write_text(
                    json.dumps(
                        {
                            "profile": {
                                "info_cache": {
                                    "Profile 3": {
                                        "name": "Persona 3",
                                        "user_name": "",
                                    }
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                (profile_dir / "Preferences").write_text(
                    json.dumps({"profile": {"name": "Persona 3"}, "account_info": []}),
                    encoding="utf-8",
                )
                with sqlite3.connect(profile_dir / "Login Data") as conn:
                    conn.execute("CREATE TABLE logins (username_value TEXT)")
                    conn.execute(
                        "INSERT INTO logins(username_value) VALUES (?)",
                        ("saved-login@example.test",),
                    )

                profiles = _load_browser_profiles(browser)

                self.assertEqual(len(profiles), 1)
                self.assertEqual(profiles[0].email, "")
