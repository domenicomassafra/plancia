"""#571: CoS native inputs and current turns reach Orca without the old wave ledger."""
import importlib.util
import json
import sys
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "agent/cos_orca_observer.py"
spec = importlib.util.spec_from_file_location("cos_orca_observer_571", SOURCE)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)

SID = "18c4e45b-cc2c-4e5f-9e63-b9df6b8c34c2"
INPUT = "1ab24dd8-0ac9-49e8-8566-a9e2e71d8bb6"
RUN = "run_9f7d0e38ea0b"
SENDER = "term_789ff2fd-f715-4e28-9a67-8e102454743d"


def test_native_issue_and_finished_turn_reach_run_once(tmp_path):
    now = 1800000000
    item = {"id": INPUT, "sessionId": SID, "state": "sent", "delivery": "sent",
            "createdAt": (now - 20) * 1000,
            "text": {"text": "## Ticket\nhttps://github.com/domenicomassafra/controlcenter/issues/571\n"}}
    issue = {"state": "open", "updated_at": "2026-10-25T10:30:00Z", "labels": []}
    native = {"session": {"id": SID, "conversationId": "conv", "toolCalls": 2,
                          "lastToolCallAt": (now - 600) * 1000,
                          "lastTurnEndAt": (now - 30) * 1000},
              "live": {"activeTurnId": "turn", "stopPending": False}}
    delivered = []
    def call(route):
        return {"inputs": [item]} if route == "/v1/inputs" else native
    def send(run, sender, subject, body, token):
        delivered.append((run, sender, subject, body, token))
    def step():
        return observer.observe(tmp_path / "state.json", RUN, SENDER,
                                calls={"studio": call}, issue=lambda repo, number: issue,
                                send=send, answers=lambda host, sid: [], now=now)
    assert step()["sent"] == 1  # Acknowledged ownership, no invented completion.
    assert step()["sent"] == 0
    issue["updated_at"] = "2026-10-25T11:00:00Z"
    assert step()["sent"] == 1
    assert "aggiornamento" in delivered[-1][2]
    native["live"]["activeTurnId"] = None
    native["session"]["lastTurnEndAt"] = (now + 1) * 1000
    assert step()["sent"] == 1
    assert "senza prova" in delivered[-1][3]
    assert step()["sent"] == 0
    assert all(row[0] == RUN and row[1] == SENDER and SID in row[3] for row in delivered)


def test_failed_orca_send_retries_and_checkpoints_only_after_ack(tmp_path):
    now = 1800000000
    item = {"id": INPUT, "sessionId": SID, "state": "sent", "delivery": "sent",
            "createdAt": now * 1000,
            "text": {"text": "## Ticket\nhttps://github.com/domenicomassafra/dstack/issues/147\n"}}
    delivered = []
    failing = [True]
    def send(*args):
        if failing[0]:
            raise TimeoutError("Orca unavailable")
        delivered.append(args)
    def run():
        return observer.observe(tmp_path / "state.json", RUN, SENDER,
                                calls={"studio": lambda _: {"inputs": [item]}},
                                issue=lambda *args: {"state": "open", "updated_at": "today"},
                                send=send, answers=lambda *args: [], now=now)
    assert run()["failed"] == 1
    failing[0] = False
    assert run()["sent"] == 1
    assert run()["sent"] == 0
    assert len(delivered) == 1


def test_ambiguous_or_unsent_inputs_never_create_ownership(tmp_path):
    now = 1800000000
    text = "## Ticket\nhttps://github.com/domenicomassafra/dstack/issues/147\n"
    entries = [
        {"id": INPUT, "sessionId": SID, "state": "queued", "delivery": "pending", "createdAt": now * 1000, "text": {"text": text}},
        {"id": "other", "sessionId": SID, "state": "sent", "delivery": "sent", "createdAt": now * 1000,
         "text": {"text": text.rstrip() + " https://github.com/domenicomassafra/dstack/issues/148\n"}},
    ]
    assert observer.observe(tmp_path / "state.json", RUN, SENDER,
                            calls={"studio": lambda _: {"inputs": entries}},
                            issue=lambda *args: {}, send=lambda *args: None,
                            answers=lambda *args: [], now=now)["observed"] == 0


def test_remote_host_failure_does_not_hide_studio_and_recovers(tmp_path):
    now = 1800000000
    def make(host):
        return {"id": host + INPUT, "sessionId": SID, "state": "sent", "delivery": "sent",
                "createdAt": now * 1000,
                "text": {"text": "## Ticket\nhttps://github.com/domenicomassafra/controlcenter/issues/571\n"}}
    remote_down = [True]
    def remote(route):
        if remote_down[0]:
            raise TimeoutError("SSH unavailable")
        return {"inputs": [make("omarchy")]} if route == "/v1/inputs" else {"session": {"id": SID}, "live": {}}
    sent = []
    def run():
        return observer.observe(tmp_path / "state.json", RUN, SENDER,
            calls={"studio": lambda route: ({"inputs": [make("studio")]} if route == "/v1/inputs" else {"session": {"id": SID}, "live": {}}),
                   "omarchy": remote},
            issue=lambda *args: {"state": "open", "updated_at": "today"},
            send=lambda *args: sent.append(args), answers=lambda *args: [], now=now)
    assert run()["sent"] == 1
    remote_down[0] = False
    assert run()["sent"] == 1
    assert run()["sent"] == 0
    assert len(sent) == 2
    assert any("host=omarchy" in x[3] for x in sent)


