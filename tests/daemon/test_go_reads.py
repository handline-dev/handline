"""Tests: the Go daemon answers the read methods as the Python one does on the same database: daemon.status, agents.list,
agent.get, agent.timeline, tasks.list, task.get and inbox.list, from the founder and from an agent session.

The database is built by the Python code (its migrations, its task and request writers) and both daemons read it as it
stands, so a column the Go code reads differently shows as a difference in the JSON.

Run on its own: python3 tests/daemon/test_go_reads.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import agentview, locks  # noqa: E402
from handline.infra import protected, sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PAIRS = 64


def _task(title: str, owner: str = "worker", by: str = "manager") -> int:
    ok, msg, tid = actions.task_new(by, owner, title, f"{title}: do it and show the result", timeout_s=3600)
    assert ok, msg
    return tid


def _seed(home: Path) -> dict:
    """A board with every state, a child, a wait, a memo, a handoff, and a founder's mailbox of each kind."""
    support._board_world(home)
    agents.register("backend")
    (home / "agents" / "manager" / "ROLE.md").write_text("# manager\n\nLeads the team.\nSecond line.\n")
    a, b, c = _task("first"), _task("second"), _task("third")
    done = _task("finished")
    con = db.connect()
    con.execute("UPDATE tasks SET parent_task = ? WHERE id = ?", (a, b))
    db.add_memo(con, a, "manager", "a note to keep")
    db.add_block(con, c, a)
    con.execute("UPDATE tasks SET done_evidence = 'shown', done_at = ? WHERE id = ?", (int(time.time()), done))
    db.add_event(con, "manager", "task_handoff", {"task_id": a, "from": "manager", "to": "worker", "note": "over to you"})
    db.add_event(con, "worker", "state", {"task_id": a, "text": "working", "actor": {"kind": "agent", "name": "worker"}})
    db.add_event(con, "worker", "key", {"method": "session.keys"})
    db.add_event(con, "worker", "plain", None)
    db.set_meta(con, f"taskhold:{c}", json.dumps({"why": "behind T1", "able": False, "since": int(time.time()) - 400}))
    say = db.add_request(con, "worker", db.HUMAN, "a report\nwith a second line", None, None, None, "say", 0, None)
    ask = db.add_request(con, "worker", db.HUMAN, "may I?", None, "subordinate", "async", "ask", 0, None)
    con.execute("UPDATE requests SET landed_at = ts, state = 'landed' WHERE id = ?", (say,))
    question = db.add_request(con, db.HUMAN, "worker", "what is the plan", None, None, None, None, 0, None)
    con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (question,))
    answer = db.add_request(con, "worker", db.HUMAN, "this is the plan", None, None, None, "say", 0, None)
    con.execute("UPDATE requests SET settles = ?, state = 'landed' WHERE id = ?", (question, answer))
    con.execute("INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:worker', ?, 0)", (say,))
    repo = home.parent / "worktree"
    repo.mkdir()
    for args in (["init", "-q", "-b", "feature"], ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    db.set_agent_state(con, "worker", "idle")
    con.execute("UPDATE agent_state SET worktree = ?, terminal = 't-1', resume_session = 'sess-9' WHERE agent = 'worker'", (str(repo),))
    db.set_agent_state(con, "manager", "idle")
    con.execute("UPDATE agent_state SET terminal = 't-2', closed_at = ? WHERE agent = 'manager'", (int(time.time()),))
    now = int(time.time())
    con.execute("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('worker', 'person', 'holder-1', ?, ?)", (now - 60, now - 5))
    con.execute("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('manager', 'person', 'holder-1', ?, ?)", (now - 9000, now - 9000))
    db.prompt_open(con, "worker", "permission", "fp1", "Run it?", ["Run ls?", "1. Yes"],
                   {"tool": "Bash", "command": "ls", "reason": "to look", "wait": 3}, 3, True, now)
    db.prompt_open(con, "manager", "question", "fp2", "Which?", ["Which?"],
                   {"options": [{"label": "A", "checked": True}, {"label": "B"}]}, None, True, now)
    con.execute("UPDATE prompt_blocks SET first_seen = ? WHERE agent = 'manager'", (now - 100,))
    decision = db.new_task(con, "Pick one of two", db.HUMAN, "worker", expects="decision")
    card = db.add_request(con, "worker", db.HUMAN, "Pick one of two\nA or B", None, None, None, "ask", 0, None)
    db.link_request(con, decision, card)
    agentview.refresh(con, int(time.time()))
    con.close()
    return {"tasks": [a, b, c, done], "ask": ask, "card": card}


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


def _forget_sessions() -> None:
    for p in (protected.root() / sessiontoken.NS).glob("*"):
        p.unlink()


def _python(method: str, params: dict, as_agent: str | None) -> dict:
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params, id=2)
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _normal(method: str, reply: dict) -> dict:
    """What the two daemons may differ in: the build, the clock, the terminal host, the account a session would be placed on, and the
    optional fields Python sends before Go does."""
    r = json.loads(json.dumps(reply))
    res = r.get("result")
    if method == "daemon.status" and res:
        r["result"] = {k: res[k] for k in ("alive", "protocol", "schema", "agents", "inbox")}
    if method == "agent.get" and res:
        res.pop("account_pick", None)
        (res.get("prompt") or {}).pop("multi", None)        # optional in the spec; the Go daemon does not send it yet
    if method in ("tasks.list", "task.get") and res:
        for t in res.get("items", [res]):
            t.pop("priority", None)                          # likewise Task.priority
    r.pop("id", None)
    return r


