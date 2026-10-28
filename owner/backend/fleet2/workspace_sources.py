"""Bounded control summary: real Notion actions and real Obsidian vault state.

Both sources are read-only here and both degrade honestly: if the Notion CLI is
not authenticated or the vault is not present on this host, the panel says so
instead of rendering an empty list that looks like "nothing to do today".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from .workspace_sources_projection import (
    MANIFEST_MAX_BYTES, PROPAGATION_MAX_ENTRIES, PROPAGATION_MAX_PROJECTIONS,
    OBSIDIAN_ALLOWED_ROOTS, _recent_notes, _active_vault_tasks,
    _read_manifest, _file_sha256, _sha256, _propagation_unavailable,
    _project_propagation as _project_propagation_impl,
)

logger = logging.getLogger("fleet2.workspace_sources")

NOTION_CLI = Path.home() / ".local" / "bin" / "ntn"
# The canonical primary Notion role supplies the Actions data-source ID at
# request time. Never embed an owner's private source ID in public code.
OWNER_TIMEZONE = ZoneInfo("Europe/Rome")
VAULT = Path.home() / "Obsidian"
NOTION_ROLES_FILE = Path(
    os.environ.get(
        "SIGNALDECK_NOTION_ROLES_FILE",
        str(VAULT / "00-system" / "notion-workspace-roles.json"),
    )
)
SERVICES_ROOT = Path.home() / "Code/dodo-services"
FACTORY_ROOT = SERVICES_ROOT / "factory"
CACHE_TTL_S = 90.0
CAPABILITY_CATALOG_PATH = Path("registry/catalog/capabilities/catalog.json")
FACTORY_CONTRACT_PREFIXES = ("surfaces", "registry")


@dataclass
class _Cache:
    value: Optional[Dict[str, Any]] = None
    at: float = 0.0

    def fresh(self) -> bool:
        return self.value is not None and (time.time() - self.at) < CACHE_TTL_S

    def put(self, value: Dict[str, Any]) -> Dict[str, Any]:
        self.value = value
        self.at = time.time()
        return value


_notion_cache = _Cache()
_vault_cache = _Cache()
_manifest_cache = _Cache()


def bounded_source_error(exc: BaseException) -> str:
    """Return operator-safe source failure text without CLI payload leakage."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "Notion connector timeout"
    if isinstance(exc, FileNotFoundError):
        return "Notion connector or owner role contract is unavailable"
    if isinstance(exc, OSError):
        return "Notion connector is unavailable on the owner host"
    if isinstance(exc, (RuntimeError, ValueError, json.JSONDecodeError)):
        text = str(exc).strip()
        return text[:180] if text else "Notion source contract is invalid"
    return "Notion source is unavailable"


def primary_notion_role() -> Dict[str, Any]:
    """Read the canonical primary-workspace identity from the vault contract.

    Signal Deck is a projection surface; it must not invent a second Notion
    workspace registry.  The vault role map owns the workspace/Registry IDs.
    """
    raw = json.loads(NOTION_ROLES_FILE.read_text(encoding="utf-8"))
    if raw.get("schema") != "life-os/notion-workspace-roles/v1":
        raise RuntimeError("unsupported Notion workspace role contract")
    role = (raw.get("roles") or {}).get("primary")
    if not isinstance(role, dict):
        raise RuntimeError("primary Notion workspace role is missing")
    workspace_id = str(role.get("workspace_id") or "")
    registry_id = str(role.get("registry_data_source_id") or "")
    if not workspace_id or not registry_id:
        raise RuntimeError("primary Notion workspace role is incomplete")
    return role


def _primary_notion_env() -> Dict[str, str]:
    """Apply the canonical Notion profile selector from the vault contract.

    `ntn` can resolve a different keyring profile depending on the caller's
    DBus/session environment. Signal Deck must not inherit that ambiguity.
    """
    env = os.environ.copy()
    # A stale direct token in the dashboard service used to force `ntn` onto
    # the retired workspace even when the canonical profile selector was set.
    # The primary workspace contract is authoritative; inherited credentials
    # must not override it.
    for key in ("NOTION_API_TOKEN", "NOTION_TOKEN", "NOTION_API_KEY"):
        env.pop(key, None)
    role = primary_notion_role()
    for key, value in (role.get("profile_env") or {}).items():
        env[str(key)] = str(value)
    return env


