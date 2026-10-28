"""Stdlib-only Codex account resolver for new Orca terminal sessions.

Account inventory belongs to Orca. Never accept a home directory from HTTP or
put CODEX_HOME into the Orca command/title/response.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

_ACCOUNT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MODEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")


def home_for(account_id: str) -> Path:
    home = Path.home()
    if account_id == "system":
        return home / ".codex"
    if not _ACCOUNT.fullmatch(account_id):
        raise ValueError("Invalid Codex account")
    return home / ".config" / "orca" / "codex-accounts" / account_id / "home"


def models_for(home: Path) -> list[str]:
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    return list(dict.fromkeys(
        item["slug"] for item in data["models"]
        if isinstance(item, dict)
        and isinstance(item.get("slug"), str)
        and _MODEL.fullmatch(item["slug"])
        and item.get("visibility") == "list"
        and item.get("supported_in_api") is True
    ))[:40]


def safe_accounts(native: dict) -> list[dict]:
    codex = native.get("codex", {})
    if not isinstance(codex, dict):
        return []
    result: list[dict] = []
    if isinstance(codex.get("systemDefault"), dict) and codex["systemDefault"].get("hasAuth") is True:
        result.append({"id": "system", "label": "Codex · sistema",
                       "models": models_for(home_for("system"))})
    managed = codex.get("accounts", [])
    if not isinstance(managed, list):
        return result
    seen = {"system"}
    for entry in managed:
        if not isinstance(entry, dict):
            continue
        account_id = entry.get("id")
        if (not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id)
                or account_id in seen or entry.get("managedHomeRuntime") != "host"):
            continue
        seen.add(account_id)
        home = home_for(account_id)
        if not home.is_dir():
            continue
        label = entry.get("workspaceLabel") or entry.get("email") or "Account Codex"
        if not isinstance(label, str):
            label = "Account Codex"
        result.append({"id": account_id, "label": label[:70],
                       "models": models_for(home)})
    return result


def native_inventory() -> dict:
    result = subprocess.run(["orca", "account", "list", "--json"],
                            capture_output=True, timeout=12, check=True)
    envelope = json.loads(result.stdout)
    if envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
        raise RuntimeError("Orca account inventory unavailable")
    return envelope["result"]


def main() -> int:
    if len(sys.argv) != 3:
        return 64
    account_id, model = sys.argv[1:]
    if not _MODEL.fullmatch(model):
        return 64
    try:
        selected = next((account for account in safe_accounts(native_inventory())
                         if account["id"] == account_id and model in account["models"]), None)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
        selected = None
    if selected is None:
        print("Account o modello Codex non disponibile; sessione non avviata.", file=sys.stderr)
        return 69
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home_for(account_id))
    os.execvpe("codex", ["codex", "--model", model], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
