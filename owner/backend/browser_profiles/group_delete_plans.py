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

from .hosts import PROFILE_HOSTS, _BROWSERS, _DELETABLE_DIRECTORY_RE, _EMAIL_RE
from .transport import _agent_result, profile_identity
from .inventory import scan_profile_fleet
from .identity import _agent_supports_safe_delete, _delete_preimage

def profile_group_delete_plan(
    browser: str,
    email: str,
    *,
    transport: Callable[..., SSHResult] = run_ssh,
) -> dict:
    """Resolve the exact, current fleet targets for a group delete without mutation.

    The group is resolved from a fresh inventory on every registered host;
    callers cannot choose arbitrary paths or silently omit an unavailable host.
    The host-local agent remains the authority for resolving its actual path.
    This is the operator-visible preflight. It does not trust client paths and
    is repeated immediately before a delete request is dispatched.
    """

    normalized_browser = browser.strip().casefold()
    normalized_email = email.strip().casefold()
    if normalized_browser not in _BROWSERS or not _EMAIL_RE.fullmatch(normalized_email):
        raise ValueError("a browser and a valid email are required for fleet deletion")

    fleet = scan_profile_fleet(transport=transport)
    unavailable = [
        inventory["host"]["id"]
        for inventory in fleet["inventories"]
        if not inventory.get("online")
    ]
    if unavailable:
        return {
            "ok": False,
            "error_code": "fleet_inventory_unavailable",
            "unavailable_hosts": unavailable,
            "recovery_class": "not_started",
        }

    targets: list[dict[str, str]] = []
    for inventory in fleet["inventories"]:
        host_id = inventory["host"]["id"]
        matches = [
            profile
            for profile in inventory["profiles"]
            if profile.get("browser") == normalized_browser
            and profile.get("email") == normalized_email
        ]
        if not matches:
            continue
        for match in matches:
            identity = profile_identity(
                host_id,
                normalized_browser,
                normalized_email,
                str(match.get("directory", "")),
            )
            if not _DELETABLE_DIRECTORY_RE.fullmatch(identity["directory"]):
                return {
                    "ok": False,
                    "error_code": "default_or_special_profile_present",
                    "host_id": host_id,
                    "recovery_class": "not_started",
                }
            targets.append(
                {
                    "host_id": host_id,
                    "host_label": PROFILE_HOSTS[host_id].label,
                    **_delete_preimage(identity, match),
                }
            )

    if not targets:
        return {
            "ok": True,
            "status": "already_absent",
            "targets": [],
            "recovery_class": "not_needed",
        }

    # Check every host before mutation, but do not multiply operator wait time
    # by the number of hosts. These probes are independent and have the same
    # bounded SSH timeout as inventory, so they fan out together.
    target_host_ids = sorted({target["host_id"] for target in targets})
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_host_ids)) as pool:
        capability_futures = {
            host_id: pool.submit(
                _agent_result,
                PROFILE_HOSTS[host_id],
                "capabilities",
                transport=transport,
            )
            for host_id in target_host_ids
        }
        for host_id in target_host_ids:
            host = PROFILE_HOSTS[host_id]
            capabilities = capability_futures[host_id].result()
            if not _agent_supports_safe_delete(host, capabilities):
                return {
                    "ok": False,
                    "error_code": "safe_delete_agent_required",
                    "host_id": host_id,
                    "targets": targets,
                    "recovery_class": "not_started",
                }

    plan_payload = [
        {key: target[key] for key in ("host_id", "browser", "email", "directory")}
        for target in targets
    ]
    plan_fingerprint = hashlib.sha256(
        json.dumps(plan_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "status": "ready",
        "targets": targets,
        "plan_fingerprint": plan_fingerprint,
        "recovery_class": "not_started",
    }
