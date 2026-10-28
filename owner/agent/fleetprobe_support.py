"""FleetProbe standard-library helpers shared by all host collectors."""
from __future__ import annotations
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

def _run(cmd: List[str], timeout: float = 8.0) -> Tuple[bool, str]:
    """Run a command and return (ok, stdout). Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    return True, proc.stdout


def _powershell(script: str, timeout: float = 15.0) -> Tuple[bool, str]:
    return _run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _percent(used: float, total: float) -> Optional[float]:
    if not total:
        return None
    return round(max(0.0, min(100.0, used / total * 100.0)), 1)


class Unmeasured:
    """Marker payload for a metric this host genuinely cannot report."""

    @staticmethod
    def value(reason: str) -> Dict[str, Any]:
        return {"value": None, "reason": reason}


def _parse_ps_table(out: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    collectors = {"ps", "iostat", "bash", "zsh", "sh", "fleetprobe.py"}
    for line in out.splitlines()[1:]:
        fields = line.split(None, 3)
        if len(fields) < 4:
            continue
        pid, cpu, mem, name = fields
        if not pid.isdigit():
            continue
        short = name.strip().rsplit("/", 1)[-1]
        if short in collectors:
            continue
        try:
            rows.append(
                {"pid": int(pid), "cpu": float(cpu), "mem": float(mem), "name": short}
            )
        except ValueError:
            continue
        if len(rows) >= 12:
            break
    return rows
