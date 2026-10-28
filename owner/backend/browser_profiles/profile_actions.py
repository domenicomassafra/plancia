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

from .hosts import PROFILE_HOSTS, PROFILE_SCHEMA_VERSION, _DELETABLE_DIRECTORY_RE, _validated_target
from .transport import _agent_result, profile_identity
from .inventory import invalidate_profile_inventory_cache, inventory_host
from .identity import _agent_supports_safe_delete, _delete_preimage, _delete_receipt_matches, _inventory_directory_profile, _inventory_exact_profile, _inventory_identity_profiles, _safe_current_targets

def _delete_agent_failure(result: SSHResult) -> str:
    """Translate a safe agent rejection without treating it as transport loss."""

    if result.failure not in ("nonzero_exit", "agent_failed"):
        return result.failure or "agent_failed"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "agent_delete_rejected"
    if isinstance(payload, dict) and payload.get("error_code") == "browser_active":
        return "browser_active"
    return "agent_delete_rejected"


def open_profile(
    host_id: str,
    browser: str,
    email: str,
    directory: str,
    *,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    target = _validated_target(host_id, browser, email, directory)
    host = PROFILE_HOSTS[host_id]
    result = _agent_result(
        host,
        "open",
        {"schema_version": PROFILE_SCHEMA_VERSION, "targets": [target]},
        transport=transport,
    )
    if not result.ok:
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": result.failure or "agent_failed",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "host_id": host_id, "error_code": "invalid_agent_response"}
    if not isinstance(payload, dict) or not payload.get("ok"):
        return {"ok": False, "host_id": host_id, "error_code": "profile_not_opened"}
    return {
        "ok": True,
        "host_id": host_id,
        "opened_count": int(payload.get("opened_count", 0)),
        "browser": target["browser"],
        "email": target["email"],
    }


def delete_profile(
    host_id: str,
    browser: str,
    email: str,
    directory: str,
    *,
    transport: Callable[..., SSHResult] = run_ssh,
    require_identity_absent: bool = True,
) -> dict:
    """Delete one exact profile directory through the host-local agent.

    The directory is part of the identity and is validated against the
    allowlist. Both sides do a fresh inventory: the local agent refuses an
    unsafe target, while this adapter records a preimage and proves absence
    with a post-read before it reports success.
    """

    identity = profile_identity(host_id, browser, email, directory)
    if not _DELETABLE_DIRECTORY_RE.fullmatch(identity["directory"]):
        raise ValueError("the default or special browser profile cannot be deleted")
    host = PROFILE_HOSTS[host_id]
    pre_inventory = inventory_host(host_id, transport=transport)
    if not pre_inventory.get("online"):
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "inventory_unavailable",
            "recovery_class": "not_started",
        }
    preimage = _inventory_exact_profile(pre_inventory, identity)
    if preimage is None:
        current_identity_targets = _inventory_identity_profiles(pre_inventory, identity)
        if current_identity_targets:
            return {
                "ok": False,
                "host_id": host_id,
                "error_code": "stale_inventory",
                "current_targets": _safe_current_targets(
                    host_id, current_identity_targets
                ),
                "recovery_class": "not_started",
            }
        conflicting = _inventory_directory_profile(pre_inventory, identity)
        if conflicting is None:
            return {
                "ok": True,
                "host_id": host_id,
                "status": "already_absent",
                "browser": identity["browser"],
                "email": identity["email"],
                "directory": identity["directory"],
                "deleted_count": 0,
                "preimage": None,
                "recovery_class": "not_needed",
                "post_read": {"absence_proven": True},
            }
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "stale_inventory",
            "current_targets": _safe_current_targets(host_id, [conflicting]),
            "recovery_class": "not_started",
        }
    safe_preimage = _delete_preimage(identity, preimage)
    capabilities = _agent_result(host, "capabilities", transport=transport)
    if not _agent_supports_safe_delete(host, capabilities):
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "safe_delete_agent_required",
            "preimage": safe_preimage,
            "recovery_class": "not_started",
        }
    result = _agent_result(
        host,
        "delete",
        {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "targets": [
                {
                    "browser": identity["browser"],
                    "email": identity["email"],
                    "directory": identity["directory"],
                }
            ],
        },
        transport=transport,
    )
    if not result.ok:
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": _delete_agent_failure(result),
            "preimage": safe_preimage,
            "recovery_class": "not_started",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "invalid_agent_response",
            "preimage": safe_preimage,
            "recovery_class": "manual_local_state_backup",
        }
    if not isinstance(payload, dict) or not payload.get("ok"):
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "profile_not_deleted",
            "error": str(payload.get("error", ""))[:240]
            if isinstance(payload, dict)
            else "",
            "preimage": safe_preimage,
            "recovery_class": "manual_local_state_backup",
        }
    if not _delete_receipt_matches(host_id, identity, payload):
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "unproven_delete_result",
            "preimage": safe_preimage,
            "recovery_class": "manual_local_state_backup",
        }
    post_inventory = inventory_host(host_id, transport=transport)
    if not post_inventory.get("online"):
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "post_delete_unproven",
            "preimage": safe_preimage,
            "recovery_class": "manual_local_state_backup",
        }
    remaining_identity = _inventory_identity_profiles(post_inventory, identity)
    if remaining_identity and require_identity_absent:
        exact_still_present = (
            _inventory_exact_profile(post_inventory, identity) is not None
        )
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "post_delete_still_present"
            if exact_still_present
            else "post_delete_identity_present",
            "preimage": safe_preimage,
            "current_targets": _safe_current_targets(host_id, remaining_identity),
            "recovery_class": "manual_local_state_backup",
        }
    if _inventory_directory_profile(post_inventory, identity) is not None:
        return {
            "ok": False,
            "host_id": host_id,
            "error_code": "post_delete_conflict",
            "preimage": safe_preimage,
            "recovery_class": "manual_local_state_backup",
        }
    invalidate_profile_inventory_cache()
    return {
        "ok": True,
        "host_id": host_id,
        "status": "deleted",
        "browser": identity["browser"],
        "email": identity["email"],
        "directory": identity["directory"],
        "deleted_count": 1,
        "preimage": safe_preimage,
        "recovery_class": "manual_local_state_backup",
        "post_read": {"absence_proven": True},
    }