def actions_data_source_id() -> str:
    """Resolve Actions from the owner role and reject a conflicting override."""
    bound = str(
        (primary_notion_role().get("operational_data_sources") or {}).get("actions") or ""
    ).strip()
    if not bound:
        raise RuntimeError("primary Notion role has no bound Actions data source")
    override = os.environ.get("SIGNALDECK_ACTIONS_DATA_SOURCE_ID", "").strip()
    if override and override != bound:
        raise RuntimeError("Signal Deck Actions override disagrees with the canonical primary role")
    return bound


def verified_primary_notion_identity() -> Dict[str, str]:
    """Fail closed unless ntn is authenticated to the canonical primary role."""
    identity = _run_ntn_identity()
    role = primary_notion_role()
    if identity.get("workspace_id") != role.get("workspace_id"):
        raise RuntimeError("Notion CLI is authenticated to a non-primary workspace")
    expected_names = {str(name) for name in role.get("expected_names") or []}
    if expected_names and identity.get("workspace") not in expected_names:
        raise RuntimeError("Notion primary workspace name does not match its role contract")
    return identity


def _run_ntn(
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 20.0,
    method: str | None = None,
) -> Dict[str, Any]:
    if not NOTION_CLI.exists():
        raise FileNotFoundError(f"Notion CLI not found at {NOTION_CLI}")
    argv = [str(NOTION_CLI), "api", path]
    if method:
        argv.extend(["-X", method])
    if payload is not None:
        argv.extend(["-d", json.dumps(payload)])
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_primary_notion_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ntn failed").strip()[:300])
    start = proc.stdout.find("{")
    if start < 0:
        raise RuntimeError("ntn returned no JSON payload")
    return json.loads(proc.stdout[start:])


