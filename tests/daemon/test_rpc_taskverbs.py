"""Tests: task.begin, task.start, task.handoff and task.progress (contract/protocol.md): what each does to the board, what it refuses, that a
retry (a lost reply, a crash between the effect and the reply) repeats nothing, and that a paired device's record is its own.

Run on its own: python3 tests/daemon/test_rpc_taskverbs.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.daemon import rpc, rpc_taskverbs  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.session import handoff as handoffmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict, as_agent: str | None = "worker", id: int = 1) -> dict:
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


def _lose_the_reply(rid: str) -> None:
    con = db.connect()
    con.execute("DELETE FROM rpc_replies WHERE request_id = ?", (rid,))
    con.close()


def _one(sql: str, *args):
    con = db.connect()
    try:
        row = con.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _founder_message(target: str = "worker") -> int:
    con = db.connect()
    try:
        return db.add_request(con, db.HUMAN, target, "please do this", None, None, None, "say", 0, None)
    finally:
        con.close()


def _task_for(owner: str, title: str = "the work") -> int:
    ok, msg, tid = actions.task_new("worker" if owner == "manager" else "manager", owner, title, "do it", timeout_s=3600)
    assert ok, msg
    return tid


def test_begin_opens_and_starts_a_task_on_a_founder_message_once(home: Path) -> None:
    support._board_world(home)
    msg = _founder_message()
    r = _call("task.begin", {"title": "the job", "message": msg, "why": "the founder asked", "request_id": "b1"})
    tid = r["result"]["task"]
    check("begin: accepted, with the task, which is the caller's own and current", r["result"]["accepted"] is True and _one("SELECT owner FROM tasks WHERE id = ?", tid) == "worker"
          and db.current_task(db.connect(), "worker") == tid, str(r))
    _lose_the_reply("b1")
    again = _call("task.begin", {"title": "the job", "message": msg, "why": "the founder asked", "request_id": "b1"}, id=2)
    check("begin: a retry after a lost reply is answered from the record and opens no second task", again["result"].get("replayed") is True and again["result"]["task"] == tid
          and _one("SELECT COUNT(*) FROM tasks") == 1, str(again))
    r = _call("task.begin", {"title": "again", "message": msg, "why": "w", "request_id": "b2"})
    check("begin: a message that already opened a task cannot open another", _code(r) == "refused" and _one("SELECT COUNT(*) FROM tasks") == 1, str(r))
    other = _call("task.begin", {"title": "x", "message": _founder_message("manager"), "why": "w", "request_id": "b3"})
    check("begin: a message to someone else is not this caller's approval", _code(other) == "refused", str(other))
    for bad, field in (({"why": ""}, "why"), ({"title": "  "}, "title"), ({"body": "b"}, "body"), ({"message": "x"}, "message"), ({"priority": 7}, None)):
        r = _call("task.begin", {"title": "t", "message": _founder_message(), "why": "w", "request_id": "bb", **bad})
        check(f"begin {bad}: bad_request", _code(r) == "bad_request" and (field is None or r["error"]["data"]["field"] == field), str(r))
    r = _call("task.begin", {"title": "t", "message": msg, "why": "w", "request_id": "bf"}, as_agent=None)
    check("begin: never in the founder's name", _code(r) == "refused", str(r))


def test_start_makes_an_owned_open_task_current(home: Path) -> None:
    support._board_world(home)
    one, two = _task_for("worker", "one"), _task_for("worker", "two")
    r = _call("task.start", {"task": two, "request_id": "s1"})
    con = db.connect()
    cur = db.current_task(con, "worker")
    con.close()
    check("start: accepted and the task is the caller's current one", r["result"]["accepted"] is True and cur == two, str(r))
    _lose_the_reply("s1")
    again = _call("task.start", {"task": two, "request_id": "s1"}, id=2)
    check("start: a retry is replayed", again["result"].get("replayed") is True, str(again))
    check("start: another's task is refused", _code(_call("task.start", {"task": _task_for("manager"), "request_id": "s2"})) == "refused")
    check("start: a task nobody has is not_found", _code(_call("task.start", {"task": 99999, "request_id": "s3"})) == "not_found")
    check("start: a task that is not a number is bad_request", _code(_call("task.start", {"task": "x", "request_id": "s4"})) == "bad_request")


def test_progress_is_kept_where_hl_progress_keeps_it(home: Path) -> None:
    support._board_world(home)
    tid = _task_for("worker")
    _call("task.start", {"task": tid, "request_id": "p0"})
    r = _call("task.progress", {"task": tid, "note": "done: a; left: b", "request_id": "p1"})
    con = db.connect()
    kept = handoffmod.get_progress(con, "worker")
    con.close()
    check("progress: accepted and kept for the handoff and the wake prompt", r["result"]["accepted"] is True and kept == "done: a; left: b", str((r, kept)))
    _lose_the_reply("p1")
    check("progress: a retry is replayed", _call("task.progress", {"task": tid, "note": "done: a; left: b", "request_id": "p1"}, id=2)["result"].get("replayed") is True)
    _call("task.progress", {"task": tid, "note": "", "request_id": "p2"})
    con = db.connect()
    check("progress: an empty note clears it", not handoffmod.get_progress(con, "worker"))
    con.close()
    other = _task_for("worker", "second")
    r = _call("task.progress", {"task": other, "note": "on the other one", "request_id": "p3"})
    con = db.connect()
    kept = db.get_meta(con, handoffmod._task_progress_key(other))
    con.close()
    check("progress: a task of the caller that is not the current one keeps its own note", r["result"]["accepted"] is True and kept == "on the other one", str((r, kept)))
    check("progress: another's task is refused, gate state", _call("task.progress", {"task": _task_for("manager"), "note": "n", "request_id": "p4"})["error"]["data"].get("gate") == "state")
    check("progress: a note past the handoff's size is bad_request", _code(_call("task.progress", {"task": tid, "note": "x" * 1600, "request_id": "p5"})) == "bad_request")
    check("progress: a note that is not text is bad_request", _code(_call("task.progress", {"task": tid, "note": 5, "request_id": "p6"})) == "bad_request")
    check("progress: a task nobody has is not_found", _code(_call("task.progress", {"task": 99999, "note": "n", "request_id": "p7"})) == "not_found")


def test_handoff_moves_the_task_with_its_note_once_even_after_a_crash(home: Path) -> None:
    support._board_world(home)
    tid = _task_for("worker")
    r = _call("task.handoff", {"task": tid, "to": "manager", "note": "over to you: the sha is x", "request_id": "h1"})
    check("handoff: accepted, the task is the new owner's, and the note was delivered to them",
          r["result"]["accepted"] is True and _one("SELECT owner FROM tasks WHERE id = ?", tid) == "manager"
          and _one("SELECT COUNT(*) FROM requests WHERE target = 'manager' AND body LIKE '%over to you%'") == 1, str(r))
    _lose_the_reply("h1")
    again = _call("task.handoff", {"task": tid, "to": "manager", "note": "over to you: the sha is x", "request_id": "h1"}, id=2)
    check("handoff: a retry after a lost reply is replayed and delivers nothing twice", again["result"].get("replayed") is True
          and _one("SELECT COUNT(*) FROM requests WHERE target = 'manager' AND body LIKE '%over to you%'") == 1, str(again))
    # a crash after the whole move and before its record: the effect is not there, the intent is
    tid2 = _task_for("worker", "second")
    real = db.record_effect
    state = {"die": True}

    def dying(*a, **kw):
        if state["die"] and a[3] == "task.handoff":
            state["die"] = False
            raise RuntimeError("the daemon died here")
        return real(*a, **kw)
    db.record_effect = dying
    try:
        first = _call("task.handoff", {"task": tid2, "to": "manager", "note": "second note", "request_id": "h2"})
    finally:
        db.record_effect = real
    check("handoff: the crash is an error, after the whole move", "error" in first and _one("SELECT owner FROM tasks WHERE id = ?", tid2) == "manager", str(first))
    retry = _call("task.handoff", {"task": tid2, "to": "manager", "note": "second note", "request_id": "h2"}, id=2)
    check("handoff: the retry of it answers and delivers the note no second time", retry["result"]["accepted"] is True
          and _one("SELECT COUNT(*) FROM requests WHERE target = 'manager' AND body LIKE '%second note%'") == 1, str(retry))
    check("handoff: not an agent that exists is not_found", _code(_call("task.handoff", {"task": tid2, "to": "nobody", "note": "n", "request_id": "h3"})) == "not_found")
    check("handoff: a task nobody has is not_found", _code(_call("task.handoff", {"task": 99999, "to": "manager", "note": "n", "request_id": "h4"})) == "not_found")
    check("handoff: a note is required", _code(_call("task.handoff", {"task": tid2, "to": "manager", "note": " ", "request_id": "h5"})) == "bad_request")
    check("handoff: another's task is refused", _code(_call("task.handoff", {"task": _task_for("manager", "m"), "to": "worker", "note": "n", "request_id": "h6"})) == "refused")


def test_a_devices_task_verbs_are_recorded_under_the_device(home: Path) -> None:
    support._board_world(home)
    ctx = types.SimpleNamespace(caller=rpc.DeviceCaller("dev_a", "phone"))
    check("device retry: the intent key names the device", "device:dev_a" in rpc_taskverbs._intent(ctx, {"request_id": "x"}, "task.handoff"))
    # a device writes as the human: task.start of a task the human owns records under device:<id>
    recorded = []
    real = db.record_effect
    db.record_effect = lambda con, who, rid, name, result: recorded.append((who, name)) or real(con, who, rid, name, result)
    try:
        rpc_taskverbs._in_one_transaction(ctx, {"request_id": "dv1"}, "task.progress", lambda con: (True, "kept", {}))
    finally:
        db.record_effect = real
    check("device retry: the record is under device:<id>, not as human", recorded == [("device:dev_a", "task.progress")], str(recorded))


def test_the_task_verb_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    support._board_world(home)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "contract"))
    import conformance
    real = sessiontoken.agent_of
    seen = {"role": None}

    def connect_as(role):
        def connect():
            a, b = socket.socketpair()
            sessiontoken.agent_of = lambda pid: "worker" if role == "agent" else None
            threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
            return a
        return connect
    try:
        results = []
        for role in ("agent", "founder"):
            results += conformance.run_all([Path(__file__).resolve().parents[2] / "contract" / "fixtures" / "task_verbs.json"], {role: connect_as(role)}, {"founder_key": _hp()["key"]})
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for s, why, n in results if s == "fail"]
    check("fixtures: every task-verb fixture passes against this daemon, each as its own role", not bad and len([1 for s, _, _ in results if s == "pass"]) == 9, str(results))


def main() -> int:
    def tests(td):
        run(lambda: test_begin_opens_and_starts_a_task_on_a_founder_message_once(Path(td) / "v1"))
        run(lambda: test_start_makes_an_owned_open_task_current(Path(td) / "v2"))
        run(lambda: test_progress_is_kept_where_hl_progress_keeps_it(Path(td) / "v3"))
        run(lambda: test_handoff_moves_the_task_with_its_note_once_even_after_a_crash(Path(td) / "v4"))
        run(lambda: test_a_devices_task_verbs_are_recorded_under_the_device(Path(td) / "v5"))
        run(lambda: test_the_task_verb_fixtures_pass_against_this_daemon(Path(td) / "v6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
