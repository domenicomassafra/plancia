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
from .transport import _agent_result
from .inventory import scan_profile_fleet
from .identity import _agent_supports_safe_rename, _category_from_profile_label, _profile_metadata_key, _resolve_category_candidate, canonical_profile_category, canonical_profile_label

def profile_name_sync_plan(
    metadata: dict[tuple[str, str], dict],
    *,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    """Build one fleet-wide, exact profile-name convergence plan.

    Signal Deck metadata is the primary category authority. When no category
    has been saved yet, a unique existing bracket tag is adopted; otherwise
    the account receives the deterministic fallback [TEST]. Absolute profile
    paths remain host-local inside the agent preview and never leave this
    adapter.
    """

    fleet = scan_profile_fleet(transport=transport)
    unavailable_hosts = sorted(
        inventory["host"]["id"]
        for inventory in fleet["inventories"]
        if not inventory.get("online")
    )
    groups: dict[str, list[tuple[str, dict]]] = {}
    for inventory in fleet["inventories"]:
        if not inventory.get("online"):
            continue
        host_id = inventory["host"]["id"]
        for profile in inventory.get("profiles", []):
            browser = str(profile.get("browser", "")).casefold()
            email = str(profile.get("email", "")).strip().casefold()
            if browser in _BROWSERS and email:
                groups.setdefault(email, []).append((host_id, profile))

    conflicts: list[dict[str, object]] = []
    targets: list[dict[str, str]] = []
    category_updates: list[dict[str, str]] = []

    for email, instances in sorted(groups.items()):
        browsers = sorted({str(profile.get("browser", "")).casefold() for _, profile in instances})
        metadata_categories: set[str] = set()
        observed_categories: set[str] = set()
        for host_id, profile in instances:
            profile_key = _profile_metadata_key(profile)
            annotation = metadata.get((host_id, profile_key), {})
            raw_category = str(annotation.get("category", "")).strip()
            if raw_category:
                try:
                    metadata_categories.add(canonical_profile_category(raw_category, email))
                except ValueError:
                    conflicts.append(
                        {
                            "browsers": browsers,
                            "email": email,
                            "reason": "invalid_metadata_category",
                            "host_id": host_id,
                        }
                    )
            for label_key in ("policy_label", "label"):
                category = _category_from_profile_label(profile.get(label_key), email)
                if category:
                    observed_categories.add(category)
                    break

        metadata_category, metadata_conflicts = _resolve_category_candidate(metadata_categories)
        if metadata_conflicts:
            conflicts.append(
                {
                    "browsers": browsers,
                    "email": email,
                    "reason": "metadata_category_conflict",
                    "categories": metadata_conflicts,
                }
            )
            continue
        if metadata_category:
            category = metadata_category
        else:
            observed_category, observed_conflicts = _resolve_category_candidate(observed_categories)
            if observed_conflicts:
                conflicts.append(
                    {
                        "browsers": browsers,
                        "email": email,
                        "reason": "observed_category_conflict",
                        "categories": observed_conflicts,
                    }
                )
                continue
            category = observed_category or "[TEST]"

        if not category:
            conflicts.append(
                {
                    "browsers": browsers,
                    "email": email,
                    "reason": "category_resolution_failed",
                }
            )
            continue

        desired_label = canonical_profile_label(category, email)
        for host_id, profile in instances:
            browser = str(profile.get("browser", "")).casefold()
            profile_key = _profile_metadata_key(profile)
            category_updates.append(
                {
                    "host_id": host_id,
                    "profile_key": profile_key,
                    "category": category,
                }
            )
            if (
                profile.get("label") == desired_label
                and profile.get("policy_label") == desired_label
                and profile.get("name_policy_ok") is True
            ):
                continue
            targets.append(
                {
                    "host_id": host_id,
                    "host_label": PROFILE_HOSTS[host_id].label,
                    "browser": browser,
                    "email": email,
                    "directory": str(profile.get("directory", "")),
                    "label": str(profile.get("label", ""))[:160],
                    "desired_label": desired_label,
                }
            )

    if conflicts:
        return {
            "ok": False,
            "status": "conflict",
            "error_code": "profile_name_policy_conflict",
            "conflicts": conflicts,
            "targets": [],
            "target_count": 0,
            "unavailable_hosts": unavailable_hosts,
            "complete": False,
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
                "status": "blocked",
                "error_code": "safe_rename_agent_required",
                "host_id": host_id,
                "targets": targets,
                "target_count": len(targets),
                "unavailable_hosts": unavailable_hosts,
                "complete": False,
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
        try:
            payload = json.loads(preview.stdout)
        except json.JSONDecodeError:
            payload = {}
        private_targets = payload.get("targets", []) if isinstance(payload, dict) else []
        if (
            not preview.ok
            or not isinstance(payload, dict)
            or not payload.get("ok")
            or not isinstance(private_targets, list)
        ):
            return {
                "ok": False,
                "status": "blocked",
                "error_code": "rename_preview_rejected",
                "host_id": host_id,
                "targets": targets,
                "target_count": len(targets),
                "unavailable_hosts": unavailable_hosts,
                "complete": False,
                "recovery_class": "not_started",
            }
        private_previews[host_id] = private_targets

    public_plan = [
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
    fingerprint_payload = {
        "targets": public_plan,
        "unavailable_hosts": unavailable_hosts,
    }
    plan_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    host_counts = {
        host_id: len(host_targets)
        for host_id, host_targets in sorted(targets_by_host.items())
    }
    browser_counts: dict[str, int] = {}
    for target in targets:
        browser_counts[target["browser"]] = browser_counts.get(target["browser"], 0) + 1

    return {
        "ok": True,
        "status": "converged" if not targets and not unavailable_hosts else "ready",
        "targets": targets,
        "target_count": len(targets),
        "host_counts": host_counts,
        "browser_counts": browser_counts,
        "unavailable_hosts": unavailable_hosts,
        "complete": not unavailable_hosts and not targets,
        "plan_fingerprint": plan_fingerprint,
        "category_updates": category_updates,
        "_private_previews": private_previews,
        "recovery_class": "not_started",
    }
