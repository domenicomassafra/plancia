"""Signal Deck Life OS workspace adapter and Notion edit guard."""
from .workspace_common import *
from . import workspace_notion_read as _notion_read
from .workspace_notion_read import (
    NotionSource,
    ACTION_ITEM_EDIT_OPERATION,
    ACTION_ITEM_SCHEMA_VERSION,
    actions_source,
    _default_actions_source,
    _registry_data_source_id,
    SourceResult,
    _iso_now,
    _digest,
    receipt_sha,
    _plain_text,
    _property_value,
    _schema,
    _is_protected_rich_text_field,
    _row,
    _same_id,
    _source_title,
    _verify_workspace,
    _verify_source,
    _source_url,
    _preimage_digest,
    _notion_database,
    notion_database,
    _notion_row,
    notion_row,
)
from .workspace_obsidian import (
    _vault_root,
    _safe_relative,
    _metadata,
    _iter_notes,
    _open_uri,
    _links_for,
    obsidian_explorer,
)
from .workspace_projects_projection import (
    external_context_projection,
    agents_projects_projection,
)

# Keep the caller module as the testable dependency boundary for CAS and
# canonical source identity. Readers receive its current, potentially patched,
# guards at call time; they never capture an out-of-date binding at import time.
def _notion_database(
    source_spec: NotionSource | None = None,
    *, cursor: str = "", query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _notion_read._notion_database(
        source_spec, cursor=cursor, query=query,
        _verify_identity=_verify_workspace, _registry_lookup=_registry_data_source_id,
    )


def notion_database(force: bool = False, cursor: str = "") -> dict[str, Any]:
    del force
    return _notion_database(cursor=cursor)


def _notion_row(source_spec: NotionSource, row_id: str) -> dict[str, Any]:
    return _notion_read._notion_row(
        source_spec, row_id, _verify_identity=_verify_workspace,
    )


def notion_row(row_id: str) -> dict[str, Any]:
    return _notion_row(actions_source(), row_id)

def action_item_edit_identity(
    row_id: str,
    changes: dict[str, Any],
    idempotency_key: str,
) -> dict[str, str]:
    """Return one stable identity for the bounded Action Items operation."""
    key = idempotency_key.strip()
    if not key or len(key) > 200:
        raise ValueError("idempotency_key action-item.edit non valida")
    if not row_id or len(row_id) > 80:
        raise ValueError("row_id action-item.edit non valida")
    return {
        "operation_id": _digest(
            {"operation": ACTION_ITEM_EDIT_OPERATION, "idempotency_key": key}
        ),
        "request_digest": _digest(
            {
                "operation": ACTION_ITEM_EDIT_OPERATION,
                "row_id": row_id,
                "changes": changes,
            }
        ),
    }


def action_item_edit_caller(
    actor: str,
    surface: str,
    caller_type: str = "operator_ui",
    agent_runtime: str = "",
    overlay_id: str | None = None,
) -> dict[str, str | None]:
    """Use the same tightly allowlisted caller envelope as Daily operations."""
    caller = {
        "actor": actor.strip(),
        "surface": surface.strip(),
        "caller_type": caller_type.strip(),
        "agent_runtime": agent_runtime.strip() or None,
        "overlay_id": overlay_id.strip() if overlay_id else None,
    }
    allowed = (
        {
            "actor": "Hermes",
            "surface": "hermes",
            "caller_type": "agent",
            "agent_runtime": "hermes",
            "overlay_id": None,
        },
        {
            "actor": "Domenico",
            "surface": "signal-deck",
            "caller_type": "operator_ui",
            "agent_runtime": None,
            "overlay_id": None,
        },
    )
    if caller not in allowed:
        raise ValueError("caller action-item.edit non autorizzato")
    return caller


def preview_action_item_edit(
    row_id: str,
    changes: dict[str, Any],
    *,
    actor: str,
    surface: str,
    idempotency_key: str,
    caller_type: str = "operator_ui",
    agent_runtime: str = "",
    overlay_id: str | None = None,
    provenance: dict[str, Any] | None = None,
    preimage_hash: str = "",
) -> dict[str, Any]:
    """Wrap the existing Action Items packet in the shared operation envelope."""
    caller = action_item_edit_caller(
        actor, surface, caller_type, agent_runtime, overlay_id
    )
    identity = action_item_edit_identity(row_id, changes, idempotency_key)
    source = actions_source()
    packet = preview_notion_edit(
        _notion_row(source, row_id),
        row_id,
        changes,
        preimage_hash,
        source_spec=source,
    )
    return {
        **packet,
        "operation_type": ACTION_ITEM_EDIT_OPERATION,
        "operation_id": identity["operation_id"],
        "caller": caller,
        "entity": {
            "type": "notion.action-item",
            "owner": OWNER_WORKSPACE,
            "row_id": row_id,
        },
        "destination": {
            "owner": "Notion",
            "workspace": OWNER_WORKSPACE,
            "data_source_id": packet["data_source_id"],
        },
        "schema_version": ACTION_ITEM_SCHEMA_VERSION,
        "idempotency_key": idempotency_key.strip(),
        "request_digest": identity["request_digest"],
        "provenance": provenance or {},
        "preview": {"status": "dry-run", "mutation_count": 0},
    }


def verify_action_item_edit_post_fetch(record: dict[str, Any]) -> dict[str, Any]:
    """Return one validated fresh row and its proven Action Items post-fetch."""
    row_id = str(record.get("row_id") or "")
    changes = record.get("changes") or {}
    if not row_id or not isinstance(changes, dict) or not changes:
        raise ValueError("receipt action-item.edit incompleta")
    source = actions_source()
    if record.get("source_key") != source.key or not _same_id(
        record.get("data_source_id"), source.data_source_id
    ):
        raise RuntimeError(
            "receipt action-item.edit non appartiene al data source Actions canonico"
        )
    row = _notion_row(source, row_id)["rows"][0]
    if any(row["properties"].get(name) != value for name, value in changes.items()):
        raise RuntimeError("action-item.edit applicato ma il post-fetch è cambiato")
    return {
        "row": row,
        "post_fetch": {
            "row_id": row_id,
            "properties": {name: row["properties"].get(name) for name in changes},
        },
    }


def _typed_change(column: dict[str, Any], value: Any) -> dict[str, Any]:
    kind = str(column.get("type"))
    if (
        kind not in EDITABLE_TYPES
        or column.get("editable") is False
        or _is_protected_rich_text_field(column.get("name"), kind)
    ):
        raise ValueError(f"proprietà non modificabile: {column.get('name')}")
    if kind in {"title", "rich_text", "email", "url", "phone_number", "date"}:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"valore non valido per {kind}")
    if kind == "number" and value is not None and not isinstance(value, (int, float)):
        raise ValueError("valore non valido per number")
    if kind == "checkbox" and not isinstance(value, bool):
        raise ValueError("valore non valido per checkbox")
    if (
        kind in {"select", "status"}
        and value is not None
        and not isinstance(value, str)
    ):
        raise ValueError(f"valore non valido per {kind}")
    if kind == "multi_select" and not isinstance(value, list):
        raise ValueError("valore non valido per multi_select")
    if kind == "title":
        return {
            "title": []
            if value is None
            else [{"type": "text", "text": {"content": value}}]
        }
    if kind == "rich_text":
        return {
            "rich_text": []
            if value is None
            else [{"type": "text", "text": {"content": value}}]
        }
    if kind in {"select", "status"}:
        return {kind: None if value in (None, "") else {"name": value}}
    if kind == "multi_select":
        return {kind: [{"name": str(item)} for item in value]}
    if kind == "date":
        return {kind: None if not value else {"start": value}}
    return {kind: value}


