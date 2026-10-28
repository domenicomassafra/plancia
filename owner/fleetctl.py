#!/usr/bin/env python3
"""Read-only local CLI for the governed Signal Deck control plane.

This intentionally talks to the same fixed-profile bridge used by agent runtimes
instead of bypassing browser authentication with obsolete HTTP endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from backend.fleet2.agent_bridge import BridgeError, build_default_bridge


def _dump(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Signal Deck local read-only CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Refresh and show the governed fleet snapshot")
    status.add_argument("--cached", action="store_true", help="Use the current poller snapshot without a forced refresh")

    host = sub.add_parser("host", help="Show one runtime identity")
    host.add_argument("host_id")
    host.add_argument("--cached", action="store_true")

    history = sub.add_parser("history", help="Show persistent telemetry history for one runtime identity")
    history.add_argument("host_id")
    history.add_argument("--minutes", type=int, default=60, choices=range(5, 1441), metavar="5..1440")

    receipts = sub.add_parser("receipts", help="Show bounded recent operation receipts")
    receipts.add_argument("--limit", type=int, default=20, choices=range(1, 51), metavar="1..50")

    capabilities = sub.add_parser("capabilities", help="Probe typed action capabilities without dispatching anything")
    capabilities.add_argument("host_id")
    capabilities.add_argument("--refresh", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    bridge = build_default_bridge()
    if args.command == "status":
        result = await bridge.call("controlcenter_fleet_status", {"refresh": not args.cached})
    elif args.command == "host":
        result = await bridge.call(
            "controlcenter_host_diagnostics",
            {"host_id": args.host_id, "refresh": not args.cached},
        )
    elif args.command == "history":
        result = await bridge.call(
            "controlcenter_telemetry_history",
            {"host_id": args.host_id, "minutes": args.minutes},
        )
    elif args.command == "receipts":
        result = await bridge.call("controlcenter_recent_receipts", {"limit": args.limit})
    elif args.command == "capabilities":
        result = await bridge.call(
            "controlcenter_action_capabilities",
            {"host_id": args.host_id, "refresh": args.refresh},
        )
    else:  # pragma: no cover - argparse enforces the command set
        raise BridgeError("unknown command")
    _dump(result)
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except (BridgeError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"fleetctl: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
