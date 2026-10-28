"""Bind existing native Orca Runs to DStack's repository coordinator groups."""
import fcntl
import json
import os
from pathlib import Path
import re

import shutil
import subprocess

RUN_ID = re.compile(r"run_[A-Za-z0-9]+\Z")
TERMINAL_ID = re.compile(r"term_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def _orca_executable():
    """LaunchAgents do not inherit a login shell's PATH on macOS."""
    for candidate in (shutil.which("orca"), "/usr/local/bin/orca",
                      "/opt/homebrew/bin/orca", str(Path.home() / ".local/bin/orca")):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError("native Orca executable unavailable")


def _orca(*args, timeout=15):
    executable = _orca_executable()
    completed = subprocess.run([executable, *args, "--json"], capture_output=True,
                               text=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError("native Orca command unavailable")
    response = json.loads(completed.stdout)
    if response.get("ok") is not True:
        raise RuntimeError("native Orca command rejected")
    return response.get("result") or {}


DEFAULT_COORDINATORS = Path.home() / ".local/state/dstack/cos-orchestrator/config.json"
REPO = re.compile(r"[A-Za-z][A-Za-z0-9.-]*\Z")


def groups(config):
    source = Path(config.get("coordinator_config", DEFAULT_COORDINATORS)).expanduser()
    value = json.loads(source.read_text()).get("coordinators", {})
    if not isinstance(value, dict):
        raise ValueError("invalid DStack coordinator groups")
    owners = set()
    for name, repos in value.items():
        if not name or not isinstance(repos, list):
            raise ValueError("invalid DStack coordinator repositories")
        for repo in repos:
            if not isinstance(repo, str) or not REPO.fullmatch(repo) or repo.casefold() in owners:
                raise ValueError("ambiguous repository coordinator ownership")
            owners.add(repo.casefold())
    return value


def destination(value):
    if (not isinstance(value, dict) or not isinstance(value.get("run"), str)
            or not RUN_ID.fullmatch(value["run"]) or not isinstance(value.get("sender"), str)
            or not TERMINAL_ID.fullmatch(value["sender"])):
        raise ValueError("invalid native coordinator destination")
    return value["run"], value["sender"]


def load_routes(config):
    issue_routes = config.get("issue_routes", {})
    bindings = config.get("coordinator_routes", {})
    if not isinstance(issue_routes, dict) or not isinstance(bindings, dict):
        raise ValueError("invalid coordinator routes")
    routes = {key: destination(value) for key, value in issue_routes.items()}
    if bindings:
        ownership = groups(config)
        if set(bindings) - set(ownership):
            raise ValueError("coordinator binding has no DStack repository scope")
        identities = [destination(binding) for binding in bindings.values()]
        if (len({run for run, _ in identities}) != len(identities)
                or len({sender for _, sender in identities}) != len(identities)):
            raise ValueError("ambiguous native coordinator identity")
        # After the first per-owner binding, DStack is the sole routing authority.
        # A missing owner or binding stays undelivered, including old issue overrides.
        routes = {"*": None}
        for name, names in ownership.items():
            target = destination(bindings[name]) if name in bindings else None
            routes.update({repo: target for repo in names})
    return routes


def bind(path, name, run, write, state):
    """Resolve a supplied real Run; never create a Run or launch an agent."""
    if not isinstance(run, str) or not RUN_ID.fullmatch(run):
        raise ValueError("a real native Orca Run is required")
    native = (_orca("orchestration", "run-show", "--id", run).get("run") or {})
    handle = native.get("coordinator_handle")
    if native.get("id") != run or not isinstance(handle, str) or not TERMINAL_ID.fullmatch(handle):
        raise ValueError("Run has no verified native coordinator terminal")
    terminal = _orca("terminal", "show", "--terminal", handle).get("terminal") or {}
    if terminal.get("handle") != handle or terminal.get("connected") is not True or terminal.get("writable") is not True:
        raise ValueError("coordinator terminal is not connected and writable")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = Path(state)
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with state.with_suffix(".lock").open("a") as observer_lock:
        os.fchmod(observer_lock.fileno(), 0o600)
        fcntl.flock(observer_lock, fcntl.LOCK_EX)
        with path.with_suffix(".lock").open("a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            config = json.loads(path.read_text()) if path.exists() else {}
            if name not in groups(config):
                raise ValueError("coordinator is not configured in DStack")
            config.setdefault("coordinator_routes", {})[name] = {"run": run, "sender": handle}
            config.setdefault("run", run)
            config.setdefault("sender", handle)
            load_routes(config)
            write(path, config)
    return {"coordinator": name, "run": run, "sender": handle}
