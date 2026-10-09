"""Tests: chat.send, chat.answer, chat.dismiss, chat.cancel and chat.seen (contract/protocol.md): what each writes, who may call it, a refusal each,
a death between the effect and its record (nothing written, the retry does it once, the next retry is replayed), a retry after a lost
reply, and `hl withdraw` still working from an agent session.

Run on its own: python3 tests/daemon/test_rpc_chatwrites.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.team import ask as _cancel_ask  # noqa: E402
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


def _world(home: Path) -> None:
    """The board world, and the person's edges to both agents: what a real team declares for them."""
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


def _add(requester: str, target: str, body: str, kind: str = "say", response=None) -> int:
    con = db.connect()
    try:
        return db.add_request(con, requester, target, body, None, None, response, kind, 0, None)
    finally:
        con.close()


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


def test_send_is_the_persons_message_to_an_agent_through_delivery(home: Path) -> None:
    _world(home)
    r = _call("chat.send", {"agent": "worker", "body": "please look at the build", "request_id": "s1"})
    rid = r["result"]["request"]
    row = _row(rid)
    check("chat.send: accepted, with the number of the message", r["result"]["accepted"] is True and isinstance(rid, int), str(r))
    check("chat.send: a message from the person to the agent, queued for delivery", (row["requester"], row["target"], row["body"], row["state"])
          == (db.HUMAN, "worker", "please look at the build", "queued"), str(dict(row)))
    n = _count("SELECT COUNT(*) FROM requests")
    _lose_the_reply("s1")
    again = _call("chat.send", {"agent": "worker", "body": "please look at the build", "request_id": "s1"})
    check("chat.send: after a lost reply the retry answers with the same message and sends nothing twice", again["result"]["request"] == rid
          and again["result"]["replayed"] is True and _count("SELECT COUNT(*) FROM requests") == n, str(again))
    n = _count("SELECT COUNT(*) FROM requests")
    _dies_then_retries("chat.send", {"agent": "worker", "body": "second message", "request_id": "s2"}, None,
                       after=lambda: _count("SELECT COUNT(*) FROM requests") == n + 1, unchanged=lambda: _count("SELECT COUNT(*) FROM requests") == n)
    for params, code in (({"agent": "worker", "body": " "}, "bad_request"), ({"agent": "worker"}, "bad_request"), ({"agent": 5, "body": "x"}, "bad_request"),
                         ({"agent": "ghost", "body": "x"}, "not_found"), ({"agent": "worker", "body": "x", "task": "t"}, "bad_request"),
                         ({"agent": "worker", "body": "x", "task": {}}, "bad_request"), ({"agent": "worker", "body": "x", "task": {"title": "t", "time_box": "soon"}}, "bad_request")):
        check(f"chat.send: {params} is {code}", _code(_call("chat.send", {**params, "request_id": f"s-{params}"})) == code)
    r = _call("chat.send", {"agent": "worker", "body": "x", "request_id": "s9"}, as_agent="manager")
    check("chat.send: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_send_with_a_task_gives_the_agent_that_task_in_one_write(home: Path) -> None:
    _world(home)
    r = _call("chat.send", {"agent": "worker", "body": "build it and show the result", "task": {"title": "the build", "time_box": "1h"}, "request_id": "t1"})
    rid = r["result"]["request"]
    con = db.connect()
    task = con.execute("SELECT * FROM tasks WHERE title = 'the build'").fetchone()
    con.close()
    check("chat.send with a task: the task is the agent's, opened by the person, with the time box", task and task["owner"] == "worker"
          and task["opened_by"] == db.HUMAN and task["timeout_s"] == 3600, str(r))
    check("chat.send with a task: its brief is the delivery the answer names", _row(rid)["kind"] == "task" and _row(rid)["body"] == "build it and show the result", str(dict(_row(rid))))
    tasks, reqs = _count("SELECT COUNT(*) FROM tasks"), _count("SELECT COUNT(*) FROM requests")
    _lose_the_reply("t1")
    again = _call("chat.send", {"agent": "worker", "body": "build it and show the result", "task": {"title": "the build", "time_box": "1h"}, "request_id": "t1"})
    check("chat.send with a task: after a lost reply the retry opens no second task and sends nothing twice", again["result"]["replayed"] is True
          and _count("SELECT COUNT(*) FROM tasks") == tasks and _count("SELECT COUNT(*) FROM requests") == reqs, str(again))
    _dies_then_retries("chat.send", {"agent": "worker", "body": "another piece of work", "task": {"title": "another"}, "request_id": "t2"}, None,
                       after=lambda: _count("SELECT COUNT(*) FROM tasks") == tasks + 1 and _count("SELECT COUNT(*) FROM requests") == reqs + 1,
                       unchanged=lambda: _count("SELECT COUNT(*) FROM tasks") == tasks and _count("SELECT COUNT(*) FROM requests") == reqs)


def test_answer_settles_what_an_agent_asked_the_person(home: Path) -> None:
    _world(home)
    ask = _add("worker", db.HUMAN, "may I deploy?", "ask", "async")
    r = _call("chat.answer", {"request": ask, "text": "yes, after lunch", "request_id": "a1"}, as_agent="worker")
    check("chat.answer: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
    side = _add("manager", "worker", "between two agents")
    r = _call("chat.answer", {"request": side, "text": "x", "request_id": "a2"})
    check("chat.answer: a message that asked nothing of the person is refused, gate state", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))
    for params, code in (({"request": 999, "text": "x"}, "not_found"), ({"request": ask, "text": " "}, "bad_request"), ({"request": "1", "text": "x"}, "bad_request")):
        check(f"chat.answer: {params} is {code}", _code(_call("chat.answer", {**params, "request_id": f"a-{params}"})) == code)
    _dies_then_retries("chat.answer", {"request": ask, "text": "yes, after lunch", "request_id": "a3"}, None,
                       after=lambda: _row(ask)["state"] != "queued", unchanged=lambda: _row(ask)["state"] == "queued")
    _lose_the_reply("a3")
    again = _call("chat.answer", {"request": ask, "text": "yes, after lunch", "request_id": "a3"})
    check("chat.answer: after a lost reply the retry replays and answers nothing twice", again["result"].get("replayed") is True, str(again))


def test_dismiss_takes_a_message_off_without_answering_it(home: Path) -> None:
    _world(home)
    msg = _add("worker", db.HUMAN, "may I merge the build?", "ask", "async")
    r = _call("chat.dismiss", {"request": msg, "request_id": "d1"}, as_agent="worker")
    check("chat.dismiss: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
    side = _add("manager", "worker", "between two agents")
    r = _call("chat.dismiss", {"request": side, "request_id": "d2"})
    check("chat.dismiss: a message not between the person and an agent is refused, gate state", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))
    for params, code in (({"request": 999}, "not_found"), ({"request": "1"}, "bad_request")):
        check(f"chat.dismiss: {params} is {code}", _code(_call("chat.dismiss", {**params, "request_id": f"d-{params}"})) == code)
    _dies_then_retries("chat.dismiss", {"request": msg, "request_id": "d3"}, None,
                       after=lambda: _row(msg)["state"] != "queued" and db.dismissal(_row(msg))[0], unchanged=lambda: _row(msg)["state"] == "queued")
    check("chat.dismiss: the dismissal is the person's, and nobody is told of it as an answer", db.dismissal(_row(msg))[1] == db.HUMAN and not _row(msg)["delivered_at"])


def test_hl_withdraw_still_works_from_an_agent_session(home: Path) -> None:
    _world(home)
    own = _add("manager", "worker", "a question for the worker")
    rc, out = support._cli(["withdraw", str(own), "--reason", "asked twice"], as_agent="manager")
    check("hl withdraw: works at role agent", rc == 0 and "withdrawn" in out and _row(own)["state"] == "withdrawn", out)


def test_send_under_a_message_answers_it_or_says_under_it(home: Path) -> None:
    _world(home)
    ask = _add("worker", db.HUMAN, "may I deploy?", "ask", "async")
    r = _call("chat.send", {"agent": "worker", "body": "yes, after lunch", "parent": ask, "request_id": "p1"})
    check("chat.send with parent: a question to the person is answered, and says which message", r["result"]["accepted"] is True and r["result"]["request"] == ask
          and _row(ask)["state"] != "queued", str(r))
    mine = _add(db.HUMAN, "worker", "look at the build")
    r = _call("chat.send", {"agent": "worker", "body": "and the tests too", "parent": mine, "request_id": "p2"})
    new = r["result"]["request"]
    check("chat.send with parent: otherwise a say to the agent under that message", new != mine and (_row(new)["requester"], _row(new)["target"], _row(new)["parent_id"])
          == (db.HUMAN, "worker", mine), str(r))
    fyi = _add("worker", db.HUMAN, "the build is green")
    con = db.connect()
    con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (fyi,))
    con.commit()
    con.close()
    r = _call("chat.send", {"agent": "worker", "body": "thanks", "parent": fyi, "request_id": "p4"})
    new = r["result"]["request"]
    check("chat.send with parent: a landed say to the person is not answered, a say goes under it", new != fyi and _row(fyi)["state"] == "landed"
          and (_row(new)["requester"], _row(new)["target"], _row(new)["parent_id"]) == (db.HUMAN, "worker", fyi), str(r))
    side = _add("manager", "worker", "between two agents")
    n = _count("SELECT COUNT(*) FROM requests")
    for params, code in (({"parent": 999}, "not_found"), ({"parent": "1"}, "bad_request"), ({"parent": True}, "bad_request"), ({"parent": mine, "task": {"title": "t"}}, "bad_request"),
                         ({"parent": mine, "agent": "manager"}, "refused")):
        check(f"chat.send: {params} is {code}", _code(_call("chat.send", {"agent": "worker", "body": "x", **params, "request_id": f"p-{params}"})) == code)
    check("chat.send: none of those sent anything", _count("SELECT COUNT(*) FROM requests") == n)
    r = _call("chat.send", {"agent": "manager", "body": "why?", "parent": side, "request_id": "p5"})
    new = r["result"]["request"]
    check("chat.send with parent: a message between two other agents is replied to by a say to the agent named, under it",
          (_row(new)["requester"], _row(new)["target"], _row(new)["parent_id"]) == (db.HUMAN, "manager", side) and _row(side)["state"] == "queued", str(r))
    again = _add("worker", db.HUMAN, "another question", "ask", "async")
    _dies_then_retries("chat.send", {"agent": "worker", "body": "yes", "parent": again, "request_id": "p3"}, None,
                       after=lambda: _row(again)["state"] != "queued", unchanged=lambda: _row(again)["state"] == "queued")


