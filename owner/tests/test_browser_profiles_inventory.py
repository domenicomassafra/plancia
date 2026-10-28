from browser_profiles_test_support import *

class BrowserProfileInventoryTests(unittest.TestCase):
    def test_group_delete_rejects_changed_preview_plan_before_mutation(self):
        calls = []
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }

        def transport(alias, command, **_kwargs):
            calls.append((alias, command))
            host_id = ALIAS_TO_HOST[alias]
            if command.endswith("capabilities"):
                return ssh_result(
                    {
                        "schema_version": 1,
                        "host": {"id": host_id},
                        "capabilities": {"safe_exact_delete_v2": True},
                    }
                )
            return ssh_result(
                inventory_payload(
                    host_id,
                    [profile] if host_id == "matebook" else [],
                )
            )

        result = delete_profile_group(
            "chrome",
            "same@example.test",
            transport=transport,
            expected_plan_fingerprint="0" * 64,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "group_plan_changed")
        self.assertTrue(
            all(not command.endswith("delete") for _alias, command in calls)
        )

    def test_group_delete_supports_duplicate_email_directories_on_one_host(self):
        profile = {
            "browser": "chrome",
            "email": "same@example.test",
            "directory": "Profile 2",
        }
        state = {
            "minipc": [],
            "mac-studio": [],
            "macbook-air": [],
            "matebook": [profile, {**profile, "directory": "Profile 3"}],
        }
        deleted_directories = []

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
                        "capabilities": {"safe_exact_delete_v2": True},
                    }
                )
            if command.endswith("delete"):
                target = json.loads(kwargs["input_text"])["targets"][0]
                state[host_id] = [
                    item
                    for item in state[host_id]
                    if item["directory"] != target["directory"]
                ]
                deleted_directories.append((host_id, target["directory"]))
                return ssh_result(
                    {
                        "ok": True,
                        "deleted_count": 1,
                        "deleted": [target],
                        "safety": {
                            "browser_inactive": True,
                            "single_target": True,
                            "path_verified": True,
                        },
                    }
                )
            return SSHResult(alias, 2, "", "unexpected action", "nonzero_exit")

        result = delete_profile_group(
            "chrome", "same@example.test", transport=transport
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(
            deleted_directories,
            [("matebook", "Profile 2"), ("matebook", "Profile 3")],
        )
        self.assertEqual(state["matebook"], [])
        self.assertEqual(
            len(result["host_results"]["matebook"]["target_results"]),
            2,
        )

    def test_minipc_inventory_uses_fixed_local_agent_without_ssh(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "schema_version": PROFILE_SCHEMA_VERSION,
                    "agent_version": PROFILE_AGENT_VERSION,
                    "runtime": "linux",
                    "host": {"id": "minipc"},
                    "profiles": [],
                }
            ),
            stderr="",
        )
        with patch(
            "backend.browser_profiles.subprocess.run",
            return_value=completed,
        ) as local_run:
            inventory = inventory_host("minipc")

        self.assertTrue(inventory["online"])
        self.assertEqual(
            local_run.call_args.args[0],
            [
                "chrome-profiles",
                "agent",
                "inventory",
            ],
        )

    def test_inventory_strips_paths_and_rejects_wrong_identity(self):
        transport = lambda *_args, **_kwargs: ssh_result(
            {
                "schema_version": 1,
                "host": {"id": "minipc"},
                "profiles": [
                    {
                        "browser": "chrome",
                        "email": "Dodo@example.com",
                        "label": "Dodo",
                        "directory": "Profile 2",
                        "path": "/home/secret/Profile 2",
                        "search": "private metadata",
                    }
                ],
            }
        )

        inventory = inventory_host("minipc", transport=transport)

        self.assertTrue(inventory["online"])
        self.assertEqual(inventory["profiles"][0]["email"], "dodo@example.com")
        self.assertEqual(
            inventory["profiles"][0]["identity_key"], "chrome:dodo@example.com"
        )
        self.assertNotIn("path", inventory["profiles"][0])
        self.assertNotIn("search", inventory["profiles"][0])

    def test_inventory_rejects_wrong_agent_version_and_windows_runtime_for_omarchy(self):
        cases = (
            (
                "wrong-version",
                {
                    "schema_version": PROFILE_SCHEMA_VERSION,
                    "agent_version": "3.0.0",
                    "runtime": "linux",
                    "host": {"id": "matebook"},
                    "profiles": [],
                },
            ),
            (
                "windows-runtime",
                {
                    "schema_version": PROFILE_SCHEMA_VERSION,
                    "agent_version": PROFILE_AGENT_VERSION,
                    "runtime": "win32",
                    "host": {"id": "matebook"},
                    "profiles": [],
                },
            ),
        )
        for label, payload in cases:
            with self.subTest(label=label):
                result = SSHResult(
                    alias="fixture",
                    exit_code=0,
                    stdout=json.dumps(payload),
                    stderr="",
                )
                inventory = inventory_host(
                    "matebook",
                    transport=lambda *_a, _result=result, **_k: _result,
                )
                self.assertFalse(inventory["online"])
                self.assertEqual(inventory["error_code"], "invalid_agent_response")

    def test_inventory_keeps_profile_without_detected_email(self):
        transport = lambda *_args, **_kwargs: ssh_result(
            {
                "schema_version": 1,
                "host": {"id": "minipc"},
                "profiles": [
                    {
                        "browser": "chrome",
                        "email": "",
                        "label": "Persona 1",
                        "directory": "Profile 2",
                    }
                ],
            }
        )

        inventory = inventory_host("minipc", transport=transport)

        self.assertTrue(inventory["online"])
        self.assertEqual(inventory["profiles"][0]["email"], "")
        self.assertEqual(inventory["profiles"][0]["directory"], "Profile 2")

    def test_failed_inventory_error_codes_never_conflate_agent_and_network(self):
        # The unreachable/agent-error contract: a network timeout, a transport
        # failure, a remote agent exit error and an unparseable response are
        # four distinct states.  The first two are reachability failures; the
        # last two mean the host was reachable but the agent did not cooperate.
        cases = {
            "timeout": SSHResult("matebook", 124, "", "", "timeout"),
            "transport": SSHResult("matebook", 127, "", "ssh not found", "transport"),
            "nonzero_exit": SSHResult("matebook", 2, "", "chrome-profiles failed", "nonzero_exit"),
        }
        for expected, result in cases.items():
            inventory = inventory_host(
                "matebook",
                transport=lambda *_a, _result=result, **_k: _result,
            )
            self.assertEqual(inventory["online"], False)
            self.assertEqual(inventory["profiles"], [])
            self.assertEqual(
                inventory["error_code"],
                expected,
                f"{expected} must be reported verbatim so the UI can label it truthfully",
            )

        # An agent that answers with unparseable bytes fails as a response
        # error, never as a network timeout.
        bad_json = inventory_host(
            "matebook",
            transport=lambda *_a, **_k: ssh_result("not json"),
        )
        self.assertFalse(bad_json["online"])
        self.assertEqual(bad_json["error_code"], "invalid_agent_response")

    def test_open_uses_fixed_command_and_json_stdin(self):
        calls = []

        def transport(alias, command, **kwargs):
            calls.append((alias, command, kwargs))
            return ssh_result({"ok": True, "opened_count": 1})

        result = open_profile(
            "matebook",
            "edge",
            "Dodo+work@example.com",
            "Profile 7",
            transport=transport,
        )

        self.assertTrue(result["ok"])
        alias, command, kwargs = calls[0]
        self.assertEqual(alias, "matebooklinux")
        self.assertEqual(command, "chrome-profiles agent open")
        payload = json.loads(kwargs["input_text"])
        self.assertEqual(kwargs["config_path"], SSH_CONFIG_PATH)
        self.assertEqual(
            payload["targets"],
            [
                {
                    "browser": "edge",
                    "email": "dodo+work@example.com",
                    "directory": "Profile 7",
                }
            ],
        )
        self.assertNotIn("dodo+work", command)

    def test_delete_uses_exact_directory_identity_and_post_read(self):
        calls = []
        profile = {
            "browser": "chrome",
            "email": "dodo@example.com",
            "label": "Dodo",
            "directory": "Profile 2",
        }
        inventories = [
            inventory_payload("matebook", [profile]),
            inventory_payload("matebook", []),
        ]

        def transport(alias, command, **kwargs):
            calls.append((alias, command, kwargs))
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
                    "deleted": [profile],
                    "safety": {
                        "browser_inactive": True,
                        "single_target": True,
                        "path_verified": True,
                    },
                }
            )

        result = delete_profile(
            "matebook",
            "chrome",
            "Dodo@example.com",
            "Profile 2",
            transport=transport,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["post_read"], {"absence_proven": True})
        self.assertEqual(result["preimage"]["directory"], "Profile 2")
        self.assertEqual(
            [call[1].split()[-1] for call in calls],
            ["inventory", "capabilities", "delete", "inventory"],
        )
        payload = json.loads(calls[2][2]["input_text"])
        self.assertEqual(
            payload["targets"],
            [
                {
                    "browser": "chrome",
                    "email": "dodo@example.com",
                    "directory": "Profile 2",
                }
            ],
        )