def test_done_is_verified_only_after_closed_issue_and_main_commit(tmp_path):
    now = 1800000000
    item = {"id": INPUT, "sessionId": SID, "state": "sent", "delivery": "sent",
            "createdAt": (now - 100) * 1000,
            "text": {"text": "## Ticket\nhttps://github.com/domenicomassafra/controlcenter/issues/571\n"}}
    issue = {"state": "closed", "updated_at": "today"}
    session = {"session": {"id": SID, "toolCalls": 7,
                           "lastTurnEndAt": (now - 30) * 1000},
               "live": {"activeTurnId": None, "stopPending": False}}
    evidence = [False]
    sent = []
    def step():
        return observer.observe(tmp_path / "state.json", RUN, SENDER,
            calls={"studio": lambda route: {"inputs": [item]} if route == "/v1/inputs" else session},
            issue=lambda *args: issue, send=lambda *args: sent.append(args),
            answers=lambda *args: ["STATO: DONE"],
            proven=lambda *args: evidence[0], now=now)
    assert step()["sent"] == 2
    assert "da verificare" in sent[-1][3]
    evidence[0] = True
    assert step()["sent"] == 1
    assert "DONE verificata" in sent[-1][3]
    assert step()["sent"] == 0


def test_turn_without_tool_calls_reports_blocked_and_requires_live_control(tmp_path):
    now = 1800000000
    item = {"id": INPUT, "sessionId": SID, "state": "sent", "delivery": "sent",
            "createdAt": (now - 100) * 1000,
            "text": {"text": "## Ticket\nhttps://github.com/domenicomassafra/controlcenter/issues/571\n"}}
    session = {"session": {"id": SID, "toolCalls": 0,
                           "lastTurnEndAt": (now - 30) * 1000},
               "live": {"activeTurnId": "turn", "stopPending": False}}
    sent = []
    def step():
        return observer.observe(tmp_path / "state.json", RUN, SENDER,
            calls={"studio": lambda route: {"inputs": [item]} if route == "/v1/inputs" else session},
            issue=lambda *args: {"state": "open", "updated_at": "today"},
            send=lambda *args: sent.append(args), answers=lambda *args: ["STATO: BLOCKED"], now=now)
    assert step()["sent"] == 1
    session["live"] = {}
    assert step()["sent"] == 0
    session["live"] = {"activeTurnId": None, "stopPending": False}
    assert step()["sent"] == 1
    assert "fine turno" in sent[-1][2] and "BLOCKED" in sent[-1][3]
    assert step()["sent"] == 0
    session["session"]["lastTurnEndAt"] = (now - 10) * 1000
    assert step()["sent"] == 1
    assert "fine turno" in sent[-1][2]


def test_many_records_are_polled_in_round_robin_bounded_batches(tmp_path, monkeypatch):
    now = 1800000000
    monkeypatch.setattr(observer, "SCAN_LIMIT", 2)
    state = {"version": 1, "records": {
        f"studio:item{i}": {"host": "studio", "sid": SID, "repo": "controlcenter",
                         "number": str(i), "issue": f"controlcenter#{i}",
                         "createdAt": (now - 100) * 1000, "acked": {"assignment": observer.fingerprint(f"studio:item{i}")}}
        for i in range(1, 6)}}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    seen = []
    def step():
        return observer.observe(path, RUN, SENDER, calls={"studio": lambda route: (
            {"inputs": []} if route == "/v1/inputs" else {"session": {}, "live": {}})},
            issue=lambda repo, number: (seen.append(number) or {"state": "open", "updated_at": "today"}),
            send=lambda *args: None, answers=lambda *args: [], now=now)
    for _ in range(3):
        step()
    assert set(seen) == {str(i) for i in range(1, 6)}
    assert len(seen) == 6


def test_closed_finished_records_expire_but_pending_notices_survive(tmp_path):
    now = 1800000000
    path = tmp_path / "state.json"
    records = {}
    for number, turn_acked in ((1, True), (2, False)):
        key = f"studio:{number}"
        records[key] = {"host": "studio", "sid": SID, "repo": "controlcenter",
                        "number": str(number), "issue": f"controlcenter#{number}",
                        "createdAt": (now - 1000000) * 1000, "closed_since": now - 900000,
                        "acked": {"assignment": observer.fingerprint(key),
                                  **({"turn": "already-acked"} if turn_acked else {})}}
    path.write_text(json.dumps({"version": 1, "records": records}))
    observer.observe(path, RUN, SENDER,
        calls={"studio": lambda route: {"inputs": []} if route == "/v1/inputs" else {"session": {}, "live": {}}},
        issue=lambda *args: {"state": "closed", "updated_at": "today"},
        send=lambda *args: None, answers=lambda *args: [], now=now)
    remaining = json.loads(path.read_text())["records"]
    assert "studio:1" not in remaining
    assert "studio:2" in remaining


