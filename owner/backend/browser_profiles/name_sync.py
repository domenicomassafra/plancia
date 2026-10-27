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

from .hosts import PROFILE_HOSTS, PROFILE_SCHEMA_VERSION
from .transport import _agent_result
from .inventory import invalidate_profile_inventory_cache, scan_profile_fleet
from .name_plans import profile_name_sync_plan

def sync_profile_names(
    metadata: dict[tuple[str, str], dict],
    *,
    expected_plan_fingerprint: str,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    """Apply the current fleet-wide naming plan after explicit UI confirmation."""

    plan = profile_name_sync_plan(metadata, transport=transport)
    if not plan.get("ok"):
        return plan
    if expected_plan_fingerprint != plan.get("plan_fingerprint"):
        return {
            "ok": False,
            "status": "blocked",
            "error_code": "group_plan_changed",
            "targets": plan.get("targets", []),
            "unavailable_hosts": plan.get("unavailable_hosts", []),
            "recovery_class": "not_started",
        }

    targets = plan["targets"]
    if not targets:
        return {
            **{key: value for key, value in plan.items() if not key.startswith("_")},
            "status": "converged" if not plan["unavailable_hosts"] else "partial_offline",
            "renamed_count": 0,
        }

    def apply_host(item: tuple[str, list[dict]]) -> tuple[str, dict]:
        host_id, private_targets = item
        if not private_targets:
            return host_id, {"ok": True, "status": "already_clean", "renamed_count": 0}
        result = _agent_result(
            PROFILE_HOSTS[host_id],
            "rename",
            {"schema_version": PROFILE_SCHEMA_VERSION, "targets": private_targets},
            transport=transport,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        if not result.ok or not isinstance(payload, dict) or not payload.get("ok"):
            return host_id, {
                "ok": False,
                "status": "failed",
                "error_code": "rename_apply_rejected",
                "renamed_count": int(payload.get("renamed_count", 0)) if isinstance(payload, dict) else 0,
            }
        return host_id, {
            "ok": True,
            "status": "renamed",
            "renamed_count": int(payload.get("renamed_count", 0)),
        }

    private_previews = plan["_private_previews"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(private_previews))) as pool:
        host_results = dict(pool.map(apply_host, sorted(private_previews.items())))
    failed_hosts = [host_id for host_id, result in host_results.items() if not result.get("ok")]
    if failed_hosts:
        return {
            "ok": False,
            "status": "partial",
            "error_code": "group_rename_partial",
            "targets": targets,
            "host_results": host_results,
            "unavailable_hosts": plan["unavailable_hosts"],
            "category_updates": plan["category_updates"],
            "recovery_class": "host_local_rename_backup",
        }

    invalidate_profile_inventory_cache()
    post_fleet = scan_profile_fleet(transport=transport)
    post_profiles = {
        (
            inventory["host"]["id"],
            profile.get("browser"),
            profile.get("email"),
            profile.get("directory"),
        ): profile
        for inventory in post_fleet["inventories"]
        if inventory.get("online")
        for profile in inventory.get("profiles", [])
    }
    mismatched = []
    for target in targets:
        profile = post_profiles.get(
            (
                target["host_id"],
                target["browser"],
                target["email"],
                target["directory"],
            )
        )
        if (
            not isinstance(profile, dict)
            or profile.get("label") != target["desired_label"]
            or profile.get("policy_label") != target["desired_label"]
            or profile.get("name_policy_ok") is not True
        ):
            mismatched.append(
                {
                    "host_id": target["host_id"],
                    "browser": target["browser"],
                    "directory": target["directory"],
                }
            )
    if mismatched:
        return {
            "ok": False,
            "status": "unproven",
            "error_code": "post_rename_unproven",
            "targets": targets,
            "host_results": host_results,
            "unverified_targets": mismatched,
            "unavailable_hosts": plan["unavailable_hosts"],
            "category_updates": plan["category_updates"],
            "recovery_class": "host_local_rename_backup",
        }

    renamed_count = sum(int(result.get("renamed_count", 0)) for result in host_results.values())
    return {
        "ok": True,
        "status": "synced" if not plan["unavailable_hosts"] else "partial_offline",
        "complete": not plan["unavailable_hosts"],
        "renamed_count": renamed_count,
        "targets": targets,
        "host_results": host_results,
        "unavailable_hosts": plan["unavailable_hosts"],
        "category_updates": plan["category_updates"],
        "plan_fingerprint": plan["plan_fingerprint"],
        "recovery_class": "host_local_rename_backup",
    }
