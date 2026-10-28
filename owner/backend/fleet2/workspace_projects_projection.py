"""Bounded owner agent and ChatGPT project evidence projections."""
from .workspace_common import *

def external_context_projection(
    notion: dict[str, Any],
    obsidian: dict[str, Any],
    manifests: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Project existing owner state without copying source bodies or adding writers."""
    checked_at = time.time() if now is None else now
    manifest_available = manifests.get("available") is True

    def source_state(value: dict[str, Any]) -> str:
        source = value.get("source") or {}
        return (
            "proven"
            if manifest_available
            and source.get("available") is True
            and source.get("freshness_s", float("inf"))
            <= source.get("stale_after_s", 0)
            else "unproven"
        )

    provider_gate = manifests.get("provider_gate") or {}
    project_state = (
        "manual"
        if manifest_available and provider_gate.get("state") == "manual"
        else "unproven"
    )
    notion_state = source_state(notion)
    obsidian_state = source_state(obsidian)
    sources = [
        {
            "id": "notion",
            "owner": "Notion",
            "state": notion_state,
            "adapter": "ntn",
            "receipt_id": None,
            "reason": None
            if notion_state == "proven"
            else "manifest o lettura fresca non disponibili",
        },
        {
            "id": "obsidian",
            "owner": "Obsidian",
            "state": obsidian_state,
            "adapter": "metadata-only",
            "receipt_id": None,
            "reason": None
            if obsidian_state == "proven"
            else "manifest o indice metadata fresco non disponibili",
        },
        {
            "id": "ordinary-chatgpt-project",
            "owner": "Dodo AI Factory",
            "state": project_state,
            "adapter": "owner manifest + manual provider refresh",
            "receipt_id": None,
            "reason": (
                provider_gate.get("reason")
                or provider_gate.get("drift")
                or "identità Project o prova runtime provider non disponibili"
            ),
        },
    ]
    states = {source["state"] for source in sources}
    return {
        "state": "unproven"
        if not manifest_available or "unproven" in states
        else ("manual" if "manual" in states else "proven"),
        "available": manifest_available,
        "generated_at": checked_at,
        "write": "none",
        "canon": False,
        "sources": sources,
        "agents_projects": agents_projects_projection(manifests, now=checked_at),
        "reason": None
        if manifest_available
        else manifests.get("reason", "manifest receipt missing"),
    }


def agents_projects_projection(
    manifests: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Project bounded agent/project evidence without creating another registry."""
    checked_at = time.time() if now is None else now
    manifest_available = manifests.get("available") is True
    manifest_reason = manifests.get("reason") or "manifest owner non disponibile"

    def evidence(state: str, reason: str | None = None) -> dict[str, Any]:
        return {"state": state, "reason": reason}

    profile_entries = manifests.get("agent_profiles") if manifest_available else []
    agents: list[dict[str, Any]] = []
    for profile in profile_entries if isinstance(profile_entries, list) else []:
        if not isinstance(profile, dict):
            continue
        digest = (
            profile.get("digest") if isinstance(profile.get("digest"), str) else None
        )
        source_valid = profile.get("published") is True and bool(digest)
        rendered = profile.get("rendered") is True
        runtime_proven = profile.get("runtime_proven") is True
        runtime_value = str(profile.get("runtime") or "deferred")
        reason = (
            profile.get("reason") if isinstance(profile.get("reason"), str) else None
        )
        agents.append(
            {
                "id": str(profile.get("id") or "hermes-signor"),
                "name": str(profile.get("name") or "Hermes · Signor"),
                "kind": "agent-profile",
                "source": evidence(
                    "source-proven" if source_valid else "unproven", reason
                ),
                "generated": evidence("generated" if rendered else "deferred", reason),
                "runtime": evidence(
                    "runtime-proven" if runtime_proven else runtime_value, reason
                ),
                "digest": digest,
            }
        )
    if not agents:
        agents.append(
            {
                "id": "hermes-signor",
                "name": "Hermes · Signor",
                "kind": "agent-profile",
                "source": evidence(
                    "deferred" if manifest_available else "unproven",
                    "manifesto del profilo pubblicato non ancora collegato"
                    if manifest_available
                    else manifest_reason,
                ),
                "generated": evidence(
                    "deferred" if manifest_available else "unproven",
                    "nessuna proiezione profilo ricevuta dal manifest owner"
                    if manifest_available
                    else manifest_reason,
                ),
                "runtime": evidence(
                    "deferred" if manifest_available else "unproven",
                    "runtime non provato dal manifest owner"
                    if manifest_available
                    else manifest_reason,
                ),
                "digest": None,
            }
        )

    propagation = manifests.get("propagation") or {}
    entries = propagation.get("entries") if propagation.get("available") is True else []
    projects: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("id") or "Project ChatGPT")
        identifier = str(entry.get("id") or "")
        if "chatgpt" not in f"{identifier} {name}".lower():
            continue
        source_digest = (
            entry.get("source_digest")
            if isinstance(entry.get("source_digest"), dict)
            else {}
        )
        digest = (
            source_digest.get("actual")
            if isinstance(source_digest.get("actual"), str)
            else None
        )
        source_valid = (
            entry.get("source_valid") is True and source_digest.get("match") is True
        )
        rendered = entry.get("rendered") is True
        runtime_value = str(entry.get("runtime") or "deferred")
        runtime_proven = entry.get("runtime_proven") is True
        limitations = (
            entry.get("limitations")
            if isinstance(entry.get("limitations"), list)
            else []
        )
        limitation = next((str(item) for item in limitations if item), None)
        projects.append(
            {
                "id": identifier or "ordinary-chatgpt-project",
                "name": name,
                "kind": "chatgpt-project-skill",
                "source": evidence(
                    "source-proven" if source_valid else "unproven", limitation
                ),
                "generated": evidence(
                    "generated" if rendered else "deferred", limitation
                ),
                "runtime": evidence(
                    "runtime-proven" if runtime_proven else runtime_value, limitation
                ),
                "digest": digest,
            }
        )

    if not projects:
        gate_reason = (
            propagation.get("reason")
            or "input Project/skill o prova runtime non ancora collegati"
        )
        projects.append(
            {
                "id": "ordinary-chatgpt-project",
                "name": "ChatGPT Project / skill",
                "kind": "chatgpt-project-skill",
                "source": evidence(
                    "deferred" if manifest_available else "unproven", gate_reason
                ),
                "generated": evidence(
                    "deferred" if manifest_available else "unproven", gate_reason
                ),
                "runtime": evidence(
                    "deferred" if manifest_available else "unproven", gate_reason
                ),
                "digest": None,
            }
        )

    levels = [
        value["state"]
        for item in [*agents, *projects]
        for value in (item["source"], item["generated"], item["runtime"])
    ]
    state = (
        "unproven"
        if not manifest_available
        else "manual"
        if "manual" in levels
        else "unproven"
        if "unproven" in levels
        else "deferred"
        if "deferred" in levels
        else "proven"
    )
    return {
        "available": manifest_available,
        "state": state,
        "reason": None if manifest_available else manifest_reason,
        "generated_at": checked_at,
        "write": "none",
        "agents": agents,
        "projects": projects,
    }