def test_go_reads_equal_python_reads_on_one_database(home: Path) -> None:
    ids = _seed(home)
    a, b, c, done = ids["tasks"]
    cases: list[tuple[str | None, str, dict]] = [
        (None, "daemon.status", {}),
        (None, "agents.list", {}),
        (None, "agent.get", {"agent": "manager"}),
        (None, "agent.get", {"agent": "worker"}),
        (None, "agent.get", {"agent": "nobody.here"}),
        (None, "agent.get", {}),
        (None, "agent.timeline", {"agent": "worker"}),
        (None, "agent.timeline", {"agent": "worker", "limit": 2}),
        (None, "agent.timeline", {"agent": "worker", "limit": 2, "before": 1000000}),
        (None, "agent.timeline", {"agent": "worker", "limit": 0}),
        (None, "tasks.list", {}),
        (None, "tasks.list", {"all": True}),
        (None, "tasks.list", {"state": "done"}),
        (None, "tasks.list", {"state": "queued", "limit": 1}),
        (None, "tasks.list", {"parent": a}),
        (None, "tasks.list", {"from": "manager", "before": c}),
        (None, "tasks.list", {"owner": "worker", "limit": 201}),
        (None, "task.get", {"task": a}),
        (None, "task.get", {"task": c}),
        (None, "task.get", {"task": done}),
        (None, "task.get", {"task": 99999}),
        (None, "task.get", {"task": "one"}),
        (None, "inbox.list", {}),
        (None, "inbox.list", {"unread": True}),
        (None, "inbox.list", {"kind": "decision"}),
        (None, "inbox.list", {"agent": "worker", "limit": 2}),
        (None, "inbox.list", {"before": ids["card"]}),
        (None, "inbox.list", {"wait_s": 601}),
        ("manager", "tasks.list", {}),
        ("worker", "tasks.list", {}),
        ("backend", "tasks.list", {}),
        ("backend", "tasks.list", {"everyone": True}),
        ("worker", "tasks.list", {"owner": "manager"}),
        ("manager", "task.get", {"task": a}),
        ("worker", "inbox.list", {}),
        ("manager", "inbox.list", {}),
        ("manager", "daemon.status", {}),
        ("manager", "agent.timeline", {"agent": "worker"}),
    ]
    real, real_live = locks.running_elsewhere, termmod.live_terminals
    locks.running_elsewhere = lambda: True      # the Go daemon is the one running
    termmod.live_terminals = lambda: set()
    try:
        want = {(who, m, json.dumps(p, sort_keys=True)): _normal(m, _python(m, p, who)) for who, m, p in cases}
    finally:
        locks.running_elsewhere, termmod.live_terminals = real, real_live
    worker = want[(None, "agent.get", json.dumps({"agent": "worker"}, sort_keys=True))]["result"]
    manager = want[(None, "agent.get", json.dumps({"agent": "manager"}, sort_keys=True))]["result"]
    check("go reads: setup: the worker has a branch, an open session, a person holding the input and a permission prompt",
          worker["worktree"]["branch"] == "feature" and worker["session_open"] and worker["input_holders"][0]["kind"] == "person"
          and worker["prompt"]["kind"] == "permission" and len(worker["prompt"]["options"]) == 3, str(worker))
    check("go reads: setup: the manager's session is closed, its idle hold has lapsed and its question has options",
          not manager["session_open"] and manager["input_holders"][0]["kind"] == "daemon" and len(manager["prompt"]["options"]) == 3, str(manager))
    with tempfile.TemporaryDirectory() as td:
        binary = str(Path(td) / "handlined")
        built = subprocess.run([str(ROOT / "tools" / "build-go.sh"), binary], capture_output=True, text=True)
        check("go reads: the daemon builds", built.returncode == 0, built.stdout + built.stderr)
        if built.returncode:
            return
        go = GoDaemon(binary, home)
        try:
            for who, m, p in cases:
                _forget_sessions()
                if who is None:
                    hello = _hp()
                else:
                    sessiontoken.record(who)
                    hello = {"client": "t", "protocol": 1}
                got = _normal(m, go.call(m, p, hello))
                expected = want[(who, m, json.dumps(p, sort_keys=True))]
                if "error" in expected and "error" in got:    # the same refusal, said in the daemon's own words
                    same = expected["error"]["code"] == got["error"]["code"] \
                        and expected["error"]["data"].get("code") == got["error"]["data"].get("code") \
                        and expected["error"]["data"].get("field") == got["error"]["data"].get("field")
                else:
                    same = got == expected
                check(f"go reads: {who or 'founder'} {m} {json.dumps(p, sort_keys=True)} answers as the Python daemon does", same,
                      f"\n go:     {json.dumps(got, sort_keys=True)[:1500]}\n python: {json.dumps(expected, sort_keys=True)[:1500]}")
        finally:
            _forget_sessions()
            log = go.stop()
            if "panic" in log:
                check("go reads: the daemon did not panic", False, log)


def main() -> int:
    def tests(td):
        run(lambda: test_go_reads_equal_python_reads_on_one_database(Path(td) / "r1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
