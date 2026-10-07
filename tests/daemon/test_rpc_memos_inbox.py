"""Tests: task.memo.add, edit and delete, and inbox.mark_read, answer and reply (contract/protocol.md): what each writes, who may call
it, a refusal each, a retry after a crash between the effect and the reply, and the commands they serve still working from an
agent session.

Run on its own: python3 tests/daemon/test_rpc_memos_inbox.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.team import ask as askmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict | None = None, as_agent: str | None = "manager") -> dict:
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


def _task() -> int:
    ok, msg, tid = actions.task_new("manager", "worker", "the work", "do it and show the result", timeout_s=3600)
    assert ok, msg
    return tid


def _to_founder(body: str, who: str = "worker", kind: str = "say") -> int:
    con = db.connect()
    try:      # an ask is one its sender parked on (response async): owed, so never read away
        return db.add_request(con, who, db.HUMAN, body, None, None, "async" if kind == "ask" else None, kind, 0, None)
    finally:
        con.close()


class Told:
    """`tell_memo_holder` replaced: records who is told what, after the memo is committed."""
    def __enter__(self):
        self.calls, self.real = [], actions.tell_memo_holder
        actions.tell_memo_holder = lambda con, tid, by, what, text: self.calls.append((tid, by, what, text, _count("SELECT COUNT(*) FROM task_memos")))
        return self

    def __exit__(self, *exc):
        actions.tell_memo_holder = self.real


def test_memos_are_added_edited_and_deleted_by_the_right_caller(home: Path) -> None:
    support._board_world(home)
    tid = _task()
    with Told() as told:
        r = _call("task.memo.add", {"task": tid, "text": "a note", "request_id": "m1"})
        mid = r["result"]["memo"]
        check("task.memo.add: accepted, with the memo's number", r["result"]["accepted"] is True and isinstance(mid, int), str(r))
        check("task.memo.add: the memo is the caller's, and the holder was told after it was committed",
              db.get_memo(db.connect(), mid)["author"] == "manager" and told.calls == [(tid, "manager", "added", "a note", 1)], str(told.calls))
        again = _call("task.memo.add", {"task": tid, "text": "a note", "request_id": "m1"})
        check("task.memo.add: a retry answers the first reply, replayed, and adds nothing",
              again["result"] == {**r["result"], "replayed": True} and _count("SELECT COUNT(*) FROM task_memos") == 1, str(again))
        _lose_the_reply("m1")
        after = _call("task.memo.add", {"task": tid, "text": "a note", "request_id": "m1"})
        check("task.memo.add: after a crash between the memo and the reply the retry finds the effect and repeats nothing",
              after["result"]["memo"] == mid and after["result"]["replayed"] is True and _count("SELECT COUNT(*) FROM task_memos") == 1, str(after))
        r = _call("task.memo.edit", {"task": tid, "memo": mid, "text": "worker tries", "request_id": "m2"}, as_agent="worker")
        check("task.memo.edit: another agent is refused and the memo is as it was", _code(r) == "refused"
              and db.get_memo(db.connect(), mid)["body"] == "a note", str(r))
        r = _call("task.memo.edit", {"task": tid, "memo": mid, "text": "the founder's wording", "request_id": "m3"}, as_agent=None)
        check("task.memo.edit: the founder may", r["result"]["accepted"] and db.get_memo(db.connect(), mid)["body"] == "the founder's wording", str(r))
        r = _call("task.memo.edit", {"task": tid, "memo": mid, "text": "the founder's wording", "request_id": "m4"})
        check("task.memo.edit: the same text is refused, not an edit", _code(r) == "refused", str(r))
        r = _call("task.memo.delete", {"task": tid, "memo": mid, "request_id": "m5"}, as_agent="worker")
        check("task.memo.delete: another agent is refused", _code(r) == "refused" and _count("SELECT COUNT(*) FROM task_memos") == 1, str(r))
        r = _call("task.memo.delete", {"task": tid, "memo": mid, "request_id": "m6"})
        check("task.memo.delete: its author may, and it is gone", r["result"]["accepted"] and _count("SELECT COUNT(*) FROM task_memos") == 0, str(r))
        check("task.memo: each change told the holder once, after its commit, a refusal none",
              [c[2] for c in told.calls] == ["added", "edited", "deleted"], str(told.calls))


def test_memos_refuse_what_is_not_there_or_not_text(home: Path) -> None:
    support._board_world(home)
    tid, other = _task(), _task()
    mid = _call("task.memo.add", {"task": other, "text": "on the other", "request_id": "x0"})["result"]["memo"]
    for method, params, code in (
            ("task.memo.add", {"task": 999, "text": "t"}, "not_found"), ("task.memo.add", {"task": tid, "text": " "}, "bad_request"),
            ("task.memo.add", {"task": "1", "text": "t"}, "bad_request"), ("task.memo.add", {"task": True, "text": "t"}, "bad_request"),
            ("task.memo.edit", {"task": tid, "memo": 999, "text": "t"}, "not_found"),
            ("task.memo.edit", {"task": tid, "memo": mid, "text": "t"}, "not_found"),
            ("task.memo.edit", {"task": tid, "memo": "1", "text": "t"}, "bad_request"),
            ("task.memo.delete", {"task": tid, "memo": mid}, "not_found"), ("task.memo.delete", {"task": 999, "memo": 1}, "not_found")):
        r = _call(method, {**params, "request_id": f"x-{method}-{params}"})
        check(f"{method}: {params} is {code}", _code(r) == code, str(r))
    check("task.memo: a memo of another task is untouched and nothing was written", _count("SELECT COUNT(*) FROM task_memos") == 1)
    r = _call("task.memo.add", {"task": tid, "text": "t"})
    check("task.memo.add: no request_id is bad_request naming it", _code(r) == "bad_request" and r["error"]["data"]["field"] == "request_id", str(r))


def test_inbox_mark_read_reads_what_the_caller_may_and_keeps_what_is_owed(home: Path) -> None:
    support._board_world(home)
    one, two = _to_founder("first report"), _to_founder("second report")
    ask = _to_founder("may I deploy?", who="manager", kind="ask")
    r = _call("inbox.mark_read", {"ids": [two], "request_id": "k1"}, as_agent=None)
    items = {i["id"]: i for i in _call("inbox.list", as_agent=None)["result"]["items"]}
    check("inbox.mark_read: the founder reads a message and what the sender said before it, and both leave the mailbox",
          r["result"]["accepted"] and one not in items and two not in items, str((r, items)))
    check("inbox.mark_read: a question owed is not read away", ask in items and not items[ask]["read"], str(items))
    r = _call("inbox.mark_read", {"ids": [one], "request_id": "k0"}, as_agent=None)
    check("inbox.mark_read: reading what was already read is accepted, nothing to do", r["result"]["accepted"], str(r))
    r = _call("inbox.mark_read", {"ids": [ask], "request_id": "k2"}, as_agent=None)
    check("inbox.mark_read: naming it is refused, gate state", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))
    r = _call("inbox.mark_read", {"ids": [999], "request_id": "k3"}, as_agent=None)
    check("inbox.mark_read: an id that is not in the mailbox is not_found", _code(r) == "not_found", str(r))
    ok, _, rid = askmod.ask("worker", "look at this", requester="manager")
    r = _call("inbox.mark_read", {"ids": [rid], "request_id": "k4"}, as_agent="worker")
    mine = [i for i in _call("inbox.list", as_agent="worker")["result"]["items"] if i["id"] == rid]
    check("inbox.mark_read: an agent reads the head of its own queue", r["result"]["accepted"] and mine and mine[0]["read"], str((r, mine)))
    r = _call("inbox.mark_read", {"ids": [one], "request_id": "k5"}, as_agent="worker")
    check("inbox.mark_read: an agent naming what was not sent to it gets not_found", _code(r) == "not_found", str(r))
    for bad in ([], ["a"], [True], "1", None):
        check(f"inbox.mark_read: ids {bad!r} is bad_request", _code(_call("inbox.mark_read", {"ids": bad, "request_id": f"k-{bad}"}, as_agent=None)) == "bad_request")
    again = _call("inbox.mark_read", {"ids": [two], "request_id": "k1"}, as_agent=None)
    check("inbox.mark_read: a retry is replayed", again["result"].get("replayed") is True, str(again))


def test_an_agents_inbox_says_how_to_close_each_item_and_how_many_wait_behind(home: Path) -> None:
    support._board_world(home)
    _, _, first = askmod.ask("worker", "look at this", requester="manager")
    askmod.ask("worker", "and this", requester="manager")
    got = _call("inbox.list", as_agent="worker")["result"]
    by = {i["id"]: i for i in got["items"]}
    check("inbox.list: an ask is closed by a say that names the asker", by[first]["how_to_close"] == '`hl say manager --parent <id> "<answer>"` answers it',
          str(by[first]))
    check("inbox.list: an agent is told how many wait behind the head", got["more_waiting"] == 1, str(got))
    mail = _call("inbox.list", as_agent=None)["result"]
    check("inbox.list: the founder's mailbox carries neither", "more_waiting" not in mail and all("how_to_close" not in i for i in mail["items"]), str(mail))


def test_inbox_answer_settles_what_the_founder_was_asked(home: Path) -> None:
    support._board_world(home)
    ask = _to_founder("may I deploy?", who="manager", kind="ask")
    r = _call("inbox.answer", {"id": ask, "text": "yes, after lunch", "request_id": "a0"}, as_agent="manager")
    check("inbox.answer: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
    for params, field in (({"id": ask}, "text"), ({"id": ask, "text": "x", "option": "yes"}, "text"), ({"id": ask, "option": "yes"}, "option"),
                          ({"id": ask, "text": "  "}, "text"), ({"id": "1", "text": "x"}, "id")):
        r = _call("inbox.answer", {**params, "request_id": f"a-{params}"}, as_agent=None)
        check(f"inbox.answer: {params} is bad_request naming {field}", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))
    check("inbox.answer: and nothing was answered", db.get_request(db.connect(), ask)["state"] == "queued")
    r = _call("inbox.answer", {"id": 999, "text": "x", "request_id": "a1"}, as_agent=None)
    check("inbox.answer: an id in no mailbox is not_found", _code(r) == "not_found", str(r))
    r = _call("inbox.answer", {"id": ask, "text": "yes, after lunch", "request_id": "a2"}, as_agent=None)
    row = db.get_request(db.connect(), ask)
    check("inbox.answer: the question is settled with the founder's words", r["result"]["accepted"] and row["state"] != "queued"
          and "after lunch" in (db.shown_verdict(row) or ""), str((r, dict(row))))
    _lose_the_reply("a2")
    n = _count("SELECT COUNT(*) FROM requests")
    again = _call("inbox.answer", {"id": ask, "text": "yes, after lunch", "request_id": "a2"}, as_agent=None)
    check("inbox.answer: after a crash between the answer and its reply the retry answers, and answers nothing twice",
          again["result"]["accepted"] and again["result"]["replayed"] is True and _count("SELECT COUNT(*) FROM requests") == n, str(again))


def test_inbox_reply_sends_a_settling_message_to_whoever_sent_the_item(home: Path) -> None:
    support._board_world(home)
    ok, _, rid = askmod.ask("worker", "which branch?", requester="manager")
    assert ok
    r = _call("inbox.reply", {"id": rid, "text": "main", "request_id": "p0"}, as_agent="manager")
    check("inbox.reply: an agent answering what it sent itself gets not_found", _code(r) == "not_found", str(r))
    r = _call("inbox.reply", {"id": rid, "text": "main", "request_id": "p1"}, as_agent="worker")
    reply = db.get_request(db.connect(), rid)
    check("inbox.reply: the worker's reply goes to the asker and settles the question", r["result"]["accepted"] and reply["state"] != "queued"
          and _count("SELECT COUNT(*) FROM requests WHERE requester = 'worker' AND target = 'manager' AND parent_id = ?", rid) == 1, str((r, dict(reply))))
    n = _count("SELECT COUNT(*) FROM requests")
    _lose_the_reply("p1")
    again = _call("inbox.reply", {"id": rid, "text": "main", "request_id": "p1"}, as_agent="worker")
    check("inbox.reply: after a crash between the send and its reply the retry replays, and sends nothing twice",
          again["result"].get("replayed") is True and _count("SELECT COUNT(*) FROM requests") == n, str(again))
    for params, code in (({"id": rid, "text": " "}, "bad_request"), ({"id": "1", "text": "x"}, "bad_request"), ({"id": 999, "text": "x"}, "not_found")):
        check(f"inbox.reply: {params} is {code}", _code(_call("inbox.reply", {**params, "request_id": f"p-{params}"}, as_agent="worker")) == code)
    report = _to_founder("a report")
    r = _call("inbox.reply", {"id": report, "text": "thanks, carry on", "request_id": "p2"}, as_agent=None)
    check("inbox.reply: the founder replies to a report: a message to its sender, with the report as parent", r["result"]["accepted"]
          and _count("SELECT COUNT(*) FROM requests WHERE requester = 'human' AND target = 'worker' AND parent_id = ?", report) == 1, str(r))


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
    check(f"{method}: a death between the effect and its record leaves the effect unwritten", "error" in first and unchanged(), str(first))
    second = _call(method, params, as_agent=as_agent)
    check(f"{method}: the retry makes the effect, once", second["result"]["accepted"] is True and not second["result"].get("replayed") and after(), str(second))
    third = _call(method, params, as_agent=as_agent)
    check(f"{method}: and the next retry replays it", third["result"].get("replayed") is True and after(), str(third))


def test_a_death_between_an_inbox_effect_and_its_record_repeats_nothing(home: Path) -> None:
    support._board_world(home)
    ok, _, rid = askmod.ask("worker", "look at this", requester="manager")
    _dies_then_retries("inbox.mark_read", {"ids": [rid], "request_id": "z1"}, "worker",
                       after=lambda: db.get_request(db.connect(), rid)["state"] == "landed",
                       unchanged=lambda: db.get_request(db.connect(), rid)["state"] == "queued")
    one = _to_founder("a report")
    _dies_then_retries("inbox.mark_read", {"ids": [one], "request_id": "z2"}, None,
                       after=lambda: one not in {i["id"] for i in _call("inbox.list", as_agent=None)["result"]["items"]},
                       unchanged=lambda: one in {i["id"] for i in _call("inbox.list", as_agent=None)["result"]["items"]})
    ask = _to_founder("may I deploy?", who="manager", kind="ask")
    _dies_then_retries("inbox.answer", {"id": ask, "text": "yes", "request_id": "z3"}, None,
                       after=lambda: db.get_request(db.connect(), ask)["state"] != "queued",
                       unchanged=lambda: db.get_request(db.connect(), ask)["state"] == "queued")
    ok, _, q = askmod.ask("worker", "which branch?", requester="manager")
    n = _count("SELECT COUNT(*) FROM requests")
    _dies_then_retries("inbox.reply", {"id": q, "text": "main", "request_id": "z4"}, "worker",
                       after=lambda: _count("SELECT COUNT(*) FROM requests") == n + 1 and db.get_request(db.connect(), q)["state"] != "queued",
                       unchanged=lambda: _count("SELECT COUNT(*) FROM requests") == n and db.get_request(db.connect(), q)["state"] == "queued")


def test_the_memo_and_inbox_commands_still_work_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    tid = _task()
    rc, out = support._cli(["task", "memo", f"T{tid}", "add", "by the command"])
    check("hl task memo add: works at role agent", rc == 0 and "added" in out, out)
    mid = _count("SELECT MAX(id) FROM task_memos")
    rc, out = support._cli(["task", "memo", f"T{tid}", "edit", str(mid), "reworded"])
    check("hl task memo edit: works at role agent", rc == 0 and "edited" in out, out)
    rc, out = support._cli(["task", "memo", f"T{tid}", "delete", str(mid)])
    check("hl task memo delete: works at role agent", rc == 0 and "deleted" in out, out)
    ok, _, rid = askmod.ask("worker", "which branch?", requester="manager")
    rc, out = support._cli(["say", "manager", "main", "--parent", str(rid)], as_agent="worker")
    check("hl say --parent (what an agent's inbox.reply is): works at role agent", rc == 0, out)


def test_the_memo_and_inbox_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connector():
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
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/memos_inbox.json")], {role: connector()},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every memo and inbox fixture passes against this daemon, each as its own role", not bad and len({n for _, _, n, _ in results}) == 11, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_memos_are_added_edited_and_deleted_by_the_right_caller(Path(td) / "m1"))
        run(lambda: test_memos_refuse_what_is_not_there_or_not_text(Path(td) / "m2"))
        run(lambda: test_inbox_mark_read_reads_what_the_caller_may_and_keeps_what_is_owed(Path(td) / "m3"))
        run(lambda: test_inbox_answer_settles_what_the_founder_was_asked(Path(td) / "m4"))
        run(lambda: test_inbox_reply_sends_a_settling_message_to_whoever_sent_the_item(Path(td) / "m5"))
        run(lambda: test_a_death_between_an_inbox_effect_and_its_record_repeats_nothing(Path(td) / "m8"))
        run(lambda: test_the_memo_and_inbox_commands_still_work_from_an_agent_session(Path(td) / "m6"))
        run(lambda: test_the_memo_and_inbox_fixtures_pass_against_this_daemon(Path(td) / "m7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
