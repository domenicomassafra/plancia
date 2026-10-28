import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.auth import ALLOWED_ORIGINS_ENV, OWNER_LOGIN_ENV, TAILSCALE_LOGIN_HEADER, CSRF_COOKIE, CSRF_HEADER
from backend.fleet2 import workspace_obsidian_writer as writer

ORIGIN = "https://fleet.example.test"
BASE = "/api/workspace/obsidian/operations/note.edit"


@pytest.fixture
def editor(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    target = root / "05-knowledge/fixture.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Fixture\n\nBefore\n")
    monkeypatch.setenv("SIGNALDECK_OBSIDIAN_ROOT", str(root))
    monkeypatch.setenv("CONTROLCENTER_DASHBOARD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CONTROLCENTER_OBSIDIAN_EDITING", "1")
    monkeypatch.setenv(OWNER_LOGIN_ENV, "owner@example.test")
    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, ORIGIN)
    monkeypatch.setenv("FLEETPROBE_TOKEN", "test-token")
    calls = []

    def write(path, content, expected):
        assert path == "05-knowledge/fixture.md"
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected
        calls.append(path)
        target.write_text(content)
        return {"sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    monkeypatch.setattr(writer, "write", write)
    from backend.server import create_app
    with TestClient(create_app(), base_url=ORIGIN) as client:
        headers = {TAILSCALE_LOGIN_HEADER: "owner@example.test"}
        client.get("/", headers=headers)
        headers.update({"Origin": ORIGIN, CSRF_HEADER: client.cookies[CSRF_COOKIE]})
        payload = {"path": "05-knowledge/fixture.md", "content": "# Fixture\n\nAfter café\n",
                   "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                   "idempotency_key": "fixture-editor"}
        yield client, headers, payload, target, calls


def test_preview_confirm_apply_readback_replay_and_redacted_audit(editor):
    client, headers, payload, target, calls = editor
    before = target.read_bytes()
    note = client.get("/api/workspace/obsidian/note", params={"path": payload["path"]}, headers=headers).json()
    assert note["editable"] is True
    preview = client.post(BASE + "/preview", json=payload, headers=headers)
    assert preview.status_code == 200
    packet = preview.json()
    assert packet["mutation_count"] == 0 and "Before" in packet["diff"] and "+After café" in packet["diff"]
    assert target.read_bytes() == before and not calls
    same = client.post(BASE + "/preview", json=payload, headers=headers).json()
    assert same["packet_id"] == packet["packet_id"]
    body = {"packet_id": packet["packet_id"], "confirmation": ""}
    assert client.post(BASE + "/apply", json=body, headers=headers).status_code == 422
    body["confirmation"] = "CONFERMA"
    applied = client.post(BASE + "/apply", json=body, headers=headers)
    assert applied.status_code == 200
    assert applied.json()["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.read_text() == payload["content"]
    replay = client.post(BASE + "/apply", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert len(calls) == 1
    audit = client.get("/api/receipts", headers=headers).text
    assert "obsidian.note.edit" in audit and "succeeded" in audit
    assert "After café" not in audit and payload["path"] not in audit


def test_stale_sha_syncthing_and_denied_path_never_reach_writer(editor):
    client, headers, payload, target, calls = editor
    packet = client.post(BASE + "/preview", json=payload, headers=headers).json()
    target.write_text("Concurrent peer edit\n")
    result = client.post(BASE + "/apply", json={"packet_id": packet["packet_id"], "confirmation": "CONFERMA"}, headers=headers)
    assert result.status_code == 409 and not calls
    assert target.read_text() == "Concurrent peer edit\n"
    for invalid in ["../05-knowledge/fixture.md", "00-system/secret.md", "05-knowledge/.secret.md", "05-knowledge/../secret.md"]:
        assert client.post(BASE + "/preview", json={**payload, "path": invalid}, headers=headers).status_code in {403, 409}
    payload["expected_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name("fixture.sync-conflict-20261023-peer.md").write_text("conflict")
    assert client.post(BASE + "/preview", json=payload, headers=headers).status_code == 409
    assert not calls


def test_owner_origin_csrf_and_unconfigured_writer_fail_closed(editor, monkeypatch):
    client, headers, payload, target, calls = editor
    for extra in [{CSRF_HEADER: "wrong"}, {"Origin": "https://foreign.example.test"}]:
        assert client.post(BASE + "/preview", json=payload, headers={**headers, **extra}).status_code == 403
    client.cookies.clear()
    assert client.post(BASE + "/preview", json=payload).status_code == 401
    monkeypatch.delenv("CONTROLCENTER_OBSIDIAN_EDITING")
    assert client.post(BASE + "/preview", json=payload, headers=headers).status_code in {401, 403}
    assert not calls
