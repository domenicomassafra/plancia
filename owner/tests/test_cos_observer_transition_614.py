"""The native observer replaces legacy dispatch notices on all three CoS hosts."""
import importlib.util
import json
from pathlib import Path
from threading import Event, Thread

import pytest

SOURCE = Path(__file__).parents[1] / "agent/cos_orca_observer.py"
spec = importlib.util.spec_from_file_location("observer_transition", SOURCE)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
RUN = "run_core"
PRODUCTS = "run_products"
SENDER = "term_789ff2fd-f715-4e28-9a67-8e102454743d"
SECOND_SENDER = "term_5084b440-301d-4a01-9574-67007e5a70d8"
SID = "18c4e45b-cc2c-4e5f-9e63-b9df6b8c34c2"


def test_minipc_is_part_of_the_existing_fleetprobe_pulse(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"run": RUN, "sender": SENDER}))
    read = []
    def call(host):
        def get(route):
            read.append((host, route))
            return {"inputs": []}
        return get
    monkeypatch.setattr(observer, "cos_client", lambda: call("studio"))
    monkeypatch.setattr(observer, "omarchy_call", call("omarchy"))
    monkeypatch.setattr(observer, "minipc_call", call("minipc"), raising=False)
    assert observer.main(["--config", str(config), "--state", str(tmp_path / "state.json")]) == 0
    assert {host for host, _ in read} == {"studio", "omarchy", "minipc"}


def test_repository_route_receives_minipc_notice_and_unbound_owner_is_retained(tmp_path):
    now = 1800000000
    entries = [{"id": str(n), "sessionId": SID, "state": "sent", "delivery": "sent",
                "createdAt": now * 1000, "text": {"text": f"## Ticket\nhttps://github.com/domenicomassafra/{repo}/issues/1"}}
               for n, repo in enumerate(("dcreator", "dstack"))]
    sent = []
    path = tmp_path / "state.json"
    result = observer.observe(path, RUN, SENDER,
        calls={"minipc": lambda route: {"inputs": entries} if route == "/v1/inputs" else {}},
        issue=lambda *args: {"state": "open"}, send=lambda *args: sent.append(args),
        answers=lambda *args: [], routes={"dcreator": (PRODUCTS, SENDER), "dstack": None}, now=now)
    assert result["sent"] == 1
    assert sent[0][0] == PRODUCTS
    assert "host=minipc" in sent[0][3]
    assert json.loads(path.read_text())["records"]["minipc:1"]["acked"] == {}


