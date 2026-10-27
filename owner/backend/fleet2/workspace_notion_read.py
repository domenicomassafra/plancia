"""Read-only typed Notion Actions projections and source validation."""
from .workspace_common import *

@dataclass(frozen=True)
class NotionSource:
    key: str
    data_source_id: str
    title: str
    title_property: str
    edit_surface: str


def actions_source() -> NotionSource:
    """Resolve the one canonical Action Items source for this operation.

    The read, preview, packet, receipt and rollback must all share a single
    resolution of ``primary.operational_data_sources.actions``. A static
    import-time pin silently keeps reading a superseded surface after a rebind,
    while resolving twice could split one transaction across two surfaces; a
    missing binding or a divergent ``SIGNALDECK_ACTIONS_DATA_SOURCE_ID`` fails
    closed through the shared resolver before any fetch.
    """
    return NotionSource(
        ACTIONS_SOURCE_KEY,
        workspace_sources.actions_data_source_id(),
        ACTIONS_SOURCE_TITLE,
        ACTIONS_SOURCE_TITLE_PROPERTY,
        ACTIONS_SOURCE_EDIT_SURFACE,
    )


def _default_actions_source() -> NotionSource:
    """Identity placeholder used only when the canonical binding is unresolved."""
    return NotionSource(
        ACTIONS_SOURCE_KEY,
        "",
        ACTIONS_SOURCE_TITLE,
        ACTIONS_SOURCE_TITLE_PROPERTY,
        ACTIONS_SOURCE_EDIT_SURFACE,
    )


ACTION_ITEM_EDIT_OPERATION = "action-item.edit"
ACTION_ITEM_SCHEMA_VERSION = "workspace.operation.v1"


def _registry_data_source_id() -> str:
    """Resolve Registry authority from the canonical owner role, without fallback."""
    value = str(
        workspace_sources.primary_notion_role().get("registry_data_source_id") or ""
    ).strip()
    if not value:
        raise RuntimeError("primary Notion Registry identity is unavailable")
    return value


