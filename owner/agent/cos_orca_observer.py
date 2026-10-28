"""#571: FleetProbe relays confirmed native CoS sends to Orca; history stays unknown."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time


RUN = re.compile(r"run_[A-Za-z0-9]+\Z")
SENDER = re.compile(r"term_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
SID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
TICKET = re.compile(r"https://github\.com/domenicomassafra/([A-Za-z][A-Za-z0-9.-]*)/issues/([1-9][0-9]*)\b")
MARKED = re.compile(r"(?m)^## Ticket\s*\n([^\n]+)")
STATUS = re.compile(r"STATO:\s*(DONE|BLOCKED)\b", re.I)
AGE_SECONDS = 48 * 3600
IDLE_SECONDS = 480
SCAN_LIMIT = 8  # Bound GitHub and remote CoS calls per FleetProbe pulse.
DISCOVERY_LIMIT = 16
FINISHED_RETENTION_SECONDS = 7 * 86400
DEFAULT_CONFIG = Path.home() / ".config/controlcenter/cos-orca-observer.json"
DEFAULT_STATE = Path.home() / ".local/state/controlcenter/cos-orca-observer.json"


def ticket(text):
    """Only a single explicit native assignment counts as issue ownership."""
    if not isinstance(text, str):
        return None
    marked = MARKED.search(text)
    found = TICKET.findall(marked.group(1)) if marked else TICKET.findall(text)
    if len(set(found)) != 1 or not found:
        return None
    return found[0]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def persist(path, state):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = path.with_suffix(".new")
    with candidate.open("w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(state, stream, sort_keys=True)
        stream.write("\n")
    os.replace(candidate, path)


def observe(path, run, sender, *, calls, issue, send, answers, now=None,
            only_issue=None, proven=None, routes=None):
    """Poll native CoS outboxes; retry notices without advancing failed checkpoints."""
    if not RUN.fullmatch(run) or not SENDER.fullmatch(sender):
        raise ValueError("invalid native Orca destination")
    routes = routes or {}
    if not isinstance(routes, dict) or any(
        not isinstance(key, str)
        or (key != "*" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*(?:#[1-9][0-9]*)?", key))
        or (target is None and "#" in key)
        or (target is not None and (not isinstance(target, (list, tuple)) or len(target) != 2
            or not isinstance(target[0], str) or not RUN.fullmatch(target[0])
            or not isinstance(target[1], str) or not SENDER.fullmatch(target[1])))
        for key, target in routes.items()
    ):
        raise ValueError("invalid issue destination routing")
    canonical_routes = {key.casefold(): value for key, value in routes.items()}
    if len(canonical_routes) != len(routes):
        raise ValueError("ambiguous case-insensitive repository routing")
    path = Path(path)
    now = time.time() if now is None else now
    state = json.loads(path.read_text()) if path.exists() else {"version": 1, "records": {}}
    if state.get("version") != 1 or not isinstance(state.get("records"), dict):
        raise ValueError("invalid observer generation state")
    records = state["records"]
    result = {"observed": 0, "sent": 0, "failed": 0, "unknown_hosts": []}
    for host, call in calls.items():
        try:
            inputs = call("/v1/inputs")["inputs"]
            if not isinstance(inputs, list):
                raise ValueError("invalid native outbox")
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            result["unknown_hosts"].append(host)
            continue
        discovered = 0
        for item in sorted((entry for entry in inputs if isinstance(entry, dict)),
                           key=lambda entry: entry.get("createdAt") if isinstance(entry.get("createdAt"), (int, float)) else 0,
                           reverse=True):
            if not isinstance(item, dict) or (item.get("state"), item.get("delivery")) != ("sent", "sent"):
                continue
            ident, sid = item.get("id"), item.get("deliveredSessionId") or item.get("sessionId")
            if not isinstance(ident, str) or not SID.fullmatch(sid or ""):
                continue
            stamp = item.get("createdAt")
            if not isinstance(stamp, (int, float)) or not 0 <= now - stamp / 1000 <= AGE_SECONDS:
                continue
            owner = ticket((item.get("text") or {}).get("text"))
            if not owner:
                continue
            if only_issue and f"{owner[0]}#{owner[1]}" != only_issue:
                continue
            key = f"{host}:{ident}"
            if key not in records:
                if discovered >= DISCOVERY_LIMIT:
                    continue  # The next pulse picks up unregistered native sends.
                records[key] = {"host": host, "sid": sid, "issue": f"{owner[0]}#{owner[1]}",
                                "repo": owner[0], "number": owner[1], "createdAt": stamp,
                                "acked": {}}
                persist(path, state)
                result["observed"] += 1
                discovered += 1

    # Keep observed sessions after outbox expiration or host failure.
    ordered = sorted(key for key, record in records.items()
                     if not only_issue or record["issue"] == only_issue)
    start = state.get("cursor", 0) % len(ordered) if ordered else 0
    batch = (ordered[start:] + ordered[:start])[:SCAN_LIMIT]
    if ordered:
        state["cursor"] = (start + len(batch)) % len(ordered)
        persist(path, state)
    for key in batch:
        record = records[key]
        host = record["host"]
        if host in result["unknown_hosts"] or host not in calls:
            continue
        sid, issue_key = record["sid"], record["issue"]
        acked = record["acked"]
        target = canonical_routes.get(issue_key.casefold(), canonical_routes.get(
            record["repo"].casefold(), canonical_routes.get("*", (run, sender))))
        if target is None:
            continue  # Repository owner is not bound yet; retain its unacknowledged notices.
        target_run, target_sender = target
        events = [("assignment", fingerprint(key), "sessione CoS osservata",
                   "invio nativo confermato; esito da verificare")]
        issue_row = None
        try:
            issue_row = issue(record["repo"], record["number"])
            if "pull_request" not in issue_row and issue_row.get("state") in ("open", "closed"):
                if issue_row["state"] == "closed":
                    record.setdefault("closed_since", now)
                else:
                    record.pop("closed_since", None)
                stamp = fingerprint([issue_row["state"], issue_row.get("updated_at") or issue_row.get("updatedAt"),
                                     sorted(label.get("name") for label in issue_row.get("labels", [])
                                            if isinstance(label, dict) and isinstance(label.get("name"), str))])
                if "issue_base" not in record:
                    record["issue_base"] = stamp
                    persist(path, state)
                elif record["issue_base"] != stamp:
                    events.append(("issue", stamp, "aggiornamento GitHub",
                                   "GitHub=" + issue_row["state"] + "; consegna da verificare"))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError):
            pass  # Native CoS ownership remains useful when GitHub is offline.
        try:
            native = calls[host]("/v1/sessions/" + sid + "?live=1")
            session, live = native.get("session"), native.get("live")
            if (isinstance(session, dict) and session.get("id") == sid
                    and isinstance(live, dict) and "activeTurnId" in live
                    and not live["activeTurnId"] and not live.get("stopPending")):
                end = session.get("lastTurnEndAt")
                last_tool = session.get("lastToolCallAt")
                stopped = (isinstance(end, (int, float)) and end >= record["createdAt"])
                idle = (isinstance(last_tool, (int, float))
                        and 0 <= now - last_tool / 1000 >= IDLE_SECONDS)
                if stopped or idle:
                    declarations = STATUS.findall("\n".join(answers(host, sid)))
                    declared = declarations[-1].upper() if declarations else None
                    verified = False
                    if declared == "DONE" and proven and issue_row:
                        try:
                            verified = bool(proven(record["repo"], record["number"], issue_row))
                        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                            pass
                    outcome = ("DONE verificata (issue chiusa, commit presente in main)" if verified
                               else "DONE dichiarato; prova GitHub da verificare" if declared == "DONE"
                               else "BLOCKED; issue da verificare" if declared == "BLOCKED"
                               else "turno fermo senza prova (non consegnato)")
                    events.append(("turn", fingerprint([end, last_tool, outcome]), "fine turno", outcome))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            pass  # No live control = UNKNOWN, never a false closed-turn event.

        for kind, stamp, subject, outcome in events:
            if acked.get(kind) == stamp and record.get("acked_targets", {}).get(kind, run) == target_run:
                continue
            body = f"origine=CoS; host={host}; issue={issue_key}; SID={sid}; esito={outcome}"
            try:
                send(target_run, target_sender, f"CoS {issue_key}: {subject}", body,
                     fingerprint([key, kind, stamp, target_run]))
            except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError):
                result["failed"] += 1
                continue
            acked[kind] = stamp
            record.setdefault("acked_targets", {})[kind] = target_run
            if kind == "issue":
                record["issue_base"] = stamp
            persist(path, state)
            result["sent"] += 1
        # Retain open issues and all unacknowledged evidence. Closed, finished
        # sessions can expire after a replay window once GitHub is reachable.
        if (isinstance(issue_row, dict) and issue_row.get("state") == "closed"
                and acked.get("turn") and acked.get("assignment")
                and all(record.get("acked_targets", {}).get(kind, run) == target_run
                        for kind in ("turn", "assignment"))
                and now - record.get("closed_since", now) >= FINISHED_RETENTION_SECONDS
                and not any(event_kind == "issue" and acked.get("issue") != stamp
                            for event_kind, stamp, _, _ in events)):
            del records[key]
            persist(path, state)
    return result


def cos_client():
    script = Path.home() / ".dstack/skills/subagentchatonsteroids/scripts/cos.py"
    spec = importlib.util.spec_from_file_location("cos_observer_client", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.call


def remote_call(host, route):
    if route != "/v1/inputs" and not re.fullmatch(r"/v1/sessions/" + SID.pattern[:-2] + r"\?live=1", route):
        raise ValueError("unsupported remote CoS route")
    script = ("import importlib.util,json,pathlib,sys;"
              "p=pathlib.Path.home()/'.dstack/skills/subagentchatonsteroids/scripts/cos.py';"
              "s=importlib.util.spec_from_file_location('remote_cos_read',p);"
              "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
              "print(json.dumps(m.call(sys.argv[1])))")
    remote = "timeout -k 2s 12s python3 -c " + shlex.quote(script) + " " + shlex.quote(route)
    completed = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                "-o", "ConnectionAttempts=1", host, remote],
                               capture_output=True, text=True, timeout=20)
    if completed.returncode:
        raise RuntimeError("remote CoS API unavailable")
    return json.loads(completed.stdout)


def omarchy_call(route):
    return remote_call("matebooklinux", route)


def minipc_call(route):
    return remote_call("minipc", route)


def github_issue(repo, number):
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    command = [gh, "api", f"repos/domenicomassafra/{repo}/issues/{number}"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if completed.returncode:
        raise RuntimeError("GitHub issue unavailable")
    return json.loads(completed.stdout)


SHA = re.compile(r"(?<![a-fA-F0-9])[a-fA-F0-9]{40}(?![a-fA-F0-9])")


def github_proven(repo, number, row):
    """Only an issue-linked PR merged on main can substantiate a declared DONE."""
    if row.get("state") != "closed":
        return False
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    reference = re.compile(r"(?<![0-9])#" + re.escape(str(number)) + r"(?![0-9])")
    issue_url = f"https://github.com/domenicomassafra/{repo}/issues/{number}"
    for page in range(1, 11):
        route = f"repos/domenicomassafra/{repo}/issues/{number}/timeline?per_page=100&page={page}"
        result = subprocess.run([gh, "api", route], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError("issue evidence unknown")
        events = json.loads(result.stdout)
        if not isinstance(events, list):
            raise ValueError("invalid GitHub issue timeline")
        for event in events:
            source = (event.get("source") or {}).get("issue") or {}
            linked = (source.get("pull_request") or {}).get("url") or ""
            if (event.get("event") != "cross-referenced"
                    or not linked.startswith(f"https://api.github.com/repos/domenicomassafra/{repo}/pulls/")):
                continue
            pr_number = source.get("number")
            if not isinstance(pr_number, int):
                continue
            pr_result = subprocess.run([gh, "api", f"repos/domenicomassafra/{repo}/pulls/{pr_number}"],
                                       capture_output=True, text=True, timeout=15)
            if pr_result.returncode:
                raise RuntimeError("linked PR evidence unknown")
            pr = json.loads(pr_result.stdout)
            description = (pr.get("body") or "") + "\n" + (pr.get("title") or "")
            sha = pr.get("merge_commit_sha")
            if (not pr.get("merged_at") or (pr.get("base") or {}).get("ref") != "main"
                    or not (issue_url in description or reference.search(description))
                    or not isinstance(sha, str) or not SHA.fullmatch(sha)):
                continue
            comparison = subprocess.run([gh, "api", f"repos/domenicomassafra/{repo}/compare/{sha}...main"],
                                        capture_output=True, text=True, timeout=15)
            if comparison.returncode:
                raise RuntimeError("merged PR main evidence unknown")
            if json.loads(comparison.stdout).get("status") in ("ahead", "identical"):
                return True
        if len(events) < 100:
            break
    return False


def assistant_status(host, sid):
    if host != "studio":
        return []  # Remote final text is private; live turn still reports UNKNOWN/provisional.
    folder = Path.home() / "Library/Application Support/chat-on-steroids/sessions" / sid / "messages"
    statuses = []
    for file in folder.glob("*.json"):
        try:
            row = json.loads(file.read_text())
            if row.get("kind") == "assistant_message":
                matches = STATUS.findall((row.get("message") or {}).get("text", ""))
                if matches:
                    statuses.append((row.get("time", 0), matches[-1]))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return ["STATO: " + value for _, value in sorted(statuses)[-1:]]


def orca_send(run, sender, subject, body, token):
    orca = shutil.which("orca") or str(Path.home() / ".local/bin/orca")
    if not Path(orca).is_file():
        orca = "/usr/local/bin/orca"
    cmd = [orca, "orchestration", "send", "--from", sender, "--to", f"run:{run}",
           "--type", "status", "--subject", subject, "--body", body,
           "--thread-id", "cos-observer-" + token, "--json"]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if completed.returncode:
        raise RuntimeError("Orca send failed")
    receipt = json.loads(completed.stdout)
    message = (receipt.get("result") or {}).get("message") or {}
    if (receipt.get("ok") is not True or not message.get("id")
            or message.get("from_handle") != sender or message.get("to_handle") != f"run:{run}"
            or message.get("type") != "status"):
        raise RuntimeError("Orca receipt not acknowledged")


def main(argv=None):
    import argparse
    import fcntl
    from cos_orca_routes import bind, load_routes
    parser = argparse.ArgumentParser(description="#571: read native CoS turns; notify Orca Run")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--only-issue", help="bounded real-consumer proof: repo#number")
    parser.add_argument("--bind-coordinator", help="bind a DStack group to an existing native Run")
    parser.add_argument("--run", help="real Orca Run for --bind-coordinator")
    args = parser.parse_args(argv)
    if args.bind_coordinator or args.run:
        if not (args.bind_coordinator and args.run):
            parser.error("--bind-coordinator and --run are required together")
        print(json.dumps(bind(args.config, args.bind_coordinator, args.run, persist, args.state)))
        return 0
    args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.state.with_suffix(".lock").open("a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"skipped": "observer already active"}))
            return 0
        config = json.loads(args.config.read_text())
        routes = load_routes(config)
        result = observe(args.state, config["run"], config["sender"],
                         calls={"studio": cos_client(), "omarchy": omarchy_call, "minipc": minipc_call},
                         issue=github_issue, send=orca_send, answers=assistant_status,
                         proven=github_proven, only_issue=args.only_issue, routes=routes)
    print(json.dumps(result))
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
