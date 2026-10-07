"""Tests: say, ask, push, push.answer and push.cancelled (contract/protocol.md): what each writes as whom, who is refused, the death between an
effect and its record (nothing written, the retry does it once, the next is replayed), a nudge that is sent once, and the fixtures.

Run on its own: python3 tests/daemon/test_rpc_messages.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.daemon import sends  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict | None = None, as_agent: str | None = "worker") -> dict:
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


def _lose_the_reply(rid: str) -> None:
    con = db.connect()
    con.execute("DELETE FROM rpc_replies WHERE request_id = ?", (rid,))
    con.close()


def _count(sql: str, *args) -> int:
    con = db.connect()
    try:
        return con.execute(sql, args).fetchone()[0]
    finally:
        con.close()


def _row(rid: int):
    con = db.connect()
    try:
        return db.get_request(con, rid)
    finally:
        con.close()


def _world(home: Path) -> None:
    support._board_world(home)
    support.hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "human", to: "worker", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")


class RecordFailsOnce:
    """`record_effect` raising once, after the effect has been written in the same transaction: the daemon dying between the two."""
    def __enter__(self):
        self.real, self.armed = db.record_effect, True

        def failing(*a, **kw):
            if self.armed:
                self.armed = False
                raise RuntimeError("the daemon died here")
            return self.real(*a, **kw)
        db.record_effect = failing
        return self

    def __exit__(self, *exc):
        db.record_effect = self.real


def _dies_then_retries(method: str, params: dict, as_agent, after, unchanged) -> None:
    """The first call dies between the effect and its record: the effect is not there. The retry makes it once, and a third call replays."""
    with RecordFailsOnce():
        first = _call(method, params, as_agent=as_agent)
    check(f"{method}: a death between the effect and its record leaves nothing written", "error" in first and unchanged(), str(first))
    second = _call(method, params, as_agent=as_agent)
    check(f"{method}: the retry makes the effect, once", second["result"]["accepted"] is True and not second["result"].get("replayed") and after(), str(second))
    third = _call(method, params, as_agent=as_agent)
    check(f"{method}: and the next retry replays it", third["result"].get("replayed") is True and after(), str(third))


class Nudges:
    """The typing the daemon does after a write, replaced: counts what it was asked to do."""
    def __enter__(self):
        self.told, self.background = [], []
        self.real = (sends._after, sends._background)
        sends._after = lambda rid, target, log: self.told.append((rid, target))
        sends._background = lambda name, fn, log: self.background.append(name)
        return self

    def __exit__(self, *exc):
        sends._after, sends._background = self.real


def test_say_is_a_message_from_the_caller_and_is_written_once(home: Path) -> None:
    _world(home)
    with Nudges() as n:
        r = _call("say", {"target": "manager", "body": "the build is green", "request_id": "m1"})
        check("say: accepted", r["result"]["accepted"] is True, str(r))
        con = db.connect()
        row = con.execute("SELECT * FROM requests WHERE body = 'the build is green'").fetchone()
        con.close()
        check("say: a say from the caller to the target, written by the daemon", (row["requester"], row["target"], row["kind"]) == ("worker", "manager", "say"), str(dict(row)))
        check("say: and the target is then told, once, after the commit", n.told == [(row["id"], "manager")], str(n.told))
        total = _count("SELECT COUNT(*) FROM requests")
        _lose_the_reply("m1")
        again = _call("say", {"target": "manager", "body": "the build is green", "request_id": "m1"})
        check("say: after a lost reply the retry is replayed and says nothing twice", again["result"].get("replayed") is True and _count("SELECT COUNT(*) FROM requests") == total
              and len(n.told) == 1, str(again))
        _dies_then_retries("say", {"target": "manager", "body": "second word", "request_id": "m2"}, "worker",
                           after=lambda: _count("SELECT COUNT(*) FROM requests WHERE body = 'second word'") == 1,
                           unchanged=lambda: _count("SELECT COUNT(*) FROM requests WHERE body = 'second word'") == 0)
        r = _call("say", {"target": "worker", "body": "I name no task", "request_id": "m3"}, as_agent="manager")
        check("say: work cannot reach a report by message: refused, as the command refuses it", _code(r) == "refused" and "task" in r["error"]["message"], str(r))
        for params in ({"target": "manager"}, {"body": "x"}, {"target": "manager", "body": " "}, {"target": "manager", "body": "x", "task": "1"},
                       {"target": "manager", "body": "x", "parent": True}, {"target": "manager", "body": "x", "no_task": ""}):
            check(f"say: {params} is bad_request", _code(_call("say", {**params, "request_id": f"m-{params}"})) == "bad_request")
        r = _call("say", {"target": "ghost", "body": "x", "request_id": "m4"})
        check("say: a target that is not there is refused", _code(r) == "refused", str(r))
        r = _call("say", {"target": "worker", "body": "a word from the person", "request_id": "m5"}, as_agent=None)
        row = _row(r["result"].get("request") or _count("SELECT MAX(id) FROM requests"))
        check("say: the founder's say is the person's, to the agent", r["result"]["accepted"] and (row["requester"], row["target"]) == (db.HUMAN, "worker"), str(r))


def test_say_with_a_task_is_that_tasks_memo_in_one_write(home: Path) -> None:
    _world(home)
    ok, msg, tid = actions.task_new("manager", "worker", "the work", "do it and show the result", timeout_s=3600)
    with Nudges():
        memos = lambda: _count("SELECT COUNT(*) FROM task_memos WHERE task_id = ?", tid)
        _dies_then_retries("say", {"target": "worker", "body": "also check the docs", "task": tid, "request_id": "t1"}, "manager",
                           after=lambda: memos() == 1, unchanged=lambda: memos() == 0)
        r = _call("say", {"target": "worker", "body": "x", "task": 999, "request_id": "t2"}, as_agent="manager")
        check("say: a task that is not there is refused", _code(r) == "refused", str(r))


def test_ask_is_a_question_that_needs_an_answer(home: Path) -> None:
    _world(home)
    with Nudges() as n:
        r = _call("ask", {"target": "manager", "title": "which branch?", "body": "main or dev?", "request_id": "q1"})
        rid = r["result"]["request"]
        row = _row(rid)
        check("ask: accepted, with the request's number", r["result"]["accepted"] and isinstance(rid, int), str(r))
        check("ask: written from the caller to the target, the title first and the body under it", (row["requester"], row["target"]) == ("worker", "manager")
              and row["body"] == "which branch?\n\nmain or dev?" and row["state"] == "queued", str(dict(row)))
        check("ask: and the target is told once", n.told == [(rid, "manager")], str(n.told))
        total = _count("SELECT COUNT(*) FROM requests")
        _lose_the_reply("q1")
        again = _call("ask", {"target": "manager", "title": "which branch?", "body": "main or dev?", "request_id": "q1"})
        check("ask: after a lost reply the retry answers with the same request and asks nothing twice", again["result"]["request"] == rid and again["result"]["replayed"] is True
              and _count("SELECT COUNT(*) FROM requests") == total, str(again))
        _dies_then_retries("ask", {"target": "manager", "title": "and the tag?", "body": "v2?", "request_id": "q2"}, "worker",
                           after=lambda: _count("SELECT COUNT(*) FROM requests WHERE body LIKE 'and the tag?%'") == 1,
                           unchanged=lambda: _count("SELECT COUNT(*) FROM requests WHERE body LIKE 'and the tag?%'") == 0)
        for params, field in (({"title": "t", "body": "b"}, "target"), ({"target": "manager", "body": "b"}, "title"), ({"target": "manager", "title": "t"}, "body"),
                              ({"target": "manager", "title": "t", "body": "b", "options": [{"label": "x"}]}, "options")):
            r = _call("ask", {**params, "request_id": f"q-{params}"})
            check(f"ask: {params} is bad_request naming {field}", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))
        r = _call("ask", {"target": "ghost", "title": "t", "body": "b", "request_id": "q3"})
        check("ask: a target that is not there is refused", _code(r) == "refused", str(r))


def test_push_nudges_what_is_already_written_and_only_once(home: Path) -> None:
    _world(home)
    ok, msg, rid = askmod.say("worker", "an old request", requester="manager")
    with Nudges() as n:
        r = _call("push", {"target": "worker", "request": rid, "request_id": "p1"}, as_agent="manager")
        check("push: the caller's own request is nudged", r["result"]["accepted"] is True and n.told == [(rid, "worker")], str((r, n.told)))
        again = _call("push", {"target": "worker", "request": rid, "request_id": "p1"}, as_agent="manager")
        _lose_the_reply("p1")
        third = _call("push", {"target": "worker", "request": rid, "request_id": "p1"}, as_agent="manager")
        check("push: a retry is replayed and nudges nothing more, also after a lost reply", again["result"].get("replayed") is True and third["result"].get("replayed") is True
              and len(n.told) == 1, str((again, third, n.told)))
        r = _call("push", {"target": "worker", "request": rid, "request_id": "p2"}, as_agent="worker")
        check("push: another's request is refused, gate state", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))
        check("push: no such request is not_found", _code(_call("push", {"target": "worker", "request": 99999, "request_id": "p3"}, as_agent="manager")) == "not_found")
        check("push: a target that is not an agent is refused", _code(_call("push", {"target": "ghost", "request_id": "p4"}, as_agent="manager")) == "refused"
              and _code(_call("push", {"target": "human", "request_id": "p5"}, as_agent="manager")) == "refused")
        check("push: a target that is not text is bad_request", _code(_call("push", {"target": 5, "request_id": "p6"}, as_agent="manager")) == "bad_request")
        r = _call("push", {"target": "worker", "request_id": "p7"}, as_agent="manager")
        check("push: with no request, the target's next task is nudged in the background", r["result"]["accepted"] and n.background == ["push-worker"], str(n.background))
        r = _call("push.answer", {"requester": "manager", "request_id": "p8"}, as_agent="worker")
        _call("push.answer", {"requester": "manager", "request_id": "p8"}, as_agent="worker")
        check("push.answer: the asker is nudged once, and a retry is replayed", r["result"]["accepted"] and n.background.count("push-answer-manager") == 1, str(n.background))
        check("push.answer: an agent that is not there is refused", _code(_call("push.answer", {"requester": "ghost", "request_id": "p9"})) == "refused")


def test_push_cancelled_tells_only_of_what_was_closed_after_delivery(home: Path) -> None:
    _world(home)
    ok, msg, rid = askmod.say("worker", "work in hand", requester="manager")
    with Nudges() as n:
        check("push.cancelled: no such request is not_found", _code(_call("push.cancelled", {"request": 99999, "request_id": "c1"}, as_agent="manager")) == "not_found")
        r = _call("push.cancelled", {"request": rid, "request_id": "c2"}, as_agent="manager")
        check("push.cancelled: a request that was not closed under its target is refused, and nothing is nudged", _code(r) == "refused" and n.background == [], str(r))
        con = db.connect()
        con.execute("UPDATE requests SET state = 'landed', delivered_at = 5 WHERE id = ?", (rid,))
        db.add_event(con, "manager", "closed_under", {"request_id": rid, "target": "worker"})
        con.close()
        r = _call("push.cancelled", {"request": rid, "request_id": "c3"}, as_agent="manager")
        check("push.cancelled: one closed under its target is told, in the background", r["result"]["accepted"] and n.background == [f"notify-cancelled-{rid}"], str((r, n.background)))
        _call("push.cancelled", {"request": rid, "request_id": "c3"}, as_agent="manager")
        check("push.cancelled: and a retry tells nothing more", len(n.background) == 1)
        fresh = askmod.say("worker", "another piece of work", requester="manager")[2]
        refused = _call("push.cancelled", {"request": fresh, "request_id": "c5"}, as_agent="manager")
        con = db.connect()
        con.execute("UPDATE requests SET state = 'landed', delivered_at = 5 WHERE id = ?", (fresh,))
        db.add_event(con, "manager", "closed_under", {"request_id": fresh, "target": "worker"})
        con.close()
        again = _call("push.cancelled", {"request": fresh, "request_id": "c5"}, as_agent="manager")
        check("push.cancelled: a refusal is not kept as a success: the same request_id after the request was cancelled is judged afresh, accepted, and nudges",
              _code(refused) == "refused" and again["result"]["accepted"] is True and not again["result"].get("replayed") and n.background[-1] == f"notify-cancelled-{fresh}", str((refused, again, n.background)))
        check("push.cancelled: a request that is not a number is bad_request", _code(_call("push.cancelled", {"request": "1", "request_id": "c4"})) == "bad_request")


def test_the_messaging_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    _world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "worker"
    try:
        results = [r for r in conformance.run_all([Path("contract/fixtures/messaging.json")], {"agent": connect}, {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n in results if st != "pass"]
    check("fixtures: every messaging fixture passes against this daemon", not bad and len({n for _, _, n in results}) == 8, str(bad))


def test_a_devices_message_is_recorded_under_the_device_so_its_retry_finds_it(home: Path) -> None:
    import types
    from handline.daemon import rpc, rpc_messages
    support._board_world(home)
    ctx = types.SimpleNamespace(caller=rpc.DeviceCaller("dev_a", "phone"))
    con = db.connect()
    rpc_messages._record(ctx, {"request_id": "dm1"}, "say")(con, "said")
    rows = [r["caller"] for r in con.execute("SELECT caller FROM write_effects WHERE request_id = 'dm1'")]
    con.close()
    check("device retry: a message a device sends is recorded under device:<id>, not as human, so its retry finds the record and sends nothing twice",
          rows == ["device:dev_a"], str(rows))


def main() -> int:
    def tests(td):
        run(lambda: test_say_is_a_message_from_the_caller_and_is_written_once(Path(td) / "g1"))
        run(lambda: test_say_with_a_task_is_that_tasks_memo_in_one_write(Path(td) / "g2"))
        run(lambda: test_ask_is_a_question_that_needs_an_answer(Path(td) / "g3"))
        run(lambda: test_push_nudges_what_is_already_written_and_only_once(Path(td) / "g4"))
        run(lambda: test_push_cancelled_tells_only_of_what_was_closed_after_delivery(Path(td) / "g5"))
        run(lambda: test_the_messaging_fixtures_pass_against_this_daemon(Path(td) / "g6"))
        run(lambda: test_a_devices_message_is_recorded_under_the_device_so_its_retry_finds_it(Path(td) / "dv"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
