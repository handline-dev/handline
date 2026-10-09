"""Tests: task.new, task.edit, task.withdraw and task.cancel (contract/protocol.md): what each does, who may
call it, a refusal each, a retry after a crash between the effect and the reply, and the commands they serve
still working from an agent session.

Run on its own: python3 tests/daemon/test_rpc_tasks.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402
from handline.team import ask as askmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict, as_agent: str | None = "manager", id: int = 1) -> dict:
    """One call on a fresh connection: from an agent session, or from the founder when as_agent is None."""
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params, id=id)
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _new(rid: str, **more) -> dict:
    return _call("task.new", {"owner": "worker", "title": "the work", "body": "do it, show the sha", "time_box": "1h",
                              "request_id": rid, **more})


def _lose_the_reply(rid: str) -> None:
    """What a crash between the effect and the reply leaves: the effect kept, the reply not."""
    con = db.connect()
    con.execute("DELETE FROM rpc_replies WHERE request_id = ?", (rid,))
    con.close()


def _count(sql: str, *args) -> int:
    con = db.connect()
    try:
        return con.execute(sql, args).fetchone()[0]
    finally:
        con.close()


def test_task_new_opens_a_task_for_its_owner_and_a_retry_opens_no_second(home: Path) -> None:
    support._board_world(home)
    r = _new("n1", priority=1)
    tid = r["result"]["task"]
    check("task.new: accepted, with the number", r["result"]["accepted"] is True and isinstance(tid, int), str(r))
    con = db.connect()
    row = db.get_task(con, tid)
    check("task.new: the task is the owner's, opened by the caller, at the priority and time box asked",
          row["owner"] == "worker" and row["opened_by"] == "manager" and row["priority"] == 1 and row["timeout_s"] == 3600)
    check("task.new: and its brief is delivered to the owner", len(db.task_requests(con, tid)) == 1)
    con.close()
    again = _new("n1", priority=1)
    check("task.new: a retry gets the reply back, marked replayed", again["result"] == {**r["result"], "replayed": True}, str(again))
    check("task.new: and opens nothing more", _count("SELECT COUNT(*) FROM tasks") == 1)
    _lose_the_reply("n1")
    after = _new("n1", priority=1)
    check("task.new: after a crash between the task and its reply, the retry finds the effect and repeats nothing",
          after["result"]["task"] == tid and after["result"]["replayed"] is True and _count("SELECT COUNT(*) FROM tasks") == 1, str(after))
    check("task.new: the effect row is the one the task was written with",
          _count("SELECT COUNT(*) FROM write_effects WHERE request_id = 'n1' AND method = 'task.new'") == 1)


def test_the_founder_opens_a_task_for_an_agent(home: Path) -> None:
    support._board_world(home)
    support.hand_edit_team("""
agents:
  manager: {}
  worker:  {}
edges:
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    r = _call("task.new", {"owner": "worker", "title": "the work", "body": "do it", "time_box": "1h", "request_id": "f1"}, as_agent=None)
    tid = r.get("result", {}).get("task")
    check("task.new: the founder opens a task for an agent", isinstance(tid, int), str(r))
    con = db.connect()
    row = db.get_task(con, tid)
    check("task.new: opened by human and queued by them, with its brief delivered to the owner",
          row["owner"] == "worker" and row["opened_by"] == "human" and row["queued_by"] == "human"
          and len(db.task_requests(con, tid)) == 1, str(dict(row)))
    con.close()


def test_task_new_refuses_what_it_cannot_open(home: Path) -> None:
    support._board_world(home)
    r = _call("task.new", {"owner": "human", "title": "t", "body": "b", "time_box": "1h", "request_id": "n2"}, as_agent=None)
    check("task.new: the founder's task for themself is refused", _code(r) == "refused" and "for an agent" in r["error"]["message"], str(r))
    r = _new("n3", owner="nobody")
    check("task.new: an owner that is no agent is not_found", _code(r) == "not_found", str(r))
    r = _new("n4", time_box="soon")
    check("task.new: a time box that is not a span is bad_request naming the field",
          _code(r) == "bad_request" and r["error"]["data"]["field"] == "time_box", str(r))
    r = _new("n5", time_box="3d")
    check("task.new: a time box past the maximum is bad_request", _code(r) == "bad_request", str(r))
    r = _call("task.new", {"owner": "worker", "title": "t", "body": "  ", "time_box": "1h", "request_id": "n6"})
    check("task.new: an empty body is bad_request", _code(r) == "bad_request", str(r))
    r = _call("task.new", {"owner": "worker", "title": "t", "time_box": "1h", "request_id": "n6b"})
    check("task.new: no body is the action's own refusal, as the command gives it",
          _code(r) == "refused" and "needs a body" in r["error"]["message"] and _count("SELECT COUNT(*) FROM tasks") == 0, str(r))
    r = _new("n7", relation="supervisor")
    check("task.new: a part that has no action behind it is refused, not dropped", _code(r) == "bad_request", str(r))
    r = _new("n8", blocked_by=999)
    check("task.new: a blocker that does not exist refuses before anything is opened",
          _code(r) == "not_found" and _count("SELECT COUNT(*) FROM tasks") == 0, str(r))
    first = _new("n9")["result"]["task"]
    r = _new("n10", blocked_by=first)
    con = db.connect()
    check("task.new: a blocker is recorded in the same step", db.task_blockers(con, r["result"]["task"])[0]["id"] == first, str(r))
    con.close()


