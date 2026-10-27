"""Continuous agent poller.

Runs for the life of the process, independent of whether a browser is open.
A host that does not answer is reported unreachable with the transport error;
it is never reported as powered off, because SSH or HTTP silence does not prove
a power state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import httpx

from .health import evaluate
from .registry import FleetHost, FleetRegistry
from .store import Store

logger = logging.getLogger("fleet2.poller")

REQUEST_TIMEOUT = 6.0
STALE_AFTER_S = 30.0
MAX_BACKOFF_S = 60.0
TAILSCALE_CACHE_TTL_S = 15.0
SUPPORTED_AGENT_SCHEMAS = {2}

TAILSCALE_BINARIES = (
    "tailscale",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
)


def _sample_stale_budget(sample: Dict[str, Any] | None) -> float:
    """Allow slower governed samplers without making fresh data look stale."""
    interval = (sample or {}).get("sample_interval_s")
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
        return STALE_AFTER_S
    return max(STALE_AFTER_S, min(300.0, float(interval) * 2.0 + 5.0))


def _find_tailscale_binary() -> Optional[str]:
    for candidate in TAILSCALE_BINARIES:
        if shutil.which(candidate):
            return candidate
        p = Path(candidate)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _query_tailscale_status(binary: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cmd = binary or _find_tailscale_binary()
    if not cmd:
        return None
    try:
        proc = subprocess.run(
            [cmd, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        logger.debug("failed to query tailscale status: %s", exc)
    return None


def _peer_matches(h_ip: str, h_name: str, h_id: str, node: Dict[str, Any]) -> bool:
    ips = node.get("TailscaleIPs") or []
    if h_ip and h_ip in ips:
        return True
    host_name = (node.get("HostName") or "").lower()
    dns_name = (node.get("DNSName") or "").rstrip(".").lower()
    dns_short = dns_name.split(".")[0] if dns_name else ""

    candidates = {c for c in (host_name, dns_name, dns_short) if c}
    if h_name and h_name in candidates:
        return True
    if h_id and h_id in candidates:
        return True
    return False


def _match_tailscale_peer(host: FleetHost, status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    matches = _match_tailscale_peers(host, status)
    return next((peer for peer in matches if peer.get("Online") is True), matches[0] if matches else None)


def _match_tailscale_peers(host: FleetHost, status: Dict[str, Any]) -> list[Dict[str, Any]]:
    ips = {value.strip() for value in (host.agent_ip, *host.agent_ips) if value}
    names = {value.strip().lower() for value in (host.agent_host, *host.agent_hosts, host.id) if value}
    nodes: list[Dict[str, Any]] = []
    self_node = status.get("Self")
    if isinstance(self_node, dict):
        nodes.append(self_node)
    nodes.extend(peer for peer in (status.get("Peer") or {}).values() if isinstance(peer, dict))

    matched: list[Dict[str, Any]] = []
    for node in nodes:
        node_ips = set(node.get("TailscaleIPs") or [])
        host_name = (node.get("HostName") or "").lower()
        dns_name = (node.get("DNSName") or "").rstrip(".").lower()
        dns_short = dns_name.split(".")[0] if dns_name else ""
        node_names = {value for value in (host_name, dns_name, dns_short) if value}
        if ips.intersection(node_ips) or names.intersection(node_names):
            matched.append(node)
    return matched


def _validate_agent_sample(host: FleetHost, payload: Any) -> Dict[str, Any]:
    """Reject a reachable endpoint that is not the expected FleetProbe contract.

    Reachability alone is not evidence that an endpoint belongs to the selected
    computer. This prevents a DNS/IP mix-up or stale agent schema from being
    rendered as trustworthy telemetry for the wrong host.
    """
    if not isinstance(payload, dict):
        raise ValueError("agent payload is not an object")
    if payload.get("host_id") != host.id:
        raise ValueError(
            f"agent identity mismatch: expected {host.id!r}, got {payload.get('host_id')!r}"
        )
    if payload.get("error"):
        raise ValueError(f"agent collector error: {str(payload['error'])[:180]}")
    schema = payload.get("schema")
    if schema not in SUPPORTED_AGENT_SCHEMAS:
        raise ValueError(f"unsupported agent schema {schema!r}")
    if not isinstance(payload.get("agent_version"), str) or not payload["agent_version"].strip():
        raise ValueError("agent version missing")
    if not isinstance(payload.get("sampled_at"), str) or not payload["sampled_at"].strip():
        raise ValueError("sample timestamp missing")
    sample_age = payload.get("sample_age_s")
    if not isinstance(sample_age, (int, float)) or isinstance(sample_age, bool) or sample_age < 0:
        raise ValueError("sample age missing or invalid")
    if payload.get("os") not in {"linux", "darwin", "windows"}:
        raise ValueError(f"unsupported sample os {payload.get('os')!r}")
    for block in ("cpu", "memory"):
        if not isinstance(payload.get(block), dict):
            raise ValueError(f"sample block {block!r} missing")
    if not isinstance(payload.get("disk"), list):
        raise ValueError("sample block 'disk' missing")
    return payload

class HostState:
    __slots__ = (
        "sample",
        "reachable",
        "error",
        "last_success",
        "last_attempt",
        "url",
        "consecutive_failures",
        "next_poll",
    )

    def __init__(self) -> None:
        self.sample: Optional[Dict[str, Any]] = None
        self.reachable = False
        self.error: Optional[str] = None
        self.last_success: Optional[float] = None
        self.last_attempt: Optional[float] = None
        self.url: Optional[str] = None
        self.consecutive_failures: int = 0
        self.next_poll: float = 0.0


class FleetPoller:
    def __init__(
        self,
        registry: FleetRegistry,
        store: Store,
        token: str,
        *,
        tailscale_status_fn: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        max_backoff_s: float = MAX_BACKOFF_S,
    ) -> None:
        self.registry = registry
        self.store = store
        self.token = token
        self.max_backoff_s = max_backoff_s
        self._tailscale_status_fn = tailscale_status_fn or _query_tailscale_status
        self._ts_status_cache: Optional[Dict[str, Any]] = None
        self._ts_status_cache_ts: float = 0.0
        self.states: Dict[str, HostState] = {host.id: HostState() for host in registry.hosts}
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._subscribers: set[asyncio.Queue] = set()

    def reset_backoff(self, host_id: Optional[str] = None) -> None:
        targets = [self.states[host_id]] if host_id and host_id in self.states else list(self.states.values())
        for state in targets:
            state.consecutive_failures = 0
            state.next_poll = 0.0

    def _compute_backoff(self, failures: int) -> float:
        return min(self.max_backoff_s, self.registry.poll_interval_s * (2 ** min(failures, 6)))

    async def _get_tailscale_status(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        if self._ts_status_cache is not None and (now - self._ts_status_cache_ts) < TAILSCALE_CACHE_TTL_S:
            return self._ts_status_cache
        data = await asyncio.to_thread(self._tailscale_status_fn)
        self._ts_status_cache = data
        self._ts_status_cache_ts = now
        return data
    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="fleet-poller")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---------------------------------------------------------------- polling

    async def _run(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        prune_at = time.time() + 3600
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            while not self._stopping.is_set():
                started = time.time()
                results = await asyncio.gather(
                    *(self._poll_host(client, host) for host in self.registry.hosts),
                    return_exceptions=True,
                )
                for host, result in zip(self.registry.hosts, results, strict=True):
                    if isinstance(result, BaseException):
                        logger.exception(
                            "unexpected poll failure for %s",
                            host.id,
                            exc_info=(type(result), result, result.__traceback__),
                        )
                await self._broadcast()
                if time.time() > prune_at:
                    await asyncio.to_thread(self.store.prune)
                    prune_at = time.time() + 3600
                elapsed = time.time() - started
                await asyncio.sleep(max(1.0, self.registry.poll_interval_s - elapsed))

    async def poll_once(self, host_id: Optional[str] = None, *, force: bool = False) -> Dict[str, Any]:
        """Refresh one/all governed hosts once and return the normal snapshot.

        This is the bounded read primitive used by the local fixed-profile
        ControlCenter agent bridge. It deliberately reuses the same registry,
        identity validation, token and health evaluation as the long-running
        dashboard poller instead of creating a second telemetry authority.
        """
        if host_id is not None and host_id not in self.states:
            raise KeyError(f"unknown host {host_id}")
        targets = (
            [self.registry.get(host_id)]
            if host_id is not None
            else list(self.registry.hosts)
        )
        hosts = [host for host in targets if host is not None]
        if force:
            for host in hosts:
                self.reset_backoff(host.id)
            self._ts_status_cache = None
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            await asyncio.gather(
                *(self._poll_host(client, host) for host in hosts),
                return_exceptions=False,
            )
        return self.snapshot()

    async def _poll_host(self, client: httpx.AsyncClient, host: FleetHost) -> None:
        state = self.states[host.id]
        now = time.time()
        if now < state.next_poll:
            return
        # Explicitly configured offline / paused
        if host.offline:
            state.reachable = False
            state.error = "runtime pending/offline in fleet config"
            state.last_attempt = now
            state.consecutive_failures = 0
            state.next_poll = now + MAX_BACKOFF_S
            return

        # Suppress probes to peers reported offline by Tailscale
        ts_status = await self._get_tailscale_status()
        if ts_status:
            peers = _match_tailscale_peers(host, ts_status)
            if peers and all(peer.get("Online") is False for peer in peers):
                state.reachable = False
                state.error = "peer offline on tailnet"
                state.last_attempt = now
                state.consecutive_failures += 1
                state.next_poll = now + self._compute_backoff(state.consecutive_failures)
                await asyncio.to_thread(self.store.record_sample, host.id, None, False)
                return

        state.last_attempt = now
        errors = []
        for url in self.registry.agent_urls(host):
            try:
                response = await client.get(f"{url}/metrics")
                if response.status_code == 401:
                    errors.append(f"{url}: agent rejected the poller token")
                    continue
                response.raise_for_status()
                state.sample = _validate_agent_sample(host, response.json())
                state.reachable = True
                state.error = None
                state.last_success = time.time()
                state.url = url
                state.consecutive_failures = 0
                state.next_poll = 0.0
                sample_fresh = float(state.sample.get("sample_age_s") or 0.0) <= _sample_stale_budget(state.sample)
                await asyncio.to_thread(
                    self.store.record_sample,
                    host.id,
                    state.sample if sample_fresh else None,
                    sample_fresh,
                )
                return
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                detail = str(exc).strip()
                errors.append(f"{url}: {type(exc).__name__}{f': {detail}' if detail else ''}")
        state.reachable = False
        state.error = "; ".join(errors) or "no agent endpoint configured"
        state.consecutive_failures += 1
        state.next_poll = time.time() + self._compute_backoff(state.consecutive_failures)
        await asyncio.to_thread(self.store.record_sample, host.id, None, False)
    # -------------------------------------------------------------- read side

    def snapshot(self) -> Dict[str, Any]:
        hosts = []
        for host in self.registry.hosts:
            state = self.states[host.id]
            age = time.time() - state.last_success if state.last_success else None
            transport_stale = age is not None and age > STALE_AFTER_S
            sample_age = (state.sample or {}).get("sample_age_s")
            sample_stale = (
                isinstance(sample_age, (int, float))
                and sample_age > _sample_stale_budget(state.sample)
            )
            stale = transport_stale or sample_stale
            error = state.error
            if state.reachable and sample_stale:
                error = f"agent sample stale ({float(sample_age):.1f}s old)"
            hosts.append(
                {
                    "id": host.id,
                    "name": host.name,
                    "subtitle": host.subtitle,
                    "os": (state.sample or {}).get("os") or host.os,
                    "role": host.role,
                    "image": host.image,
                    "notes": host.notes,
                    "is_control_plane": host.is_control_plane,
                    "lifecycle": "pending" if host.offline else "managed",
                    "reachable": state.reachable,
                    "stale": stale,
                    "error": error,
                    "last_success_age_s": round(age, 1) if age is not None else None,
                    "sample": state.sample,
                    "health": evaluate(state.sample, state.reachable and not stale),
                }
            )
        managed_hosts = [entry for entry in hosts if entry["lifecycle"] == "managed"]
        severities = [entry["health"]["severity"] for entry in managed_hosts]
        return {
            "generated_at": time.time(),
            "poll_interval_s": self.registry.poll_interval_s,
            "hosts": hosts,
            "fleet": {
                "total": len(hosts),
                "managed_total": len(managed_hosts),
                "pending": sum(1 for entry in hosts if entry["lifecycle"] == "pending"),
                "reachable": sum(
                    1
                    for entry in managed_hosts
                    if entry["reachable"] and not entry["stale"]
                ),
                "worst_severity": _worst(severities),
            },
        }

    # ------------------------------------------------------------ live stream

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def _broadcast(self) -> None:
        if not self._subscribers:
            return
        payload = self.snapshot()
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A slow tab must not slow the fleet down; it will catch up on
                # the next tick.
                pass


def _worst(severities: list[str]) -> str:
    order = {"ok": 0, "unknown": 1, "warn": 2, "crit": 3}
    if not severities:
        return "unknown"
    return max(severities, key=lambda value: order.get(value, 1))