def test_partial_coordinator_binding_does_not_leak_unknown_or_unbound_repositories(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    from cos_orca_routes import load_routes

    owners = tmp_path / "dstack.json"
    owners.write_text(json.dumps({"coordinators": {
        "core": ["dstack"], "products": ["Strumentini.it"]}}))
    routes = load_routes({
        "coordinator_config": str(owners),
        "coordinator_routes": {"products": {"run": PRODUCTS, "sender": SENDER}},
        "issue_routes": {"unknown#1": {"run": RUN, "sender": SENDER}},
    })
    entries = [{"id": str(i), "sessionId": SID, "state": "sent", "delivery": "sent",
                "createdAt": 1800000000000,
                "text": {"text": f"## Ticket\nhttps://github.com/domenicomassafra/{repo}/issues/1"}}
               for i, repo in enumerate(("strumentini.it", "dstack", "unknown"))]
    sent = []
    state = tmp_path / "state.json"
    result = observer.observe(
        state, RUN, SENDER,
        calls={"minipc": lambda route: {"inputs": entries} if route == "/v1/inputs" else {}},
        issue=lambda *args: {"state": "open"},
        send=lambda *args: sent.append(args), answers=lambda *args: [],
        now=1800000000, routes=routes)
    assert result["sent"] == 1
    assert sent[0][0] == PRODUCTS
    assert "issue=strumentini.it#1" in sent[0][3]
    records = json.loads(state.read_text())["records"]
    assert records["minipc:1"]["acked"] == {}
    assert records["minipc:2"]["acked"] == {}


def test_two_future_coordinators_bind_real_runs_and_keep_dstack_repository_ownership(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    import cos_orca_routes
    source = tmp_path / "dstack.json"
    source.write_text(json.dumps({"coordinators": {"core": ["dstack"], "products": ["dcreator"]}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"run": RUN, "sender": SENDER, "coordinator_config": str(source),
                                 "issue_routes": {"dcreator#1": {"run": "run_old", "sender": SENDER}}}))
    calls = []
    def native(*args):
        calls.append(args)
        if args[:2] == ("orchestration", "run-show"):
            return {"run": {"id": args[-1], "coordinator_handle":
                            SENDER if args[-1] == RUN else SECOND_SENDER}}
        return {"terminal": {"handle": args[-1], "connected": True, "writable": True}}
    monkeypatch.setattr(cos_orca_routes, "_orca", native)
    for name, run in (("core", RUN), ("products", PRODUCTS)):
        assert observer.main(["--config", str(config), "--bind-coordinator", name, "--run", run]) == 0
    from cos_orca_routes import load_routes
    routes = load_routes(json.loads(config.read_text()))
    assert routes["dstack"] == (RUN, SENDER)
    assert routes["dcreator"] == (PRODUCTS, SECOND_SENDER)
    assert "dcreator#1" not in routes  # Stale issue overrides cannot cross the new repository scopes.
    assert sum(row[:2] == ("orchestration", "run-show") for row in calls) == 2
    assert not any(row[:2] == ("orchestration", "run-create") for row in calls)


def test_same_native_terminal_cannot_own_two_dstack_groups(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    import cos_orca_routes
    owners = tmp_path / "dstack.json"
    owners.write_text(json.dumps({"coordinators": {"core": ["dstack"], "products": ["dcreator"]}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"run": RUN, "sender": SENDER, "coordinator_config": str(owners)}))
    def native(*args):
        if args[:2] == ("orchestration", "run-show"):
            return {"run": {"id": args[-1], "coordinator_handle": SENDER}}
        return {"terminal": {"handle": SENDER, "connected": True, "writable": True}}
    monkeypatch.setattr(cos_orca_routes, "_orca", native)
    assert observer.main(["--config", str(config), "--bind-coordinator", "core", "--run", RUN]) == 0
    with pytest.raises(ValueError, match="coordinator identity"):
        observer.main(["--config", str(config), "--bind-coordinator", "products", "--run", PRODUCTS])
    assert "products" not in json.loads(config.read_text()).get("coordinator_routes", {})


def test_binding_waits_for_in_flight_notice_delivery(tmp_path, monkeypatch):
    """A completed binding must never race a pulse still using the old root route."""
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    import cos_orca_routes

    owners = tmp_path / "owners.json"
    owners.write_text(json.dumps({"coordinators": {"core": ["dstack"]}}))
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"run": RUN, "sender": SENDER,
                                  "coordinator_config": str(owners)}))
    pulse_entered, release_pulse, lookup_done, binding_done = (Event() for _ in range(4))
    errors = []

    def pulse(*args, **kwargs):
        pulse_entered.set()
        assert release_pulse.wait(3), "observer pulse was never released"
        return {"failed": 0}

    def native(*args):
        if args[:2] == ("orchestration", "run-show"):
            lookup_done.set()
            return {"run": {"id": PRODUCTS, "coordinator_handle": SECOND_SENDER}}
        return {"terminal": {"handle": SECOND_SENDER, "connected": True, "writable": True}}

    monkeypatch.setattr(observer, "observe", pulse)
    monkeypatch.setattr(observer, "cos_client", lambda: lambda _: {"inputs": []})
    monkeypatch.setattr(cos_orca_routes, "_orca", native)

    def invoke(argv, finished=None):
        try:
            observer.main(argv)
        except Exception as exc:
            errors.append(exc)
        finally:
            if finished:
                finished.set()

    heartbeat = Thread(target=invoke, args=(["--config", str(config), "--state", str(state)],))
    binding = Thread(target=invoke, args=(["--config", str(config), "--state", str(state),
                                          "--bind-coordinator", "core", "--run", PRODUCTS], binding_done))
    heartbeat.start()
    try:
        assert pulse_entered.wait(2)
        binding.start()
        assert lookup_done.wait(2)
        assert not binding_done.wait(0.15), "binding returned while old-route delivery was in progress"
    finally:
        release_pulse.set()
        heartbeat.join(timeout=3)
        if binding.ident is not None:
            binding.join(timeout=3)
    assert not errors
    assert binding_done.is_set()
    assert json.loads(config.read_text())["coordinator_routes"]["core"]["run"] == PRODUCTS