def test_cancel_takes_back_a_message_the_person_sent(home: Path) -> None:
    _world(home)
    waiting = _add(db.HUMAN, "worker", "not yet told")
    r = _call("chat.cancel", {"request": waiting, "request_id": "c1"}, as_agent="worker")
    check("chat.cancel: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
    _dies_then_retries("chat.cancel", {"request": waiting, "request_id": "c2"}, None,
                       after=lambda: _row(waiting)["state"] == "withdrawn", unchanged=lambda: _row(waiting)["state"] == "queued")
    delivered = _add(db.HUMAN, "worker", "already told")
    con = db.connect()
    con.execute("UPDATE requests SET delivered_at = ?, typed_at = ? WHERE id = ?", (1, 1, delivered))
    con.close()
    r = _call("chat.cancel", {"request": delivered, "request_id": "c3"})
    data = r["error"]["data"] if "error" in r else {}
    check("chat.cancel: a delivered message without confirm is refused with the plan, gate confirm, and stays", _code(r) == "refused" and data.get("gate") == "confirm"
          and "worker is told to stop" in data.get("plan", "") and data["plan"] == data["reason"] and _row(delivered)["state"] == "queued", str(r))
    _dies_then_retries("chat.cancel", {"request": delivered, "confirm": True, "request_id": "c4"}, None,
                       after=lambda: _row(delivered)["state"] == "landed" and "withdrawn by the founder" in (db.shown_verdict(_row(delivered)) or ""), unchanged=lambda: _row(delivered)["state"] == "queued")
    others = _add("manager", "worker", "not the person's")
    for params, code in (({"request": others}, "refused"), ({"request": delivered}, "refused"), ({"request": 999}, "not_found"), ({"request": "1"}, "bad_request"),
                         ({"request": waiting, "confirm": "yes"}, "bad_request")):
        check(f"chat.cancel: {params} is {code}", _code(_call("chat.cancel", {**params, "request_id": f"c-{params}"})) == code)
    check("chat.cancel: another's message stays", _row(others)["state"] == "queued")


def test_cancel_sees_a_delivery_that_lands_after_its_check(home: Path) -> None:
    _world(home)
    racing = _add(db.HUMAN, "worker", "told between the check and the write")
    real = _cancel_ask.cancel

    def delivered_first(rid, **kw):
        con = db.connect()
        con.execute("UPDATE requests SET delivered_at = ?, typed_at = ? WHERE id = ?", (1, 1, rid))
        con.close()
        return real(rid, **kw)
    _cancel_ask.cancel = delivered_first
    try:
        r = _call("chat.cancel", {"request": racing, "request_id": "r1"})
    finally:
        _cancel_ask.cancel = real
    data = r["error"]["data"] if "error" in r else {}
    check("chat.cancel: a delivery after the check is refused, gate confirm, with the delivered plan, and the message stays",
          _code(r) == "refused" and data.get("gate") == "confirm" and "worker is told to stop" in data.get("plan", "") and _row(racing)["state"] == "queued", str(r))
    r = _call("chat.cancel", {"request": racing, "confirm": True, "request_id": "r2"})
    check("chat.cancel: with confirm the delivered message is cancelled", r.get("result", {}).get("accepted") is True and _row(racing)["state"] == "landed", str(r))


def test_parent_answer_refusal_carries_the_state_gate(home: Path) -> None:
    _world(home)
    ask = _add("worker", db.HUMAN, "ship it?", kind="ask", response="wait")
    real = actions.answer_ok
    actions.answer_ok = lambda *a, **kw: (False, f"#{ask} was closed while this ran")
    try:
        r = _call("chat.send", {"agent": "worker", "body": "yes", "parent": ask, "request_id": "g1"})
    finally:
        actions.answer_ok = real
    check("chat.send with parent: an answer that is refused carries gate state",
          _code(r) == "refused" and r["error"]["data"].get("gate") == "state", str(r))


def test_seen_moves_an_agents_messages_out_of_for_you(home: Path) -> None:
    _world(home)
    said = _add("worker", db.HUMAN, "done with the build")
    later = _add("worker", db.HUMAN, "and the tests")
    r = _call("chat.seen", {"agent": "worker", "up_to": said, "request_id": "e1"}, as_agent="worker")
    check("chat.seen: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
    _dies_then_retries("chat.seen", {"agent": "worker", "up_to": said, "request_id": "e2"}, None,
                       after=lambda: _row(said)["read_at"] is not None, unchanged=lambda: _row(said)["read_at"] is None)
    check("chat.seen: what came after stays new", _row(later)["read_at"] is None)
    for params, code in (({"agent": "ghost", "up_to": 1}, "not_found"), ({"agent": "worker", "up_to": "1"}, "bad_request"), ({"agent": "worker"}, "bad_request"),
                         ({"up_to": 1}, "bad_request")):
        check(f"chat.seen: {params} is {code}", _code(_call("chat.seen", {**params, "request_id": f"e-{params}"})) == code)


def test_agent_get_says_what_the_chat_page_may_offer_and_where_a_shell_runs(home: Path) -> None:
    _world(home)
    got = _call("agent.get", {"agent": "worker"})["result"]
    verbs = ("terminal", "wake", "close", "restart", "park", "fold", "move")
    con = db.connect()
    try:
        want = {v: actions.can(con, "worker", v) for v in verbs}
        cwd = actions.cwd_for(con, "worker")
    finally:
        con.close()
    check("agent.get: can has each verb, with the answer actions.can gives", list(got["can"]) == list(verbs)
          and all(got["can"][v] == {"ok": want[v][0], "why": want[v][1] or None} for v in verbs), str(got["can"]))
    check("agent.get: cwd is where a shell command runs", got["cwd"] == cwd and Path(cwd).is_dir(), str((got["cwd"], cwd)))
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: None
    try:
        results = conformance.run_all([Path("contract/fixtures/agent_page.json")], {"founder": connect}, {"founder_key": _hp()["key"], "page_agent": "worker"})
    finally:
        sessiontoken.agent_of = real
    check("fixtures: the agent page fixture passes against this daemon", [r[0] for r in results] == ["pass"], str(results))


def test_the_chat_write_fixtures_pass_against_this_daemon(home: Path) -> None:
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
    _world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent", "anonymous"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/chat_writes.json")], {role: connector()},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every chat write fixture passes against this daemon, each as its own role", not bad and len({n for _, _, n, _ in results}) == 15, str(bad))


