"""Regression tests for the owner-only, versioned Obsidian note reader."""

import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.auth import ALLOWED_ORIGINS_ENV, OWNER_LOGIN_ENV, TAILSCALE_LOGIN_HEADER
from backend.fleet2 import workspace_obsidian


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "Obsidian"
    (root / "05-knowledge" / "nested").mkdir(parents=True)
    (root / "04-areas" / "project").mkdir(parents=True)
    (root / "00-system").mkdir()
    (root / "09-archive").mkdir()
    (root / "05-knowledge" / ".private").mkdir()
    monkeypatch.setenv("SIGNALDECK_OBSIDIAN_ROOT", str(root))
    return root


def test_full_note_returns_exact_raw_hash_without_mutating_file(vault):
    target = vault / "05-knowledge" / "nested" / "Accents.md"
    content = "---\ntitle: Café\n---\n\n[[Other]]\n"
    raw = content.encode("utf-8")
    target.write_bytes(raw)
    before = target.stat()

    note = workspace_obsidian.obsidian_full_note("05-knowledge/nested/Accents.md")

    assert note["content"] == content
    assert note["sha256"] == hashlib.sha256(raw).hexdigest()
    assert note["size"] == len(raw)
    assert note["editable"] is False
    assert note["reason"]
    assert target.read_bytes() == raw
    assert target.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("invalid", [
    "../05-knowledge/nested/Accents.md",
    "/05-knowledge/nested/Accents.md",
    "05-knowledge/../../00-system/secret.md",
    "05-knowledge//nested/Accents.md",
    "05-knowledge/./nested/Accents.md",
    "05-knowledge/nested/Accents.md/",
    "05-knowledge/nested/Accents.txt",
    "05-knowledge\\nested\\Accents.md",
    "05-knowledge/.private/secret.md",
    "09-archive/old.md",
    "06-life/personal.md",
    "01-input/secret.md",
])
def test_full_note_rejects_unapproved_paths(vault, invalid):
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        workspace_obsidian.obsidian_full_note(invalid)


def test_full_note_rejects_directory_and_file_symlinks(vault, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("private")
    (vault / "05-knowledge" / "link.md").symlink_to(outside)
    (vault / "05-knowledge" / "linkdir").symlink_to(vault / "00-system", target_is_directory=True)
    with pytest.raises(OSError):
        workspace_obsidian.obsidian_full_note("05-knowledge/link.md")
    with pytest.raises(OSError):
        workspace_obsidian.obsidian_full_note("05-knowledge/linkdir/secret.md")


def test_full_note_rejects_oversize_and_invalid_utf8(vault):
    target = vault / "05-knowledge" / "large.md"
    target.write_bytes(b"a" * (workspace_obsidian.MAX_EDITOR_CONTENT_BYTES + 1))
    with pytest.raises(OverflowError):
        workspace_obsidian.obsidian_full_note("05-knowledge/large.md")
    target.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        workspace_obsidian.obsidian_full_note("05-knowledge/large.md")


def test_full_note_endpoint_is_session_protected_and_never_advertises_edit(vault, monkeypatch):
    target = vault / "05-knowledge" / "safe.md"
    target.write_text("# Verified note\n", encoding="utf-8")
    origin = "https://fleet.example.test"
    monkeypatch.setenv(OWNER_LOGIN_ENV, "owner@example.test")
    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, origin)
    monkeypatch.setenv("FLEETPROBE_TOKEN", "test-token")
    from backend import server

    app = server.create_app()
    app.state.session_store.clear()
    with TestClient(app, base_url=origin) as client:
        assert client.get("/api/workspace/obsidian/note", params={"path": "05-knowledge/safe.md"}).status_code == 401
        headers = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
        assert client.get("/", headers=headers).status_code == 200
        response = client.get("/api/workspace/obsidian/note", params={"path": "05-knowledge/safe.md"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["editable"] is False
        assert response.json()["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        excluded = client.get("/api/workspace/obsidian/note", params={"path": "09-archive/old.md"}, headers=headers)
        assert excluded.status_code == 404
        assert "09-archive" not in excluded.text