def test_task_edit_adds_and_removes_a_wait_in_the_transaction_of_its_other_parts(home: Path) -> None:
    support._board_world(home)
    tid, blocker = _new("w0")["result"]["task"], _new("w1")["result"]["task"]
    r = _call("task.edit", {"task": tid, "title": "waits now", "blocked_by": blocker, "request_id": "w2"})
    con = db.connect()
    check("task.edit: blocked_by with a title is one change", r["result"]["accepted"] is True and db.get_task(con, tid)["title"] == "waits now"
          and [b["id"] for b in db.task_blockers(con, tid)] == [blocker], str(r))
    r = _call("task.edit", {"task": tid, "title": "never kept", "blocked_by": tid, "request_id": "w3"})
    check("task.edit: a later part's refusal undoes the earlier one (a task cannot wait on itself)",
          _code(r) == "refused" and db.get_task(con, tid)["title"] == "waits now", str(r))
    r = _call("task.edit", {"task": tid, "unblock": blocker, "request_id": "w4"})
    check("task.edit: unblock clears the wait", r["result"]["accepted"] is True and not db.task_blockers(con, tid), str(r))
    r = _call("task.edit", {"task": tid, "unblock": blocker, "request_id": "w5"})
    check("task.edit: unblocking what it is not waiting on is refused", _code(r) == "refused", str(r))
    r = _call("task.edit", {"task": tid, "blocked_by": "x", "request_id": "w6"})
    check("task.edit: blocked_by is a task number", _code(r) == "bad_request", str(r))
    con.close()


def test_task_edit_changes_what_the_opener_names_and_refuses_the_rest(home: Path) -> None:
    support._board_world(home)
    tid = _new("e0")["result"]["task"]
    r = _call("task.edit", {"task": tid, "title": "a better title", "time_box": "2h", "request_id": "e1"})
    con = db.connect()
    row = db.get_task(con, tid)
    check("task.edit: the opener's change takes", r["result"]["accepted"] is True and row["title"] == "a better title"
          and row["timeout_s"] == 7200, str(r))
    con.close()
    _lose_the_reply("e1")
    again = _call("task.edit", {"task": tid, "title": "a better title", "time_box": "2h", "request_id": "e1"})
    check("task.edit: a retry after a crash is answered from the kept effect, not refused as a no-change",
          again["result"]["accepted"] is True and again["result"]["replayed"] is True, str(again))
    r = _call("task.edit", {"task": tid, "title": "same again", "request_id": "e2"}, as_agent="worker")
    check("task.edit: someone who did not open it is refused", _code(r) == "refused", str(r))
    r = _call("task.edit", {"task": 999, "title": "x", "request_id": "e3"})
    check("task.edit: no such task is not_found", _code(r) == "not_found", str(r))
    r = _call("task.edit", {"task": tid, "request_id": "e4"})
    check("task.edit: nothing to change is bad_request", _code(r) == "bad_request", str(r))
    r = _call("task.edit", {"task": tid, "title": "x", "waits_on": "a review", "request_id": "e5"})
    check("task.edit: waits_on is refused until an action backs it", _code(r) == "bad_request", str(r))
    r = _call("task.edit", {"task": tid, "title": "the same title", "request_id": "e6"}, as_agent="manager")
    r = _call("task.edit", {"task": tid, "title": "the same title", "request_id": "e7"}, as_agent="manager")
    check("task.edit: a change that changes nothing is refused", _code(r) == "refused", str(r))


