"""Host-local runtime paths for the dashboard control plane."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

STATE_DIR_ENV = "CONTROLCENTER_DASHBOARD_STATE_DIR"
CONTROL_CENTER_ROOT = Path(__file__).resolve().parents[2]


def xdg_home(name: str, default: str, environ: Mapping[str, str] | None = None) -> Path:
    """Resolve an XDG base directory; the specification ignores relative values."""
    env = os.environ if environ is None else environ
    value = env.get(name, "")
    candidate = Path(value).expanduser() if value else Path.home() / default
    return candidate if candidate.is_absolute() else Path.home() / default


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def ensure_private_file(path: Path) -> None:
    if os.name == "posix" and path.exists():
        path.chmod(0o600)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    state_root: Path
    database_path: Path
    logs_dir: Path
    cache_dir: Path
    diagnostics_dir: Path
    exports_dir: Path
    secret_refs_dir: Path
    backups_dir: Path

    sample_retention_days: int = 14
    receipt_retention_days: int = 90

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        controlcenter_root: Path | None = None,
    ) -> "RuntimeConfig":
        env = os.environ if environ is None else environ
        controlcenter = (controlcenter_root or CONTROL_CENTER_ROOT).resolve()
        configured = env.get(STATE_DIR_ENV)
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                raise ValueError(f"{STATE_DIR_ENV} must be an absolute path")
            state_root = candidate.resolve()
        else:
            base = xdg_home("XDG_STATE_HOME", ".local/state", env)
            state_root = (base / "controlcenter/dashboard-state").resolve()

        local_root = (controlcenter / "_local").resolve()
        if _is_within(state_root, controlcenter) and not _is_within(
            state_root, local_root
        ):
            raise ValueError("dashboard runtime state cannot use a synced path")

        return cls(
            state_root=state_root,
            database_path=state_root / "db" / "fleet2.sqlite3",
            logs_dir=state_root / "logs",
            cache_dir=state_root / "cache",
            diagnostics_dir=state_root / "diagnostics",
            exports_dir=state_root / "exports",
            secret_refs_dir=state_root / "secret-references",
            backups_dir=state_root / "backups",
        )

    @property
    def directories(self) -> tuple[Path, ...]:
        return (
            self.state_root,
            self.database_path.parent,
            self.logs_dir,
            self.cache_dir,
            self.diagnostics_dir,
            self.exports_dir,
            self.secret_refs_dir,
            self.backups_dir,
        )

    def ensure_directories(self) -> None:
        for directory in self.directories:
            ensure_private_directory(directory)


def get_runtime_config() -> RuntimeConfig:
    return RuntimeConfig.from_env()
