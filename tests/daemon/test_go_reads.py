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
from handline.daemon import agentview, locks, schedules, stream  # noqa: E402
from handline.infra import protected, sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PAIRS = 128


def _task(title: str, owner: str = "worker", by: str = "manager") -> int:
    ok, msg, tid = actions.task_new(by, owner, title, f"{title}: do it and show the result", timeout_s=3600)
    assert ok, msg
    return tid


def _seed_accounts(home: Path, con, now: int) -> None:
    """Three accounts: a measured one with a label, a cap and a typed end, one with a carried legacy-shaped window, one with no reading."""
    os.environ["TZ"] = "Asia/Seoul"     # both daemons read a zoneless start as local time; a zone away from UTC tells them apart
    time.tzset()
    acc = home / "accounts"
    for name in ("alpha", "beta", "gamma", "delta", "epsilon"):
        (acc / name).mkdir(parents=True, exist_ok=True)
    (acc / "alpha" / "status.json").write_text(json.dumps({"windows": [
        {"minutes": 10080, "used_percent": 41.2, "resets_at": now + 86400, "observed_at": now, "carried": False, "lost": False},
        {"minutes": 300, "used_percent": 12.0, "resets_at": now + 3600, "observed_at": now, "carried": False, "lost": False}]}))
    (acc / "beta" / "status.json").write_text(json.dumps({"pct_5h": 28.999999999999996, "reset_5h": now + 100, "rl_carried": 1, "pct_7d": None}))
    # profiles: a zoned start, a zoneless one (read as local time) and a month-end one, each on a cycle with no typed end
    for name, started in (("gamma", "2026-03-31T15:00:00"), ("delta", "2025-08-31T23:30:00.433836Z"), ("epsilon", "2024-01-31T00:00:00+09:00")):
        (acc / name / ".claude").mkdir(parents=True, exist_ok=True)
        (acc / name / ".claude" / ".claude.json").write_text(json.dumps({"oauthAccount": {"subscriptionCreatedAt": started}}))
    (home / "config.yaml").write_text("active_cap: 3\nactive_cap_alpha: 1\nlabel_alpha: Main\nmonth_cycle_alpha: true\n"
                                      "sub_end_alpha: \"2099-01-31\"\npark_timeout_s: 1200\nbg_notice_s: 77\nhandoff_note: false\ndev_mode: 1\n"
                                      "ignore_cache_expiry: true\ninput_hold_idle_s: 45\ntoken_prices: {opus: 7}\nusage_recover_beta: false\nmonth_cycle_gamma: true\nmonth_cycle_delta: true\nmonth_cycle_epsilon: true\n")
    (home / "shared" / ".claude").mkdir(parents=True, exist_ok=True)       # hooks of ours for some events only: not installed
    (home / "shared" / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "/usr/local/bin/hl hook Stop"}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": "echo hook SessionStart"}]}]}}))
    con.execute("UPDATE agent_state SET account = 'alpha' WHERE agent = 'worker'")
    db.add_event(con, "manager", "moved_account", {"from": "beta", "to": "alpha", "trigger": "wall"})
    db.add_event(con, "worker", "moved_account", {"from": "alpha", "to": None})
    for kind, at in (("UserPromptSubmit", now - 600), ("Stop", now - 500), ("UserPromptSubmit", now - 400), ("UserPromptSubmit", now - 300), ("SessionEnd", now - 100)):
        db.add_event(con, "worker", kind, None)
        con.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (at,))


