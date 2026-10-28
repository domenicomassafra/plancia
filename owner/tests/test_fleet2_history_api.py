"""The dashboard API exposes all retained Fleet2 history."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.fleet2.api import build_router
from backend.fleet2.store import Store


def test_history_api_allows_14_days_but_not_unbounded_reads(tmp_path):
    store = Store(tmp_path / "fleet.sqlite3")
    store.record_sample("matebooklinux", None, reachable=False)
    registry = SimpleNamespace(get=lambda host_id: object() if host_id == "matebooklinux" else None)
    app = FastAPI()
    app.include_router(
        build_router(
            registry=registry,
            poller=SimpleNamespace(),
            store=store,
            engine=SimpleNamespace(),
            session_store=SimpleNamespace(),
        )
    )
    client = TestClient(app)

    result = client.get("/api/fleet/matebooklinux/history", params={"minutes": 20160})
    assert result.status_code == 200
    assert result.json()["minutes"] == 20160
    assert result.json()["points"][0]["reachable"] is False
    assert client.get("/api/fleet/matebooklinux/history", params={"minutes": 20161}).status_code == 422
    assert client.get("/api/fleet/matebook/history", params={"minutes": 20160}).status_code == 404