def test_a_devices_chat_send_retried_after_a_lost_reply_sends_once(home: Path) -> None:
    from test_relay import Relay, _record_gateway
    _world(home)
    _record_gateway(os.getpid())
    a = Relay()
    a.open("dev_a", "phone")
    params = {"agent": "worker", "body": "hello from the phone", "request_id": "cs1"}
    first = a.call("chat.send", params)
    check("device retry: the send is accepted", first.get("result", {}).get("accepted") is True, str(first))
    con = db.connect()
    con.execute("DELETE FROM rpc_replies")          # the reply kept for the retry is gone: only the effect's own record is left
    rows = [r["caller"] for r in con.execute("SELECT caller FROM write_effects WHERE request_id = 'cs1'")]
    con.close()
    check("device retry: its record is under device:<id>", rows == ["device:dev_a"], str(rows))
    again = a.call("chat.send", params, id=2)
    con = db.connect()
    sent = con.execute("SELECT COUNT(*) FROM requests WHERE requester = ? AND body = ?", (db.HUMAN, "hello from the phone")).fetchone()[0]
    con.close()
    check("device retry: the retry is answered from that record and sends nothing twice", again.get("result", {}).get("replayed") is True and sent == 1, str((again, sent)))
    a.close()


def main() -> int:
    def tests(td):
        run(lambda: test_send_is_the_persons_message_to_an_agent_through_delivery(Path(td) / "w1"))
        run(lambda: test_send_with_a_task_gives_the_agent_that_task_in_one_write(Path(td) / "w2"))
        run(lambda: test_answer_settles_what_an_agent_asked_the_person(Path(td) / "w3"))
        run(lambda: test_dismiss_takes_a_message_off_without_answering_it(Path(td) / "w4"))
        run(lambda: test_hl_withdraw_still_works_from_an_agent_session(Path(td) / "w6"))
        run(lambda: test_send_under_a_message_answers_it_or_says_under_it(Path(td) / "w8"))
        run(lambda: test_cancel_takes_back_a_message_the_person_sent(Path(td) / "w9"))
        run(lambda: test_cancel_sees_a_delivery_that_lands_after_its_check(Path(td) / "wc"))
        run(lambda: test_parent_answer_refusal_carries_the_state_gate(Path(td) / "wd"))
        run(lambda: test_seen_moves_an_agents_messages_out_of_for_you(Path(td) / "wa"))
        run(lambda: test_agent_get_says_what_the_chat_page_may_offer_and_where_a_shell_runs(Path(td) / "wb"))
        run(lambda: test_the_chat_write_fixtures_pass_against_this_daemon(Path(td) / "w7"))
        run(lambda: test_a_devices_chat_send_retried_after_a_lost_reply_sends_once(Path(td) / "wd"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
