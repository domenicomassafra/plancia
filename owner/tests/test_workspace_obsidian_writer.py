import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.fleet2 import workspace_obsidian_writer as writer


def test_mcp_uses_native_writer_bound_preflight_and_private_server_credential(monkeypatch):
    monkeypatch.setenv("CONTROLCENTER_OBSIDIAN_EDITING", "1")
    monkeypatch.setattr(writer.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="synthetic-private-token"))
    requests = []

    def transport(request):
        assert request.headers["authorization"] == "Bearer synthetic-private-token"
        if request.method == "DELETE":
            requests.append("closed")
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        requests.append(body)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}, headers={"Mcp-Session-Id": "fixture-session"})
        assert request.headers["mcp-session-id"] == "fixture-session"
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        arguments = body["params"]["arguments"]
        assert body["params"]["name"] == "vault_write"
        assert arguments["expected_sha256"] == "a" * 64
        assert arguments["mutation"]["preflight"]["canonical_destination"] == "05-knowledge/fixture.md"
        payload = {"sha256": "b" * 64, "mutation_receipt": {"outcome": "success"}}
        result = {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
        return httpx.Response(200, text="event: message\ndata: " + json.dumps(result) + "\n\n", headers={"content-type": "text/event-stream"})

    client = httpx.Client
    monkeypatch.setattr(writer.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(transport), **kw))
    result = writer.write("05-knowledge/fixture.md", "# Fixture\n", "a" * 64)
    assert result["sha256"] == "b" * 64
    assert "synthetic-private-token" not in json.dumps(result)
    assert requests[-1] == "closed"


def test_missing_mcp_result_fails_closed_without_stop_iteration():
    with pytest.raises(ValueError, match="no result"):
        writer._json(httpx.Response(200, text="event: message\n\n", headers={"content-type": "text/event-stream"}, request=httpx.Request("POST", writer.ENDPOINT)))
