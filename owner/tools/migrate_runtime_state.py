#!/usr/bin/env python3
"""Conservative offline migration for the dashboard SQLite database.

Dry-run is the default.  The source is always preserved; rollback removes only
an unchanged destination copy.  This utility never opens SQLite or prints file
contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Sequence


DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))

from backend.runtime_config import (  # noqa: E402
    RuntimeConfig,
    ensure_private_directory,
    ensure_private_file,
)


class MigrationError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(f"{path}-wal"), Path(f"{path}-shm")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_paths(source: Path, destination: Path) -> dict[str, object]:
    source = _absolute(source)
    destination = _absolute(destination)
    source_sidecars = [str(path) for path in _sidecars(source) if path.exists()]
    destination_sidecars = [
        str(path) for path in _sidecars(destination) if path.exists()
    ]
    return {
        "operation": "inspect",
        "status": "observed",
        "source": str(source),
        "destination": str(destination),
        "source_exists": source.exists(),
        "source_is_regular_file": source.is_file() and not source.is_symlink(),
        "source_size_bytes": source.stat().st_size if source.is_file() else None,
        "destination_exists": destination.exists(),
        "destination_size_bytes": (
            destination.stat().st_size if destination.is_file() else None
        ),
        "source_sidecars": source_sidecars,
        "destination_sidecars": destination_sidecars,
    }


def _blockers(source: Path, destination: Path) -> list[str]:
    report = inspect_paths(source, destination)
    blockers = []
    if _absolute(source) == _absolute(destination):
        blockers.append("source_equals_destination")
    if not report["source_exists"]:
        blockers.append("source_missing")
    elif not report["source_is_regular_file"]:
        blockers.append("source_not_regular_file")
    if report["source_sidecars"]:
        blockers.append("source_has_wal_or_shm_sidecar")
    if report["destination_exists"]:
        blockers.append("destination_exists")
    if report["destination_sidecars"]:
        blockers.append("destination_has_wal_or_shm_sidecar")
    return blockers


def dry_run(source: Path, destination: Path) -> dict[str, object]:
    blockers = _blockers(source, destination)
    return {
        "operation": "dry-run",
        "status": "blocked" if blockers else "ready",
        "source": str(_absolute(source)),
        "destination": str(_absolute(destination)),
        "blockers": blockers,
        "mutation_performed": False,
    }


def verify_migration(source: Path, destination: Path) -> dict[str, object]:
    source = _absolute(source)
    destination = _absolute(destination)
    blockers = []
    if source == destination:
        blockers.append("source_equals_destination")
    for label, path in (("source", source), ("destination", destination)):
        if not path.is_file() or path.is_symlink():
            blockers.append(f"{label}_missing_or_not_regular")
        if any(sidecar.exists() for sidecar in _sidecars(path)):
            blockers.append(f"{label}_has_wal_or_shm_sidecar")
    matches = False
    if not blockers:
        matches = (
            source.stat().st_size == destination.stat().st_size
            and _digest(source) == _digest(destination)
        )
    return {
        "operation": "verify",
        "status": "verified" if matches else "mismatch",
        "source": str(source),
        "destination": str(destination),
        "size_bytes": source.stat().st_size if source.is_file() else None,
        "blockers": blockers,
        "matches": matches,
        "mutation_performed": False,
    }


def apply_migration(source: Path, destination: Path) -> dict[str, object]:
    source = _absolute(source)
    destination = _absolute(destination)
    blockers = _blockers(source, destination)
    if blockers:
        raise MigrationError(f"migration blocked: {', '.join(blockers)}")

    ensure_private_directory(destination.parent)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise MigrationError("migration blocked: destination_exists") from exc

    try:
        with source.open("rb") as source_handle, os.fdopen(
            descriptor, "wb"
        ) as destination_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                destination_handle.write(chunk)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        ensure_private_file(destination)
        verification = verify_migration(source, destination)
        if not verification["matches"]:
            destination.unlink(missing_ok=True)
            raise MigrationError("migration verification failed; copy removed")
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {
        "operation": "apply",
        "status": "applied",
        "source": str(source),
        "destination": str(destination),
        "size_bytes": destination.stat().st_size,
        "source_preserved": True,
        "mutation_performed": True,
    }


def rollback_migration(source: Path, destination: Path) -> dict[str, object]:
    source = _absolute(source)
    destination = _absolute(destination)
    verification = verify_migration(source, destination)
    if not verification["matches"]:
        raise MigrationError(
            "rollback blocked: source and destination are not identical"
        )
    destination.unlink()
    return {
        "operation": "rollback",
        "status": "rolled_back",
        "source": str(source),
        "destination": str(destination),
        "source_preserved": True,
        "mutation_performed": True,
    }


def _parser() -> argparse.ArgumentParser:
    config = RuntimeConfig.from_env()
    parser = argparse.ArgumentParser(
        description="Inspect or copy the dashboard database into host-local state."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--inspect", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--rollback", action="store_true")
    parser.add_argument(
        "--source",
        type=Path,
        default=DASHBOARD_ROOT / "data" / "telemetry.db",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=config.database_path,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = RuntimeConfig.from_env()
    destination = args.destination.expanduser().resolve()
    if not destination.is_relative_to(config.state_root):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": "destination_outside_runtime_state_root",
                }
            ),
            file=sys.stderr,
        )
        return 2

    try:
        if args.inspect:
            report = inspect_paths(args.source, destination)
        elif args.apply:
            config.ensure_directories()
            report = apply_migration(args.source, destination)
        elif args.verify:
            report = verify_migration(args.source, destination)
        elif args.rollback:
            report = rollback_migration(args.source, destination)
        else:
            report = dry_run(args.source, destination)
    except MigrationError as exc:
        print(
            json.dumps({"status": "blocked", "reason": str(exc)}),
            file=sys.stderr,
        )
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] not in {"blocked", "mismatch"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
