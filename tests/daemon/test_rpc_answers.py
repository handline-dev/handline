"""Tests: answers.list, answer.add, answer.remove and inbox.get (contract/protocol.md): the founder's standing answers, a retry that does not
add or remove twice, who may read and who may write, and one request read whole (with the wait that does not hold up the connection).

Run on its own: python3 tests/daemon/test_rpc_answers.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict | None = None, as_agent: str | None = None) -> dict:
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


class RecordFailsOnce:
    """`record_effect` raising once, after the write was made: the daemon dying between the two."""
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


def _answers() -> list[dict]:
    return _call("answers.list")["result"]["answers"]


def test_the_founders_saved_answers_are_listed_and_only_theirs(home: Path) -> None:
    support._board_world(home)
    folder = home / "trusted"
    folder.mkdir()
    _call("answer.add", {"folder": str(folder), "request_id": "l1"})
    _call("answer.add", {"vendor": "claude", "title": "Do you trust this?", "label": "Yes", "request_id": "l2"})
    got = _answers()
    check("answers.list: the trusted folder, key [folder]", {"kind": "trust", "key": [str(folder.resolve())], "text": f"trust {folder.resolve()}"} in got
          or any(a["kind"] == "trust" and a["key"][0].endswith("trusted") for a in got), str(got))
    check("answers.list: the dialog rule, key [vendor, title, label]", any(a["kind"] == "dialog" and a["key"] == ["claude", "Do you trust this?", "Yes"] for a in got), str(got))
    for method, params in (("answers.list", {}), ("answer.add", {"folder": str(folder), "request_id": "l3"}), ("answer.remove", {"kind": "trust", "key": ["x"], "request_id": "l4"})):
        r = _call(method, params, as_agent="manager")
        check(f"{method}: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_adding_an_answer_is_checked_and_a_retry_adds_nothing_twice(home: Path) -> None:
    support._board_world(home)
    folder = home / "work"
    folder.mkdir()
    r = _call("answer.add", {"folder": str(folder), "request_id": "a1"})
    check("answer.add: a folder that exists is trusted", r["result"]["accepted"] is True and len(_answers()) == 1, str(r))
    again = _call("answer.add", {"folder": str(folder), "request_id": "a1"})
    check("answer.add: a retry with the reply kept is replayed", again["result"].get("replayed") is True and len(_answers()) == 1, str(again))
    r = _call("answer.add", {"folder": str(folder), "request_id": "a2"})
    check("answer.add: the same folder under a new request is refused: already inside a declared root", _code(r) == "refused", str(r))
    for params, code in (({}, "bad_request"), ({"folder": 5}, "bad_request"), ({"folder": "  "}, "bad_request")):
        check(f"answer.add: {params} is {code}", _code(_call("answer.add", {**params, "request_id": f"a-{params}"})) == code)
    for params in ({"folder": str(home / "not-a-folder")}, {"vendor": "claude"}, {"vendor": "claude", "title": "t"}):
        r = _call("answer.add", {**params, "request_id": f"a-{params}"})
        check(f"answer.add: {params} is refused, and nothing was added", _code(r) == "refused" and len(_answers()) == 1, str(r))
    other = home / "other"
    other.mkdir()
    with RecordFailsOnce():
        first = _call("answer.add", {"folder": str(other), "request_id": "a3"})
    check("answer.add: a death between the write and its record is an error, and the write is there", "error" in first and len(_answers()) == 2, str(first))
    retry = _call("answer.add", {"folder": str(other), "request_id": "a3"})
    check("answer.add: the retry finds it already so and accepts, adding nothing", retry["result"]["accepted"] is True and len(_answers()) == 2, str(retry))
    third = _call("answer.add", {"folder": str(other), "request_id": "a3"})
    check("answer.add: and the next retry is replayed", third["result"].get("replayed") is True, str(third))
    _call("answer.add", {"vendor": "claude", "title": "Proceed?", "label": "Yes", "request_id": "a4"})
    con = db.connect()
    db.set_cursor(con, "intent:answer.add:human:a5", 1)     # an intent left behind: the first attempt died before it wrote anything, as far as the retry can tell
    con.close()
    refusal = _call("answer.add", {"vendor": "claude", "title": "Proceed?", "label": "No", "request_id": "a5"})
    check("answer.add: a retry whose first attempt left an intent still judges by the store: a rule with another label is refused, not reported as saved",
          _code(refusal) == "refused" and ["claude", "Proceed?", "Yes"] in [a["key"] for a in _answers()] and ["claude", "Proceed?", "No"] not in [a["key"] for a in _answers()], str(refusal))
    check("answer.add: no intent is left behind", db.connect().execute("SELECT COUNT(*) FROM cursors WHERE name LIKE 'intent:%'").fetchone()[0] == 0)


def test_removing_an_answer_by_the_key_the_list_gave(home: Path) -> None:
    support._board_world(home)
    folder = home / "work"
    folder.mkdir()
    _call("answer.add", {"folder": str(folder), "request_id": "r1"})
    _call("answer.add", {"vendor": "claude", "title": "Proceed?", "label": "Yes", "request_id": "r2"})
    for params, code in (({"kind": "other", "key": ["x"]}, "bad_request"), ({"kind": "trust", "key": []}, "bad_request"), ({"kind": "trust", "key": "x"}, "bad_request"),
                         ({"kind": "dialog", "key": ["claude", "Proceed?"]}, "bad_request"), ({"kind": "trust", "key": ["/no/such/folder"]}, "not_found"),
                         ({"kind": "dialog", "key": ["claude", "Proceed?", "No"]}, "not_found")):
        check(f"answer.remove: {params} is {code}", _code(_call("answer.remove", {**params, "request_id": f"r-{params}"})) == code)
    key = next(a["key"] for a in _answers() if a["kind"] == "trust")
    r = _call("answer.remove", {"kind": "trust", "key": key, "request_id": "r3"})
    check("answer.remove: a trusted folder is taken off", r["result"]["accepted"] and all(a["kind"] != "trust" for a in _answers()), str(r))
    again = _call("answer.remove", {"kind": "trust", "key": key, "request_id": "r3"})
    check("answer.remove: a retry with the reply kept is replayed", again["result"].get("replayed") is True, str(again))
    with RecordFailsOnce():
        first = _call("answer.remove", {"kind": "dialog", "key": ["claude", "Proceed?", "Yes"], "request_id": "r4"})
    check("answer.remove: a death between the removal and its record is an error, and the rule is gone", "error" in first and not _answers(), str(first))
    retry = _call("answer.remove", {"kind": "dialog", "key": ["claude", "Proceed?", "Yes"], "request_id": "r4"})
    check("answer.remove: the retry finds it already gone and accepts", retry["result"]["accepted"] is True and not retry["result"].get("replayed"), str(retry))
    third = _call("answer.remove", {"kind": "dialog", "key": ["claude", "Proceed?", "Yes"], "request_id": "r4"})
    check("answer.remove: and the next retry is replayed", third["result"].get("replayed") is True, str(third))
    r = _call("answer.remove", {"kind": "dialog", "key": ["claude", "Proceed?", "Yes"], "request_id": "r5"})
    check("answer.remove: a first attempt at one that is gone is judged strictly: not_found", _code(r) == "not_found", str(r))


def _add(requester: str, target: str, body: str) -> int:
    con = db.connect()
    try:
        return db.add_request(con, requester, target, body, None, None, None, "ask", 0, None)
    finally:
        con.close()


def test_inbox_get_reads_one_request_whole(home: Path) -> None:
    support._board_world(home)
    rid = _add("manager", "worker", "what is the plan?")
    r = _call("inbox.get", {"id": rid}, as_agent="worker")
    item = r["result"]
    check("inbox.get: the target reads it, the other end named as its agent", item["id"] == rid and item["agent"] == "manager" and item["body"] == "what is the plan?"
          and item["answered"] is False, str(item))
    item = _call("inbox.get", {"id": rid}, as_agent="manager")["result"]
    check("inbox.get: the sender reads it too, and the agent is the other end", item["agent"] == "worker", str(item))
    stranger = _call("inbox.get", {"id": rid}, as_agent="manager2")["result"]
    check("inbox.get: any agent may read any request, as hl read always allowed", stranger["id"] == rid and stranger["agent"] == "worker", str(stranger))
    con = db.connect()
    db.add_answer(con, rid, "the plan is to ship")
    con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (rid,))
    con.close()
    check("inbox.get: once answered, it says so", _call("inbox.get", {"id": rid}, as_agent="manager")["result"]["answered"] is True)
    hidden = _add("manager", "worker", "not yet passed by the routing table")
    con = db.connect()
    con.execute("UPDATE requests SET edge_ok = 0 WHERE id = ?", (hidden,))
    con.close()
    check("inbox.get: one its routing table has not passed is not the target's to read", _code(_call("inbox.get", {"id": hidden}, as_agent="worker")) == "not_found")
    founder = _call("inbox.get", {"id": rid})["result"]
    check("inbox.get: the founder reads it as well; neither end is theirs, so the agent is the target (the rule: the sender when the reader is the target, else the target)",
          founder["id"] == rid and founder["agent"] == "worker", str(founder))
    mine = _add(db.HUMAN, "worker", "from the person")
    theirs = _add("worker", db.HUMAN, "to the person")
    check("inbox.get: a request from the person, read by the agent it is for, names the person (as inbox.list does)", _call("inbox.get", {"id": mine}, as_agent="worker")["result"]["agent"] == db.HUMAN)
    check("inbox.get: a request to the person, read by the agent that sent it, names the person", _call("inbox.get", {"id": theirs}, as_agent="worker")["result"]["agent"] == db.HUMAN)
    check("inbox.get: and read by the person, each names the agent", _call("inbox.get", {"id": mine})["result"]["agent"] == "worker" and _call("inbox.get", {"id": theirs})["result"]["agent"] == "worker")
    for params, code in (({"id": 999999}, "not_found"), ({"id": "1"}, "bad_request"), ({}, "bad_request"), ({"id": rid, "wait_s": -1}, "bad_request"),
                         ({"id": rid, "wait_s": 601}, "bad_request")):
        check(f"inbox.get: {params} is {code}", _code(_call("inbox.get", params, as_agent="worker")) == code)


def test_inbox_get_waits_for_the_answer_without_holding_up_the_connection(home: Path) -> None:
    support._board_world(home)
    rid = _add("manager", "worker", "will you answer?")
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "manager"
    w = Wire()
    try:
        w.call("hello", {"client": "t", "protocol": 1})
        w.send({"jsonrpc": "2.0", "id": 10, "method": "inbox.get", "params": {"id": rid, "wait_s": 30}})
        t0 = time.time()
        quick = w.call("whoami", id=11)
        check("inbox.get: a call behind the waiting one is answered meanwhile", quick["id"] == 11 and "result" in quick and time.time() - t0 < 5, str(quick))
        con = db.connect()
        db.add_answer(con, rid, "yes")
        con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (rid,))
        con.close()
        got = w.read()
        check("inbox.get: and the waiting one returns once the request is settled, answered", got["id"] == 10 and got["result"]["answered"] is True and time.time() - t0 < 10, str(got))
        left = threading.active_count()
        w.send({"jsonrpc": "2.0", "id": 12, "method": "inbox.get", "params": {"id": _add("manager", "worker", "never answered"), "wait_s": 600}})
        time.sleep(0.3)
        w.close()
        t1 = time.time()
        while threading.active_count() > left and time.time() - t1 < 5:
            time.sleep(0.05)
        check("inbox.get: a client that hangs up ends its wait", threading.active_count() <= left)
    finally:
        sessiontoken.agent_of = real


def test_hl_read_still_works_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    rid = _add("manager", "worker", "read me whole")
    rc, out = support._cli(["read", str(rid)], as_agent="manager")
    check("hl read: works at role agent", rc == 0 and "read me whole" in out, out)


def test_the_standing_answer_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
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
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/standing_answers.json")], {role: connector()},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every standing-answer fixture passes against this daemon, each as its own role", not bad and len({n for _, _, n, _ in results}) == 8, str(bad))


def test_a_devices_answer_writes_are_kept_under_the_device(home: Path) -> None:
    import types
    from handline.daemon import rpc, rpc_answers
    support._board_world(home)
    ctx = types.SimpleNamespace(caller=rpc.DeviceCaller("dev_a", "phone"))
    other = types.SimpleNamespace(caller=rpc.DeviceCaller("dev_b", "tablet"))
    check("device retry: the intent key of a device's write names the device, and another device's is not the same",
          "device:dev_a" in rpc_answers._intent(ctx, {"request_id": "x"}, "answer.add") and rpc_answers._intent(ctx, {"request_id": "x"}, "answer.add") != rpc_answers._intent(other, {"request_id": "x"}, "answer.add"))
    rpc_answers._finish(ctx, {"request_id": "da1"}, "answer.add", "added")
    con = db.connect()
    rows = [r["caller"] for r in con.execute("SELECT caller FROM write_effects WHERE request_id = 'da1'")]
    con.close()
    check("device retry: the finished write is recorded under device:<id>, so its retry finds it", rows == ["device:dev_a"], str(rows))


def main() -> int:
    def tests(td):
        run(lambda: test_the_founders_saved_answers_are_listed_and_only_theirs(Path(td) / "n1"))
        run(lambda: test_adding_an_answer_is_checked_and_a_retry_adds_nothing_twice(Path(td) / "n2"))
        run(lambda: test_removing_an_answer_by_the_key_the_list_gave(Path(td) / "n3"))
        run(lambda: test_inbox_get_reads_one_request_whole(Path(td) / "n4"))
        run(lambda: test_inbox_get_waits_for_the_answer_without_holding_up_the_connection(Path(td) / "n5"))
        run(lambda: test_hl_read_still_works_from_an_agent_session(Path(td) / "n6"))
        run(lambda: test_the_standing_answer_fixtures_pass_against_this_daemon(Path(td) / "n7"))
        run(lambda: test_a_devices_answer_writes_are_kept_under_the_device(Path(td) / "da"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
