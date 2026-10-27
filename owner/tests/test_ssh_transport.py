import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ssh_transport import run_ssh


class SSHTransportTests(unittest.TestCase):
    @patch("backend.ssh_transport.subprocess.run")
    def test_success_uses_system_alias_and_strict_noninteractive_options(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="minipc\n", stderr=""
        )

        result = run_ssh(
            "minipc",
            "hostname",
            timeout_seconds=12,
            connect_timeout_seconds=5,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "minipc\n")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("ConnectTimeout=5", argv)
        self.assertEqual(argv[-2:], ["minipc", "hostname"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertIsNone(run.call_args.kwargs["input"])

    @patch("backend.ssh_transport.subprocess.run")
    def test_typed_stdin_is_forwarded_without_shell(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"ok":true}\n', stderr=""
        )

        result = run_ssh(
            "matebook",
            "chrome-profiles agent open",
            input_text='{"schema_version":1,"targets":[]}',
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            run.call_args.kwargs["input"],
            '{"schema_version":1,"targets":[]}',
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("backend.ssh_transport.subprocess.run")
    def test_explicit_config_bypasses_host_global_ssh_includes(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )

        result = run_ssh(
            "mac-studio",
            "hostname",
            config_path="/home/test-user/.ssh/config",
        )

        self.assertTrue(result.ok)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0:3], ["ssh", "-F", "/home/test-user/.ssh/config"])

    @patch("backend.ssh_transport.subprocess.run")
    def test_timeout_returns_redacted_partial_output(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            cmd=["ssh"],
            timeout=3,
            output=b"token=topsecret partial",
            stderr=b"timed out",
        )

        result = run_ssh("minipc", "hostname", timeout_seconds=3)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "timeout")
        self.assertEqual(result.exit_code, 124)
        self.assertNotIn("topsecret", result.stdout)
        self.assertIn("[redacted]", result.stdout)

    @patch("backend.ssh_transport.subprocess.run")
    def test_nonzero_exit_is_preserved(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=7, stdout="", stderr="command failed"
        )

        result = run_ssh("matebook", "exit 7")

        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "agent_failed")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stderr, "command failed")

    @patch("backend.ssh_transport.subprocess.run")
    def test_dns_failure_is_classified(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=255,
            stdout="",
            stderr="ssh: Could not resolve hostname mac-studio: No address associated with hostname",
        )

        result = run_ssh("macstudio", "hostname")

        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "dns_failure")
        self.assertEqual(result.exit_code, 255)

    @patch("backend.ssh_transport.subprocess.run")
    def test_unreachable_is_classified(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=255,
            stdout="",
            stderr="ssh: connect to host matebook port 22: Connection timed out",
        )

        result = run_ssh("matebook", "hostname")

        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "unreachable")
        self.assertEqual(result.exit_code, 255)

    @patch("backend.ssh_transport.subprocess.run")
    def test_missing_installation_is_classified(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=127,
            stdout="",
            stderr="bash: /Users/test-user/.local/bin/chrome-profiles: No such file or directory",
        )

        result = run_ssh("macstudio", "chrome-profiles agent inventory")

        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "missing_installation")
        self.assertEqual(result.exit_code, 127)

    @patch("backend.ssh_transport.subprocess.run")
    def test_host_key_failure_is_classified_fail_closed(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=255,
            stdout="",
            stderr=(
                "No ED25519 host key is known for minipc and you have requested "
                "strict checking. Host key verification failed."
            ),
        )

        result = run_ssh("minipc", "hostname")

        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "host_key")
        self.assertEqual(result.exit_code, 255)

    @patch("backend.ssh_transport.subprocess.run")
    def test_output_is_redacted_and_bounded(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Authorization: Bearer secret-value " + ("x" * 200),
            stderr="api_key=another-secret",
        )

        result = run_ssh("macstudio", "status", max_output_chars=64)

        self.assertLessEqual(len(result.stdout), 64)
        self.assertLessEqual(len(result.stderr), 64)
        self.assertNotIn("secret-value", result.stdout)
        self.assertNotIn("another-secret", result.stderr)
        self.assertIn("[redacted]", result.stdout)
        self.assertIn("[redacted]", result.stderr)
        self.assertIn("[truncated]", result.stdout)

    @patch("backend.ssh_transport.subprocess.run")
    def test_invalid_alias_is_rejected_before_subprocess(self, run):
        with self.assertRaises(ValueError):
            run_ssh("-oProxyCommand=bad", "hostname")

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
