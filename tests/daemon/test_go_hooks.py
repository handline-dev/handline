"""Tests: the Go daemon records hook events as the Python one does: the same hook lines into one Python-migrated database,
through Python (events.apply_hook, spool.ingest) and through Go (hook.record over the socket, the spool at start), leave
the same events, hook_applied, agent_state, session_starts and rpc_replies rows and the same wait files. The spool cases:
a bad line is set aside, a duplicate request id applies once, an ingest that was killed is finished by the next start.

Run on its own: python3 tests/daemon/test_go_hooks.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import spool  # noqa: E402
from handline.infra import protected, sessiontoken  # noqa: E402
from handline.session import events  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import _hp  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PAIRS = 16


def _seed(home: Path, base: int) -> None:
    support._board_world(home)
    con = db.connect()
    db.set_agent_state(con, "manager", "IDLE")
    db.set_agent_state(con, "worker", "RUNNING")
    con.execute("UPDATE agent_state SET account = 'acct1' WHERE agent = 'worker'")
    now = int(time.time())
    con.execute("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt) VALUES('worker', ?, NULL, 'launch', 0, '', '')", (now,))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()


def _lines(base: int) -> list[tuple[str, str, dict, str, int]]:
    """(agent, kind, data, request_id, at): every kind of effect, a late one and a duplicate."""
    perm = {"tool_name": "Bash", "tool_input": {"command": "rm -rf build\nthen more", "description": "clean it"}, "permission_mode": "default"}
    ask = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "which?", "options": ["a", "b"]}]}}
    return [
        ("worker", "SessionStart", {"session_id": "sess-1", "source": "startup", "cwd": "/w", "hook_event_name": "SessionStart"}, "r01", base),
        ("worker", "UserPromptSubmit", {"prompt": "do the thing please", "session_id": "sess-1"}, "r02", base + 1),
        ("worker", "PermissionRequest", {**perm, "session_id": "sess-1"}, "r03", base + 2),
        ("worker", "UserPromptSubmit", {"prompt": "x"}, "r04", base + 3),                 # ends the wait, already RUNNING
        ("worker", "PermissionRequest", {**ask, "session_id": "sess-1"}, "r05", base + 4),
        ("worker", "Stop", {"session_id": "sess-1"}, "r06", base + 5),                     # RUNNING -> IDLE, clears the wait
        ("worker", "StopFailure", {"error": "rate_limit", "error_details": "limit " + "é" * 300}, "r07", base + 6),
        ("worker", "Stop", {}, "r08", base + 2),                                           # late: recorded, moves nothing
        ("worker", "UserPromptSubmit", {"prompt": "again", "model": {"id": "m-1"}, "error": 7, "transcript_path": "/t/x.jsonl"}, "r09", base + 7),
        ("worker", "StopFailure", {"error": "rate_limit"}, "r10", base + 8),               # a limit: RUNNING -> IDLE with its text
        ("worker", "UserPromptSubmit", {"prompt": "p"}, "r11", base + 9),
        ("worker", "UserPromptSubmit", {"prompt": "p"}, "r11", base + 10),                 # the same request id: once
        ("manager", "PostToolUse", {"tool_name": "Bash"}, "r12", base + 1),               # a tool ran: IDLE -> RUNNING
        ("manager", "Notification", {"message": "not one we keep"}, "r13", base + 2),
        ("manager", "Stop", {}, "r14", base + 3),
        ("manager", "TeammateIdle", {"teammate_name": "x"}, "r15", base + 4),
        ("manager", "SessionEnd", {"reason": "other", "session_id": "s2"}, "r16", base + 5),
    ]


def _dump(home: Path) -> dict:
    con = sqlite3.connect(home / "db.sqlite")
    con.row_factory = sqlite3.Row
    try:
        def rows(sql: str) -> list:
            return [dict(r) for r in con.execute(sql)]
        evs = rows("SELECT ts, agent, account, kind, payload FROM events WHERE kind != 'hook_note' ORDER BY id")
        for e in evs:
            e["payload"] = json.loads(e["payload"]) if e["payload"] else None
        replies = rows("SELECT caller, request_id, method, params, reply FROM rpc_replies ORDER BY request_id")
        for r in replies:
            r["reply"] = json.loads(r["reply"])
        waits = {}
        for p in sorted((home / "waiting").glob("*.json")) if (home / "waiting").is_dir() else []:
            w = json.loads(p.read_text())
            w.pop("since", None)
            waits[p.name] = w
        rejected = []
        rf = home / spool.REJECTED
        if rf.exists():
            rejected = [json.loads(ln)["line"] for ln in rf.read_text().splitlines()]    # the error text is each language's own
        return {"events": evs, "replies": replies, "waits": waits, "rejected": rejected,
                "applied": rows("SELECT request_id, agent, at FROM hook_applied ORDER BY request_id"),
                "state": rows("SELECT agent, state, wake_at, detail, account, closed_at FROM agent_state ORDER BY agent"),
                "starts": rows("SELECT agent, ts, session_id, cause, resumed FROM session_starts ORDER BY id"),
                "spool_left": sorted(p.name for p in home.glob("hook-spool*") if p.name != spool.REJECTED)}
    finally:
        con.close()


def _reset(home: Path, snapshot: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        (home / f"db.sqlite{suffix}").unlink(missing_ok=True)
    shutil.copy(snapshot, home / "db.sqlite")
    shutil.rmtree(home / "waiting", ignore_errors=True)
    for p in home.glob("hook-spool*"):
        p.unlink()


def _forget_sessions() -> None:
    for p in (protected.root() / sessiontoken.NS).glob("*"):
        p.unlink()


def _spool_file(lines: list) -> None:
    for agent, kind, data, rid, at in lines:
        spool.append(agent, kind, data, rid, at)
    with open(spool.path(), "a") as f:                 # a line that is not JSON, one missing keys, one of the wrong shape
        f.write("this is not json\n" + json.dumps({"agent": "worker"}) + "\n" + json.dumps([1, 2]) + "\n")


class GoDaemon:
    def __init__(self, binary: str, home: Path) -> None:
        pairs = [socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM) for _ in range(PAIRS)]
        theirs = [b.fileno() for _a, b in pairs]
        self.proc = subprocess.Popen([binary, "-home", str(home), "-serve-fds", ",".join(map(str, theirs))], pass_fds=theirs,
                                     stderr=subprocess.PIPE)
        for _a, b in pairs:
            b.close()
        self.near = [a for a, _b in pairs]

    def call(self, method: str, params: dict, hello: dict) -> dict:
        s = self.near.pop(0)
        s.settimeout(15)
        f = s.makefile("rwb")
        for i, (m, p) in enumerate([("hello", hello), (method, params)], 1):
            f.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": p}).encode() + b"\n")
            f.flush()
            reply = json.loads(f.readline())
        s.close()
        return reply

    def stop(self) -> str:
        self.proc.terminate()
        return self.proc.communicate()[1].decode(errors="replace")


def _go_run(binary: str, home: Path, spooled: list, called: list) -> tuple[dict, list]:
    """The spool's lines at start, then the others over the socket (an agent's own, or the founder's for another's)."""
    _forget_sessions()
    _spool_file(spooled)
    go = GoDaemon(binary, home)
    replies = []
    try:
        for agent, kind, data, rid, at in called:
            _forget_sessions()
            if agent == "manager":
                sessiontoken.record("manager")
                hello = {"client": "t", "protocol": 1}
            else:
                hello = _hp()
            replies.append(go.call("hook.record", {"agent": agent, "kind": kind, "data": data, "at": at, "request_id": rid}, hello))
    finally:
        _forget_sessions()
        log = go.stop()
    return replies, [log]


def test_go_records_hooks_as_python_does(home: Path) -> None:
    base = int(time.time()) - 1000
    _seed(home, base)
    snapshot = home.parent / "seed.sqlite"
    shutil.copy(home / "db.sqlite", snapshot)
    lines = _lines(base)
    spooled, called = lines[:6] + [lines[10]], lines[6:10] + lines[11:]
    called.append(("worker", "UserPromptSubmit", {"prompt": "dup"}, "r02", base + 20))      # a request id the spool applied

    # Python: the spool at its start, then the calls.
    con = db.connect()
    _spool_file(spooled)
    spool.ingest(con, lambda m: None)
    for agent, kind, data, rid, at in called:
        events.apply_hook(con, agent, kind, data, rid, at)
    con.close()
    want = _dump(home)
    want["replies"] = []            # Python's calls went by apply_hook; the replies are compared below

    _reset(home, snapshot)
    with tempfile.TemporaryDirectory() as td:
        binary = str(Path(td) / "handlined")
        built = subprocess.run([str(ROOT / "tools" / "build-go.sh"), binary], capture_output=True, text=True)
        check("go hooks: the daemon builds", built.returncode == 0, built.stdout + built.stderr)
        if built.returncode:
            return
        replies, log = _go_run(binary, home, spooled, called)
        got = _dump(home)
        check("go hooks: every call was accepted", all(r.get("result") == {} for r in replies), str(replies))
        got_replies = got.pop("replies")
        want.pop("replies")
        for key in want:
            check(f"go hooks: {key} equal to Python's", got[key] == want[key],
                  f"\n go:     {json.dumps(got[key], sort_keys=True)[:2500]}\n python: {json.dumps(want[key], sort_keys=True)[:2500]}")
        check("go hooks: each call kept its reply under the caller and the params as Python writes them",
              len(got_replies) == len(called) and all(r["method"] == "hook.record" and r["reply"] == {} for r in got_replies)
              and json.loads(got_replies[0]["params"]) is not None, str(got_replies[:2]))
        check("go hooks: the spool was read and the bad lines set aside", len(got["rejected"]) == 3 and got["spool_left"] == [],
              str(got["rejected"]))
        check("go hooks: the daemon did not panic", "panic" not in log[0], log[0])

        # The wire: a retry is answered from the kept reply, another call under the id is refused, a non-self agent is refused.
        _reset(home, snapshot)
        _forget_sessions()
        go = GoDaemon(binary, home)
        try:
            sessiontoken.record("manager")
            agent_hello = {"client": "t", "protocol": 1}
            args = {"agent": "manager", "kind": "Stop", "data": {}, "at": base, "request_id": "w1"}
            first = go.call("hook.record", args, agent_hello)
            again = go.call("hook.record", args, agent_hello)
            other = go.call("hook.record", {**args, "kind": "SessionEnd"}, agent_hello)
            notself = go.call("hook.record", {**args, "agent": "worker", "request_id": "w2"}, agent_hello)
            noid = go.call("hook.record", {k: v for k, v in args.items() if k != "request_id"}, agent_hello)
            badat = go.call("hook.record", {**args, "request_id": "w3", "at": "now"}, agent_hello)
        finally:
            _forget_sessions()
            go.stop()
        check("go hooks: a first call is accepted", first.get("result") == {}, str(first))
        check("go hooks: a retry is answered replayed and applies nothing", again.get("result") == {"replayed": True}, str(again))
        check("go hooks: the same id for another call is refused", other.get("error", {}).get("data", {}).get("field") == "request_id", str(other))
        check("go hooks: another agent's name is refused, gate not_self", notself.get("error", {}).get("data", {}).get("gate") == "not_self", str(notself))
        check("go hooks: a call with no request_id names it", noid.get("error", {}).get("data", {}).get("field") == "request_id", str(noid))
        check("go hooks: at must be a time", badat.get("error", {}).get("data", {}).get("field") == "at", str(badat))
        d = _dump(home)
        check("go hooks: the retried hook was recorded once", [e["kind"] for e in d["events"] if e["agent"] == "manager"] == ["Stop"], str(d["events"]))


def test_go_finishes_an_ingest_that_was_killed(home: Path) -> None:
    base = int(time.time()) - 1000
    _seed(home, base)
    snapshot = home.parent / "seed2.sqlite"
    shutil.copy(home / "db.sqlite", snapshot)
    lines = _lines(base)[:3]
    for agent, kind, data, rid, at in lines:
        spool.append(agent, kind, data, rid, at)
    os.rename(spool.path(), spool.path().with_suffix(".ingesting"))          # the ingest was killed after taking the file
    spool.append("worker", "Stop", {}, "late1", base + 50)                   # and a hook wrote a new one meanwhile
    with tempfile.TemporaryDirectory() as td:
        binary = str(Path(td) / "handlined")
        built = subprocess.run([str(ROOT / "tools" / "build-go.sh"), binary], capture_output=True, text=True)
        if built.returncode:
            check("go hooks: the daemon builds", False, built.stdout + built.stderr)
            return
        _forget_sessions()
        go = GoDaemon(binary, home)
        try:
            time.sleep(0.5)
            _forget_sessions()
            go.call("hook.record", {"agent": "worker", "kind": "Stop", "data": {}, "at": base + 60, "request_id": "after"}, _hp())
            deadline = time.time() + 6
            while time.time() < deadline and any(home.glob("hook-spool.*")):
                time.sleep(0.2)
        finally:
            go.stop()
    d = _dump(home)
    ids = [a["request_id"] for a in d["applied"]]
    check("go hooks: the file a killed ingest left was applied at start, the new one after it",
          {"r01", "r02", "r03", "late1", "after"} <= set(ids), str(ids))
    check("go hooks: no spool file is left", d["spool_left"] == [], str(d["spool_left"]))


def main() -> int:
    def tests(td):
        run(lambda: test_go_records_hooks_as_python_does(Path(td) / "r1" / "home"))
        run(lambda: test_go_finishes_an_ingest_that_was_killed(Path(td) / "r2" / "home"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
