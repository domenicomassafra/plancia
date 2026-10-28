import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.auth import (
    ALLOWED_ORIGINS_ENV,
    CSRF_COOKIE,
    CSRF_HEADER,
    OWNER_LOGIN_ENV,
    TAILSCALE_LOGIN_HEADER,
)
from backend.browser_profiles import (
    PROFILE_AGENT_VERSION,
    PROFILE_HOSTS,
    PROFILE_SCHEMA_VERSION,
    SSH_CONFIG_PATH,
    ProfileInventoryCache,
    _resolve_profile_hosts,
    canonical_profile_category,
    canonical_profile_label,
    delete_profile,
    delete_profile_group,
    inventory_host,
    open_profile,
    profile_group_delete_plan,
    profile_group_rename_plan,
    profile_name_sync_plan,
    rename_profile_group,
    scan_profile_fleet,
    sync_profile_names,
)
from backend.fleet2 import api as api_module
from backend.ssh_transport import SSHResult

OWNER = "owner@example.test"
ORIGIN = "https://fleet.example.test"

ALIAS_TO_HOST = {
    "minipc": "minipc",
    "macstudio": "mac-studio",
    "mac-studio": "mac-studio",
    "macbookair": "macbook-air",
    "macbook-air": "macbook-air",
    "matebook": "matebook",
    "matebooklinux": "matebook",
}


def agent_runtime(host_id: str) -> str:
    return "darwin" if host_id in {"mac-studio", "macbook-air"} else "linux"


def ssh_result(payload: dict | str) -> SSHResult:
    if isinstance(payload, str):
        return SSHResult(
            alias="fixture",
            exit_code=0,
            stdout=payload,
            stderr="",
        )
    payload = dict(payload)
    host = payload.get("host")
    host_id = str(host.get("id", "")) if isinstance(host, dict) else ""
    if host_id and "schema_version" in payload:
        payload["schema_version"] = PROFILE_SCHEMA_VERSION
        payload.setdefault("agent_version", PROFILE_AGENT_VERSION)
        payload.setdefault("runtime", agent_runtime(host_id))
    return SSHResult(
        alias="fixture",
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
    )


def inventory_payload(host_id: str, profiles: list[dict]) -> dict:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "agent_version": PROFILE_AGENT_VERSION,
        "runtime": agent_runtime(host_id),
        "host": {"id": host_id},
        "profiles": profiles,
    }


# Shared imports form the compatibility surface for the extracted test suites.
__all__ = (
    "json", "os", "subprocess", "sys", "threading", "unittest", "Path", "patch",
    "TestClient", "ALLOWED_ORIGINS_ENV", "CSRF_COOKIE", "CSRF_HEADER",
    "OWNER_LOGIN_ENV", "TAILSCALE_LOGIN_HEADER", "PROFILE_AGENT_VERSION",
    "PROFILE_HOSTS", "PROFILE_SCHEMA_VERSION", "SSH_CONFIG_PATH",
    "ProfileInventoryCache", "_resolve_profile_hosts",
    "canonical_profile_category", "canonical_profile_label", "delete_profile",
    "delete_profile_group", "inventory_host", "open_profile",
    "profile_group_delete_plan", "profile_group_rename_plan",
    "profile_name_sync_plan", "rename_profile_group", "scan_profile_fleet",
    "sync_profile_names", "api_module", "SSHResult", "OWNER", "ORIGIN",
    "ALIAS_TO_HOST", "agent_runtime", "ssh_result", "inventory_payload",
)
