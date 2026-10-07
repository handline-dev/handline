"""Tests: the PC-app gaps in the Python daemon (contract/protocol.md): Task.priority and task.edit priority, chat.send_now,
stats.outlook and the multi prompt (Prompt.multi, prompt.answer with an array of keys). Each: what it does, who may call it, a
refusal, and for the writes a retry after a crash between the effect and the reply. `hl task edit` still works from an agent session.

Run on its own: python3 tests/daemon/test_rpc_pc_gaps.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import _code  # noqa: E402
from test_rpc_tasks import _call, _count, _lose_the_reply, _new  # noqa: E402


def test_a_task_row_carries_its_priority_and_task_edit_changes_it(home: Path) -> None:
    support._board_world(home)
    tid = _new("p0", priority=1)["result"]["task"]
    row = _call("task.get", {"task": tid}, as_agent=None)
    check("Task.priority: the row says the priority the task was opened with", row["result"]["priority"] == 1, str(row))
    r = _call("task.edit", {"task": tid, "priority": 3, "request_id": "p1"})
    con = db.connect()
    check("task.edit: a priority alone is a change, and it takes", r["result"]["accepted"] is True
          and db.get_task(con, tid)["priority"] == 3, str(r))
    con.close()
    row = _call("tasks.list", {}, as_agent=None)
    check("Task.priority: and the list shows the new one", [t["priority"] for t in row["result"]["items"] if t["id"] == tid] == [3], str(row))
    _lose_the_reply("p1")
    again = _call("task.edit", {"task": tid, "priority": 3, "request_id": "p1"})
    check("task.edit: a retry after a crash between the change and its reply is answered from the kept effect",
          again["result"]["accepted"] is True and again["result"]["replayed"] is True, str(again))
    for bad in (4, 0, "P1", True, 1.5):
        r = _call("task.edit", {"task": tid, "priority": bad, "request_id": f"pb{bad}"})
        check(f"task.edit: priority {bad!r} is bad_request naming the field",
              _code(r) == "bad_request" and r["error"]["data"]["field"] == "priority", str(r))


def test_chat_send_now_marks_the_founders_queued_message_and_a_retry_marks_nothing_twice(home: Path) -> None:
    support._board_world(home)
    con = db.connect()
    rid = db.add_request(con, db.HUMAN, "worker", "please look at this", None, None, "fire_and_forget", "say", 0, None)
    other = db.add_request(con, "manager", "worker", "from the manager", None, None, "fire_and_forget", "say", 0, None)
    con.commit()
    con.close()
    check("chat.send_now: the founder's message is queued", _count("SELECT COUNT(*) FROM requests WHERE id = ? AND state = 'queued'", rid) == 1)
    r = _call("chat.send_now", {"request": rid, "request_id": "s1"}, as_agent=None)
    check("chat.send_now: accepted", r["result"]["accepted"] is True, str(r))
    con = db.connect()
    check("chat.send_now: the message is marked to go now", db.get_request(con, rid)["send_now"] is not None)
    con.close()
    _lose_the_reply("s1")
    again = _call("chat.send_now", {"request": rid, "request_id": "s1"}, as_agent=None)
    check("chat.send_now: a retry after a crash between the mark and the reply is answered from the kept effect",
          again["result"]["accepted"] is True and again["result"]["replayed"] is True
          and _count("SELECT COUNT(*) FROM write_effects WHERE request_id = 's1' AND method = 'chat.send_now'") == 1, str(again))
    check("chat.send_now: a request that is not the founder's is not_found",
          _code(_call("chat.send_now", {"request": other, "request_id": "s2"}, as_agent=None)) == "not_found")
    check("chat.send_now: nor is one that does not exist",
          _code(_call("chat.send_now", {"request": 99999, "request_id": "s3"}, as_agent=None)) == "not_found")
    r = _call("chat.send_now", {"request": "7", "request_id": "s4"}, as_agent=None)
    check("chat.send_now: a request that is not an integer is bad_request naming it",
          _code(r) == "bad_request" and r["error"]["data"]["field"] == "request", str(r))
    check("chat.send_now: an agent session is refused by role",
          _code(_call("chat.send_now", {"request": rid, "request_id": "s5"})) == "refused")
    con = db.connect()
    con.execute("UPDATE requests SET state = 'closed' WHERE id = ?", (rid,))
    con.commit()
    con.close()
    r = _call("chat.send_now", {"request": rid, "request_id": "s6"}, as_agent=None)
    check("chat.send_now: a message no longer waiting is refused", _code(r) == "refused", str(r))


def test_stats_outlook_states_its_range_and_keeps_recorded_apart_from_guessed(home: Path) -> None:
    support._board_world(home)
    for rng, days in (("12h", 1), ("7d", 7)):
        r = _call("stats.outlook", {"range": rng}, as_agent=None)["result"]
        check(f"stats.outlook {rng}: the range and its parts", r["range"] == rng and r["accounts"] == 0 and r["past"] == []
              and r["future"] == [] and isinstance(r["runway"], list) and r["cost_per_day"] == [], str(r))
    r = _call("stats.outlook", {"range": "2d"}, as_agent=None)
    check("stats.outlook: a range the spec does not list is bad_request naming it",
          _code(r) == "bad_request" and r["error"]["data"]["field"] == "range", str(r))
    check("stats.outlook: an agent session is refused by role", _code(_call("stats.outlook", {"range": "7d"})) == "refused")


def test_a_multi_prompt_takes_an_array_of_keys_and_no_other_prompt_does(home: Path) -> None:
    from handline.daemon import rpc, rpc_agents, rpc_prompts
    def block(multi: bool) -> dict:
        return {"agent": "worker", "kind": "question", "fingerprint": "f", "panel": ["pick"], "first_seen": 1, "last_seen": 1,
                "detail": {"multi_select": multi, "options": [{"label": "a"}, {"label": "b"}, {"label": "c"}]}}
    check("Prompt.multi: a multiSelect question says so", rpc_agents.prompt_of(block(True))["multi"] is True)
    check("Prompt.multi: a single-select one says false", rpc_agents.prompt_of(block(False))["multi"] is False)
    got = rpc_prompts._answer_for(block(True), ["1", "3"])
    check("prompt.answer: an array of keys becomes the boxes to check, zero-based", got == {"choices": [0, 2], "text": None}, str(got))
    check("prompt.answer: an empty array is no box checked", rpc_prompts._answer_for(block(True), [])["choices"] == [])
    for bad, why in ((["1"], "single-select"), (["9"], "unoffered key"), (["1", "1"], "a key twice"), (["esc"], "esc")):
        try:
            rpc_prompts._answer_for(block(why != "single-select"), bad)
            check(f"prompt.answer: {why} is refused", False)
        except rpc.RpcError as e:
            check(f"prompt.answer: {why} is bad_request naming choice", e.data.get("field") == "choice" and e.code == "bad_request", str(e.data))


def test_hl_task_edit_with_a_priority_works_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    rc, out = support._cli(["task", "new", "-t", "1h", "--owner", "worker", "by the command", "the brief, with a result"])
    tid = _count("SELECT MAX(id) FROM tasks")
    rc, out = support._cli(["task", "edit", f"T{tid}", "--priority", "P1"])
    check("hl task edit --priority: works at role agent", rc == 0 and _count("SELECT priority FROM tasks WHERE id = ?", tid) == 1, out)


def main() -> int:
    def tests(td):
        run(lambda: test_a_task_row_carries_its_priority_and_task_edit_changes_it(Path(td) / "t1"))
        run(lambda: test_chat_send_now_marks_the_founders_queued_message_and_a_retry_marks_nothing_twice(Path(td) / "t2"))
        run(lambda: test_stats_outlook_states_its_range_and_keeps_recorded_apart_from_guessed(Path(td) / "t3"))
        run(lambda: test_a_multi_prompt_takes_an_array_of_keys_and_no_other_prompt_does(Path(td) / "t4"))
        run(lambda: test_hl_task_edit_with_a_priority_works_from_an_agent_session(Path(td) / "t5"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
