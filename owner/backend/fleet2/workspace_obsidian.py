"""Allowlisted Obsidian vault metadata explorer and versioned note reader."""
import stat
from pathlib import PurePosixPath

from .workspace_common import *
from .workspace_notion_read import SourceResult

MAX_NOTE_CONTENT_BYTES = 64 * 1024
MAX_EDITOR_CONTENT_BYTES = 256 * 1024


def obsidian_full_note(note: str) -> dict[str, Any]:
    """Read full UTF-8 source and its raw-byte preimage, never mutate the vault.

    Walk from the canonical vault directory using dir_fds and O_NOFOLLOW to
    prevent a symlink swap from escaping an approved note path. The PWA must
    not infer write permission from this read-only contract.
    """
    parts = PurePosixPath(note).parts
    if (
        not note
        or note.startswith("/")
        or "\\" in note
        or "//" in note
        or not parts
        or parts[0] not in ALLOWED_ROOTS
        or any(part in {".", ".."} or part in EXCLUDED_NAMES or part.startswith(".") for part in parts)
        or not note.endswith(".md")
        or PurePosixPath(*parts).as_posix() != note
    ):
        raise FileNotFoundError("note not allowed")

    root = _vault_root().resolve(strict=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    fd = os.open(root, directory_flags)
    try:
        for folder in parts[:-1]:
            next_fd = os.open(folder, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise FileNotFoundError("note not a regular file")
            if before.st_size > MAX_EDITOR_CONTENT_BYTES:
                raise OverflowError("note exceeds the editor read limit")
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                raw = handle.read(MAX_EDITOR_CONTENT_BYTES + 1)
            after = os.fstat(file_fd)
            latest = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if (
                before.st_ino != latest.st_ino
                or before.st_dev != latest.st_dev
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise RuntimeError("note changed while reading")
            if len(raw) > MAX_EDITOR_CONTENT_BYTES:
                raise OverflowError("note exceeds the editor read limit")
            content = raw.decode("utf-8", errors="strict")
            if "\x00" in content:
                raise ValueError("binary note not editable")
            return {
                "path": note,
                "content": content,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "modified": latest.st_mtime,
                "size": len(raw),
                "editable": False,
                "reason": "writer canonico non disponibile tramite questa API",
            }
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def _read_selected_note(path: Path) -> tuple[str, bool]:
    """Read at most 64 KiB from one already allowlisted note, never write."""
    with path.open("rb") as handle:
        data = handle.read(MAX_NOTE_CONTENT_BYTES + 1)
    return data[:MAX_NOTE_CONTENT_BYTES].decode("utf-8", errors="replace"), len(data) > MAX_NOTE_CONTENT_BYTES


def _vault_root() -> Path:
    configured = os.environ.get("SIGNALDECK_OBSIDIAN_ROOT", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / "Obsidian"


def _safe_relative(root: Path, candidate: Path) -> str | None:
    try:
        relative = candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not relative.parts or relative.parts[0] not in ALLOWED_ROOTS:
        return None
    if any(part in EXCLUDED_NAMES or part.startswith(".") for part in relative.parts):
        return None
    return relative.as_posix()


def _metadata(path: Path) -> dict[str, Any]:
    allowed = {
        "title",
        "uid",
        "status",
        "type",
        "area",
        "tags",
        "aliases",
        "source",
        "updated",
    }
    values: dict[str, Any] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[
            :MAX_FRONTMATTER_BYTES
        ]
    except OSError:
        return values
    if not text.startswith("---"):
        return values
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in allowed:
            continue
        values[key] = value.strip().strip('"').strip("'")[:240]
    return values


def _iter_notes(root: Path, folder: str = "") -> Iterable[Path]:
    base = root / folder if folder else root
    if not base.exists() or _safe_relative(root, base) is None and folder:
        return []
    found: list[Path] = []
    for candidate in base.rglob("*.md"):
        relative = _safe_relative(root, candidate)
        if relative:
            found.append(candidate)
    found.sort(
        key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True
    )
    return found


def _open_uri(relative: str) -> str:
    vault_name = os.environ.get("SIGNALDECK_OBSIDIAN_VAULT_NAME", "Obsidian")
    return f"obsidian://open?vault={quote(vault_name)}&file={quote(relative)}"


def _links_for(
    path: Path, root: Path, candidates: dict[str, str]
) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:MAX_LINK_SCAN_BYTES]
    except OSError:
        return []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in LINK_RE.finditer(text):
        target = (match.group(2) or match.group(1) or "").split("#", 1)[0].strip()
        target = target.removesuffix(".md").replace("%20", " ")
        relative = candidates.get(target) or candidates.get(Path(target).name)
        if not relative or relative in seen:
            continue
        seen.add(relative)
        results.append(
            {
                "title": Path(relative).stem,
                "path": relative,
                "open_uri": _open_uri(relative),
            }
        )
    return results[:30]


def obsidian_explorer(
    query: str = "", folder: str = "", note: str = ""
) -> dict[str, Any]:
    root = _vault_root()
    fetched_at = time.time()
    if not root.is_dir():
        return {
            "source": {
                **SourceResult(
                    "offline", False, f"vault non disponibile: {root}", fetched_at
                ).as_dict(),
                "kind": "obsidian",
                "vault": "Obsidian",
            },
            "nodes": [],
            "results": [],
            "selected": None,
            "allowlisted_roots": list(ALLOWED_ROOTS),
        }
    try:
        paths = list(_iter_notes(root, folder))
    except OSError as exc:
        return {
            "source": {
                **SourceResult("error", False, str(exc), fetched_at).as_dict(),
                "kind": "obsidian",
                "vault": "Obsidian",
            },
            "nodes": [],
            "results": [],
            "selected": None,
            "allowlisted_roots": list(ALLOWED_ROOTS),
        }
    # The recent index is bounded, but an explicitly selected, allowlisted
    # old note must remain readable even when it falls outside those 180.
    recent = paths[:MAX_NODES]
    if note:
        requested = note.removeprefix("/")
        selected_candidate = next(
            (path for path in paths if _safe_relative(root, path) == requested), None
        )
        if selected_candidate and selected_candidate not in recent:
            recent = recent[: MAX_NODES - 1] + [selected_candidate]
    paths = recent
    candidates: dict[str, str] = {}
    for path in paths:
        relative = _safe_relative(root, path)
        if relative:
            candidates.setdefault(relative.removesuffix(".md"), relative)
            candidates.setdefault(path.stem, relative)
    nodes: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    needle = query.strip().casefold()
    for path in paths:
        relative = _safe_relative(root, path)
        if not relative:
            continue
        stat = path.stat()
        metadata = _metadata(path)
        item = {
            "kind": "note",
            "title": metadata.get("title") or path.stem,
            "path": relative,
            "modified": stat.st_mtime,
            "size": stat.st_size,
            "metadata": metadata,
            "open_uri": _open_uri(relative),
        }
        nodes.append(item)
        if (
            not needle
            or needle in str(item["title"]).casefold()
            or needle in relative.casefold()
            or any(needle in str(value).casefold() for value in metadata.values())
        ):
            results.append(item)
    selected: dict[str, Any] | None = None
    if note:
        requested = note.removeprefix("/")
        selected_path = next(
            (path for path in paths if _safe_relative(root, path) == requested), None
        )
        if selected_path:
            selected_relative = _safe_relative(root, selected_path)
            if not selected_relative:
                selected_path = None
            else:
                outgoing = _links_for(selected_path, root, candidates)
        if selected_path:
            backlinks = []
            for candidate in paths:
                candidate_relative = _safe_relative(root, candidate)
                if not candidate_relative or candidate == selected_path:
                    continue
                if any(
                    link["path"] == selected_relative
                    for link in _links_for(candidate, root, candidates)
                ):
                    backlinks.append(
                        {
                            "title": candidate.stem,
                            "path": candidate_relative,
                            "open_uri": _open_uri(candidate_relative),
                        }
                    )
            try:
                content, content_truncated = _read_selected_note(selected_path)
            except OSError:
                content, content_truncated = "", False
            selected = {
                **next(item for item in nodes if item["path"] == selected_relative),
                "content": content,
                "content_truncated": content_truncated,
                "links": outgoing,
                "backlinks": backlinks[:30],
            }
    return {
        "source": {
            **SourceResult("healthy", True, None, fetched_at).as_dict(),
            "kind": "obsidian",
            "vault": "Obsidian",
        },
        "nodes": nodes[:MAX_NODES],
        "results": results[:MAX_SEARCH_RESULTS],
        "selected": selected,
        "allowlisted_roots": list(ALLOWED_ROOTS),
    }
