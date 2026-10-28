"""History and receipts must survive a restart and stay honest about gaps."""

import sqlite3
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.fleet2.store import Store


def make_store(tmp_path) -> Store:
    return Store(tmp_path / "state" / "fleet2.sqlite3")


def linux_sample(cpu=10.0, memory=50.0, temp=60.0, gpu=25.0, storage_available=67.0):
    return {
        "os": "linux",
        "cpu": {"percent": cpu},
        "memory": {"percent": memory},
        "disk": [{"mount": "/", "percent": 33.0, "free_percent": storage_available}],
        "gpu": {"utilization_percent": gpu},
        "thermal": {"cpu_c": temp},
        "battery": {"percent": None},
    }


def test_history_records_and_reads_back(tmp_path):
    store = make_store(tmp_path)
    for index in range(5):
        store.record_sample("minipc", linux_sample(cpu=10.0 + index), reachable=True)
    points = store.history("minipc", minutes=60)
    assert len(points) == 5
    assert points[-1]["cpu"] == 14.0
    assert points[-1]["storage_available"] == 67.0
    assert points[-1]["gpu"] == 25.0
    assert points[-1]["battery"] is None
    assert points[-1]["reachable"] is True


def test_store_applies_private_permissions_and_connection_pragmas(tmp_path):
    store = make_store(tmp_path)
    if sys.platform != "win32":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with store._connect() as conn:  # noqa: SLF001 - storage contract assertion
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_unreachable_sample_is_stored_without_inventing_values(tmp_path):
    store = make_store(tmp_path)
    store.record_sample("mac-studio", None, reachable=False)
    point = store.history("mac-studio", minutes=60)[0]
    assert point["cpu"] is None
    assert point["reachable"] is False


def test_darwin_history_stores_pressure_not_used_percentage(tmp_path):
    store = make_store(tmp_path)
    store.record_sample(
        "mac-studio",
        {
            "os": "darwin",
            "cpu": {"percent": 20.0},
            "memory": {"percent": 99.0, "pressure_used_percent": 12},
            "disk": [{"mount": "/System/Volumes/Data", "percent": 95.0, "available_percent": 14.5}],
            "gpu": {"utilization_percent": None},
            "thermal": {"cpu_c": None},
            "battery": {"percent": None},
        },
        reachable=True,
    )
    point = store.history("mac-studio", minutes=60)[0]
    assert point["memory"] == 12
    assert point["storage_available"] == 14.5
    assert point["gpu"] is None


def test_store_migrates_existing_sample_history_additively(tmp_path):
    path = tmp_path / "state" / "fleet2.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE samples ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, host_id TEXT NOT NULL, ts REAL NOT NULL, "
            "cpu REAL, memory REAL, disk REAL, temp REAL, battery REAL, reachable INTEGER NOT NULL DEFAULT 1)"
        )
    Store(path)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    assert {"storage_available", "gpu"}.issubset(columns)


def test_history_downsamples_to_the_requested_resolution(tmp_path):
    store = make_store(tmp_path)
    for index in range(120):
        store.record_sample("minipc", linux_sample(cpu=float(index % 50)), reachable=True)
    points = store.history("minipc", minutes=60, points=20)
    assert 1 <= len(points) <= 20


def test_two_week_history_preserves_host_identity_and_sample_timestamps(tmp_path):
    store = make_store(tmp_path)
    now = time.time()
    recent = now - 13 * 24 * 3600
    expired = now - 15 * 24 * 3600
    with store._connect() as conn:
        conn.executemany(
            "INSERT INTO samples (host_id, ts, cpu, reachable) VALUES (?, ?, ?, ?)",
            [
                ("matebooklinux", expired, 1.0, 1),
                ("matebooklinux", recent, 25.0, 1),
                ("matebook", recent, 95.0, 0),
            ],
        )

    assert store.history("matebooklinux", minutes=1440) == []
    points = store.history("matebooklinux", minutes=20160)
    assert len(points) == 1
    assert points[0]["t"] == recent
    assert points[0]["cpu"] == 25.0
    assert store.history("matebook", minutes=20160)[0]["reachable"] is False
    store.prune()
    assert len(store.history("matebooklinux", minutes=20160)) == 1


def test_history_bucket_reachability_uses_latest_state_not_any_old_success(tmp_path):
    store = make_store(tmp_path)
    for _ in range(3):
        store.record_sample("minipc", linux_sample(), reachable=True)
    store.record_sample("minipc", None, reachable=False)
    points = store.history("minipc", minutes=60, points=1)
    assert len(points) == 1
    assert points[0]["reachable"] is False


