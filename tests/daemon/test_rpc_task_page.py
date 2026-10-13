"""Tests: what the task page draws (contract/protocol.md): Task.last_activity, TaskDetail's asker, discarded, merged_into, time,
parent_title, subtasks, timeline and people, and task.ask. task.ask serves no hl command (an agent answers with `hl say --parent`).

Run on its own: python3 tests/daemon/test_rpc_task_page.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
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
    check("TaskDetail.subtasks: the child with its title, owner and state", d["subtasks"] == [{"id": kid, "title": "the kid", "owner": d["subtasks"][0]["owner"], "state": d["subtasks"][0]["state"]}]
          and d["subtasks"][0]["owner"] in ("manager", "worker") and d["subtasks"][0]["state"]
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


def _list(params: dict) -> dict:
    return _call("tasks.list", params, as_agent=None)["result"]


def test_a_list_row_carries_what_blocks_it_and_the_clock_task_get_gives(home: Path) -> None:
    support._board_world(home)
    held, blocker, free = (_new(f"l{n}")["result"]["task"] for n in range(3))
    con = db.connect()
    db.add_block(con, held, blocker)
    con.close()
    rows = {r["id"]: r for r in _list({"owner": "worker"})["items"]}
    check("tasks.list: blocked_by names the open blockers, and block_reason says so",
          rows[held]["blocked_by"] == [blocker] and rows[held]["block_reason"] == f"waiting on T{blocker}", str(rows[held]))
    check("tasks.list: a task nothing holds has an empty blocked_by", rows[free]["blocked_by"] == [] and rows[free]["block_reason"] is None)
    check("tasks.list: time is the object task.get gives for the same task", all(
        rows[t]["time"] is not None and rows[t]["time"]["length_s"] == _get(t)["time"]["length_s"] for t in (held, blocker, free)))
    con = db.connect()
    con.execute("UPDATE tasks SET done_at = ?, done_evidence = 'shipped' WHERE id = ?", (int(time.time()), blocker))
    con.commit()
    con.close()
    after = {r["id"]: r for r in _list({"owner": "worker", "all": True})["items"]}
    check("tasks.list: a blocker that is done no longer holds", after[held]["blocked_by"] == [])
    check("tasks.list: a done task has no clock and still lists blocked_by", after[blocker]["time"] is None and after[blocker]["blocked_by"] == [])


def test_all_with_limit_and_before_pages_through_every_task_once(home: Path) -> None:
    support._board_world(home)
    made = sorted(_new(f"p{n}")["result"]["task"] for n in range(7))
    seen, before = [], None
    for _ in range(10):
        page = _list({"owner": "worker", "all": True, "limit": 3, **({"before": before} if before else {})})
        seen += [i["id"] for i in page["items"]]
        if not page["more"]:
            break
        before = page["items"][-1]["id"]
    check("tasks.list paging: newest first, every task once, and the last page says no more",
          seen == sorted(made, reverse=True) and not page["more"], str((seen, made)))
    big = _list({"all": True, "limit": 200})
    check("tasks.list paging: limit 200 holds all of them", {i["id"] for i in big["items"]} >= set(made) and big["more"] is False)


def test_the_list_field_fixtures_pass_against_this_daemon(home: Path) -> None:
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance
    support._board_world(home)
    owners = {"tasks_blocked_owner": "a.t.blocked", "tasks_timed_owner": "a.t.timed", "tasks_overdue_owner": "a.t.overdue",
              "tasks_done_owner": "a.t.done", "tasks_paused_owner": "a.t.paused"}
    for o in owners.values():
        agents.register(o)
    con = db.connect()
    made = {}
    for k, o in owners.items():
        made[k] = db.new_task(con, f"{o} work", o, "manager")
        db.set_task_queued(con, made[k], "manager")
        con.execute("UPDATE tasks SET sent_at = ?, delivered_at = ? WHERE id = ?", (int(time.time()), int(time.time()), made[k]))
    db.add_block(con, made["tasks_blocked_owner"], db.new_task(con, "first", "manager", "manager"))
    db.new_task(con, "another of the blocked owner's", owners["tasks_blocked_owner"], "manager")      # the paging fixture needs a second
    con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?", (int(time.time()) - 600, made["tasks_overdue_owner"]))
    con.execute("UPDATE tasks SET done_at = ?, done_evidence = 'shipped' WHERE id = ?", (int(time.time()), made["tasks_done_owner"]))
    con.execute("UPDATE tasks SET timeout_left = 1200 WHERE id = ?", (made["tasks_paused_owner"],))
    con.commit()
    con.close()

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    doc = json.loads(Path("contract/fixtures/tasks_list_fields.json").read_text())
    results = conformance.run_all([Path("contract/fixtures/tasks_list_fields.json")], {"founder": connect},
                                  {"founder_key": _hp()["key"], **owners})
    bad = [(n, why) for st, why, n in results if st != "pass"]
    check("fixtures: every tasks.list field fixture passes against this daemon", len(results) == len(doc["fixtures"]) and not bad, str(bad))


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


def test_the_list_row_says_what_hl_task_list_prints_and_the_board_says_who_holds_each(home: Path) -> None:
    support._board_world(home)
    tid = _new("l0")["result"]["task"]
    _call("task.memo.add", {"task": tid, "text": "first", "request_id": "l1"})
    _call("task.memo.edit", {"task": tid, "memo": 1, "text": "second", "request_id": "l2"})
    got = _get(tid)
    check("task.get: a memo says when it was edited, and null before", got["memos"][0]["edited_at"] is not None, str(got["memos"]))
    real = actions.reason_gaps
    actions.reason_gaps = lambda *a, **k: (_ for _ in ()).throw(AssertionError("a plain tasks.list counted the reason gaps"))
    try:
        row = _call("tasks.list", {"everyone": True}, as_agent=None)
    finally:
        actions.reason_gaps = real
    check("tasks.list: a plain call does not count the reason gaps", "result" in row and "reason_gaps" not in row["result"], str(row)[:300])
    row = row["result"]
    item = next(i for i in row["items"] if i["id"] == tid)
    check("tasks.list: a row carries asker, edited_by, discarded, mismatch and its linked requests",
          item["asker"] == "manager" and item["edited_by"] is None and item["discarded"] is None and item["mismatch"].startswith("draft with work out")
          and [(r["from"], r["to"]) for r in item["requests"]] == [("manager", "worker")], str(item))
    row = _call("tasks.list", {"everyone": True, "reason_gaps": True}, as_agent=None)["result"]
    check("tasks.list: asked for, the result carries the 30-day reason gaps", set(row["reason_gaps"]) == {
        "retirements", "retirements_blank", "closures_sha", "closures_request", "closures_reason"}, str(row.get("reason_gaps")))
    board = _call("task.board", {}, as_agent="manager")["result"]
    mine = [r for r in board["reports"] if r["task"]["id"] == tid]
    check("task.board: a report's task is under reports, held by who the request is with",
          mine and mine[0]["now"] == {"kind": "held", "agents": ["worker"], "tasks": []} and mine[0]["holder"] == "worker", str(board))
    con = db.connect()
    owned = db.new_task(con, "owned by worker", "manager", "manager", reporter="worker")
    con.commit(), con.close()
    theirs = _call("task.board", {}, as_agent="worker")["result"]
    check("task.board: an agent's board is the tasks it works or owns, and its reports'",
          all("worker" in (r["task"]["to"], r["task"]["asker"]) for r in theirs["mine"])
          and any(r["task"]["id"] == owned for r in theirs["mine"]) and not theirs["reports"], str(theirs))
    mark = _call("task.board", {"everyone": True}, as_agent=None)["result"]
    check("task.board: the founder's board is the tasks it owns, with everyone asked for all", mark["mine"] == []
          and any(r["task"]["id"] == tid for r in mark["reports"]), str(mark))
    plain = _call("task.board", {}, as_agent=None)["result"]
    check("task.board: with nothing the founder owns, its board is empty", plain == {"mine": [], "reports": []}, str(plain))


def test_progress_get_reads_the_note_progress_keeps(home: Path) -> None:
    support._board_world(home)
    _new("p0")
    first = _call("progress.get", {}, as_agent="worker")["result"]
    check("progress.get: an agent with no note has null", first == {"note": None}, str(first))
    _call("task.progress", {"task": 1, "note": "half done", "request_id": "p1"}, as_agent="worker")
    got = _call("progress.get", {}, as_agent="worker")["result"]
    check("progress.get: it reads what task.progress kept", got == {"note": "half done"}, str(got))
    r = _call("progress.get", {}, as_agent=None)
    check("progress.get: the founder has no progress note to read", _code(r) == "refused", str(r))


def main() -> int:
    def tests(td):
        run(lambda: test_task_get_says_what_the_page_draws(Path(td) / "t1"))
        run(lambda: test_a_discarded_and_a_merged_task_say_so_and_have_no_clock(Path(td) / "t2"))
        run(lambda: test_a_task_past_its_deadline_says_since_when(Path(td) / "t3"))
        run(lambda: test_task_ask_sends_a_linked_question_once_and_a_death_between_repeats_nothing(Path(td) / "t4"))
        run(lambda: test_the_list_row_says_what_hl_task_list_prints_and_the_board_says_who_holds_each(Path(td) / "t6"))
        run(lambda: test_progress_get_reads_the_note_progress_keeps(Path(td) / "t7"))
        run(lambda: test_the_task_page_fixtures_pass_against_this_daemon(Path(td) / "t5"))
        run(lambda: test_a_list_row_carries_what_blocks_it_and_the_clock_task_get_gives(Path(td) / "t8"))
        run(lambda: test_all_with_limit_and_before_pages_through_every_task_once(Path(td) / "t9"))
        run(lambda: test_the_list_field_fixtures_pass_against_this_daemon(Path(td) / "t10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
