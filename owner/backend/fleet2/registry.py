"""Fleet registry: the small, hand-maintained description of the machines.

Everything measured comes from the agents. This file only says which machines
exist, how to reach them and how to present them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_FILE = REPO_ROOT / "fleet.json"


@dataclass(frozen=True, slots=True)
class FleetHost:
    id: str
    name: str
    subtitle: str
    agent_host: str
    agent_ip: str
    ssh_alias: str
    os: str
    role: str
    image: Optional[str]
    mac: Optional[str]
    notes: str
    offline: bool = False
    agent_hosts: tuple[str, ...] = ()
    agent_ips: tuple[str, ...] = ()

    @property
    def is_control_plane(self) -> bool:
        return self.role == "primary"


@dataclass(frozen=True, slots=True)
class FleetRegistry:
    agent_port: int
    poll_interval_s: float
    hosts: List[FleetHost]

    def get(self, host_id: str) -> Optional[FleetHost]:
        return next((host for host in self.hosts if host.id == host_id), None)

    def agent_urls(self, host: FleetHost) -> List[str]:
        """MagicDNS first, then the pinned tailnet IP as a fallback."""
        if host.offline:
            return []
        candidates: List[str] = []
        for name in (host.agent_host, *host.agent_hosts):
            if name:
                candidates.append(f"http://{name}:{self.agent_port}")
        for ip in (host.agent_ip, *host.agent_ips):
            if ip:
                candidates.append(f"http://{ip}:{self.agent_port}")
        return list(dict.fromkeys(candidates))


@lru_cache(maxsize=1)
def load_registry(path: Path | None = None) -> FleetRegistry:
    installed_config = os.environ.get("PLANCIA_FLEET_CONFIG")
    if path is None and os.environ.get("PLANCIA_REQUIRE_PRIVATE_CONFIG") == "1" and not installed_config:
        raise RuntimeError("installed Plancia requires an explicit private fleet inventory")
    source = path if path is not None else Path(installed_config or FLEET_FILE)
    raw = json.loads(source.read_text(encoding="utf-8"))
    hosts = [
        FleetHost(
            id=entry["id"],
            name=entry["name"],
            subtitle=entry.get("subtitle", ""),
            agent_host=entry.get("agent_host", ""),
            agent_ip=entry.get("agent_ip", ""),
            ssh_alias=entry.get("ssh_alias", entry["id"]),
            os=entry.get("os", "unknown"),
            role=entry.get("role", "workstation"),
            image=entry.get("image"),
            mac=entry.get("mac"),
            notes=entry.get("notes", ""),
            offline=bool(entry.get("offline", False) or entry.get("poll_paused", False)),
            agent_hosts=tuple(entry.get("agent_hosts", [])),
            agent_ips=tuple(entry.get("agent_ips", [])),
        )
        for entry in raw.get("hosts", [])
    ]
    return FleetRegistry(
        agent_port=int(raw.get("agent_port", 9631)),
        poll_interval_s=float(raw.get("poll_interval_s", 5)),
        hosts=hosts,
    )