def test_task_withdraw_and_task_cancel_end_a_task_for_the_right_caller(home: Path) -> None:
    support._board_world(home)
    a = _new("w0")["result"]["task"]
    r = _call("task.withdraw", {"task": a, "request_id": "w1"}, as_agent="worker")
    check("task.withdraw: not by an agent that did not open it", _code(r) == "refused", str(r))
    r = _call("task.withdraw", {"task": 999, "request_id": "w2"})
    check("task.withdraw: no such task is not_found", _code(r) == "not_found", str(r))
    # the delivery of an open task is still queued to its owner: the opener takes it back first
    r = _call("task.withdraw", {"task": a, "request_id": "w3"})
    open_request = _code(r) == "refused"
    check("task.withdraw: a task whose request is still open is refused with the way out", open_request and "request" in r["error"]["message"], str(r))
    r = _call("task.cancel", {"task": a, "reason": "not needed", "request_id": "c1"}, as_agent="manager")
    check("task.cancel: an agent is below the role gate", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
    r = _call("task.cancel", {"task": a, "reason": "not needed", "request_id": "c2"}, as_agent=None)
    con = db.connect()
    row = db.get_task(con, a)
    check("task.cancel: the founder ends it, with its open requests, and the reason is kept",
          r["result"]["accepted"] is True and row["discarded_by"] == "human" and row["discard_reason"] == "not needed", str(r))
    con.close()
    _lose_the_reply("c2")
    again = _call("task.cancel", {"task": a, "reason": "not needed", "request_id": "c2"}, as_agent=None)
    check("task.cancel: a retry after a crash is answered from the kept effect, not refused as already ended",
          again["result"]["replayed"] is True, str(again))
    b = _new("w4")["result"]["task"]
    con = db.connect()
    rid = db.task_requests(con, b)[0]["id"]
    con.close()
    os.environ["OCP_AGENT"] = "manager"
    try:
        ok, said = askmod.withdraw(rid, reason="changed my mind")
    finally:
        os.environ.pop("OCP_AGENT", None)
    check("task.withdraw: setup: the opener took its request back", ok, said)
    r = _call("task.withdraw", {"task": b, "request_id": "w5"})
    con = db.connect()
    check("task.withdraw: the opener then throws its own task away", r["result"]["accepted"] is True
          and db.get_task(con, b)["discarded_by"] == "manager", str(r))
    con.close()
    _lose_the_reply("w5")
    again = _call("task.withdraw", {"task": b, "request_id": "w5"})
    check("task.withdraw: a retry after a crash is answered from the kept effect", again["result"]["replayed"] is True, str(again))


def test_the_commands_these_methods_serve_still_work_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    rc, out = support._cli(["task", "new", "-t", "1h", "--owner", "worker", "by the command", "the brief, with a result"])
    check("hl task new: works at role agent", rc == 0 and "opened for worker" in out, out)
    tid = _count("SELECT MAX(id) FROM tasks")
    rc, out = support._cli(["task", "edit", f"T{tid}", "--title", "renamed"])
    check("hl task edit: works at role agent", rc == 0 and "renamed" in out, out)


def test_the_task_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connector(role):
        def connect():
            a, b = socket.socketpair()
            threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
            return a
        return connect
    support._board_world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            only = [Path("contract/fixtures/tasks.json")]
            results += [(*r, role) for r in conformance.run_all(only, {role: connector(role)}, {"founder_key": _hp()["key"]})
                        if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    names = {n for _, _, n, _ in results}
    check("fixtures: every task fixture passes against this daemon, each as its own role", not bad and len(names) == 8, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_task_new_opens_a_task_for_its_owner_and_a_retry_opens_no_second(Path(td) / "t1"))
        run(lambda: test_task_new_refuses_what_it_cannot_open(Path(td) / "t2"))
        run(lambda: test_the_founder_opens_a_task_for_an_agent(Path(td) / "t2f"))
        run(lambda: test_task_edit_changes_what_the_opener_names_and_refuses_the_rest(Path(td) / "t3"))
        run(lambda: test_task_edit_adds_and_removes_a_wait_in_the_transaction_of_its_other_parts(Path(td) / "t3b"))
        run(lambda: test_task_withdraw_and_task_cancel_end_a_task_for_the_right_caller(Path(td) / "t4"))
        run(lambda: test_the_commands_these_methods_serve_still_work_from_an_agent_session(Path(td) / "t5"))
        run(lambda: test_the_task_fixtures_pass_against_this_daemon(Path(td) / "t6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
