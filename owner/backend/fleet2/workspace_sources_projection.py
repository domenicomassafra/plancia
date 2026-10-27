"""Pure Obsidian vault projections and bounded Factory manifest readers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Callable

MANIFEST_MAX_BYTES = 256_000
PROPAGATION_MAX_ENTRIES = 12
PROPAGATION_MAX_PROJECTIONS = 8
OBSIDIAN_ALLOWED_ROOTS = (
    "00-system",
    "02-workbench",
    "03-projects",
    "04-areas",
    "05-knowledge",
)

def _recent_notes(root: Path, limit: int, skip_dirs: tuple[str, ...]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for path in root.rglob("*.md"):
        path_parts = path.relative_to(root).parts
        parts = set(path_parts)
        if (
            not path_parts
            or path_parts[0] not in OBSIDIAN_ALLOWED_ROOTS
            or parts & set(skip_dirs)
            or path.name.startswith(".")
        ):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(
            {
                "title": path.stem,
                "path": str(path.relative_to(root)),
                "modified": stat.st_mtime,
                "size": stat.st_size,
            }
        )
    entries.sort(key=lambda item: item["modified"], reverse=True)
    return entries[:limit]


def _active_vault_tasks(vault: Path, limit: int = 8) -> List[Dict[str, Any]]:
    tasks_dir = vault / "tasks"
    if not tasks_dir.is_dir():
        return []
    found: List[Dict[str, Any]] = []
    for entry in tasks_dir.iterdir():
        goal = entry / "goal.md"
        if not entry.is_dir() or not goal.is_file():
            continue
        text = goal.read_text(encoding="utf-8", errors="replace")[:2000]
        status = "open"
        title = entry.name
        for line in text.splitlines():
            if line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
            elif line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"')
        if status == "done":
            continue
        found.append(
            {
                "slug": entry.name,
                "title": title,
                "status": status,
                "modified": max(
                    (child.stat().st_mtime for child in entry.glob("*.md")), default=0.0
                ),
            }
        )
    found.sort(key=lambda item: item["modified"], reverse=True)
    return found[:limit]


def _read_manifest(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"manifest non disponibile: {path}")
    if path.stat().st_size > MANIFEST_MAX_BYTES:
        raise ValueError(f"manifest troppo grande: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"manifest non valido: {path.name}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    return value if all(char in "0123456789abcdef" for char in value) else None


def _propagation_unavailable(reason: str) -> Dict[str, Any]:
    return {
        "available": False,
        "state": "unavailable",
        "reason": reason,
        "entries": [],
    }


def _project_propagation(catalog: Dict[str, Any], _contract_digest: Callable[[Any], Dict[str, Any]]) -> Dict[str, Any]:
    """Expose bounded cross-surface evidence for Signal Deck consumers only."""
    propagation = catalog.get("propagation")
    if not isinstance(propagation, dict) or not isinstance(propagation.get("entries"), list):
        raise ValueError("catalogo capability senza propagazione valida")

    entries = []
    for raw in propagation["entries"]:
        if not isinstance(raw, dict):
            continue
        consumers = raw.get("consumers", [])
        if not isinstance(consumers, list):
            raise ValueError("consumer capability non validi")
        if "signal-deck" not in consumers:
            continue
        if len(entries) >= PROPAGATION_MAX_ENTRIES:
            break
        entry_id = str(raw.get("id") or "capability")
        source = _contract_digest(raw.get("canonical_contract"))
        projections = []
        drift = [] if source["match"] else ["source-digest"]
        raw_projections = raw.get("projections", [])
        if not isinstance(raw_projections, list):
            raise ValueError(f"proiezioni capability non valide: {entry_id}")
        for projection in raw_projections[:PROPAGATION_MAX_PROJECTIONS]:
            if not isinstance(projection, dict):
                continue
            projection_id = str(projection.get("id") or "projection")
            digest = _contract_digest(projection)
            aligned = projection.get("status") == "aligned" and digest["match"]
            projections.append(
                {
                    "id": projection_id,
                    "status": str(projection.get("status") or "unproven"),
                    "digest": digest,
                    "aligned": aligned,
                }
            )
            if not aligned:
                drift.append(f"projection:{projection_id}")
        rendered = bool(projections) and all(item["aligned"] for item in projections)
        receipt = raw.get("last_propagation_receipt") or {}
        liveness = raw.get("liveness_proof") or {}
        applied = str(receipt.get("status") or "unproven") if isinstance(receipt, dict) else "unproven"
        runtime = str(liveness.get("status") or "unproven") if isinstance(liveness, dict) else "unproven"
        limitations = raw.get("limitations") if isinstance(raw.get("limitations"), list) else []
        entries.append(
            {
                "id": entry_id,
                "name": str(raw.get("name") or entry_id),
                "version": raw.get("version") if isinstance(raw.get("version"), int) else None,
                "status": str(raw.get("status") or "unproven"),
                "source_valid": source["match"],
                "source_digest": source,
                "rendered": rendered,
                "projections": projections,
                "applied": applied,
                "runtime": runtime,
                "runtime_proven": runtime == "runtime-proven",
                "drift": drift,
                "limitations": [str(item) for item in limitations[:4] if isinstance(item, str)],
            }
        )

    state = "drift" if any(item["drift"] for item in entries) else "partial"
    if entries and all(
        item["source_valid"]
        and item["rendered"]
        and item["applied"] in {"applied", "live"}
        and item["runtime_proven"]
        for item in entries
    ):
        state = "healthy"
    return {"available": True, "state": state, "reason": None, "entries": entries}