def _run_ntn_identity(timeout: float = 20.0) -> Dict[str, str]:
    """Return the authenticated Notion workspace without exposing credentials."""
    if not NOTION_CLI.exists():
        raise FileNotFoundError(f"Notion CLI not found at {NOTION_CLI}")
    proc = subprocess.run(
        [str(NOTION_CLI), "whoami"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_primary_notion_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ntn whoami failed").strip()[:300])
    fields = proc.stdout.strip().split("\t")
    if len(fields) < 8:
        raise RuntimeError("ntn whoami returned an incomplete identity")
    return {
        "user_id": fields[0],
        "user_name": fields[1],
        "user_type": fields[2],
        "email": fields[3],
        "workspace_id": fields[4],
        "workspace": fields[5],
        "owner_id": fields[6],
        "plan": fields[7],
    }


def _plain_title(prop: Dict[str, Any]) -> str:
    return "".join(part.get("plain_text", "") for part in prop.get("title", []))


def notion_actions(force: bool = False) -> Dict[str, Any]:
    """Open action items that are due today or already overdue."""
    if not force and _notion_cache.fresh():
        return _notion_cache.value  # type: ignore[return-value]

    today = datetime.now(OWNER_TIMEZONE).date().isoformat()
    try:
        identity = verified_primary_notion_identity()
        actions_source = actions_data_source_id()
        source = _run_ntn(f"/v1/data_sources/{actions_source}")
        source_title = "".join(
            str(part.get("plain_text") or "")
            for part in source.get("title", [])
            if isinstance(part, dict)
        ).strip()
        if source_title != "Actions":
            raise RuntimeError("Notion Actions source identity does not match")
        query = {
            "filter": {
                "and": [
                    {"property": "Status", "status": {"does_not_equal": "Done"}},
                    {"property": "Do Date", "date": {"on_or_before": today}},
                ]
            },
            "sorts": [{"property": "Do Date", "direction": "ascending"}],
            "page_size": 100,
        }
        results: List[Dict[str, Any]] = []
        cursor: str | None = None
        has_more = False
        # Two bounded pages cover normal owner usage while preserving an honest
        # lower-bound marker if the source ever grows beyond that window.
        for _ in range(2):
            request = dict(query)
            if cursor:
                request["start_cursor"] = cursor
            page = _run_ntn(
                f"/v1/data_sources/{actions_source}/query",
                request,
            )
            results.extend(row for row in page.get("results", []) if isinstance(row, dict))
            has_more = page.get("has_more") is True
            cursor = page.get("next_cursor") if has_more else None
            if not cursor:
                break
    except Exception as exc:  # unauthenticated CLI, network, schema drift
        logger.warning("Notion Actions source refresh failed", exc_info=True)
        return _notion_cache.put(
            {
                "available": False,
                "reason": bounded_source_error(exc),
                "items": [],
                "counts": {},
            }
        )

    items: List[Dict[str, Any]] = []
    overdue = 0
    for row in results:
        props = row.get("properties", {})
        due = ((props.get("Do Date") or {}).get("date") or {}).get("start")
        status = ((props.get("Status") or {}).get("status") or {}).get("name")
        priority = ((props.get("Priority") or {}).get("select") or {}).get("name")
        kind = ((props.get("Type") or {}).get("select") or {}).get("name")
        is_overdue = bool(due and due[:10] < today)
        overdue += 1 if is_overdue else 0
        items.append(
            {
                "id": row.get("id"),
                "title": _plain_title(props.get("Action", {})) or "(untitled)",
                "due": due,
                "overdue": is_overdue,
                "status": status,
                "priority": priority,
                "type": kind,
                "url": row.get("url"),
            }
        )

    priority_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(
        key=lambda item: (
            priority_rank.get(str(item["priority"] or "").casefold(), 9),
            item["due"] or "9999",
            item["title"].casefold(),
        )
    )
    return _notion_cache.put(
        {
            "available": True,
            "reason": None,
            "source": {
                "workspace_id": identity.get("workspace_id"),
                "workspace": identity.get("workspace"),
                "data_source_id": actions_source,
                "title": "Actions",
            },
            "items": items,
            "has_more": has_more,
            "counts": {
                "due_or_overdue": len(items),
                "overdue": overdue,
                "focus": sum(1 for item in items if item["priority"]),
            },
        }
    )


def obsidian_state(force: bool = False, vault: Path | None = None) -> Dict[str, Any]:
    root = vault or VAULT
    if not force and _vault_cache.fresh():
        return _vault_cache.value  # type: ignore[return-value]
    if not root.is_dir():
        return _vault_cache.put(
            {
                "available": False,
                "reason": f"vault not present at {root}",
                "recent": [],
                "tasks": [],
            }
        )
    try:
        knowledge = _recent_notes(
            root,
            limit=8,
            skip_dirs=(".git", ".obsidian", ".trash", "09-archive", "tmp", "_local"),
        )
        tasks = _active_vault_tasks(root)
    except OSError as exc:
        return _vault_cache.put(
            {"available": False, "reason": str(exc), "recent": [], "tasks": []}
        )
    return _vault_cache.put(
        {
            "available": True,
            "reason": None,
            "vault": str(root),
            "recent": knowledge,
            "tasks": tasks,
            "allowlisted_roots": list(OBSIDIAN_ALLOWED_ROOTS),
        }
    )


def _factory_contract_path(relative_path: Any) -> Path:
    """Resolve one bounded Factory contract path, never an arbitrary file."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("percorso contratto Factory non valido")
    candidate_relative = Path(relative_path)
    if (
        candidate_relative.is_absolute()
        or ".." in candidate_relative.parts
        or not candidate_relative.parts
        or candidate_relative.parts[0] not in FACTORY_CONTRACT_PREFIXES
    ):
        raise ValueError("percorso contratto Factory non consentito")
    root = FACTORY_ROOT.resolve()
    candidate = (FACTORY_ROOT / candidate_relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("percorso contratto Factory fuori root") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"contratto Factory non disponibile: {relative_path}")
    if candidate.stat().st_size > MANIFEST_MAX_BYTES:
        raise ValueError(f"contratto Factory troppo grande: {candidate.name}")
    return candidate


def _contract_digest(reference: Any) -> Dict[str, Any]:
    if not isinstance(reference, dict):
        raise ValueError("riferimento contratto non valido")
    relative_path = reference.get("path")
    path = _factory_contract_path(relative_path)
    expected = _sha256(reference.get("sha256"))
    actual = _file_sha256(path)
    return {
        "path": relative_path,
        "expected": expected,
        "actual": actual,
        "match": bool(expected and actual == expected),
    }


def _project_propagation(catalog: Dict[str, Any]) -> Dict[str, Any]:
    return _project_propagation_impl(catalog, _contract_digest)


def canonical_manifests(force: bool = False) -> Dict[str, Any]:
    """Project only allowlisted owner manifests; never scans or executes them."""
    if not force and _manifest_cache.fresh():
        return _manifest_cache.value  # type: ignore[return-value]

    paths = {
        "factory_registry": FACTORY_ROOT / "registry/targets.json",
        "obsidian_app": FACTORY_ROOT / "surfaces/chatgpt/apps/obsidian/app.json",
        "app_catalog": FACTORY_ROOT / "registry/catalog/chatgpt-app-servers/catalog.json",
        "obsidian_source": SERVICES_ROOT / "services/obsidian-mcp/SOURCE.json",
        "council_source": SERVICES_ROOT / "services/dodo-council/SOURCE.json",
    }
    capability_catalog_path = FACTORY_ROOT / CAPABILITY_CATALOG_PATH
    try:
        registry = _read_manifest(paths["factory_registry"])
        app = _read_manifest(paths["obsidian_app"])
        catalog = _read_manifest(paths["app_catalog"])
        obsidian_source = _read_manifest(paths["obsidian_source"])
        council_source = _read_manifest(paths["council_source"])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _manifest_cache.put(
            {
                "available": False,
                "state": "offline",
                "reason": f"{type(exc).__name__}: {exc}",
                "sources": [{"name": name, "path": str(path), "available": path.is_file()} for name, path in paths.items()],
                "factory": {"targets": []},
                "services": [],
                "notion": {"state": "operational", "write": "dry-run/proof-gated"},
                "obsidian": {"state": "canonical", "write": "read-first; provider gate required"},
                "news": {"state": "manual", "reason": "nessun feed canonico verificato"},
                "provider_gate": {"state": "manual", "reason": "manifest canonici non leggibili"},
                "propagation": _propagation_unavailable("manifest canonici non leggibili"),
            }
        )

    try:
        propagation = _project_propagation(_read_manifest(capability_catalog_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        propagation = _propagation_unavailable(f"{type(exc).__name__}: {exc}")

    targets = [
        {
            "id": str(target_id),
            "kind": str(value.get("kind", "")),
            "operation": str(value.get("operation", "")),
            "bundle": str(value.get("bundle", "")),
        }
        for target_id, value in (registry.get("targets") or {}).items()
        if isinstance(value, dict)
    ][:60]
    obsidian_app = app.get("app") or {}
    obsidian_catalog = (catalog.get("servers") or {}).get("obsidian") or {}
    live_proof = {
        "state": "manual",
        "expected_tool_count": obsidian_app.get("expected_tool_count"),
        "observed_tool_count": 19,
        "observed_at": "2026-07-26",
        "drift": "live contract differs from provider manifest; refresh required on owner host",
        "action": (app.get("update_workflow") or {}).get("action", "Aggiorna"),
        "owner_host": (app.get("update_workflow") or {}).get("owner_host", "mac-studio"),
    }
    payload = {
        "available": True,
        "state": "healthy",
        "reason": None,
        "generated_at": time.time(),
        "sources": [
            {"name": name, "path": str(path), "available": True}
            for name, path in paths.items()
        ] + [{"name": "capability_catalog", "path": str(capability_catalog_path), "available": propagation["available"]}],
        "factory": {
            "repo": str(FACTORY_ROOT),
            "schema_version": registry.get("schema_version"),
            "targets": targets,
        },
        "services": [
            {
                "id": "obsidian-mcp",
                "repo": str(SERVICES_ROOT),
                "source_revision": obsidian_source.get("legacy_commit") or obsidian_app.get("server_revision"),
                "runtime": obsidian_catalog.get("runtime", {}).get("service"),
                "status": obsidian_catalog.get("status", "active"),
            },
            {
                "id": "dodo-council",
                "repo": str(SERVICES_ROOT),
                "source_revision": council_source.get("source_commit"),
                "runtime": "dodo-council",
                "status": "promoted" if council_source.get("runtime_promoted") else "source-only",
            },
        ],
        "notion": {
            "state": "operational",
            "owner": "Domenico",
            "adapter": "ntn",
            "write": "dry-run/proof-gated",
        },
        "obsidian": {
            "state": "canonical",
            "owner": "MiniPC",
            "adapter": "obsidian-mcp",
            "write": "read-first; no dashboard vault write",
        },
        "news": {
            "state": "manual",
            "reason": "nessun feed canonico verificato nei manifest owner",
        },
        "provider_gate": live_proof,
        "propagation": propagation,
    }
    return _manifest_cache.put(payload)


def control_summary(force: bool = False) -> Dict[str, Any]:
    return {
        "generated_at": time.time(),
        "notion": notion_actions(force=force),
        "obsidian": obsidian_state(force=force),
        "manifests": canonical_manifests(force=force),
    }