def preview_notion_edit(
    database: dict[str, Any],
    row_id: str,
    changes: dict[str, Any],
    preimage_hash: str = "",
    *,
    source_spec: NotionSource | None = None,
) -> dict[str, Any]:
    if not changes:
        raise ValueError("patch Notion vuota")
    source_spec = source_spec or actions_source()
    row = next(
        (
            candidate
            for candidate in database.get("rows", [])
            if candidate.get("id") == row_id
        ),
        None,
    )
    if not row:
        raise ValueError("riga non trovata nella preimage fresca")
    # Prefer the full-value digest computed from the untruncated raw row when
    # the caller supplied one. The truncated list projection is only a display
    # shape: deriving the CAS preimage from it would accept a stale preimage
    # whenever the real change sits past the truncation boundary and
    # last_edited_time did not move.
    current_hash = str(database.get("preimage_by_row", {}).get(str(row_id)) or "") or _digest(
        {
            "id": row["id"],
            "last_edited_time": row.get("last_edited_time"),
            "properties": row.get("properties"),
        }
    )
    if preimage_hash and preimage_hash != current_hash:
        raise RuntimeError("preimage Notion obsoleta: ricaricare prima di modificare")
    schema_by_name = {column["name"]: column for column in database.get("schema", [])}
    patch: dict[str, Any] = {}
    before: dict[str, Any] = {}
    for name, value in changes.items():
        if name not in schema_by_name:
            raise ValueError(f"proprietà sconosciuta: {name}")
        patch[name] = _typed_change(schema_by_name[name], value)
        before[name] = row["properties"].get(name)
    if all(before[name] == value for name, value in changes.items()):
        raise ValueError("patch Notion senza modifiche")
    return {
        "packet_id": _digest({"row": row_id, "patch": patch, "preimage": current_hash}),
        "data_source_id": source_spec.data_source_id,
        "row_id": row_id,
        "preimage_hash": current_hash,
        "schema_fetched_at": database.get("fetched_at"),
        "before": before,
        "changes": changes,
        "properties_patch": patch,
        "rollback": before,
        "registry": {
            "data_source_id": _registry_data_source_id(),
            "status": "required-before-apply",
        },
        "status": "dry-run",
        "apply_allowed": True,
        "manual_gate": "apply consentito solo con conferma, Registry fresco, post-fetch e rollback",
    }


