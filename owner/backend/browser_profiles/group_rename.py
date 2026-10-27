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

from .hosts import PROFILE_HOSTS, PROFILE_SCHEMA_VERSION, _BROWSERS
from .transport import _agent_result, profile_identity
from .inventory import invalidate_profile_inventory_cache, scan_profile_fleet
from .identity import _agent_supports_safe_rename, canonical_profile_label

def profile_group_rename_plan(
    browser: str,
    email: str,
    category: str,
    *,
    target_hosts: list[str] | None = None,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    """Resolve the exact, current fleet targets for a group rename without mutation.

    Returns an opaque, host-redacted plan fingerprint that callers pass back to
    apply to guarantee preimage consistency and prevent drift.
    """

    normalized_browser = browser.strip().casefold()
    normalized_email = email.strip().casefold()
    if normalized_browser not in _BROWSERS:
        raise ValueError("browser must be chrome or edge")
    desired_label = canonical_profile_label(category, normalized_email)
    fleet = scan_profile_fleet(transport=transport)
    unavailable = {
        inventory["host"]["id"]
        for inventory in fleet["inventories"]
        if not inventory.get("online")
    }

    if target_hosts is not None:
        targeted_unavailable = [h for h in target_hosts if h in unavailable]
        if targeted_unavailable:
            return {
                "ok": False,
                "error_code": "fleet_inventory_unavailable",
                "unavailable_hosts": targeted_unavailable,
                "recovery_class": "not_started",
            }

    targets: list[dict[str, str]] = []
    for inventory in fleet["inventories"]:
        host_id = inventory["host"]["id"]
        if target_hosts is not None and host_id not in target_hosts:
            continue
        matches = [
            profile
            for profile in inventory.get("profiles", [])
            if profile.get("browser") == normalized_browser
            and profile.get("email") == normalized_email
        ]
        for match in matches:
            identity = profile_identity(
                host_id,
                normalized_browser,
                normalized_email,
                str(match.get("directory", "")),
            )
            targets.append(
                {
                    "host_id": host_id,
                    "host_label": PROFILE_HOSTS[host_id].label,
                    "browser": identity["browser"],
                    "email": identity["email"],
                    "directory": identity["directory"],
                    "label": str(match.get("label", ""))[:160],
                    "desired_label": desired_label,
                }
            )

    if not targets:
        if unavailable and target_hosts is None:
            return {
                "ok": False,
                "error_code": "fleet_inventory_unavailable",
                "unavailable_hosts": sorted(unavailable),
                "recovery_class": "not_started",
            }
        return {
            "ok": False,
            "error_code": "profile_not_found",
            "recovery_class": "not_started",
        }

    targets_by_host: dict[str, list[dict[str, str]]] = {}
    for target in targets:
        targets_by_host.setdefault(target["host_id"], []).append(target)

    private_previews: dict[str, list[dict]] = {}
    for host_id, host_targets in sorted(targets_by_host.items()):
        host = PROFILE_HOSTS[host_id]
        capability = _agent_result(host, "capabilities", transport=transport)
        if not _agent_supports_safe_rename(host, capability):
            return {
                "ok": False,
                "error_code": "safe_rename_agent_required",
                "host_id": host_id,
                "targets": targets,
                "recovery_class": "not_started",
            }
        preview = _agent_result(
            host,
            "rename-preview",
            {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "targets": [
                    {
                        key: target[key]
                        for key in ("browser", "email", "directory", "desired_label")
                    }
                    for target in host_targets
                ],
            },
            transport=transport,
        )
        if not preview.ok:
            return {
                "ok": False,
                "error_code": "rename_preview_rejected",
                "host_id": host_id,
                "targets": targets,
                "recovery_class": "not_started",
            }
        payload: object = {}
        try:
            payload = json.loads(preview.stdout)
            private_targets = (
                payload.get("targets", []) if isinstance(payload, dict) else []
            )
        except json.JSONDecodeError:
            private_targets = []
        if (
            not isinstance(payload, dict)
            or not payload.get("ok")
            or not isinstance(private_targets, list)
        ):
            return {
                "ok": False,
                "error_code": "rename_preview_rejected",
                "host_id": host_id,
                "targets": targets,
                "recovery_class": "not_started",
            }
        private_previews[host_id] = private_targets

    plan_payload = [
        {
            key: target[key]
            for key in (
                "host_id",
                "browser",
                "email",
                "directory",
                "label",
                "desired_label",
            )
        }
        for target in targets
    ]
    plan_fingerprint = hashlib.sha256(
        json.dumps(plan_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "ok": True,
        "status": "ready",
        "desired_label": desired_label,
        "targets": targets,
        "plan_fingerprint": plan_fingerprint,
        "_private_previews": private_previews,
        "recovery_class": "not_started",
    }


def rename_profile_group(
    browser: str,
    email: str,
    category: str,
    *,
    target_hosts: list[str] | None = None,
    expected_plan_fingerprint: str | None = None,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    """Rename one Chrome/Edge identity across its exact managed installations.

    A host-local preview obtains the private path and current Chromium label,
    then the same opaque preview is applied only after every target host has
    proved the rename capability.  Absolute paths remain inside the adapter.
    """
    plan = profile_group_rename_plan(
        browser,
        email,
        category,
        target_hosts=target_hosts,
        transport=transport,
    )
    if not plan.get("ok"):
        return plan

    if (
        expected_plan_fingerprint is not None
        and expected_plan_fingerprint != plan.get("plan_fingerprint")
    ):
        return {
            "ok": False,
            "error_code": "group_plan_changed",
            "targets": plan.get("targets", []),
            "recovery_class": "not_started",
        }

    targets = plan["targets"]
    desired_label = plan["desired_label"]
    private_previews = plan["_private_previews"]

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
                "error_code": "rename_apply_rejected",
                "renamed_count": int(payload.get("renamed_count", 0)) if isinstance(payload, dict) else 0,
            }
        return host_id, {
            "ok": True,
            "status": "renamed",
            "renamed_count": int(payload.get("renamed_count", 0)),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(private_previews)) as pool:
        host_results = dict(pool.map(apply_host, sorted(private_previews.items())))
    failed = {host_id: result for host_id, result in host_results.items() if not result.get("ok")}
    if failed:
        return {
            "ok": False,
            "error_code": "group_rename_partial",
            "targets": targets,
            "host_results": host_results,
            "recovery_class": "host_local_rename_backup",
        }

    invalidate_profile_inventory_cache()
    post_fleet = scan_profile_fleet(transport=transport)
    post_labels = {
        (
            inventory["host"]["id"],
            profile.get("browser"),
            profile.get("email"),
            profile.get("directory"),
        ): profile.get("label")
        for inventory in post_fleet["inventories"]
        if inventory.get("online")
        for profile in inventory.get("profiles", [])
    }
    mismatched = [
        target["host_id"]
        for target in targets
        if post_labels.get(
            (
                target["host_id"],
                target["browser"],
                target["email"],
                target["directory"],
            )
        )
        != desired_label
    ]
    if mismatched:
        return {
            "ok": False,
            "error_code": "post_rename_unproven",
            "targets": targets,
            "host_results": host_results,
            "unverified_hosts": mismatched,
            "recovery_class": "host_local_rename_backup",
        }
    return {
        "ok": True,
        "status": "renamed",
        "renamed_count": sum(
            int(result.get("renamed_count", 0)) for result in host_results.values()
        ),
        "desired_label": desired_label,
        "targets": targets,
        "plan_fingerprint": plan.get("plan_fingerprint"),
        "host_results": host_results,
        "recovery_class": "host_local_rename_backup",
    }