def _seed(home: Path) -> dict:
    """A board with every state, a child, a wait, a memo, a handoff, and a founder's mailbox of each kind."""
    support._board_world(home)
    agents.register("backend")
    support.hand_edit_agent("worker", (home / "agents" / "worker" / "agent.yaml").read_text()
                            + "park_timeout_s: 90\nignore_cache_expiry: false\nbg_check_s: 33\n")
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
    # 2.5 h ago reads "for 2h" for the next half hour; an age in minutes changes wording every 60 s, and the two
    # daemons are asked minutes apart (the build, a loaded machine).
    db.set_meta(con, f"taskhold:{c}", json.dumps({"why": "behind T1", "able": False, "since": int(time.time()) - 9000}))
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
    git = lambda d, *args: subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)
    (repo / "a.txt").write_text("one\n")
    (repo / "kept ünï.txt").write_text("k\n")
    git(repo, "add", "."), git(repo, "commit", "-q", "-m", "files")
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "new.txt").write_text("fresh\n")
    (repo / "link").symlink_to("/etc")
    boss_tree = home.parent / "boss-tree"
    boss_tree.mkdir()
    git(boss_tree, "init", "-q", "-b", "trunk"), git(boss_tree, "commit", "-q", "--allow-empty", "-m", "x")
    extra_tree = home.parent / "extra-tree"
    extra_tree.mkdir()
    git(extra_tree, "init", "-q", "-b", "side"), git(extra_tree, "commit", "-q", "--allow-empty", "-m", "x")
    agents.write("worker", {**(agents.read("worker") or {}), "repo": str(repo)})
    agents.write("manager", {**(agents.read("manager") or {}), "repo": str(boss_tree)})
    sched = [{"cron": "*/15 9-17 * * 1-5", "message": "check  the board"}, {"cron": "0 8 1 * *", "message": "monthly"}, {"cron": "30 2 * * 0,7", "message": "weekly"}]
    agents.write("worker", {**(agents.read("worker") or {}), "schedules": json.dumps(sched)})
    agents.write("backend", {**(agents.read("backend") or {}), "schedules": "not json"})
    db.set_agent_state(con, "worker", "idle")
    con.execute("UPDATE agent_state SET worktree = ?, terminal = 't-1', resume_session = 'sess-9' WHERE agent = 'worker'", (str(repo),))
    db.set_agent_state(con, "manager", "idle")
    con.execute("UPDATE agent_state SET terminal = 't-2', closed_at = ? WHERE agent = 'manager'", (int(time.time()),))
    con.execute("UPDATE agent_state SET worktree = ?, extra_worktrees = ? WHERE agent = 'manager'", ("id-7::" + str(boss_tree), json.dumps([str(extra_tree), "/nowhere"])))
    for op, state, step, msg, res in (("op-1", "done", "start", "ok", {"agent": "worker"}), ("op-2", "running", "register", None, None), ("op-3", "failed", "start", "no repo", None)):
        con.execute("INSERT INTO operations(id, kind, state, step, steps_done, steps_total, message, result_json, agent, created_at, updated_at) "
                    "VALUES(?, 'agent.create', ?, ?, 1, 2, ?, ?, 'worker', 1, 1)", (op, state, step, msg, json.dumps(res) if res else None))
    now = int(time.time())
    con.execute("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('worker', 'person', 'holder-1', ?, ?)", (now - 60, now - 5))
    con.execute("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('manager', 'person', 'holder-1', ?, ?)", (now - 9000, now - 9000))
    db.prompt_open(con, "worker", "permission", "fp1", "Run it?", ["Run ls?", "1. Yes"],
                   {"tool": "Bash", "command": "ls", "reason": "to look", "wait": 3}, 3, True, now)
    db.prompt_open(con, "manager", "question", "fp2", "Which?", ["Which?"],
                   {"options": [{"label": "A", "checked": True}, {"label": "B"}]}, None, True, now)
    con.execute("UPDATE prompt_blocks SET first_seen = ? WHERE agent = 'manager'", (now - 100,))
    con.execute("UPDATE prompt_blocks SET last_seen = ?", (now + 3600,))    # still seen when the slow run reaches prompt.list: the stale cut-off is not what this holds
    decision = db.new_task(con, "Pick one of two", db.HUMAN, "worker", expects="decision")
    card = db.add_request(con, "worker", db.HUMAN, "Pick one of two\nA or B", None, None, None, "ask", 0, None)
    db.link_request(con, decision, card)
    _seed_accounts(home, con, now)
    db.add_event(con, "worker", "schedule_fired", {"cron": "0 8 1 * *"})
    db.set_cursor(con, f"schedule_at:worker:{schedules.sid({'cron': '30 2 * * 0,7', 'message': 'weekly'})}", now - 7200)
    stream.append(con, "notifications", "task.done", {"task": a, "n": 1}, agent="worker", actor={"kind": "agent", "name": "worker"}, title="done")
    stream.append(con, "tasks", "task.changed", {"task": a}, target={"kind": "task", "id": a})
    stream.append(con, "notifications", "plain", {})
    stream.append(con, "notifications", "task.done", {"task": a, "n": 1}, agent="worker", actor={"kind": "agent", "name": "worker"}, title="done")
    stream.append(con, "tasks", "task.changed", {"task": a}, target={"kind": "task", "id": a})
    stream.append(con, "notifications", "plain", {})
    db.set_cursor(con, stream.PRUNED, 2)
    protected.put("trust", "roots.json", json.dumps([{"root": "/srv/repo", "why": "w", "added_by": "human", "added_at": 1}]))
    protected.put("dialog_rules", "rules.json", json.dumps([{"vendor": "claude", "title": "Trust this folder?", "label": "Yes"}, {"vendor": "", "title": "t", "label": "l"}]))
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
    """What the two daemons may differ in: the build, the clock, the terminal host, the account a session would be placed on, and the optional fields Python sends before Go does."""
    r = json.loads(json.dumps(reply))
    res = r.get("result")
    if method == "daemon.status" and res:
        r["result"] = {k: res[k] for k in ("alive", "protocol", "schema", "agents", "inbox")}
    if method == "agent.get" and res:
        res.pop("account_pick", None)
        res.pop("cwd", None)        # the chat page's fields: the Go daemon does not send them yet
        res.pop("can", None)
    if method in ("agents.list", "agent.get") and res:
        for a in res.get("agents", [res]):
            for k in ("kind", "task_count", "ready", "wall"):
                a.pop(k, None)      # the AgentView fields the Go daemon sends before Python does
    if method == "chat.requests" and res:
        res["version"] = len(res["version"])        # opaque: each daemon hashes what changes its own way
    if method in ("tasks.list", "task.get") and res:
        res.pop("reason_gaps", None)
        for t in res.get("items", [res]):
            for k in ("last_activity", "edited_by", "mismatch", "requests", "asker", "discarded", "merged_into", "time", "blocked_by", "parent_title", "subtasks", "timeline", "people"):
                t.pop(k, None)      # what the task page reads: optional in the spec, the Go daemon does not send it yet
    if method == "schedules.list" and res:
        for e in res.get("errors", []):
            e["error"] = e["error"].split(": ")[0] + ":" if e["error"].startswith("schedules is not JSON: ") else e["error"]    # the decoder's own words are not compared
    if method == "settings.get" and res:
        res.pop("terminal_host", None)      # the machine's: Python probes the host, standalone Go drives none
    if method == "stats.get" and res:
        res["since"], res["until"] = None, None     # the clock: now less the window, read a second apart
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
        (None, "worktrees.list", {}),
        (None, "worktrees.list", {"owner": "worker"}),
        (None, "worktrees.list", {"repo": "boss-tree"}),
        (None, "worktrees.list", {"repo": 3}),
        ("worker", "worktrees.list", {}),
        (None, "worktree.diff", {"path": str(home.parent / "worktree")}),
        (None, "worktree.diff", {"path": str(home.parent / "worktree"), "file": "a.txt"}),
        (None, "worktree.diff", {"path": str(home.parent / "worktree"), "file": "new.txt"}),
        (None, "worktree.diff", {"path": str(home.parent / "worktree"), "file": "link"}),
        (None, "worktree.diff", {"path": str(home.parent / "worktree"), "file": "../x"}),
        (None, "worktree.diff", {"path": str(home.parent / "worktree"), "file": "-x"}),
        (None, "worktree.diff", {"path": str(home.parent / "boss-tree")}),
        (None, "worktree.diff", {"path": "/tmp"}),
        (None, "worktree.diff", {}),
        (None, "team.get", {}),
        ("worker", "team.get", {}),
        (None, "schedules.list", {}),
        ("worker", "schedules.list", {}),
        (None, "agent.options", {}),
        ("worker", "agent.options", {}),
        (None, "operation.get", {"operation": "op-1"}),
        (None, "operation.get", {"operation": "op-2"}),
        (None, "operation.get", {"operation": "op-3"}),
        (None, "operation.get", {"operation": "nope"}),
        (None, "operation.get", {}),
        (None, "accounts.list", {}),
        ("manager", "accounts.list", {}),
        (None, "stats.get", {"window": "24h"}),
        (None, "stats.get", {"window": "7d"}),
        ("manager", "stats.get", {"window": "7d"}),
        (None, "stats.get", {"window": "1h"}),
        (None, "stats.get", {}),
        (None, "settings.get", {}),
        (None, "settings.get", {"agent": "worker"}),
        (None, "settings.get", {"agent": "nobody.here"}),
        (None, "settings.get", {"agent": ""}),
        ("manager", "settings.get", {}),
        (None, "answers.list", {}),
        ("manager", "answers.list", {}),
        (None, "inbox.get", {"id": ids["ask"]}),
        (None, "inbox.get", {"id": ids["card"]}),
        (None, "inbox.get", {"id": ids["ask"], "wait_s": 1}),
        (None, "inbox.get", {"id": 99999}),
        (None, "inbox.get", {"id": "x"}),
        (None, "inbox.get", {"id": ids["ask"], "wait_s": 601}),
        ("worker", "inbox.get", {"id": ids["ask"]}),
        ("manager", "inbox.get", {"id": ids["card"]}),
        (None, "events.feed", {}),
        (None, "events.feed", {"topics": ["tasks", "notifications"], "limit": 2}),
        (None, "events.feed", {"since": 1}),
        (None, "events.feed", {"since": 0, "limit": 3}),
        (None, "events.feed", {"since": 2}),
        (None, "events.feed", {"since": 3, "topics": ["notifications"]}),
        (None, "events.feed", {"since": 99, "topics": ["tasks"]}),
        (None, "events.feed", {"topics": ["devices"]}),
        (None, "events.feed", {"topics": []}),
        (None, "events.feed", {"since": -1}),
        (None, "events.feed", {"limit": 0}),
        (None, "prompt.list", {"agent": "worker"}),
        (None, "prompt.list", {"agent": "backend"}),
        (None, "prompt.list", {"agent": "nobody.here"}),
        (None, "prompt.list", {}),
        (None, "chat.messages", {"agent": "worker", "scope": "mine"}),
        (None, "chat.messages", {"agent": "worker", "scope": "all", "limit": 2}),
        (None, "chat.messages", {"agent": "worker", "scope": "mine", "before": ids["card"]}),
        (None, "chat.messages", {"agent": "worker", "scope": "mine", "since": ids["ask"]}),
        (None, "chat.messages", {"agent": "worker", "scope": "mine", "before": 3, "since": 1}),
        (None, "chat.requests", {"agent": "worker", "scope": "mine"}),
        (None, "chat.requests", {"agent": "worker", "scope": "all", "limit": 2}),
        (None, "chat.requests", {"agent": "worker", "scope": "mine", "since": ids["ask"]}),
        (None, "chat.transcript", {"agent": "worker"}),
        (None, "chat.transcript", {"agent": "worker", "limit": 2}),
        (None, "chat.transcript", {"agent": "manager"}),
        (None, "chat.locate", {"request": ids["ask"]}),
        (None, "chat.locate", {"request": ids["ask"], "agent": "backend", "limit": 3}),
        (None, "chat.locate", {"request": 99999}),
        (None, "chat.search", {"query": "plan"}),
        (None, "chat.search", {"query": "a", "agent": "worker", "scope": "all", "limit": 3}),
        (None, "chat.search", {"query": "100%_"}),
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
                _refresh_prompts()      # a prompt is open while the screen watch has seen it in the last 10 s; the build above can take longer than that
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


def _refresh_prompts() -> None:
    con = db.connect()
    try:
        con.execute("UPDATE prompt_blocks SET last_seen = ? WHERE gone_at IS NULL", (int(time.time()),))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_go_reads_equal_python_reads_on_one_database(Path(td) / "r1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
