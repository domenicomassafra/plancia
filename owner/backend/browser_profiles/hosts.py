"""Typed Profile Atlas adapter for the authenticated dashboard."""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.fleet2.registry import load_registry
from backend.ssh_transport import SSHResult, run_ssh

PROFILE_SCHEMA_VERSION = 2
PROFILE_AGENT_VERSION = "3.2.7"
SSH_CONFIG_PATH = str(Path.home() / ".ssh" / "config")
_BROWSERS = {"chrome", "edge"}
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,126}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
)
_CATEGORY_TAG_RE = re.compile(r"^\[([^\[\]\r\n]{1,32})\]$")
_DIRECTORY_RE = re.compile(r"^(?:Default|Profile(?: \d+)?)$")
_DELETABLE_DIRECTORY_RE = re.compile(r"^Profile(?: \d+)?$")
PROFILE_INVENTORY_TTL_SECONDS = 45


@dataclass(frozen=True, slots=True)
class ProfileHost:
    host_id: str
    label: str
    alias: str
    agent_command: str
    runtime: str
    image: str | None = None


def _resolve_profile_hosts(registry_loader=None) -> dict[str, ProfileHost]:
    commands_file = os.getenv("PLANCIA_PROFILE_COMMANDS_FILE")
    commands = json.loads(Path(commands_file).read_text(encoding="utf-8")) if commands_file else {}
    if not isinstance(commands, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.startswith("/")
        for key, value in commands.items()
    ):
        raise ValueError("profile commands must be a mapping of host ids to absolute paths")
    defaults = {
        "minipc": (
            "MiniPC",
            "minipc",
            commands.get("minipc", "chrome-profiles"),
            "linux",
            "minipc-owner.png",
        ),
        "mac-studio": (
            "Mac Studio",
            "macstudio",
            commands.get("mac-studio", "chrome-profiles"),
            "darwin",
            "mac-studio-owner.png",
        ),
        "macbook-air": (
            "MacBook Air",
            "macbookair",
            commands.get("macbook-air", "chrome-profiles"),
            "darwin",
            "macbook-air-owner.png",
        ),
        # Profile Atlas currently governs the live Omarchy browser runtime on
        # the physical MateBook. Windows is a separate runtime identity and is
        # never used as an implicit fallback for Linux evidence.
        "matebook": (
            "MateBook",
            "matebooklinux",
            commands.get("matebook", "chrome-profiles"),
            "linux",
            "matebook-owner.png",
        ),
    }
    # Registry load is part of the safety boundary. A malformed/missing fleet
    # definition must stop startup instead of silently falling back to stale
    # aliases that could target the wrong runtime identity.
    reg = (registry_loader or load_registry)()
    for host in reg.hosts:
        if host.id in defaults:
            label, alias, cmd, runtime, image = defaults[host.id]
            # Profile Atlas keeps the physical-device key ``matebook`` for
            # its 4-computer account view, but the only active transport is
            # the explicitly governed Omarchy runtime.
            if host.id != "matebook":
                alias = host.ssh_alias
                runtime = host.os
            defaults[host.id] = (label, alias, cmd, runtime, host.image or image)

    return {
        host_id: ProfileHost(
            host_id,
            label,
            alias,
            cmd,
            runtime,
            image,
        )
        for host_id, (label, alias, cmd, runtime, image) in defaults.items()
    }


PROFILE_HOSTS = _resolve_profile_hosts()


def _validated_target(
    host_id: str,
    browser: str,
    email: str,
    directory: str,
) -> dict[str, str]:
    if host_id not in PROFILE_HOSTS:
        raise ValueError("host is not registered for Profile Atlas")
    normalized_browser = browser.strip().casefold()
    normalized_email = email.strip().casefold()
    if normalized_browser not in _BROWSERS:
        raise ValueError("browser must be chrome or edge")
    if normalized_email and not _EMAIL_RE.fullmatch(normalized_email):
        raise ValueError("email identity is invalid")
    normalized_directory = directory.strip()
    if not _DIRECTORY_RE.fullmatch(normalized_directory):
        raise ValueError("profile directory is invalid")
    return {
        "browser": normalized_browser,
        "email": normalized_email,
        "directory": normalized_directory,
    }
