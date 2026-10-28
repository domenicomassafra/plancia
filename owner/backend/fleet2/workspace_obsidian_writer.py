"""Server-side MCP client for the existing canonical Obsidian writer."""

import json
import os
import subprocess
from pathlib import Path

import httpx

ENDPOINT = "http://127.0.0.1:8420/mcp"


def enabled() -> bool:
    return os.environ.get("CONTROLCENTER_OBSIDIAN_EDITING") == "1"


def mutation(path: str, expected: str) -> dict:
    parts = path.split("/")
    return {
        "reason": "Modifica confermata dall'owner in Plancia", "destination": path,
        "preflight": {
            "source_input_class": "owner-ui-edit",
            "entity_area": "/".join(parts[:2]) if parts[0] == "04-areas" else parts[0],
            "capability": "operations" if parts[0] == "04-areas" else "knowledge",
            "canonical_destination": path, "candidate_destinations": [path],
            "file_kind": "atomic-note", "operation": "update", "confidence": 1.0,
            "reason": "Modifica della nota canonica esistente selezionata dall'owner",
            "preimage_requirement": f"sha256:{expected}",
            "rollback_target": f"restore-preimage:{path}",
        },
    }


def _json(response: httpx.Response) -> dict:
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("writer response exceeds limit")
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for event in response.text.split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data:
                return json.loads(data)
        raise ValueError("writer returned no result")
    return response.json()


def write(path: str, content: str, expected: str) -> dict:
    if not enabled():
        raise RuntimeError("canonical editing is not enabled")
    # Only the installed secret consumer can resolve credentials; never return
    # stdout/stderr or exception headers to the browser or application logs.
    token = subprocess.run([str(Path.home() / ".local/bin/secret"), "get", "SIGNOR_OBSIDIAN_MCP_TOKEN"],
                           capture_output=True, text=True, timeout=5, check=True).stdout
    if not token or len(token) > 8192:
        raise RuntimeError("canonical credential is unavailable")
    with httpx.Client(timeout=30, trust_env=False, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
    }) as client:
        init = client.post(ENDPOINT, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "plancia-owner", "version": "1"},
        }})
        handshake = _json(init)
        if "result" not in handshake:
            raise RuntimeError("canonical MCP handshake failed")
        session = init.headers.get("mcp-session-id")
        if session:
            client.headers["Mcp-Session-Id"] = session
        client.headers["MCP-Protocol-Version"] = handshake["result"]["protocolVersion"]
        client.post(ENDPOINT, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).raise_for_status()
        reply = _json(client.post(ENDPOINT, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "vault_write", "arguments": {"path": path, "content": content,
                "create_dirs": False, "overwrite": True, "expected_sha256": expected,
                "mutation": mutation(path, expected)},
        }}))
        result = reply.get("result", {})
        if result.get("isError") or "error" in reply:
            raise ValueError("canonical writer rejected the edit")
        blocks = result.get("content", [])
        text = next((b.get("text") for b in blocks if b.get("type") == "text"), None)
        if not isinstance(text, str):
            raise RuntimeError("canonical writer returned no text result")
        payload = json.loads(text)
        if not isinstance(payload, dict) or "error" in payload:
            raise ValueError("canonical writer rejected the preimage or policy")
        if session:
            try:
                client.delete(ENDPOINT)
            except httpx.HTTPError:
                pass
        return {"sha256": payload.get("sha256"), "mutation_receipt": payload.get("mutation_receipt")}