def test_receipts_open_and_close(tmp_path):
    store = make_store(tmp_path)
    store.open_receipt("abc123", "matebook", "reboot", {"destructive": True})
    running = store.receipts()[0]
    assert running["outcome"] == "running"
    assert running["payload"]["destructive"] is True

    store.close_receipt("abc123", "dispatched", "reboot dispatched")
    closed = store.receipts()[0]
    assert closed["outcome"] == "dispatched"
    assert closed["finished_at"] is not None


def test_server_restart_marks_abandoned_receipts_interrupted_but_leaves_agent_bridge(tmp_path):
    store = make_store(tmp_path)
    store.open_receipt("host-running", "mac-studio", "restart-agent", {})
    store.open_receipt("notion-running", "control-plane", "action-item.edit", {})
    store.open_receipt("agent-running", "mac-studio", "agent-action-apply", {})

    assert store.interrupt_abandoned_server_receipts() == 2
    assert store.receipt("host-running")["outcome"] == "interrupted"
    assert store.receipt("notion-running")["outcome"] == "interrupted"
    assert store.receipt("agent-running")["outcome"] == "running"


def test_receipt_payload_can_be_updated_and_fetched_by_id(tmp_path):
    store = make_store(tmp_path)
    store.open_receipt(
        "daily-operation",
        "control-plane",
        "daily.checkin",
        {"request_digest": "before"},
    )

    store.update_receipt_payload(
        "daily-operation",
        {"request_digest": "after", "registry_id": "registry-row"},
    )
    receipt = store.receipt("daily-operation")

    assert receipt is not None
    assert receipt["payload"] == {
        "request_digest": "after",
        "registry_id": "registry-row",
    }
    assert store.receipt("missing") is None


def test_corrupt_receipt_payload_does_not_break_audit_reads(tmp_path):
    store = make_store(tmp_path)
    store.open_receipt("bad-json", "minipc", "restart-agent", {})
    with store._connect() as conn:  # noqa: SLF001 - corruption resilience assertion
        conn.execute("UPDATE receipts SET payload = ? WHERE id = ?", ("{not-json", "bad-json"))
    assert store.receipt("bad-json")["payload"] == {}


def test_claim_receipt_allows_only_one_idempotent_owner(tmp_path):
    store = make_store(tmp_path)

    assert store.claim_receipt(
        "daily-operation",
        "control-plane",
        "daily.checkin",
        {"request_digest": "same"},
    )
    assert not store.claim_receipt(
        "daily-operation",
        "control-plane",
        "daily.checkin",
        {"request_digest": "same"},
    )


def test_profile_metadata_round_trips_and_updates_in_place(tmp_path):
    store = make_store(tmp_path)
    stored = store.set_profile_metadata(
        "mac-studio",
        "chrome:owner@example.com:Profile 2",
        "Gmail principale",
        "Personale",
    )
    assert stored["alias"] == "Gmail principale"

    store.set_profile_metadata(
        "mac-studio",
        "chrome:owner@example.com:Profile 2",
        "Amministrazione",
        "Lavoro",
    )
    metadata = store.profile_metadata()[
        ("mac-studio", "chrome:owner@example.com:Profile 2")
    ]
    assert metadata["alias"] == "Amministrazione"
    assert metadata["category"] == "Lavoro"


def test_prune_drops_only_expired_rows(tmp_path):
    store = make_store(tmp_path)
    store.record_sample("minipc", linux_sample(), reachable=True)
    with store._connect() as conn:  # noqa: SLF001 - storage-level assertion
        conn.execute(
            "INSERT INTO samples (host_id, ts, cpu, reachable) VALUES (?, ?, ?, 1)",
            ("minipc", time.time() - 40 * 24 * 3600, 5.0),
        )
    assert store.prune() == 1
    assert len(store.history("minipc", minutes=60)) == 1


def test_prune_removes_only_terminal_receipts_past_audit_retention(tmp_path):
    store = make_store(tmp_path)
    store.open_receipt("old-terminal", "minipc", "restart-agent", {})
    store.close_receipt("old-terminal", "succeeded", "done")
    store.open_receipt("old-running", "minipc", "restart-agent", {})
    store.open_receipt("recent-terminal", "minipc", "restart-agent", {})
    store.close_receipt("recent-terminal", "succeeded", "done")
    old = time.time() - (91 * 24 * 3600)
    with store._connect() as conn:  # noqa: SLF001 - retention fixture
        conn.execute(
            "UPDATE receipts SET finished_at = ?, started_at = ? WHERE id = ?",
            (old, old, "old-terminal"),
        )
        conn.execute(
            "UPDATE receipts SET started_at = ? WHERE id = ?",
            (old, "old-running"),
        )

    assert store.prune() == 1
    assert store.receipt("old-terminal") is None
    assert store.receipt("old-running") is not None
    assert store.receipt("recent-terminal") is not None
