from __future__ import annotations

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.fleet2.registry import load_registry
from backend.ssh_transport import SSHResult, run_ssh

from .hosts import PROFILE_INVENTORY_TTL_SECONDS, ProfileHost, SSH_CONFIG_PATH, _BROWSERS, _EMAIL_RE, _validated_target

def profile_identity(
    host_id: str,
    browser: str,
    email: str,
    directory: str,
) -> dict[str, str]:
    """Return the normalized, exact identity used by Profile Atlas."""

    target = _validated_target(host_id, browser, email, directory)
    return {
        **target,
        "profile_key": (f"{target['browser']}:{target['email']}:{target['directory']}"),
    }


def profile_account_identity(browser: str, email: str) -> str:
    """Return the host-independent identity for one browser account.

    ``profile_key`` is deliberately host-local because a Chromium directory is
    a concrete deletion target.  This key has the opposite job: it is the
    canonical browser/account identity that joins the same signed-in profile
    across hosts, even when Chromium assigned different directory numbers.
    """

    normalized_browser = browser.strip().casefold()
    normalized_email = email.strip().casefold()
    if normalized_browser not in _BROWSERS:
        raise ValueError("browser must be chrome or edge")
    if not _EMAIL_RE.fullmatch(normalized_email):
        raise ValueError("email identity is invalid")
    return f"{normalized_browser}:{normalized_email}"


def _agent_result(
    host: ProfileHost,
    action: str,
    payload: dict | None = None,
    *,
    transport: Callable[..., SSHResult] = run_ssh,
) -> SSHResult:
    if host.host_id == "minipc" and transport is run_ssh:
        argv = [host.agent_command, "agent", action]
        try:
            completed = subprocess.run(
                argv,
                input=json.dumps(payload, separators=(",", ":")) if payload else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SSHResult(host.alias, 124, "", "", "timeout")
        except OSError:
            return SSHResult(host.alias, 127, "", "", "missing_installation")
        failure = None
        if completed.returncode != 0:
            failure = "missing_installation" if completed.returncode == 127 else "agent_failed"
        return SSHResult(
            alias=host.alias,
            exit_code=completed.returncode,
            stdout=completed.stdout[:256_000],
            stderr=completed.stderr[:256_000],
            failure=failure,
        )
    def invoke(alias: str, command: str) -> SSHResult:
        return transport(
            alias,
            f"{command} agent {action}",
            config_path=SSH_CONFIG_PATH,
            input_text=json.dumps(payload, separators=(",", ":")) if payload else None,
            timeout_seconds=30,
            connect_timeout_seconds=6,
            max_output_chars=256_000,
        )

    return invoke(host.alias, host.agent_command)


def _offline_inventory(host: ProfileHost, error_code: str) -> dict:
    return {
        "host": {"id": host.host_id, "label": host.label},
        "online": False,
        "profiles": [],
        "error_code": error_code,
    }


def _profile_status(inventory: dict) -> str:
    return "available" if inventory.get("online") else "unavailable"


def _with_inventory_freshness(
    fleet: dict, *, observed_at: float, freshness: str
) -> dict:
    """Attach bounded, UI-safe scan state without exposing host filesystem paths."""

    result = copy.deepcopy(fleet)
    result["observed_at"] = observed_at
    result["freshness"] = freshness
    for inventory in result.get("inventories", []):
        inventory["observed_at"] = observed_at
        inventory["freshness"] = freshness
        inventory["status"] = _profile_status(inventory)
        for profile in inventory.get("profiles", []):
            profile["status"] = "available"
            # The directory name is the protocol identity; an absolute path is
            # deliberately never forwarded to the dashboard.
            profile["path_redacted"] = True
    return result


class ProfileInventoryCache:
    """Small in-process cache that coalesces profile scans across dashboard tabs."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PROFILE_INVENTORY_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Condition()
        self._fleet: dict | None = None
        self._observed_at = 0.0
        self._scanning = False

    def get(self, *, refresh: bool, scanner: Callable[[], dict]) -> dict:
        now = self._clock()
        with self._lock:
            if (
                not refresh
                and self._fleet is not None
                and now - self._observed_at < self._ttl_seconds
            ):
                return _with_inventory_freshness(
                    self._fleet, observed_at=self._observed_at, freshness="cached"
                )
            if self._scanning:
                self._lock.wait_for(lambda: not self._scanning)
                if self._fleet is not None:
                    return _with_inventory_freshness(
                        self._fleet,
                        observed_at=self._observed_at,
                        freshness="cached",
                    )
            self._scanning = True

        try:
            fleet = scanner()
        except Exception:
            with self._lock:
                self._scanning = False
                self._lock.notify_all()
                if self._fleet is not None:
                    return _with_inventory_freshness(
                        self._fleet,
                        observed_at=self._observed_at,
                        freshness="stale",
                    )
            raise

        observed_at = self._clock()
        with self._lock:
            self._fleet = copy.deepcopy(fleet)
            self._observed_at = observed_at
            self._scanning = False
            self._lock.notify_all()
        return _with_inventory_freshness(
            fleet, observed_at=observed_at, freshness="fresh"
        )

    def invalidate(self) -> None:
        with self._lock:
            self._fleet = None
            self._observed_at = 0.0