def _rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": value[:1900]}}]}


def _title(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value[:500]}}]}


def _verify_registry() -> None:
    registry_id = _registry_data_source_id()
    registry = workspace_sources._run_ntn(f"v1/data_sources/{registry_id}")
    if (
        not _same_id(registry.get("id"), registry_id)
        or _source_title(registry) != "Registry / Mutation Log"
    ):
        raise RuntimeError("Registry canonico non verificato")


def _open_notion_registry(
    *,
    packet: dict[str, Any],
    object_name: str,
    action: str,
) -> str:
    registry_id = _registry_data_source_id()
    registry_row = workspace_sources._run_ntn(
        "v1/pages",
        {
            "parent": {
                "type": "data_source_id",
                "data_source_id": registry_id,
            },
            "properties": {
                "Entry": _title(f"Signal Deck · {packet['packet_id']}"),
                "Action": {"select": {"name": action}},
                "Status": {"select": {"name": "planned"}},
                "Object": _rich_text(object_name),
                "Object ID": _rich_text(
                    packet.get("row_id") or f"date:{packet.get('date', '')}"
                ),
                "Before": _rich_text(
                    json.dumps(packet["before"], ensure_ascii=False, sort_keys=True)
                ),
                "After": _rich_text(
                    json.dumps(packet["changes"], ensure_ascii=False, sort_keys=True)
                ),
                "Rollback": _rich_text(
                    json.dumps(packet["rollback"], ensure_ascii=False, sort_keys=True)
                ),
            },
        },
    )
    registry_id = str(registry_row.get("id") or "")
    if not registry_id:
        raise RuntimeError("Registry row non creata")
    return registry_id


def _close_notion_registry(
    registry_id: str, status: str, after: dict[str, Any] | None = None
) -> None:
    properties: dict[str, Any] = {"Status": {"select": {"name": status}}}
    if after is not None:
        properties["After"] = _rich_text(
            json.dumps(after, ensure_ascii=False, sort_keys=True)
        )
    workspace_sources._run_ntn(
        f"v1/pages/{registry_id}",
        {"properties": properties},
        method="PATCH",
    )


