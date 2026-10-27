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

from .hosts import PROFILE_AGENT_VERSION, PROFILE_SCHEMA_VERSION, ProfileHost, _CATEGORY_TAG_RE, _EMAIL_RE
from .transport import profile_identity

def _inventory_exact_profile(inventory: dict, identity: dict[str, str]) -> dict | None:
    for profile in inventory.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        if (
            profile.get("browser") == identity["browser"]
            and profile.get("email") == identity["email"]
            and profile.get("directory") == identity["directory"]
        ):
            return profile
    return None


def _inventory_directory_profile(
    inventory: dict, identity: dict[str, str]
) -> dict | None:
    for profile in inventory.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        if (
            profile.get("browser") == identity["browser"]
            and profile.get("directory") == identity["directory"]
        ):
            return profile
    return None


def _inventory_identity_profiles(
    inventory: dict, identity: dict[str, str]
) -> list[dict]:
    """Find the same browser/account independently from its local directory."""

    return [
        profile
        for profile in inventory.get("profiles", [])
        if isinstance(profile, dict)
        and profile.get("browser") == identity["browser"]
        and profile.get("email") == identity["email"]
    ]


def _delete_preimage(identity: dict[str, str], profile: dict) -> dict[str, str]:
    return {
        "browser": identity["browser"],
        "email": identity["email"],
        "directory": identity["directory"],
        "label": str(profile.get("label", ""))[:160],
    }


def _safe_current_targets(host_id: str, profiles: list[dict]) -> list[dict[str, str]]:
    """Return exact current targets without exposing absolute host paths."""

    targets: list[dict[str, str]] = []
    for profile in profiles:
        try:
            identity = profile_identity(
                host_id,
                str(profile.get("browser", "")),
                str(profile.get("email", "")),
                str(profile.get("directory", "")),
            )
        except ValueError:
            continue
        targets.append(_delete_preimage(identity, profile))
    return targets


def _delete_receipt_matches(
    host_id: str, identity: dict[str, str], payload: dict
) -> bool:
    deleted = payload.get("deleted")
    if not isinstance(deleted, list) or len(deleted) != 1:
        return False
    safety = payload.get("safety")
    if not isinstance(safety, dict) or not all(
        safety.get(key) is True
        for key in ("browser_inactive", "single_target", "path_verified")
    ):
        return False
    try:
        deleted_count = int(payload.get("deleted_count", 0))
    except (TypeError, ValueError):
        return False
    if deleted_count != 1:
        return False
    try:
        received = profile_identity(
            host_id,
            str(deleted[0].get("browser", "")),
            str(deleted[0].get("email", "")),
            str(deleted[0].get("directory", "")),
        )
    except (AttributeError, ValueError):
        return False
    return received["profile_key"] == identity["profile_key"]


def _agent_supports_safe_delete(host: ProfileHost, result: SSHResult) -> bool:
    if not result.ok:
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("schema_version") == PROFILE_SCHEMA_VERSION
        and payload.get("agent_version") == PROFILE_AGENT_VERSION
        and payload.get("runtime") == host.runtime
        and isinstance(payload.get("host"), dict)
        and payload["host"].get("id") == host.host_id
        and isinstance(payload.get("capabilities"), dict)
        and payload["capabilities"].get("safe_exact_delete_v2") is True
    )


def _agent_supports_safe_rename(host: ProfileHost, result: SSHResult) -> bool:
    if not result.ok:
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("schema_version") == PROFILE_SCHEMA_VERSION
        and payload.get("agent_version") == PROFILE_AGENT_VERSION
        and payload.get("runtime") == host.runtime
        and isinstance(payload.get("host"), dict)
        and payload["host"].get("id") == host.host_id
        and isinstance(payload.get("capabilities"), dict)
        and payload["capabilities"].get("safe_exact_rename_v1") is True
    )


def canonical_profile_label(category: str, email: str) -> str:
    """Build the only browser-visible name Profile Atlas is allowed to write."""

    normalized_email = email.strip().casefold()
    if not _EMAIL_RE.fullmatch(normalized_email):
        raise ValueError("a valid email is required for profile naming")
    raw_category = category.strip()
    if raw_category and not raw_category.startswith("["):
        raw_category = f"[{raw_category}]"
    match = _CATEGORY_TAG_RE.fullmatch(raw_category)
    if match is None or not match.group(1).strip():
        raise ValueError("category must be a short prefix such as [AICONIC]")
    tag = f"[{match.group(1).strip().upper()}]"
    return f"{tag} {normalized_email}"


def canonical_profile_category(category: str, email: str) -> str:
    """Return the normalized bracket tag without splitting multi-word tags."""

    normalized_email = email.strip().casefold()
    if not _EMAIL_RE.fullmatch(normalized_email):
        raise ValueError("a valid email is required for profile naming")
    raw_category = category.strip()
    if raw_category and not raw_category.startswith("["):
        raw_category = f"[{raw_category}]"
    match = _CATEGORY_TAG_RE.fullmatch(raw_category)
    if match is None or not match.group(1).strip():
        raise ValueError("category must be a short prefix such as [AICONIC]")
    return f"[{match.group(1).strip().upper()}]"


def _category_from_profile_label(label: object, email: str) -> str:
    """Extract a valid [TAG] only when the label belongs to the same email."""

    if not isinstance(label, str):
        return ""
    normalized_email = email.strip().casefold()
    match = re.fullmatch(r"(\[[^\[\]\r\n]{1,32}\])\s+(.+)", label.strip())
    if match is None or match.group(2).strip().casefold() != normalized_email:
        return ""
    try:
        return canonical_profile_category(match.group(1), normalized_email)
    except ValueError:
        return ""


def _profile_metadata_key(profile: dict) -> str:
    return (
        f"{str(profile.get('browser', '')).casefold()}:"
        f"{str(profile.get('email', '')).strip().casefold()}:"
        f"{str(profile.get('directory', ''))}"
    )


def _resolve_category_candidate(categories: set[str]) -> tuple[str, list[str]]:
    """Resolve one category, treating [TEST] as the fallback rather than a peer.

    A single intentional non-TEST tag wins over stale/fallback [TEST] copies.
    Two different intentional tags remain a hard conflict and are never guessed.
    """

    intentional = sorted(category for category in categories if category != "[TEST]")
    if len(intentional) > 1:
        return "", intentional
    if intentional:
        return intentional[0], []
    if "[TEST]" in categories:
        return "[TEST]", []
    return "", []