@dataclass(frozen=True)
class SourceResult:
    state: str
    available: bool
    reason: str | None
    fetched_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "available": self.available,
            "reason": self.reason,
            "fetched_at": self.fetched_at,
            "freshness_s": max(0, round(time.time() - self.fetched_at, 1)),
            "stale_after_s": FRESHNESS_SECONDS,
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def receipt_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _plain_text(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    fragments: list[str] = []
    for part in value.get("title", []) + value.get("rich_text", []):
        if isinstance(part, dict):
            fragments.append(
                str(part.get("plain_text") or part.get("text", {}).get("content") or "")
            )
    return "".join(fragments).strip()


def _property_value(prop: dict[str, Any]) -> Any:
    kind = str(prop.get("type") or "")
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return _plain_text(prop)
    if kind in {"select", "status"}:
        return (value or {}).get("name") if isinstance(value, dict) else None
    if kind == "multi_select":
        return [
            item.get("name")
            for item in (value or [])
            if isinstance(item, dict) and item.get("name")
        ]
    if kind == "date":
        return (value or {}).get("start") if isinstance(value, dict) else None
    if kind in {"relation", "people"}:
        return {"count": len(value or [])} if isinstance(value, list) else {"count": 0}
    if kind == "formula":
        if not isinstance(value, dict):
            return None
        formula_kind = value.get("type")
        return value.get(formula_kind)
    if kind in {"created_by", "last_edited_by"}:
        return (value or {}).get("name") if isinstance(value, dict) else None
    return value


def _schema(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, prop in (raw.get("properties") or {}).items():
        kind = str(prop.get("type") or "unknown")
        options = []
        config = prop.get(kind)
        if isinstance(config, dict):
            options = [
                str(option.get("name"))
                for option in config.get("options", [])
                if option.get("name")
            ]
        result.append(
            {
                "id": prop.get("id", name),
                "name": name,
                "type": kind,
                "editable": kind in EDITABLE_TYPES
                and not _is_protected_rich_text_field(name, kind),
                "options": options[:80],
            }
        )
    return result


def _is_protected_rich_text_field(name: Any, kind: Any) -> bool:
    if str(kind or "") != "rich_text":
        return False
    tokens = set(re.findall(r"[a-z0-9]+", str(name or "").lower()))
    return bool(tokens & PROTECTED_RICH_TEXT_TOKENS)


def _row(
    raw: dict[str, Any],
    schema: list[dict[str, Any]],
    *,
    truncate_strings: bool = True,
) -> dict[str, Any]:
    properties = raw.get("properties") or {}
    values: dict[str, Any] = {}
    types: dict[str, str] = {}
    for column in schema:
        name = column["name"]
        prop = properties.get(name) or {}
        types[name] = str(prop.get("type") or column["type"])
        value = _property_value(prop)
        if truncate_strings and isinstance(value, str):
            value = value[:500]
        values[name] = value
    title = (
        values.get("Action")
        or values.get("Action Item")
        or values.get("Name")
        or values.get("Entry")
        or next(
            (value for value in values.values() if isinstance(value, str) and value),
            "(senza titolo)",
        )
    )
    return {
        "id": raw.get("id"),
        "title": str(title)[:500],
        "url": raw.get("url"),
        "last_edited_time": raw.get("last_edited_time"),
        "properties": values,
        "property_types": types,
    }


def _same_id(left: Any, right: Any) -> bool:
    return str(left or "").replace("-", "") == str(right or "").replace("-", "")


def _source_title(raw: dict[str, Any]) -> str:
    return "".join(
        str(part.get("plain_text") or "")
        for part in raw.get("title", [])
        if isinstance(part, dict)
    ).strip()


def _verify_workspace() -> dict[str, str]:
    return workspace_sources.verified_primary_notion_identity()


def _verify_source(raw: dict[str, Any], source: NotionSource) -> None:
    title = _source_title(raw)
    if (
        not _same_id(raw.get("id"), source.data_source_id)
        or title != source.title
    ):
        raise RuntimeError(f"data source Notion non corrisponde a {source.title}")


def _source_url(raw: dict[str, Any], source: NotionSource) -> str:
    """Return a link for the verified source, never a stale API-projected URL."""
    raw_url = str(raw.get("url") or "")
    compact_id = source.data_source_id.replace("-", "")
    if compact_id and compact_id in raw_url.replace("-", ""):
        return raw_url
    return f"https://app.notion.com/p/{compact_id}"


def _preimage_digest(raw: dict[str, Any], schema: list[dict[str, Any]]) -> str:
    """Digest the preimage from the UNTRUNCATED row projection.

    The list projection returned by ``_notion_database`` shortens strings to
    ``_row``'s default 500 characters so the workspace UI cannot become a body
    dump. Reusing that truncated projection as the compare-and-set preimage
    would let a change that only differs past character 500 collide whenever
    ``last_edited_time`` is unchanged. Mutation previews therefore always
    digest the full value, exactly like the single-row fetch path.
    """
    row = _row(raw, schema, truncate_strings=False)
    return _digest(
        {
            "id": row["id"],
            "last_edited_time": row.get("last_edited_time"),
            "properties": row.get("properties"),
        }
    )


def _notion_database(
    source_spec: NotionSource | None = None,
    *,
    cursor: str = "",
    query: dict[str, Any] | None = None,
    _verify_identity=None,
    _registry_lookup=None,
) -> dict[str, Any]:
    fetched_at = time.time()
    registry_id = ""
    resolved = source_spec
    try:
        registry_id = (_registry_lookup or _registry_data_source_id)()
        resolved = source_spec or actions_source()
        identity = (_verify_identity or _verify_workspace)()
        source = workspace_sources._run_ntn(f"v1/data_sources/{resolved.data_source_id}")
        _verify_source(source, resolved)
        columns = _schema(source)
        request: dict[str, Any] = {"page_size": MAX_ROWS, **(query or {})}
        if cursor:
            request["start_cursor"] = cursor
        payload = workspace_sources._run_ntn(
            f"v1/data_sources/{resolved.data_source_id}/query",
            request,
        )
        rows = [_row(item, columns) for item in payload.get("results", [])[:MAX_ROWS]]
        return {
            "source": {
                **SourceResult("healthy", True, None, fetched_at).as_dict(),
                "kind": "notion",
                "workspace": identity["workspace"],
                "workspace_id": identity.get("workspace_id"),
                "data_source_id": resolved.data_source_id,
                "title": resolved.title,
                "url": _source_url(source, resolved),
                "source_key": resolved.key,
                "edit_surface": resolved.edit_surface,
            },
            "registry": {
                "data_source_id": registry_id,
                "required_for_write": True,
            },
            "schema": columns,
            "rows": rows,
            # Full-value digests per row, keyed by row id, computed from the raw
            # untruncated projection. `rows` stays truncated for display; the
            # preimage comparison must not be derived from it.
            "preimage_by_row": {
                str(item.get("id")): _preimage_digest(item, columns)
                for item in payload.get("results", [])[:MAX_ROWS]
            },
            "has_more": bool(payload.get("has_more")),
            "next_cursor": payload.get("next_cursor"),
            "fetched_at": fetched_at,
        }
    except Exception as exc:
        logger.warning("Notion workspace projection failed", exc_info=True)
        spec = resolved or _default_actions_source()
        return {
            "source": {
                **SourceResult(
                    "offline",
                    False,
                    workspace_sources.bounded_source_error(exc),
                    fetched_at,
                ).as_dict(),
                "kind": "notion",
                "workspace": OWNER_WORKSPACE,
                "data_source_id": spec.data_source_id,
                "title": spec.title,
                "source_key": spec.key,
                "edit_surface": spec.edit_surface,
            },
            "registry": {
                "data_source_id": registry_id,
                "required_for_write": True,
            },
            "schema": [],
            "rows": [],
            "has_more": False,
            "next_cursor": None,
            "fetched_at": fetched_at,
        }


def notion_database(force: bool = False, cursor: str = "") -> dict[str, Any]:
    """Fetch the one active Notion Actions source and a bounded page slice."""
    del force
    return _notion_database(cursor=cursor)


def _notion_row(source_spec: NotionSource, row_id: str, *, _verify_identity=None) -> dict[str, Any]:
    fetched_at = time.time()
    (_verify_identity or _verify_workspace)()
    source = workspace_sources._run_ntn(f"v1/data_sources/{source_spec.data_source_id}")
    _verify_source(source, source_spec)
    raw = workspace_sources._run_ntn(f"v1/pages/{row_id}")
    parent_id = str((raw.get("parent") or {}).get("data_source_id") or "")
    if not _same_id(parent_id, source_spec.data_source_id):
        raise ValueError(
            f"la riga non appartiene al data source {source_spec.title} allowlisted"
        )
    columns = _schema(source)
    return {
        "schema": columns,
        # Mutation previews and post-fetch verification must retain the full
        # property value. The list projection remains bounded via _row's
        # default so the workspace UI cannot accidentally become a body dump.
        "rows": [_row(raw, columns, truncate_strings=False)],
        "fetched_at": fetched_at,
    }


def notion_row(row_id: str) -> dict[str, Any]:
    """Fetch one fresh row and fail closed unless it belongs to Action Items."""
    return _notion_row(actions_source(), row_id)