def _apply_notion_edit(
    source_spec: NotionSource,
    row_id: str,
    changes: dict[str, Any],
    preimage_hash: str,
    packet_id: str,
    *,
    registry_action: str = "update",
    registry_object_name: str | None = None,
) -> dict[str, Any]:
    _verify_workspace()
    database = _notion_row(source_spec, row_id)
    packet = preview_notion_edit(
        database,
        row_id,
        changes,
        preimage_hash,
        source_spec=source_spec,
    )
    if packet["packet_id"] != packet_id:
        raise RuntimeError("packet non corrisponde alla preimage fresca")

    _verify_registry()
    registry_id = _open_notion_registry(
        packet=packet,
        object_name=registry_object_name or source_spec.title,
        action=registry_action,
    )

    try:
        workspace_sources._run_ntn(
            f"v1/pages/{row_id}",
            {"properties": packet["properties_patch"]},
            method="PATCH",
        )
        post_database = _notion_row(source_spec, row_id)
        post = post_database["rows"][0]
        if any(
            post["properties"].get(name) != value for name, value in changes.items()
        ):
            raise RuntimeError("post-fetch Notion non corrisponde alla patch")
        _close_notion_registry(
            registry_id,
            "applied",
            {name: post["properties"].get(name) for name in changes},
        )
    except Exception:
        # Never issue a blind inverse write here. A target PATCH can race with
        # another editor after our preimage check; an automatic rollback would
        # then clobber that newer value. The explicit rollback path below first
        # proves the current postimage still equals the recorded writer output.
        raise
    return {
        "ok": True,
        "packet_id": packet_id,
        "registry_id": registry_id,
        "row": post,
        "rollback": packet["rollback"],
    }


def apply_notion_edit(
    row_id: str,
    changes: dict[str, Any],
    preimage_hash: str,
    packet_id: str,
    data_source_id: str = "",
) -> dict[str, Any]:
    """Apply one fresh, Registry-backed Action Items patch with exact rollback."""
    source = actions_source()
    if data_source_id and not _same_id(data_source_id, source.data_source_id):
        raise RuntimeError(
            "Action Items rebind: il packet appartiene a un data source non più canonico"
        )
    return _apply_notion_edit(
        source,
        row_id,
        changes,
        preimage_hash,
        packet_id,
    )


def rollback_notion_edit(record: dict[str, Any]) -> dict[str, Any]:
    """Reverse one proven Action Items edit without overwriting later changes."""
    source = actions_source()
    if record.get("source_key") != source.key or not _same_id(
        record.get("data_source_id"), source.data_source_id
    ):
        raise ValueError("receipt non appartiene a DB Action Items")
    row_id = str(record.get("row_id") or "")
    before = record.get("before")
    after = record.get("after")
    if (
        not row_id
        or not isinstance(before, dict)
        or not before
        or not isinstance(after, dict)
    ):
        raise ValueError("receipt Action Items incompleta")
    if set(before) != set(after):
        raise ValueError("receipt Action Items incoerente")

    _verify_workspace()
    database = _notion_row(source, row_id)
    row = database["rows"][0]
    if any(row["properties"].get(name) != value for name, value in after.items()):
        raise RuntimeError("riga Notion cambiata dopo l'apply: rollback rifiutato")

    packet = preview_notion_edit(
        database,
        row_id,
        before,
        source_spec=source,
    )
    result = _apply_notion_edit(
        source,
        row_id,
        before,
        packet["preimage_hash"],
        packet["packet_id"],
        registry_action="rollback",
        registry_object_name=f"{source.title} · rollback",
    )
    return {
        "ok": True,
        "status": "rolled-back",
        "mutation_count": 1,
        "rollback_registry_id": result["registry_id"],
        "post_fetch": {
            "row_id": row_id,
            "properties": {
                name: result["row"]["properties"].get(name) for name in before
            },
        },
    }


def workspace_snapshot(force: bool = False) -> dict[str, Any]:
    """Active v3 workspace projection; excludes retired Daily/report surfaces."""
    return {
        "generated_at": _iso_now(),
        "notion": notion_database(force=force),
        "obsidian": obsidian_explorer(),
    }
