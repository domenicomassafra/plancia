"""Host-local CLI for chrome-profile-launcher: list, open, check, and agent."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Sequence

from . import AGENT_PROTOCOL_VERSION, __version__
from .config import browser_configs
from .profiles import Profile, load_profiles


def _cmd_list(profiles: list[Profile], query: str | None = None) -> int:
    """Print profiles with optional fuzzy search."""
    if query:
        tokens = [t.casefold() for t in query.strip().split()]
        profiles = [p for p in profiles if all(t in p.search_text for t in tokens)]

    if not profiles:
        print("Nessun profilo trovato.")
        return 1

    print(f"{'Browser':<8} {'Email':<42} {'Nome':<30} {'Profilo':<16}")
    print("-" * 96)
    for p in profiles:
        email_display = p.email or "(senza email)"
        print(f"{p.browser.name:<8} {email_display:<42.42} {p.label:<30.30} {p.directory:<16}")
    print(f"\n{len(profiles)} profili.")
    return 0


def _cmd_open(profiles: list[Profile], query: str | None = None) -> int:
    """Open profiles matching query (or all if no query)."""
    if query:
        tokens = [t.casefold() for t in query.strip().split()]
        profiles = [p for p in profiles if all(t in p.search_text for t in tokens)]

    if not profiles:
        print("Nessun profilo corrisponde alla ricerca.")
        return 1

    exes: dict[str, str] = {}
    for browser in browser_configs():
        exe = browser.detect_exe()
        if exe:
            exes[browser.key] = exe

    count = 0
    for p in profiles:
        exe = exes.get(p.browser.key)
        if not exe:
            print(f"ERRORE: {p.browser.name} non trovato.")
            continue
        args = [exe] + p.browser.exe_args(p.directory) + ["--new-window"]
        subprocess.Popen(args, close_fds=True)
        count += 1
        print(f"Aperto: {p.browser.name} / {p.label} ({p.directory})")

    print(f"\nAperti {count} profili.")
    return 0 if count == len(profiles) else 1


def _cmd_check() -> int:
    profiles = load_profiles()
    exes = {b.key: b.detect_exe() for b in browser_configs()}
    print(f"Profili totali: {len(profiles)}")
    for browser in browser_configs():
        count = sum(1 for p in profiles if p.browser.key == browser.key)
        print(f"  {browser.name}: {count} profili, exe={'OK' if exes.get(browser.key) else 'MANCANTE'}")
    print(f"\nData dir: {sys.modules['chrome_profile_launcher.config'].DATA_DIR}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chrome-profiles",
        description="Cross-platform Chrome & Edge profile launcher.",
    )
    sub = parser.add_subparsers(dest="command", title="comandi")

    # list
    list_cmd = sub.add_parser("list", help="Elenca profili")
    list_cmd.add_argument("query", nargs="?", help="Filtro fuzzy (nome, email, browser...)")

    # open
    open_cmd = sub.add_parser("open", help="Apri profili")
    open_cmd.add_argument("query", nargs="?", help="Filtro fuzzy per selezionare quali aprire")

    # check
    sub.add_parser("check", help="Verifica profili e percorsi browser")
    # Private machine-to-machine protocol used by the fleet web UI.
    agent_cmd = sub.add_parser("agent", help=argparse.SUPPRESS)
    agent_cmd.add_argument(
        "action",
        choices=("inventory", "capabilities", "open", "delete", "rename-preview", "rename"),
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        return _cmd_check()

    if args.command == "agent":
        from .fleet import (
            current_host_id,
            delete_local_targets,
            local_inventory,
            open_local_targets,
        )
        from .sync import preview_local_rename_targets, rename_local_targets

        if args.action == "inventory":
            # ASCII-safe JSON survives Windows OpenSSH console encodings.
            print(json.dumps(local_inventory()))
            return 0
        if args.action == "capabilities":
            print(
                json.dumps(
                    {
                        "schema_version": AGENT_PROTOCOL_VERSION,
                        "agent_version": __version__,
                        "runtime": sys.platform,
                        "host": {"id": current_host_id()},
                        "capabilities": {
                            "safe_exact_delete_v2": True,
                            "safe_exact_rename_v1": True,
                        },
                    }
                )
            )
            return 0
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError):
            print(json.dumps({"ok": False, "error": "Payload JSON non valido."}))
            return 2
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != AGENT_PROTOCOL_VERSION
        ):
            print(json.dumps({"ok": False, "error": "Versione protocollo non valida."}))
            return 2
        actions = {
            "open": open_local_targets,
            "delete": delete_local_targets,
            "rename-preview": preview_local_rename_targets,
            "rename": rename_local_targets,
        }
        action = actions[args.action]
        receipt = action(payload.get("targets", []))
        print(json.dumps(receipt))
        return 0 if receipt.get("ok") else 2

    profiles = load_profiles()

    if args.command == "list":
        return _cmd_list(profiles, getattr(args, "query", None))

    if args.command == "open":
        return _cmd_open(profiles, getattr(args, "query", None))

    # Default: show help
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
