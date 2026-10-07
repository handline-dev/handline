"""Tests: what the task page draws (contract/protocol.md): Task.last_activity, TaskDetail's asker, discarded, merged_into, time,
parent_title, subtasks, timeline and people, and task.ask. task.ask serves no hl command (an agent answers with `hl say --parent`).

Run on its own: python3 tests/daemon/test_rpc_task_page.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import _code, _hp  # noqa: E402
from test_rpc_task_moves import _dies_then_retries  # noqa: E402
from test_rpc_tasks import _call, _count, _new  # noqa: E402


def _world(home: Path) -> None:
    """The board world and the person's edges to both agents, as a real team declares them."""
    support._board_world(home)
    support.hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "human", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "human", to: "manager", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")


def _get(tid: int) -> dict:
    return _call("task.get", {"task": tid}, as_agent=None)["result"]


def test_task_get_says_what_the_page_draws(home: Path) -> None:
    support._board_world(home)
    parent = _new("g0")["result"]["task"]
    kid = _new("g1", parent=parent, title="the kid")["result"]["task"]
    d = _get(parent)
    check("TaskDetail.asker: the opener when no request came from the founder", d["asker"] == "manager", str(d["asker"]))
    check("TaskDetail: not discarded, not merged", d["discarded"] is None and d["merged_into"] is None)
    check("TaskDetail.subtasks: the child with its title and state", d["subtasks"] == [{"id": kid, "title": "the kid", "state": d["subtasks"][0]["state"]}]
          and d["children"] == [kid], str(d["subtasks"]))
    k = _get(kid)
    check("TaskDetail.parent_title: the parent's title; Task.parent stays the id", k["parent_title"] == "the work" and k["parent"] == parent, str(k["parent_title"]))
    check("TaskDetail.parent_title: null without a parent", d["parent_title"] is None)
    t = d["time"]
    check("TaskDetail.time: the box in seconds, the default flag, a moment and a count", t["length_s"] == 3600 and t["default"] is False
          and t["overdue_since"] is None and t["told"] == 0, str(t))
    check("Task.last_activity: a moment on the row and in the list", d["last_activity"] is not None
          and all(i["last_activity"] for i in _call("tasks.list", {}, as_agent=None)["result"]["items"]))
    r = _call("task.memo.add", {"task": parent, "text": "a memo\x1b[31m with a control char", "request_id": "m1"})
    d = _get(parent)
    memo = [e for e in d["timeline"] if e["kind"] == "memo"]
    check("TaskDetail.timeline: a memo's entry, its id, and its text with no control character", len(memo) == 1 and memo[0]["memo"] is not None
          and "\x1b" not in memo[0]["text"], str(r) + str(d["timeline"]))
    check("TaskDetail.timeline: oldest first", [e["at"] for e in d["timeline"]] == sorted(e["at"] for e in d["timeline"]))
    check("TaskDetail.people: each agent once, the holder's team before the rest", set(d["people"]) == {"recent", "team", "others"}
          and sorted(sum(d["people"].values(), [])) == sorted(set(sum(d["people"].values(), []))))


def test_a_discarded_and_a_merged_task_say_so_and_have_no_clock(home: Path) -> None:
    support._board_world(home)
    a, b = _new("d0")["result"]["task"], _new("d1")["result"]["task"]
    con = db.connect()
    con.execute("UPDATE tasks SET discarded_by = 'manager', discard_reason = 'not needed', discarded_at = ? WHERE id = ?", (int(time.time()), a))
    con.commit()
    con.close()
    d = _get(a)
    check("TaskDetail.discarded: who, why and when", d["discarded"]["by"] == "manager" and d["discarded"]["reason"] == "not needed"
          and d["discarded"]["at"], str(d["discarded"]))
    check("TaskDetail.time: null once discarded", d["time"] is None)
    con = db.connect()
    con.execute("UPDATE tasks SET merged_into = ? WHERE id = ?", (b, a))
    con.commit()
    con.close()
    check("TaskDetail.merged_into: the task it went into", _get(a)["merged_into"] == b)


def test_a_task_past_its_deadline_says_since_when(home: Path) -> None:
    support._board_world(home)
    tid = _new("o0")["result"]["task"]
    con = db.connect()
    end = int(time.time()) - 600
    con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?", (end, tid))
    con.commit()
    con.close()
    t = _get(tid)["time"]
    check("TaskDetail.time: a missed deadline is overdue since that moment, and has its end", t["overdue_since"] is not None and t["end"] is not None, str(t))


def test_task_ask_sends_a_linked_question_once_and_a_death_between_repeats_nothing(home: Path) -> None:
    _world(home)
    tid = _new("k0")["result"]["task"]
    params = {"task": tid, "to": "worker", "text": "why is it late?", "request_id": "k1"}
    before = _count("SELECT COUNT(*) FROM requests")
    _dies_then_retries("task.ask", params, None,
                       after=lambda: _count("SELECT COUNT(*) FROM requests") == before + 1,
                       unchanged=lambda: _count("SELECT COUNT(*) FROM requests") == before)
    check("task.ask: the question is linked to the task and a task_question event is kept",
          _count("SELECT COUNT(*) FROM task_requests l JOIN requests r ON r.id = l.request_id WHERE l.task_id = ? AND r.requester = ?", tid, db.HUMAN) == 1
          and _count("SELECT COUNT(*) FROM events WHERE kind = 'task_question'") == 1)
    check("task.ask: the holder does not change", db.get_task(db.connect(), tid)["owner"] == "worker")
    r = _call("task.ask", {**params, "request_id": "k2", "to": "nobody"}, as_agent=None)
    check("task.ask: an unknown agent is not_found", _code(r) == "not_found", str(r))
    r = _call("task.ask", {**params, "request_id": "k3", "text": " "}, as_agent=None)
    check("task.ask: an empty question is bad_request naming text", _code(r) == "bad_request" and r["error"]["data"]["field"] == "text", str(r))
    r = _call("task.ask", {**params, "request_id": "k4"})
    check("task.ask: an agent session is refused by role", _code(r) == "refused", str(r))


def test_the_task_page_fixtures_pass_against_this_daemon(home: Path) -> None:
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    support._board_world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/task_page.json")], {role: connect},
                                                                {"founder_key": _hp()["key"], "task_owner": "worker"}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    names = {n for _, _, n, _ in results}
    check("fixtures: every task page fixture passes against this daemon, each as its own role", not bad and len(names) == 5, str(bad) + str(names))


def main() -> int:
    def tests(td):
        run(lambda: test_task_get_says_what_the_page_draws(Path(td) / "t1"))
        run(lambda: test_a_discarded_and_a_merged_task_say_so_and_have_no_clock(Path(td) / "t2"))
        run(lambda: test_a_task_past_its_deadline_says_since_when(Path(td) / "t3"))
        run(lambda: test_task_ask_sends_a_linked_question_once_and_a_death_between_repeats_nothing(Path(td) / "t4"))
        run(lambda: test_the_task_page_fixtures_pass_against_this_daemon(Path(td) / "t5"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
