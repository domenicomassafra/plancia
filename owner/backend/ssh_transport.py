"""Strict system-OpenSSH transport for governed dashboard adapters.

Profile Atlas and typed host actions use this transport. It relies on
operator-owned SSH aliases and existing known_hosts files; it never accepts or
installs a host key and is not itself an authorization layer.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FailureKind = Literal[
    "timeout",
    "host_key",
    "dns_failure",
    "unreachable",
    "missing_installation",
    "agent_failed",
    "nonzero_exit",
    "transport",
]

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOST_KEY_FAILURE_MARKERS = (
    "host key verification failed",
    "remote host identification has changed",
    "you have requested strict checking",
)
_DNS_FAILURE_MARKERS = (
    "could not resolve hostname",
    "name or service not known",
    "nodename nor servname provided",
)
_UNREACHABLE_MARKERS = (
    "connection timed out",
    "operation timed out",
    "connection refused",
    "no route to host",
    "network is unreachable",
    "host is down",
    "port 22: connection refused",
    "connect to host",
)
_MISSING_INSTALLATION_MARKERS = (
    "command not found",
    "no such file or directory",
    "not found",
)
_REDACTIONS = (
    (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
        r"\1[redacted]",
    ),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)\S+"
        ),
        r"\1[redacted]",
    ),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9._-]{12,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
        "[redacted]",
    ),
)


@dataclass(frozen=True, slots=True)
class SSHResult:
    alias: str
    exit_code: int
    stdout: str
    stderr: str
    failure: FailureKind | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.failure is None


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _safe_output(value: str | bytes | None, max_chars: int) -> str:
    text = _as_text(value)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    if len(text) <= max_chars:
        return text
    marker = "\n...[truncated]"
    if max_chars <= len(marker):
        return marker[:max_chars]
    return text[: max_chars - len(marker)] + marker


def run_ssh(
    alias: str,
    remote_command: str,
    *,
    config_path: str | None = None,
    input_text: str | None = None,
    timeout_seconds: float = 15,
    connect_timeout_seconds: int = 8,
    max_output_chars: int = 32_768,
) -> SSHResult:
    """Run one command through a preconfigured SSH alias, without a shell.

    Unknown or changed host keys fail closed through OpenSSH's normal
    ``known_hosts`` handling.  Returned stdout and stderr are redacted and
    bounded independently.  Callers remain responsible for choosing an
    allowlisted remote command; this transport is not an authorization layer.
    """
    if not _ALIAS_RE.fullmatch(alias):
        raise ValueError("alias must be a plain system SSH alias")
    if not isinstance(remote_command, str) or not remote_command.strip():
        raise ValueError("remote_command must be a non-empty string")
    if "\0" in remote_command:
        raise ValueError("remote_command cannot contain NUL")
    if config_path is not None and (
        not Path(config_path).is_absolute() or "\0" in config_path
    ):
        raise ValueError("config_path must be an absolute path")
    if input_text is not None and not isinstance(input_text, str):
        raise ValueError("input_text must be text")
    if timeout_seconds <= 0 or connect_timeout_seconds <= 0:
        raise ValueError("timeouts must be positive")
    if max_output_chars <= 0:
        raise ValueError("max_output_chars must be positive")

    argv = ["ssh"]
    if config_path is not None:
        argv.extend(["-F", config_path])
    argv.extend(
        [
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={connect_timeout_seconds}",
            alias,
            remote_command,
        ]
    )
    try:
        completed = subprocess.run(
            argv,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SSHResult(
            alias=alias,
            exit_code=124,
            stdout=_safe_output(exc.stdout, max_output_chars),
            stderr=_safe_output(exc.stderr, max_output_chars),
            failure="timeout",
        )
    except OSError as exc:
        return SSHResult(
            alias=alias,
            exit_code=127,
            stdout="",
            stderr=_safe_output(str(exc), max_output_chars),
            failure="transport",
        )

    stdout = _safe_output(completed.stdout, max_output_chars)
    stderr = _safe_output(completed.stderr, max_output_chars)
    combined = f"{completed.stdout}\n{completed.stderr}".lower()
    if completed.returncode == 0:
        failure: FailureKind | None = None
    elif any(marker in combined for marker in _HOST_KEY_FAILURE_MARKERS):
        failure = "host_key"
    elif any(marker in combined for marker in _DNS_FAILURE_MARKERS):
        failure = "dns_failure"
    elif any(marker in combined for marker in _UNREACHABLE_MARKERS):
        failure = "unreachable"
    elif completed.returncode == 127 or any(
        marker in combined for marker in _MISSING_INSTALLATION_MARKERS
    ):
        failure = "missing_installation"
    elif completed.returncode == 255:
        failure = "unreachable"
    else:
        failure = "agent_failed"
    return SSHResult(
        alias=alias,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        failure=failure,
    )