def test_github_proof_requires_a_linked_merged_pr_on_main(monkeypatch):
    from types import SimpleNamespace
    unrelated = "a" * 40
    merged = "b" * 40
    calls = []
    def fake_run(command, **kwargs):
        route = command[-1]
        calls.append(route)
        if "/timeline?" in route:
            body = [{"event": "cross-referenced", "source": {"issue": {
                "number": 653, "pull_request": {"url": "https://api.github.com/repos/domenicomassafra/controlcenter/pulls/653"}}}}]
        elif route.endswith("/pulls/653"):
            body = {"merged_at": "2026-10-25T10:00:00Z", "base": {"ref": "main"},
                    "merge_commit_sha": merged,
                    "body": "Addresses https://github.com/domenicomassafra/controlcenter/issues/571"}
        elif "/compare/" in route:
            body = {"status": "ahead"}
        else:
            raise AssertionError(route)
        return SimpleNamespace(returncode=0, stdout=json.dumps(body))
    monkeypatch.setattr(observer.subprocess, "run", fake_run)
    assert observer.github_proven("controlcenter", "571", {"state": "closed"})
    assert not any(unrelated in route for route in calls)
    calls.clear()
    assert not observer.github_proven("controlcenter", "572", {"state": "closed"})
    assert not observer.github_proven("controlcenter", "571", {"state": "open"})


def test_changed_issue_destination_is_delivered_once_to_new_run(tmp_path):
    now = 1800000000
    item = {"id": INPUT, "sessionId": SID, "state": "sent", "delivery": "sent",
            "createdAt": now * 1000,
            "text": {"text": "## Ticket\nhttps://github.com/domenicomassafra/controlcenter/issues/571\n"}}
    secondary = ("run_eb3493468d93", SENDER)
    delivered = []
    def step(routes=None):
        return observer.observe(tmp_path / "state.json", RUN, SENDER,
            calls={"studio": lambda route: {"inputs": [item]} if route == "/v1/inputs" else {"session": {}, "live": {}}},
            issue=lambda *args: {"state": "open", "updated_at": "today"},
            send=lambda *args: delivered.append(args), answers=lambda *args: [], now=now,
            routes=routes)
    assert step()["sent"] == 1
    assert step()["sent"] == 0
    assert step({"controlcenter#571": secondary})["sent"] == 1
    assert delivered[-1][0] == secondary[0]
    assert step({"controlcenter#571": secondary})["sent"] == 0


def test_issue_event_failure_is_retried_before_finished_record_compaction(tmp_path):
    now = 1800000000
    key = "studio:older"
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "records": {key: {
        "host": "studio", "sid": SID, "repo": "controlcenter", "number": "571",
        "issue": "controlcenter#571", "createdAt": (now - 900000) * 1000,
        "closed_since": now - 900000,
        "issue_base": "before", "acked": {"assignment": observer.fingerprint(key), "turn": "ok"}
    }}}))
    sent = []
    failing = [True]
    def send(*args):
        if failing[0]:
            raise TimeoutError("orca unavailable")
        sent.append(args)
    def step():
        return observer.observe(path, RUN, SENDER,
            calls={"studio": lambda route: {"inputs": []} if route == "/v1/inputs" else {"session": {}, "live": {}}},
            issue=lambda *args: {"state": "closed", "updated_at": "after"},
            send=send, answers=lambda *args: [], now=now)
    assert step()["failed"] == 1
    assert key in json.loads(path.read_text())["records"]
    failing[0] = False
    assert step()["sent"] == 1
    assert len(sent) == 1
    assert key not in json.loads(path.read_text())["records"]


def test_embedded_fleetprobe_uses_its_own_arguments(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(SOURCE.parent))  # Same sibling imports as the installed service.
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"run": RUN, "sender": SENDER}))
    observed = []
    monkeypatch.setattr(sys, "argv", ["fleetprobe.py", "--serve", "--host-id", "mac-studio"])
    monkeypatch.setattr(observer, "cos_client", lambda: lambda _: {"inputs": []})
    monkeypatch.setattr(observer, "omarchy_call", lambda _: {"inputs": []})
    monkeypatch.setattr(observer, "minipc_call", lambda _: {"inputs": []})
    monkeypatch.setattr(observer, "orca_send", lambda *args: observed.append(args))
    assert observer.main(["--config", str(config), "--state", str(tmp_path / "state.json")]) == 0
    assert json.loads(capsys.readouterr().out)["failed"] == 0
    assert observed == []
