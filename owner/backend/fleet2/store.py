"""Host-local SQLite storage for telemetry history and action receipts.

The dashboard keeps sampling while nobody is watching, so history has to
survive in a file rather than in a browser tab.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id     TEXT NOT NULL,
    ts          REAL NOT NULL,
    cpu         REAL,
    memory      REAL,
    disk        REAL,
    temp        REAL,
    battery     REAL,
    storage_available REAL,
    gpu         REAL,
    reachable   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_samples_host_ts ON samples (host_id, ts);

CREATE TABLE IF NOT EXISTS receipts (
    id          TEXT PRIMARY KEY,
    host_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL,
    outcome     TEXT NOT NULL,
    detail      TEXT,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_receipts_started ON receipts (started_at DESC);

CREATE TABLE IF NOT EXISTS profile_metadata (
    host_id     TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    alias       TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL,
    PRIMARY KEY (host_id, profile_key)
);
"""

class Store:
    def __init__(
        self,
        path: Path,
        *,
        sample_retention_days: int = 14,
        receipt_retention_days: int = 90,
    ) -> None:
        if sample_retention_days <= 0 or receipt_retention_days <= 0:
            raise ValueError("retention days must be positive")
        self.path = path
        self.sample_retention_seconds = sample_retention_days * 24 * 3600
        self.receipt_retention_seconds = receipt_retention_days * 24 * 3600
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_samples(conn)
        if os.name == "posix":
            self.path.chmod(0o600)

    @staticmethod
    def _migrate_samples(conn: sqlite3.Connection) -> None:
        """Apply small additive migrations to persistent MiniPC history in place."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(samples)").fetchall()}
        for name in ("storage_available", "gpu"):
            if name not in columns:
                conn.execute(f"ALTER TABLE samples ADD COLUMN {name} REAL")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- samples

    def record_sample(self, host_id: str, sample: Optional[Dict[str, Any]], reachable: bool) -> None:
        cpu = memory = disk = storage_available = gpu = temp = battery = None
        if sample:
            cpu = (sample.get("cpu") or {}).get("percent")
            mem_block = sample.get("memory") or {}
            memory = (
                mem_block.get("pressure_used_percent")
                if sample.get("os") == "darwin" and mem_block.get("pressure_used_percent") is not None
                else mem_block.get("percent")
            )
            disks = sample.get("disk") or []
            disk = max((d.get("percent") or 0) for d in disks) if disks else None
            available_values = []
            for item in disks:
                value = item.get("available_percent")
                if value is None:
                    value = item.get("free_percent")
                if value is None and item.get("percent") is not None:
                    value = max(0.0, 100.0 - float(item["percent"]))
                if value is not None:
                    available_values.append(float(value))
            storage_available = min(available_values) if available_values else None
            gpu = (sample.get("gpu") or {}).get("utilization_percent")
            temp = (sample.get("thermal") or {}).get("cpu_c")
            battery = (sample.get("battery") or {}).get("percent")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO samples"
                " (host_id, ts, cpu, memory, disk, temp, battery, storage_available, gpu, reachable)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    host_id,
                    time.time(),
                    cpu,
                    memory,
                    disk,
                    temp,
                    battery,
                    storage_available,
                    gpu,
                    1 if reachable else 0,
                ),
            )

    def history(self, host_id: str, minutes: int = 60, points: int = 120) -> List[Dict[str, Any]]:
        since = time.time() - minutes * 60
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, cpu, memory, disk, temp, battery, storage_available, gpu, reachable FROM samples"
                " WHERE host_id = ? AND ts >= ? ORDER BY ts",
                (host_id, since),
            ).fetchall()
        if not rows:
            return []
        # Downsample by bucket so a chart never receives 720 points it cannot draw.
        bucket_size = max(1, (len(rows) + points - 1) // points)
        out: List[Dict[str, Any]] = []
        for start in range(0, len(rows), bucket_size):
            chunk = rows[start : start + bucket_size]
            out.append(
                {
                    "t": chunk[-1]["ts"],
                    "cpu": _mean(chunk, "cpu"),
                    "memory": _mean(chunk, "memory"),
                    "disk": _mean(chunk, "disk"),
                    "storage_available": _mean(chunk, "storage_available"),
                    "gpu": _mean(chunk, "gpu"),
                    "temp": _mean(chunk, "temp"),
                    "battery": _mean(chunk, "battery"),
                    "reachable": bool(chunk[-1]["reachable"]),
                }
            )
        return out

    def prune(self) -> int:
        now = time.time()
        sample_cutoff = now - self.sample_retention_seconds
        receipt_cutoff = now - self.receipt_retention_seconds
        with self._lock, self._connect() as conn:
            samples = conn.execute("DELETE FROM samples WHERE ts < ?", (sample_cutoff,))
            receipts = conn.execute(
                "DELETE FROM receipts WHERE finished_at IS NOT NULL AND finished_at < ?",
                (receipt_cutoff,),
            )
            return samples.rowcount + receipts.rowcount

    # --------------------------------------------------------------- receipts

    def open_receipt(self, receipt_id: str, host_id: str, action: str, payload: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO receipts (id, host_id, action, started_at, outcome, payload)"
                " VALUES (?, ?, ?, ?, 'running', ?)",
                (receipt_id, host_id, action, time.time(), json.dumps(payload)),
            )

    def claim_receipt(self, receipt_id: str, host_id: str, action: str, payload: Dict[str, Any]) -> bool:
        """Atomically reserve one idempotent operation receipt."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO receipts"
                " (id, host_id, action, started_at, outcome, payload)"
                " VALUES (?, ?, ?, ?, 'running', ?)",
                (receipt_id, host_id, action, time.time(), json.dumps(payload)),
            )
            return cursor.rowcount == 1

    def close_receipt(self, receipt_id: str, outcome: str, detail: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE receipts SET finished_at = ?, outcome = ?, detail = ? WHERE id = ?",
                (time.time(), outcome, detail[:4000], receipt_id),
            )

    def interrupt_abandoned_server_receipts(self) -> int:
        """Close server-owned receipts left running by a previous process.

        Agent-bridge receipts can be owned by a separate live stdio process and
        therefore are deliberately excluded from server-start reconciliation.
        """
        now = time.time()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE receipts
                   SET finished_at = ?, outcome = 'interrupted',
                       detail = 'server restarted before a terminal outcome was recorded'
                 WHERE outcome = 'running'
                   AND action NOT LIKE 'agent-%'
                """,
                (now,),
            )
            return cursor.rowcount

    def update_receipt_payload(self, receipt_id: str, payload: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE receipts SET payload = ? WHERE id = ?",
                (json.dumps(payload), receipt_id),
            )

    def receipt(self, receipt_id: str) -> Dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        return _receipt(row) if row else None

    def receipts(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM receipts ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_receipt(row) for row in rows]

    # -------------------------------------------------------- profile metadata

    def set_profile_metadata(
        self,
        host_id: str,
        profile_key: str,
        alias: str,
        category: str,
    ) -> Dict[str, Any]:
        updated_at = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_metadata (host_id, profile_key, alias, category, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(host_id, profile_key) DO UPDATE SET
                    alias = excluded.alias,
                    category = excluded.category,
                    updated_at = excluded.updated_at
                """,
                (host_id, profile_key, alias, category, updated_at),
            )
        return {
            "host_id": host_id,
            "profile_key": profile_key,
            "alias": alias,
            "category": category,
            "updated_at": updated_at,
        }

    def set_profile_category(
        self,
        host_id: str,
        profile_key: str,
        category: str,
    ) -> Dict[str, Any]:
        """Persist the canonical category without overwriting a user alias."""

        updated_at = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_metadata (host_id, profile_key, alias, category, updated_at)
                VALUES (?, ?, '', ?, ?)
                ON CONFLICT(host_id, profile_key) DO UPDATE SET
                    category = excluded.category,
                    updated_at = excluded.updated_at
                """,
                (host_id, profile_key, category, updated_at),
            )
            row = conn.execute(
                "SELECT alias, category FROM profile_metadata WHERE host_id = ? AND profile_key = ?",
                (host_id, profile_key),
            ).fetchone()
        return {
            "host_id": host_id,
            "profile_key": profile_key,
            "alias": row["alias"] if row else "",
            "category": row["category"] if row else category,
            "updated_at": updated_at,
        }

    def profile_metadata(self) -> Dict[tuple[str, str], Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT host_id, profile_key, alias, category, updated_at"
                " FROM profile_metadata"
            ).fetchall()
        return {
            (row["host_id"], row["profile_key"]): {
                "alias": row["alias"],
                "category": row["category"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }


def _mean(rows: List[sqlite3.Row], column: str) -> Optional[float]:
    values = [row[column] for row in rows if row[column] is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _receipt(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "host_id": row["host_id"],
        "action": row["action"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "outcome": row["outcome"],
        "detail": row["detail"],
        "payload": _safe_json_object(row["payload"]),
    }


def _safe_json_object(value: str | None) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
