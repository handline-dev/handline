"""Tests: daemon.status, tasks.list, task.get and inbox.list (contract/protocol.md): the shape each returns, what it
filters and pages, who sees what, a refusal each, and the commands they serve still working from an agent session.

Run on its own: python3 tests/daemon/test_rpc_reads.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict | None = None, as_agent: str | None = "manager") -> dict:
    """One call on a fresh connection: from an agent session, or from the founder when as_agent is None."""
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params or {})
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _task(title: str, owner: str = "worker", by: str = "manager") -> int:
    ok, msg, tid = actions.task_new(by, owner, title, f"{title}: do it and show the result", timeout_s=3600)
    assert ok, msg
    return tid


def _to_founder(body: str, who: str = "worker", kind: str = "say") -> int:
    con = db.connect()
    try:
        return db.add_request(con, who, db.HUMAN, body, None, None, None, kind, 0, None)
    finally:
        con.close()


def test_daemon_status_says_what_runs_and_what_it_holds(home: Path) -> None:
    support._board_world(home)
    termmod.live_terminals = lambda: {"t1", "t2"}
    _to_founder("a report")
    r = _call("daemon.status")["result"]
    check("status: alive, with the version, protocol and schema it runs",
          r["alive"] is True and r["protocol"] == 1 and r["schema"] == db.LATEST_VERSION and r["version"], str(r))
    check("status: when it started, as a time", r["started_at"].endswith("Z") and "T" in r["started_at"], str(r))
    check("status: the terminal host, its health and what it sees",
          r["terminal_host"]["health"] == "ok" and r["terminal_host"]["name"] and "2 terminal" in r["terminal_host"]["detail"], str(r))
    check("status: agents counted by state, summing to the total", r["agents"]["total"] == 2
          and sum(r["agents"]["by_state"].values()) == 2, str(r))
    check("status: and the founder's inbox count", r["inbox"]["unread"] == 1, str(r))
    termmod.live_terminals = lambda: None
    check("status: a host that could not answer is degraded, not down",
          _call("daemon.status")["result"]["terminal_host"]["health"] == "degraded")
    def boom():
        raise RuntimeError("no host")
    termmod.live_terminals = boom
    check("status: a host that fails is down and the call still answers",
          _call("daemon.status")["result"]["terminal_host"]["health"] == "down")
    r = _call("daemon.status", {}, as_agent=None)
    check("status: the founder may ask too", "result" in r, str(r))
    from handline.daemon import rpc
    w = Wire()
    r = w.call("daemon.status", {})
    w.close()
    check("status: before hello is refused like any call", _code(r) == "bad_request", str(r))
    check("status: and is for an agent or above", rpc.METHODS["daemon.status"].role == "agent")


def test_tasks_list_pages_filters_and_shows_each_caller_its_own(home: Path) -> None:
    support._board_world(home)
    a, b, c = _task("first"), _task("second"), _task("third")
    r = _call("tasks.list")["result"]
    check("tasks.list: newest first, in the spec's shape", [t["id"] for t in r["items"]] == [c, b, a] and r["more"] is False, str(r))
    t = r["items"][0]
    check("tasks.list: each row carries who, state and times", t["from"] == "manager" and t["to"] == "worker"
          and t["title"] == "third" and t["state"] in ("queued", "doing", "draft") and t["time_box"] == "1h"
          and t["created"].endswith("Z") and t["done"] is None and t["evidence"] is None and t["parent"] is None, str(t))
    check("tasks.list: and the keys the spec requires", {"relation", "response", "delivered", "hold_reason", "block_reason"} <= set(t), str(t))
    r = _call("tasks.list", {"limit": 2})["result"]
    check("tasks.list: a page of two says there is more", [t["id"] for t in r["items"]] == [c, b] and r["more"] is True)
    r = _call("tasks.list", {"limit": 2, "before": b})["result"]
    check("tasks.list: and before continues it", [t["id"] for t in r["items"]] == [a] and r["more"] is False)
    check("tasks.list: an unrelated agent sees none of them", _call("tasks.list", as_agent="backend")["result"]["items"] == [])
    check("tasks.list: unless it asks for everyone's",
          len(_call("tasks.list", {"everyone": True}, as_agent="backend")["result"]["items"]) == 3)
    check("tasks.list: the owner sees it too", len(_call("tasks.list", as_agent="worker")["result"]["items"]) == 3)
    check("tasks.list: the founder sees all", len(_call("tasks.list", as_agent=None)["result"]["items"]) == 3)
    check("tasks.list: from narrows to an opener", _call("tasks.list", {"from": "worker"})["result"]["items"] == [])
    child = _task("child of first")
    con = db.connect()
    con.execute("UPDATE tasks SET parent_task = ? WHERE id = ?", (a, child))
    con.close()
    check("tasks.list: parent narrows to the children", [t["id"] for t in _call("tasks.list", {"parent": a})["result"]["items"]] == [child])
    con = db.connect()
    ok, msg = actions.discard_task(con, b, db.HUMAN, "not needed", stop=True)
    con.close()
    check("tasks.list: a discarded task is left out", b not in [t["id"] for t in _call("tasks.list")["result"]["items"]], msg)
    r = _call("tasks.list", {"all": True})["result"]
    check("tasks.list: unless all is asked, and then it says so", [t["state"] for t in r["items"] if t["id"] == b] == ["discarded"], str(r))
    check("tasks.list: state narrows", [t["id"] for t in _call("tasks.list", {"state": "discarded"})["result"]["items"]] == [b])
    for bad in ({"limit": 0}, {"limit": 201}, {"limit": "x"}, {"before": "x"}, {"all": "yes"}, {"owner": 3}):
        r = _call("tasks.list", bad)
        check(f"tasks.list: {bad} is bad_request", _code(r) == "bad_request", str(r))


def test_task_get_is_one_task_whole(home: Path) -> None:
    support._board_world(home)
    a, b = _task("the first"), _task("the second")
    con = db.connect()
    db.add_memo(con, a, "manager", "a note to keep")
    db.add_block(con, b, a)
    con.execute("UPDATE tasks SET parent_task = ? WHERE id = ?", (a, b))
    con.close()
    r = _call("task.get", {"task": a})["result"]
    check("task.get: the task with its body", r["id"] == a and r["title"] == "the first" and "do it and show the result" in r["body"], str(r))
    check("task.get: its memos", [m["text"] for m in r["memos"]] == ["a note to keep"] and r["memos"][0]["task"] == a, str(r))
    check("task.get: its children", r["children"] == [b], str(r))
    check("task.get: its delivery", len(r["deliveries"]) == 1 and r["deliveries"][0]["at"].endswith("Z"), str(r))
    check("task.get: its recorded events, as events", r["events"] and all(e["topic"] == "tasks" and e["target"]["id"] == str(a) for e in r["events"]), str(r))
    r = _call("task.get", {"task": b})["result"]
    check("task.get: what it waits on", r["blockers"] and r["blockers"][0]["task"] == a and r["block_reason"] == f"waiting on T{a}", str(r))
    r = _call("task.get", {"task": 999})
    check("task.get: no such task is not_found", _code(r) == "not_found", str(r))
    for bad in ({}, {"task": "1"}, {"task": True}):
        check(f"task.get: {bad} is bad_request", _code(_call("task.get", bad)) == "bad_request")


def test_inbox_list_is_the_founders_mailbox_or_an_agents_own_requests(home: Path) -> None:
    support._board_world(home)
    one = _to_founder("first report\nwith a second line")
    two = _to_founder("a question?", who="manager", kind="ask")
    r = _call("inbox.list", as_agent=None)["result"]
    check("inbox.list: the founder's items, newest first", [i["id"] for i in r["items"]] == [two, one] and r["more"] is False, str(r))
    i = r["items"][1]
    check("inbox.list: title is the first line, body whole, from whom, unread",
          i["title"] == "first report" and i["body"].endswith("second line") and i["agent"] == "worker"
          and i["read"] is False and i["answered"] is False and i["options"] == [] and i["prompt"] is None, str(i))
    check("inbox.list: the keys the spec requires", {"id", "kind", "agent", "title", "body", "evidence", "task", "created", "read", "answered", "options", "prompt"} <= set(i))
    r = _call("inbox.list", {"agent": "worker"}, as_agent=None)["result"]
    check("inbox.list: agent narrows to a sender", [i["id"] for i in r["items"]] == [one], str(r))
    r = _call("inbox.list", {"limit": 1}, as_agent=None)["result"]
    check("inbox.list: a page of one says there is more", len(r["items"]) == 1 and r["more"] is True)
    con = db.connect()
    actions.for_you_seen(con, "worker", one)
    con.close()
    r = _call("inbox.list", {"unread": True}, as_agent=None)["result"]
    check("inbox.list: what the founder has seen leaves the mailbox", [i["id"] for i in r["items"]] == [two], str(r))
    tid = _task("brief for the worker")
    mine = _call("inbox.list", as_agent="worker")["result"]["items"]
    check("inbox.list: an agent sees what was sent to it, with its task", len(mine) == 1 and mine[0]["agent"] == "manager" and mine[0]["task"] == tid, str(mine))
    check("inbox.list: and not the founder's", all(i["id"] not in (one, two) for i in mine))
    con = db.connect()
    db.add_request(con, "manager", "worker", "a notice, only information", None, None, None, "say", 0, None, notice=True)
    con.close()
    check("inbox.list: a board notice is information and is not an item", len(_call("inbox.list", as_agent="worker")["result"]["items"]) == 1)
    check("inbox.list: a request still queued is unread to its target",
          len(_call("inbox.list", {"unread": True}, as_agent="worker")["result"]["items"]) == 1)
    for bad in ({"limit": 0}, {"unread": "yes"}, {"before": "x"}, {"wait_s": 601}, {"wait_s": -1}, {"kind": 3}):
        check(f"inbox.list: {bad} is bad_request", _code(_call("inbox.list", bad, as_agent=None)) == "bad_request")


def test_inbox_list_holds_for_wait_s_and_returns_when_something_arrives(home: Path) -> None:
    import threading
    import time as _t
    support._board_world(home)
    from handline.daemon import rpc_inbox
    rpc_inbox.POLL_S = 0.05
    t0 = _t.time()
    r = _call("inbox.list", {"wait_s": 1}, as_agent=None)["result"]
    check("inbox.list: with nothing to give it waits out wait_s and answers empty", r["items"] == [] and _t.time() - t0 >= 0.9, str(r))
    threading.Timer(0.3, lambda: _to_founder("late news")).start()
    t0 = _t.time()
    r = _call("inbox.list", {"wait_s": 5}, as_agent=None)["result"]
    check("inbox.list: and returns as soon as something arrives", len(r["items"]) == 1 and _t.time() - t0 < 3, str(r))


def test_the_commands_these_methods_serve_still_work_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    tid = _task("listed by the command")
    rc, out = support._cli(["task", "list"])
    check("hl task list: works at role agent", rc == 0 and "listed by the command" in out, out)
    rc, out = support._cli(["task", "show", f"T{tid}"])
    check("hl task show: works at role agent", rc == 0 and "listed by the command" in out, out)
    rc, out = support._cli(["inbox"], as_agent="worker")
    check("hl inbox: works at role agent", rc == 0, out)


def main() -> int:
    def tests(td):
        run(lambda: test_daemon_status_says_what_runs_and_what_it_holds(Path(td) / "r1"))
        run(lambda: test_tasks_list_pages_filters_and_shows_each_caller_its_own(Path(td) / "r2"))
        run(lambda: test_task_get_is_one_task_whole(Path(td) / "r3"))
        run(lambda: test_inbox_list_is_the_founders_mailbox_or_an_agents_own_requests(Path(td) / "r4"))
        run(lambda: test_inbox_list_holds_for_wait_s_and_returns_when_something_arrives(Path(td) / "r5"))
        run(lambda: test_the_commands_these_methods_serve_still_work_from_an_agent_session(Path(td) / "r6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
