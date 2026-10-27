from fleet_test_support import *
from fleet_test_support import (
    _browser, _write_profile, _write_local_state, _load_browser_profiles,
    _browser_process_environment, _close_browsers, __version__,
)

class OpenProfileTests(unittest.TestCase):
    def test_linux_open_uses_live_user_session_environment(self) -> None:
        completed = subprocess.CompletedProcess(
            ["systemctl", "--user", "show-environment"],
            0,
            stdout=(
                "DISPLAY=:0\n"
                "WAYLAND_DISPLAY=wayland-1\n"
                "XDG_RUNTIME_DIR=/run/user/1000\n"
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n"
                "SECRET_DO_NOT_COPY=private\n"
            ),
            stderr="",
        )
        with patch("chrome_profile_launcher.fleet.sys.platform", "linux"), patch.dict(
            os.environ,
            {"WAYLAND_DISPLAY": "wayland-0"},
            clear=True,
        ), patch(
            "chrome_profile_launcher.fleet.subprocess.run",
            return_value=completed,
        ):
            env = _browser_process_environment()

        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-1")
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(
            env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus"
        )
        self.assertNotIn("SECRET_DO_NOT_COPY", env)

    def test_open_rescans_and_dispatches_only_exact_browser_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chrome = _browser(Path(tmp), "chrome")
            edge = _browser(Path(tmp), "edge")
            _write_local_state(chrome, {"Profile 2": "same@example.com"})
            _write_profile(chrome, "Profile 2", "same@example.com")
            _write_local_state(edge, {"Profile 8": "same@example.com"})
            _write_profile(edge, "Profile 8", "same@example.com")

            with (
                patch(
                    "chrome_profile_launcher.fleet.browser_configs",
                    return_value=[chrome, edge],
                ),
                patch(
                    "chrome_profile_launcher.fleet.BrowserConfig.detect_exe",
                    return_value="/bin/browser",
                ),
                patch("chrome_profile_launcher.fleet.subprocess.Popen") as popen,
                patch("chrome_profile_launcher.fleet._browser_process_environment", return_value={}),
                patch(
                    "chrome_profile_launcher.fleet._active_browser_processes",
                    return_value=["Microsoft Edge"],
                ),
                patch("chrome_profile_launcher.fleet.time.sleep"),
            ):
                receipt = open_local_targets(
                    [{"browser": "edge", "email": "SAME@example.com"}]
                )

            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["opened_count"], 1)
            self.assertEqual(receipt["opened"][0]["directory"], "Profile 8")
            argv = popen.call_args.args[0]
            self.assertEqual(argv[0], "/bin/browser")
            self.assertIn("--profile-directory=Profile 8", argv)
            self.assertNotIn("Profile 2", argv)
            self.assertNotIn("shell", popen.call_args.kwargs)
            self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_open_reports_missing_browser_process_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chrome = _browser(Path(tmp), "chrome")
            _write_local_state(chrome, {"Profile 2": "same@example.com"})
            _write_profile(chrome, "Profile 2", "same@example.com")
            with (
                patch(
                    "chrome_profile_launcher.fleet.browser_configs",
                    return_value=[chrome],
                ),
                patch(
                    "chrome_profile_launcher.fleet.BrowserConfig.detect_exe",
                    return_value="/bin/browser",
                ),
                patch("chrome_profile_launcher.fleet.subprocess.Popen"),
                patch("chrome_profile_launcher.fleet._browser_process_environment", return_value={}),
                patch(
                    "chrome_profile_launcher.fleet._active_browser_processes",
                    return_value=[],
                ),
                patch("chrome_profile_launcher.fleet.time.sleep"),
            ):
                receipt = open_local_targets(
                    [
                        {
                            "browser": "chrome",
                            "email": "same@example.com",
                            "directory": "Profile 2",
                        }
                    ]
                )

            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["error_code"], "browser_launch_failed")
            self.assertEqual(receipt["opened_count"], 0)

    def test_open_fails_closed_when_identity_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chrome = _browser(Path(tmp), "chrome")
            _write_local_state(chrome, {"Profile 2": "other@example.com"})
            _write_profile(chrome, "Profile 2", "other@example.com")
            with (
                patch(
                    "chrome_profile_launcher.fleet.browser_configs",
                    return_value=[chrome],
                ),
                patch("chrome_profile_launcher.fleet.subprocess.Popen") as popen,
            ):
                receipt = open_local_targets(
                    [{"browser": "chrome", "email": "missing@example.com"}]
                )

            self.assertFalse(receipt["ok"])
            popen.assert_not_called()
