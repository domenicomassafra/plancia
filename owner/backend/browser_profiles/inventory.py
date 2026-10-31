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

from .hosts import PROFILE_AGENT_VERSION, PROFILE_HOSTS, PROFILE_SCHEMA_VERSION, ProfileHost, _BROWSERS, _DIRECTORY_RE, _EMAIL_RE
from .transport import ProfileInventoryCache, _agent_result, _offline_inventory, profile_account_identity

_inventory_cache = ProfileInventoryCache()


def _profile_runtime_label(host: ProfileHost) -> str:
    if host.host_id == "matebook":
        return "Omarchy"
    if host.host_id == "macbook-air" and host.runtime == "linux":
        return "Omarchy"
    if host.runtime == "darwin":
        return "macOS"
    if host.host_id == "minipc":
        return "Ubuntu"
    return ""


def _parse_inventory(host: ProfileHost, raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("profile agent returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError("profile agent schema is unsupported")
    if payload.get("agent_version") != PROFILE_AGENT_VERSION:
        raise ValueError("profile agent version is unsupported")
    if payload.get("runtime") != host.runtime:
        raise ValueError("profile agent runtime does not match target host")
    reported_host = payload.get("host")
    if not isinstance(reported_host, dict) or reported_host.get("id") != host.host_id:
        raise ValueError("profile agent identity does not match target host")

    profiles: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_profile in payload.get("profiles", []):
        if not isinstance(raw_profile, dict):
            continue
        browser = str(raw_profile.get("browser", "")).casefold()
        email = str(raw_profile.get("email", "")).strip().casefold()
        directory = str(raw_profile.get("directory", ""))
        if (
            browser not in _BROWSERS
            or (email and not _EMAIL_RE.fullmatch(email))
            or not _DIRECTORY_RE.fullmatch(directory)
        ):
            continue
        key = (browser, email, directory)
        if key in seen:
            continue
        seen.add(key)
        profiles.append(
            {
                "browser": browser,
                "email": email,
                # Keep the raw local label separate from the fleet policy
                # projection. ``name_policy_ok`` carries the strict native
                # picker invariant, including provider/GAIA prefix drift.
                "label": str(
                    raw_profile.get(
                        "label",
                        raw_profile.get("policy_label", raw_profile.get("visible_label", "")),
                    )
                )[:160],
                "policy_label": str(
                    raw_profile.get("policy_label", raw_profile.get("label", ""))
                )[:160],
                "name_policy_ok": bool(raw_profile.get("name_policy_ok", False)),
                "directory": directory,
                "profile_key": f"{browser}:{email}:{directory}",
                # This is intentionally independent from ``directory`` and
                # host. A different Profile N on each PC remains one account.
                "identity_key": profile_account_identity(browser, email)
                if email
                else "",
            }
        )
    profiles.sort(key=lambda item: (item["email"], item["browser"], item["directory"]))
    return {
        "host": {
            "id": host.host_id,
            "label": host.label,
            "runtime": _profile_runtime_label(host),
            "agent_version": PROFILE_AGENT_VERSION,
            "image": host.image,
        },
        "online": True,
        "profiles": profiles,
    }


def inventory_host(
    host_id: str,
    *,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    host = PROFILE_HOSTS.get(host_id)
    if host is None:
        raise ValueError("host is not registered for Profile Atlas")
    result = _agent_result(host, "inventory", transport=transport)
    if not result.ok:
        return _offline_inventory(host, result.failure or "agent_failed")
    try:
        return _parse_inventory(host, result.stdout)
    except ValueError:
        return _offline_inventory(host, "invalid_agent_response")


def _group_accounts(inventories: list[dict]) -> list[dict]:
    accounts: dict[str, dict] = {}
    for inventory in inventories:
        if not inventory.get("online"):
            continue
        host = inventory["host"]
        for profile in inventory["profiles"]:
            account_key = profile.get("identity_key") or (
                f"unknown:{host['id']}:{profile['browser']}:{profile['directory']}"
            )
            account = accounts.setdefault(
                account_key,
                {
                    "identity_key": profile.get("identity_key", ""),
                    "browser": profile["browser"],
                    "email": profile["email"],
                    "label": profile["label"] or profile["email"],
                    "browsers": set(),
                    "instances": [],
                },
            )
            account["browsers"].add(profile["browser"])
            account["instances"].append({**profile, "host": host})
    result = []
    for account in accounts.values():
        account["browsers"] = sorted(account["browsers"])
        # The canonical inventory already contains the per-host instances.
        # Keeping a second copy here doubled the response size for no UI use.
        account["instance_count"] = len(account["instances"])
        del account["instances"]
        result.append(account)
    return sorted(result, key=lambda item: (item["browser"], item["email"]))


def scan_profile_fleet(
    *,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PROFILE_HOSTS)) as pool:
        futures = {
            pool.submit(inventory_host, host_id, transport=transport): host_id
            for host_id in PROFILE_HOSTS
        }
        inventories = [
            future.result() for future in concurrent.futures.as_completed(futures)
        ]
    inventories.sort(key=lambda item: item["host"]["label"])
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "inventories": inventories,
        "accounts": _group_accounts(inventories),
    }


def profile_inventory_fleet(*, refresh: bool = False) -> dict:
    """Return the canonical fleet inventory with cache/freshness semantics."""

    return _inventory_cache.get(refresh=refresh, scanner=scan_profile_fleet)


def invalidate_profile_inventory_cache() -> None:
    _inventory_cache.invalidate()
