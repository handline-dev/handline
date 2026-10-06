"""Tests: task.done, reopen, discard, merge, queue, draft and release (contract/protocol.md): what each writes, who may call it, a refusal
each, a retry after a crash between the effect and the reply, and the commands they serve still working from an agent session.

Run on its own: python3 tests/daemon/test_rpc_task_moves.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

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


def _row(tid: int):
    con = db.connect()
    try:
        return db.get_task(con, tid)
    finally:
        con.close()


def _state(tid: int) -> str:
    con = db.connect()
    try:
        return actions.task_state(con, db.get_task(con, tid))
    finally:
        con.close()


def _doing(title: str = "the work") -> int:
    """A task its opener (manager) sent to its owner (worker): doing."""
    ok, msg, tid = actions.task_new("manager", "worker", title, "do it and show the result", timeout_s=3600)
    assert ok, msg
    return tid


def _draft(title: str = "a draft") -> int:
    con = db.connect()
    try:
        return db.new_task(con, title, "worker", "manager")
    finally:
        con.close()


def _done(tid: int) -> None:
    con = db.connect()
    try:
        ok, msg = actions.mark_task_done(con, tid, "worker", reason="did it")
        assert ok, msg
    finally:
        con.close()


def test_done_is_the_owners_with_one_kind_of_evidence(home: Path) -> None:
    support._board_world(home)
    tid = _doing()
    r = _call("task.done", {"task": tid, "evidence": "checked by hand", "request_id": "d0"}, as_agent="manager")
    check("task.done: the opener is not the owner: refused, and the task is not done", _code(r) == "refused" and not _row(tid)["done_evidence"], str(r))
    for params, field in (({"evidence": "x", "request": 1}, "evidence"), ({}, "evidence"), ({"evidence": " "}, "evidence"),
                          ({"request": "1"}, "request"), ({"evidence": "x", "reason": "yes"}, "reason"), ({"evidence": "abc1234"}, "repo")):
        r = _call("task.done", {"task": tid, **params, "request_id": f"d-{params}"}, as_agent="worker")
        check(f"task.done: {params} is bad_request naming {field}", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))
    check("task.done: and nothing was written", not _row(tid)["done_evidence"])
    r = _call("task.done", {"task": tid, "evidence": "abc1234", "repo": str(home), "request_id": "d1"}, as_agent="worker")
    check("task.done: a sha that is on no main is refused, not recorded as words", _code(r) == "refused" and not _row(tid)["done_evidence"], str(r))
    r = _call("task.done", {"task": tid, "evidence": "abc1234", "reason": True, "request_id": "d2"}, as_agent="worker")
    check("task.done: reason: true records hex as words", r["result"]["accepted"] and _row(tid)["done_evidence"] == "abc1234", str(r))
    n = db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'mark_task_done'").fetchone()[0]
    _lose_the_reply("d2")
    again = _call("task.done", {"task": tid, "evidence": "abc1234", "reason": True, "request_id": "d2"}, as_agent="worker")
    check("task.done: after a crash between the write and the reply the retry answers it, and marks nothing twice",
          again["result"]["accepted"] and again["result"]["replayed"] is True
          and db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'mark_task_done'").fetchone()[0] == n == 1, str(again))
    r = _call("task.done", {"task": tid, "evidence": "again", "request_id": "d3"}, as_agent="worker")
    check("task.done: a done task is refused", _code(r) == "refused", str(r))
    r = _call("task.done", {"task": 999, "evidence": "x", "request_id": "d4"}, as_agent="worker")
    check("task.done: no such task is not_found", _code(r) == "not_found", str(r))
    other = _doing("answered elsewhere")
    r = _call("task.done", {"task": other, "request": 999, "request_id": "d5"}, as_agent="worker")
    check("task.done: a request that does not exist is refused", _code(r) == "refused" and not _row(other)["done_evidence"], str(r))


def test_reopen_is_the_askers_with_a_reason(home: Path) -> None:
    support._board_world(home)
    tid = _doing()
    r = _call("task.reopen", {"task": tid, "note": "wrong", "request_id": "o0"})
    check("task.reopen: a task that is not done is refused", _code(r) == "refused", str(r))
    _done(tid)
    r = _call("task.reopen", {"task": tid, "note": "the result was wrong", "request_id": "o1"}, as_agent="worker")
    check("task.reopen: the owner is not the asker: refused, still done", _code(r) == "refused" and _row(tid)["done_evidence"], str(r))
    r = _call("task.reopen", {"task": tid, "request_id": "o2"})
    check("task.reopen: no reason is refused, still done", _code(r) == "refused" and _row(tid)["done_evidence"], str(r))
    r = _call("task.reopen", {"task": tid, "note": "the result was wrong", "request_id": "o3"})
    check("task.reopen: the asker reopens it with a reason", r["result"]["accepted"] and not _row(tid)["done_evidence"], str(r))
    n = db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'reopen_task'").fetchone()[0]
    _lose_the_reply("o3")
    again = _call("task.reopen", {"task": tid, "note": "the result was wrong", "request_id": "o3"})
    check("task.reopen: after a crash between the write and the reply the retry answers it and reopens nothing twice",
          again["result"]["replayed"] is True and db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'reopen_task'").fetchone()[0] == n == 1, str(again))
    check("task.reopen: no such task is not_found, a text note is needed as text",
          _code(_call("task.reopen", {"task": 999, "note": "x", "request_id": "o4"})) == "not_found"
          and _code(_call("task.reopen", {"task": tid, "note": 5, "request_id": "o5"})) == "bad_request")


def test_discard_is_the_askers_with_a_reason(home: Path) -> None:
    support._board_world(home)
    kept = _draft("kept")
    r = _call("task.discard", {"task": kept, "reason": "not wanted", "request_id": "x0"}, as_agent="worker")
    check("task.discard: not the asker: refused, and the task stays", _code(r) == "refused" and not _row(kept)["discarded_by"], str(r))
    for params in ({"task": kept}, {"task": kept, "reason": " "}, {"task": "1", "reason": "x"}):
        check(f"task.discard: {params} is bad_request", _code(_call("task.discard", {**params, "request_id": f"x-{params}"})) == "bad_request")
    r = _call("task.discard", {"task": kept, "reason": "not wanted", "request_id": "x1"})
    check("task.discard: the asker discards it with a reason", r["result"]["accepted"] and _row(kept)["discarded_by"] == "manager"
          and _row(kept)["discard_reason"] == "not wanted", str(r))
    n = db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'discard_task'").fetchone()[0]
    _lose_the_reply("x1")
    again = _call("task.discard", {"task": kept, "reason": "not wanted", "request_id": "x1"})
    check("task.discard: after a crash between the write and the reply the retry answers it and discards nothing twice",
          again["result"]["replayed"] is True and db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'discard_task'").fetchone()[0] == n == 1, str(again))
    check("task.discard: no such task is not_found", _code(_call("task.discard", {"task": 999, "reason": "x", "request_id": "x2"})) == "not_found")
    busy = _doing()
    r = _call("task.discard", {"task": busy, "reason": "changed my mind", "request_id": "x3"})
    check("task.discard: a task with a request out is refused: that is task.cancel's", _code(r) == "refused" and not _row(busy)["discarded_by"], str(r))


def test_merge_folds_tasks_into_one_for_whoever_may_edit_them(home: Path) -> None:
    support._board_world(home)
    keep, a, b = _draft("keep"), _draft("a"), _draft("b")
    r = _call("task.merge", {"into": keep, "tasks": [a, b], "request_id": "g0"}, as_agent="worker")
    check("task.merge: not the opener: refused, nothing merged", _code(r) == "refused" and not _row(a)["discarded_by"], str(r))
    for params in ({"into": keep, "tasks": []}, {"into": keep, "tasks": ["1"]}, {"into": "1", "tasks": [a]}, {"into": keep}):
        check(f"task.merge: {params} is bad_request", _code(_call("task.merge", {**params, "request_id": f"g-{params}"})) == "bad_request")
    r = _call("task.merge", {"into": keep, "tasks": [a, 999], "request_id": "g1"})
    check("task.merge: a task that is not there refuses all of it, and a is untouched", _code(r) in ("refused", "not_found") and _state(a) == "draft", str(r))
    r = _call("task.merge", {"into": keep, "tasks": [a, b], "request_id": "g2"})
    check("task.merge: the opener folds both into the first", r["result"]["accepted"] and "(+ T" in _row(keep)["title"] and _row(a)["discarded_by"], str(r))
    n = db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'merge_task'").fetchone()[0]
    _lose_the_reply("g2")
    again = _call("task.merge", {"into": keep, "tasks": [a, b], "request_id": "g2"})
    check("task.merge: after a crash between the write and the reply the retry answers it and merges nothing twice",
          again["result"]["replayed"] is True and db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'merge_task'").fetchone()[0] == n == 2, str(again))


def test_queue_and_draft_move_every_task_named_or_none(home: Path) -> None:
    support._board_world(home)
    one, two = _draft("one"), _draft("two")
    r = _call("task.queue", {"tasks": [one, two], "request_id": "q0"}, as_agent="worker")
    check("task.queue: not the owner's manager: refused, both still draft", _code(r) == "refused" and _state(one) == "draft" == _state(two), str(r))
    for params in ({"tasks": []}, {"tasks": [True]}, {"tasks": "1"}, {}):
        check(f"task.queue: {params} is bad_request", _code(_call("task.queue", {**params, "request_id": f"q-{params}"})) == "bad_request")
    r = _call("task.queue", {"tasks": [one, 999], "request_id": "q1"})
    check("task.queue: one task that is not there refuses the batch: none queued", _code(r) in ("refused", "not_found") and _state(one) == "draft", str(r))
    r = _call("task.queue", {"tasks": [one, two], "request_id": "q2"})
    check("task.queue: the manager queues both, and the free owner is sent the first", r["result"]["accepted"]
          and {_state(one), _state(two)} <= {"queued", "doing"} and "doing" in {_state(one), _state(two)}, str(r))
    n = db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'queue_task'").fetchone()[0]
    _lose_the_reply("q2")
    again = _call("task.queue", {"tasks": [one, two], "request_id": "q2"})
    check("task.queue: after a crash between the write and the reply the retry answers it and queues nothing twice",
          again["result"]["replayed"] is True and db.connect().execute("SELECT COUNT(*) FROM events WHERE kind = 'queue_task'").fetchone()[0] == n == 2, str(again))
    queued = [t for t in (one, two) if _state(t) == "queued"]
    r = _call("task.draft", {"tasks": queued, "request_id": "q3"})
    check("task.draft: a queued task goes back to draft", r["result"]["accepted"] and all(_state(t) == "draft" for t in queued) and queued, str(r))
    r = _call("task.draft", {"tasks": queued, "request_id": "q4"})
    check("task.draft: a draft one is refused as already draft", _code(r) == "refused", str(r))


def test_release_takes_a_doing_task_back_for_its_opener(home: Path) -> None:
    support._board_world(home)
    tid = _doing()
    r = _call("task.release", {"task": tid, "request_id": "r0"}, as_agent="worker")
    check("task.release: not the opener: refused, still doing", _code(r) == "refused" and _state(tid) == "doing", str(r))
    for params, field in (({"task": tid, "to": "draft"}, "to"), ({"task": tid, "reason": "slow"}, "reason"), ({"task": "1"}, "task")):
        r = _call("task.release", {**params, "request_id": f"r-{params}"})
        check(f"task.release: {params} is bad_request naming {field}", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))
    r = _call("task.release", {"task": tid, "request_id": "r1"})
    check("task.release: the opener takes it back, to draft", r["result"]["accepted"] and _state(tid) == "draft", str(r))
    again = _call("task.release", {"task": tid, "request_id": "r1"})
    check("task.release: a retry with the reply kept is replayed", again["result"]["replayed"] is True and _state(tid) == "draft", str(again))
    r = _call("task.release", {"task": tid, "request_id": "r2"})
    check("task.release: a task that is not doing is refused", _code(r) == "refused", str(r))
    check("task.release: no such task is not_found", _code(_call("task.release", {"task": 999, "request_id": "r3"})) == "not_found")


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


def test_a_death_between_a_task_move_and_its_record_repeats_nothing(home: Path) -> None:
    support._board_world(home)
    t = _doing("done by hand")
    _dies_then_retries("task.done", {"task": t, "evidence": "by hand", "request_id": "y1"}, "worker",
                       after=lambda: bool(_row(t)["done_evidence"]), unchanged=lambda: not _row(t)["done_evidence"])
    _dies_then_retries("task.reopen", {"task": t, "note": "wrong", "request_id": "y2"}, "manager",
                       after=lambda: not _row(t)["done_evidence"], unchanged=lambda: bool(_row(t)["done_evidence"]))
    d = _draft("thrown away")
    _dies_then_retries("task.discard", {"task": d, "reason": "not wanted", "request_id": "y3"}, "manager",
                       after=lambda: bool(_row(d)["discarded_by"]), unchanged=lambda: not _row(d)["discarded_by"])
    keep, a = _draft("keep"), _draft("folded")
    _dies_then_retries("task.merge", {"into": keep, "tasks": [a], "request_id": "y4"}, "manager",
                       after=lambda: bool(_row(a)["discarded_by"]), unchanged=lambda: not _row(a)["discarded_by"])
    busy = _doing("keeps the worker busy")
    q = _draft("waits")
    _dies_then_retries("task.queue", {"tasks": [q], "request_id": "y5"}, "manager",
                       after=lambda: _state(q) == "queued", unchanged=lambda: _state(q) == "draft")
    _dies_then_retries("task.draft", {"tasks": [q], "request_id": "y6"}, "manager",
                       after=lambda: _state(q) == "draft", unchanged=lambda: _state(q) == "queued")
    _dies_then_retries("task.release", {"task": busy, "request_id": "y7"}, "manager",
                       after=lambda: _state(busy) == "draft", unchanged=lambda: _state(busy) == "doing")


def test_the_task_commands_still_work_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    tid = _doing()
    rc, out = support._cli(["task", "done", f"T{tid}", "by the command"], as_agent="worker")
    check("hl task done: works at role agent", rc == 0 and "done" in out, out)
    rc, out = support._cli(["task", "reopen", f"T{tid}", "wrong"])
    check("hl task reopen: works at role agent", rc == 0 and "reopened" in out, out)
    sent = _doing("taken back")
    rc, out = support._cli(["task", "release", f"T{sent}"])
    check("hl task release: works at role agent", rc == 0 and "released" in out, out)
    first = _draft("goes out")
    rc, out = support._cli(["task", "queue", f"T{first}"])
    check("hl task queue: works at role agent", rc == 0 and "queued" in out, out)
    second, third = _draft("waits"), _draft("folded")
    support._cli(["task", "queue", f"T{second}"])
    rc, out = support._cli(["task", "draft", f"T{second}"])
    check("hl task draft: works at role agent", rc == 0 and "draft" in out, out)
    rc, out = support._cli(["task", "merge", f"T{second}", f"T{third}"])
    check("hl task merge: works at role agent", rc == 0 and "merged" in out, out)
    rc, out = support._cli(["task", "discard", f"T{second}", "done with it"])
    check("hl task discard: works at role agent", rc == 0 and "discarded" in out, out)


def test_the_task_move_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    support._board_world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "manager"
    try:
        results = [r for r in conformance.run_all([Path("contract/fixtures/task_moves.json")], {"agent": connect},
                                                  {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n in results if st != "pass"]
    check("fixtures: every task move fixture passes against this daemon", not bad and len({n for _, _, n in results}) == 14, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_done_is_the_owners_with_one_kind_of_evidence(Path(td) / "k1"))
        run(lambda: test_reopen_is_the_askers_with_a_reason(Path(td) / "k2"))
        run(lambda: test_discard_is_the_askers_with_a_reason(Path(td) / "k3"))
        run(lambda: test_merge_folds_tasks_into_one_for_whoever_may_edit_them(Path(td) / "k4"))
        run(lambda: test_queue_and_draft_move_every_task_named_or_none(Path(td) / "k5"))
        run(lambda: test_release_takes_a_doing_task_back_for_its_opener(Path(td) / "k6"))
        run(lambda: test_a_death_between_a_task_move_and_its_record_repeats_nothing(Path(td) / "k9"))
        run(lambda: test_the_task_commands_still_work_from_an_agent_session(Path(td) / "k7"))
        run(lambda: test_the_task_move_fixtures_pass_against_this_daemon(Path(td) / "k8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
