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

from .hosts import PROFILE_HOSTS
from .transport import profile_identity
from .inventory import scan_profile_fleet
from .identity import _inventory_identity_profiles, _safe_current_targets
from .profile_actions import delete_profile
from .group_delete_plans import profile_group_delete_plan

def delete_profile_group(
    browser: str,
    email: str,
    *,
    transport: Callable[..., SSHResult] = run_ssh,
    expected_plan_fingerprint: str | None = None,
) -> dict:
    """Delete a freshly preflighted group through exact host-local agents."""

    plan = profile_group_delete_plan(browser, email, transport=transport)
    if not plan.get("ok") or plan.get("status") == "already_absent":
        return {**plan, "deleted_count": 0}
    if expected_plan_fingerprint is not None and expected_plan_fingerprint != plan.get(
        "plan_fingerprint"
    ):
        return {
            "ok": False,
            "error_code": "group_plan_changed",
            "targets": plan["targets"],
            "recovery_class": "not_started",
        }
    targets = plan["targets"]

    # Hosts run in parallel, but multiple matching directories on one host are
    # deleted strictly in sequence.  That avoids concurrent Local State writes
    # while still making browser+email a single operator row across the fleet.
    targets_by_host: dict[str, list[dict[str, str]]] = {}
    for target in targets:
        targets_by_host.setdefault(target["host_id"], []).append(target)

    def delete_host_targets(item: tuple[str, list[dict[str, str]]]) -> tuple[str, dict]:
        host_id, host_targets = item
        target_results: list[dict] = []
        deleted_count = 0
        for target in sorted(host_targets, key=lambda value: value["directory"]):
            identity = profile_identity(
                host_id,
                target["browser"],
                target["email"],
                target["directory"],
            )
            result = delete_profile(
                host_id,
                identity["browser"],
                identity["email"],
                identity["directory"],
                transport=transport,
                require_identity_absent=False,
            )
            target_results.append(result)
            deleted_count += int(result.get("deleted_count", 0))
            if not result.get("ok"):
                return host_id, {
                    **result,
                    "deleted_count": deleted_count,
                    "target_results": target_results,
                }
        return host_id, {
            "ok": True,
            "host_id": host_id,
            "status": "deleted" if deleted_count else "already_absent",
            "deleted_count": deleted_count,
            "target_results": target_results,
            "recovery_class": "manual_local_state_backup" if deleted_count else "not_needed",
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets_by_host)) as pool:
        results = dict(pool.map(delete_host_targets, sorted(targets_by_host.items())))

    # Individual host checks prove the exact directory they acted on. A final
    # fleet scan additionally proves that the logical browser/account identity
    # did not reappear at another directory between those checks.
    final_fleet = scan_profile_fleet(transport=transport)
    final_inventories = {
        inventory["host"]["id"]: inventory
        for inventory in final_fleet["inventories"]
    }
    targets_by_host = {target["host_id"]: target for target in targets}
    fleet_identity = {
        "browser": targets[0]["browser"],
        "email": targets[0]["email"],
        # Directory is irrelevant to identity-wide lookup, but retaining the
        # validated planned value keeps this compatible with exact helpers.
        "directory": targets[0]["directory"],
    }
    for host_id in PROFILE_HOSTS:
        planned_target = host_id in targets_by_host
        result = results.get(
            host_id,
            {
                "ok": True,
                "host_id": host_id,
                "status": "not_targeted",
                "deleted_count": 0,
                "recovery_class": "not_needed",
            },
        )
        result["planned_target"] = planned_target
        inventory = final_inventories.get(host_id)
        if inventory is None or not inventory.get("online"):
            final_read = {
                "absence_proven": False,
                "error_code": "inventory_unavailable",
            }
            if result.get("ok"):
                result = {
                    **result,
                    "ok": False,
                    "status": "unproven",
                    "error_code": "post_delete_unproven",
                }
            result["final_read"] = final_read
            results[host_id] = result
            continue

        remaining = _inventory_identity_profiles(inventory, fleet_identity)
        if remaining:
            if result.get("ok"):
                result = {
                    **result,
                    "ok": False,
                    "status": "unproven",
                    "error_code": "post_delete_identity_present",
                }
            result["current_targets"] = _safe_current_targets(host_id, remaining)
            result["final_read"] = {
                "absence_proven": False,
                "error_code": "identity_still_present",
            }
        else:
            result["final_read"] = {"absence_proven": True}
        results[host_id] = result

    failed = {
        host_id: result for host_id, result in results.items() if not result.get("ok")
    }
    if failed:
        return {
            "ok": False,
            "error_code": "group_delete_unproven",
            "host_results": results,
            "recovery_class": "manual_local_state_backup",
        }
    return {
        "ok": True,
        "status": "deleted",
        "deleted_count": sum(
            int(result.get("deleted_count", 0)) for result in results.values()
        ),
        "host_results": results,
        "recovery_class": "manual_local_state_backup",
    }
