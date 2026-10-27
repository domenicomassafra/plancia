"""Power actions with capability probing and durable receipts.

The rule this module enforces: an action is offered only when the host has been
proven able to run it unattended. Everything else is shown as unavailable with
the exact reason and the exact one-line fix, instead of a button that fails.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from backend.ssh_transport import run_ssh

from .registry import FleetHost, FleetRegistry
from .store import Store

logger = logging.getLogger("fleet2.actions")

PROBE_TTL_S = 300.0
CONFIRMATION_WORD = "CONFERMA"
SSH_CONFIG_PATH = str(Path.home() / ".ssh" / "config")


@dataclass(frozen=True, slots=True)
class ActionSpec:
    key: str
    label: str
    description: str
    destructive: bool


ACTIONS: Dict[str, ActionSpec] = {
    "reboot": ActionSpec(
        key="reboot",
        label="Riavvia",
        description="Riavvia il computer; la telemetria tornerà disponibile quando FleetProbe risponde di nuovo.",
        destructive=True,
    ),
    "shutdown": ActionSpec(
        key="shutdown",
        label="Spegni",
        description="Spegne il computer. Signal Deck non presume di poterlo riaccendere senza una capability Wake verificata.",
        destructive=True,
    ),
    "restart-agent": ActionSpec(
        key="restart-agent",
        label="Riavvia agente",
        description="Riavvia soltanto FleetProbe senza riavviare il computer.",
        destructive=False,
    ),
}

_COMMANDS: Dict[str, Dict[str, str]] = {
    "linux": {
        "probe": (
            "sudo -n -l /usr/bin/systemctl reboot >/dev/null 2>&1 && "
            "sudo -n -l /usr/bin/systemctl poweroff >/dev/null 2>&1; echo rc=$?"
        ),
        "restart-probe": "systemctl --user cat fleetprobe.service >/dev/null 2>&1; echo rc=$?",
        "reboot": "sudo -n systemctl reboot",
        "shutdown": "sudo -n systemctl poweroff",
        "restart-agent": "systemctl --user restart fleetprobe.service",
    },
    "darwin": {
        "probe": "sudo -n -l /sbin/shutdown >/dev/null 2>&1; echo rc=$?",
        "restart-probe": "launchctl print gui/$(id -u)/com.controlcenter.fleetprobe >/dev/null 2>&1; echo rc=$?",
        "reboot": "sudo -n shutdown -r now",
        "shutdown": "sudo -n shutdown -h now",
        "restart-agent": "launchctl kickstart -k gui/$(id -u)/com.controlcenter.fleetprobe",
    },
    "windows": {
        "probe": (
            "powershell -NoProfile -Command "
            '"if (([Security.Principal.WindowsPrincipal]'
            "[Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) "
            '{ \'rc=0\' } else { \'rc=1\' }"'
        ),
        "restart-probe": (
            "powershell -NoProfile -Command "
            '"if (Get-ScheduledTask -TaskName \'ControlCenter FleetProbe\' -ErrorAction SilentlyContinue) '
            "{ 'rc=0' } else { 'rc=1' }\""
        ),
        "reboot": "shutdown /r /t 5 /c \"Signal Deck reboot\"",
        "shutdown": "shutdown /s /t 5 /c \"Signal Deck shutdown\"",
        "restart-agent": (
            "powershell -NoProfile -Command "
            '"Stop-ScheduledTask -TaskName \'ControlCenter FleetProbe\'; '
            "Start-ScheduledTask -TaskName 'ControlCenter FleetProbe'\""
        ),
    },
}

_FIX_HINT = {
    "linux": "Run `sudo visudo` on the exact host and allow only the logged-in SSH user to run `/usr/bin/systemctl reboot` and `/usr/bin/systemctl poweroff` without a password.",
    "darwin": "Run `sudo visudo` on the host and add: <user> ALL=(ALL) NOPASSWD: /sbin/shutdown",
    "windows": "Run the fleetprobe logon task, or the SSH session, under an account with administrator rights.",
}


class ActionEngine:
    def __init__(self, registry: FleetRegistry, store: Store) -> None:
        self.registry = registry
        self.store = store
        self._probe_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ capability

    def capabilities(self, host: FleetHost, refresh: bool = False) -> Dict[str, Any]:
        with self._lock:
            cached = self._probe_cache.get(host.id)
            if cached and not refresh and time.time() - cached[0] < PROBE_TTL_S:
                return cached[1]

        commands = _COMMANDS.get(host.os, {})
        privileged = False
        transport_ok = False
        restart_proven = False
        probe_detail = (
            "runtime marked pending/offline in fleet registry"
            if host.offline
            else "no probe defined for this platform"
        )
        if not host.offline and commands.get("probe"):
            result = run_ssh(
                host.ssh_alias,
                commands["probe"],
                config_path=SSH_CONFIG_PATH,
                timeout_seconds=20,
            )
            probe_detail = (result.stdout or result.stderr or "").strip()[:200]
            transport_ok = result.ok
            privileged = bool(re.search(r"rc=0\s*$", probe_detail))
            if not result.ok and not privileged:
                probe_detail = probe_detail or f"ssh exit {result.exit_code}"
            if transport_ok and commands.get("restart-probe"):
                restart_result = run_ssh(
                    host.ssh_alias,
                    commands["restart-probe"],
                    config_path=SSH_CONFIG_PATH,
                    timeout_seconds=20,
                )
                restart_detail = (restart_result.stdout or restart_result.stderr or "").strip()[:200]
                restart_proven = bool(
                    restart_result.ok and re.search(r"rc=0\s*$", restart_detail)
                )

        entries: Dict[str, Any] = {}
        for key, spec in ACTIONS.items():
            available = True
            reason = None
            # A fix hint is only the *actual* way to enable an action. Policy
            # blocks (control plane, pending runtime, unsupported platform,
            # unproven agent service) have no privilege remediation, so they
            # must not advertise one.
            fix = None
            if host.offline:
                available, reason = (
                    False,
                    "runtime pending/offline: no action is allowed until this exact runtime identity is booted and proven",
                )
            elif not transport_ok:
                available, reason = (
                    False,
                    f"trasporto SSH non verificato ({probe_detail or 'host non raggiungibile'})",
                )
            elif key not in commands:
                available, reason = False, "nessun comando verificato per questa piattaforma"
            elif key == "restart-agent" and not restart_proven:
                available, reason = (
                    False,
                    "servizio FleetProbe non verificato sul runtime selezionato",
                )
            elif spec.destructive and host.is_control_plane:
                available, reason = (
                    False,
                    "questo computer ospita Signal Deck; spegnerlo o riavviarlo da qui rimuoverebbe il control plane",
                )
            elif spec.destructive and not privileged:
                available, reason = (
                    False,
                    f"privilegio unattended non verificato ({probe_detail or 'probe fallito'})",
                )
                fix = _FIX_HINT.get(host.os)
            entries[key] = {
                "key": key,
                "label": spec.label,
                "description": spec.description,
                "destructive": spec.destructive,
                "available": available,
                "reason": reason,
                "fix": fix,
            }

        payload = {
            "host_id": host.id,
            "probed_at": time.time(),
            "privileged": privileged,
            "transport_ok": transport_ok,
            "restart_agent_proven": restart_proven,
            "probe_detail": probe_detail,
            "actions": entries,
        }
        with self._lock:
            self._probe_cache[host.id] = (time.time(), payload)
        return payload

    # --------------------------------------------------------------- preview

    def preview(self, host: FleetHost, action_key: str) -> Dict[str, Any]:
        """Recheck the native capability and record a non-mutating audit receipt."""
        spec = ACTIONS.get(action_key)
        if spec is None:
            raise KeyError(f"unknown action {action_key}")
        capability = self.capabilities(host, refresh=True)["actions"][action_key]
        if not capability["available"]:
            raise PermissionError(capability["reason"] or "action unavailable")

        receipt_id = f"preview-{uuid.uuid4().hex[:20]}"
        self.store.open_receipt(
            receipt_id,
            host.id,
            f"{action_key}-preview",
            {"destructive": spec.destructive, "mutation_count": 0},
        )
        self.store.close_receipt(receipt_id, "dry-run", "mutation_count=0")
        return {
            "receipt_id": receipt_id,
            "host_id": host.id,
            "action": action_key,
            "preview": {
                "status": "dry-run",
                "mutation_count": 0,
                "requires_confirmation": spec.destructive,
            },
        }

    # -------------------------------------------------------------- dispatch

    def dispatch(
        self,
        host: FleetHost,
        action_key: str,
        confirmation: str,
        on_finished: Optional[Callable[[str], None]] = None,
        *,
        idempotency_key: str | None = None,
    ) -> Dict[str, Any]:
        spec = ACTIONS.get(action_key)
        if spec is None:
            raise KeyError(f"unknown action {action_key}")

        receipt_payload: Dict[str, Any] = {
            "host": host.name,
            "action": spec.label,
            "destructive": spec.destructive,
        }
        receipt_id: str | None = None
        if idempotency_key is not None:
            normalized_key = idempotency_key.strip()
            if not 8 <= len(normalized_key) <= 200:
                raise ValueError("idempotency_key must contain between 8 and 200 characters")
            digest = hashlib.sha256(
                f"{host.id}\0{action_key}\0{normalized_key}".encode("utf-8")
            ).hexdigest()
            receipt_id = f"action-{digest[:20]}"
            receipt_payload["idempotency_digest"] = digest
            # A true retry is answered from the durable receipt before a fresh
            # capability probe. Otherwise a transient outage caused by the
            # already-dispatched action could turn a successful retry into a
            # misleading new authorization error.
            existing = self.store.receipt(receipt_id)
            if existing:
                if existing.get("host_id") != host.id or existing.get("action") != action_key:
                    raise RuntimeError("idempotency receipt collision")
                return {
                    "receipt_id": receipt_id,
                    "host_id": host.id,
                    "action": action_key,
                    "replayed": True,
                    "outcome": existing.get("outcome"),
                }

        # A cached capability is useful for display, but never sufficient to
        # authorize a mutation. Re-probe the exact runtime immediately before
        # every dispatch so a five-minute-old SSH/sudo/service result cannot
        # become write authority.
        capability = self.capabilities(host, refresh=True)["actions"][action_key]
        if not capability["available"]:
            raise PermissionError(capability["reason"] or "action unavailable")
        if spec.destructive and confirmation.strip().upper() != CONFIRMATION_WORD:
            raise ValueError(f"destructive actions require the typed word {CONFIRMATION_WORD}")

        if idempotency_key is not None:
            if receipt_id is None:
                raise RuntimeError("idempotency receipt id was not derived")
            if not self.store.claim_receipt(
                receipt_id, host.id, action_key, receipt_payload
            ):
                existing = self.store.receipt(receipt_id)
                if not existing or existing.get("host_id") != host.id or existing.get("action") != action_key:
                    raise RuntimeError("idempotency receipt collision")
                return {
                    "receipt_id": receipt_id,
                    "host_id": host.id,
                    "action": action_key,
                    "replayed": True,
                    "outcome": existing.get("outcome"),
                }
        else:
            receipt_id = uuid.uuid4().hex[:12]
            self.store.open_receipt(
                receipt_id,
                host.id,
                action_key,
                receipt_payload,
            )
        thread = threading.Thread(
            target=self._execute,
            args=(receipt_id, host, action_key, on_finished),
            name=f"action-{receipt_id}",
            daemon=True,
        )
        thread.start()
        return {
            "receipt_id": receipt_id,
            "host_id": host.id,
            "action": action_key,
            "replayed": False,
        }

    def _execute(
        self,
        receipt_id: str,
        host: FleetHost,
        action_key: str,
        on_finished: Optional[Callable[[str], None]],
    ) -> None:
        command = _COMMANDS[host.os][action_key]
        try:
            result = run_ssh(
                host.ssh_alias,
                command,
                config_path=SSH_CONFIG_PATH,
                timeout_seconds=30,
            )
            detail = (result.stdout or result.stderr or "").strip()[:1000]
            if action_key in ("reboot", "shutdown"):
                # A real power transition can close SSH before the remote shell
                # returns. Only transport-loss/timeout is ambiguous enough to
                # count as dispatched; permission/command failures stay failed.
                dispatched = result.ok or result.failure in {"unreachable", "timeout"}
                if dispatched:
                    suffix = (
                        "command accepted"
                        if result.ok
                        else "transport closed before completion could be confirmed"
                    )
                    self.store.close_receipt(
                        receipt_id,
                        "dispatched",
                        detail or f"{action_key} dispatched · {suffix}",
                    )
                else:
                    self.store.close_receipt(
                        receipt_id,
                        "failed",
                        detail or f"{action_key} command failed",
                    )
            elif result.ok:
                self.store.close_receipt(receipt_id, "succeeded", detail or "completed")
            else:
                self.store.close_receipt(receipt_id, "failed", detail or "command failed")
        except Exception as exc:
            logger.exception("typed fleet action %s failed on %s", action_key, host.id)
            self.store.close_receipt(
                receipt_id,
                "failed",
                f"{type(exc).__name__}: action execution failed",
            )
        finally:
            if on_finished:
                on_finished(receipt_id)
