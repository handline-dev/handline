"""Tests: `ocp ask` and `ocp say` as socket verbs (T351 phase 2) - the row is
written by the daemon, the notice is typed at once even mid-turn, a task keeps
the one-at-a-time rule, and with the daemon down nothing is sent.

Run on its own: python3 tests/daemon/test_sends.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.cli import ask as cli_ask  # noqa: E402
from ocp.daemon import sends  # noqa: E402


def _world(home: Path) -> list:
    """backend can be typed at: a terminal, a live session, a clear screen."""
    _daemon_home(home, 20)
    sessions_are("backend")
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.screen_blocker = lambda h: None
    orcamod.typeable_now = lambda h: False    # only an immediate telling can type
    orcamod.live_terminals = lambda: {"term_x"}
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    orcamod.send = send
    return sent


def _verb(payload: dict) -> dict:
    return dmn_socket_server._handle_request(payload, lambda _m: None)


def test_an_ask_is_written_by_the_daemon_and_typed_at_once_even_mid_turn(home: Path) -> None:
    sent = _world(home)
    reply = _verb({"verb": "ask", "requester": "product", "speaker": "product",
                   "target": "backend", "body": "please look at the retry loop"})
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM requests WHERE target = 'backend'").fetchone()
        check("ask: the reply names the new row", reply["ok"] and row is not None
              and f"#{row['id']}" in reply["said"], str(reply))
        check("ask: typed at once although the agent is not idle (typeable_now is false)",
              len(sent) == 1 and f"#{row['id']} from product" in sent[0][1]
              and "please look at the retry loop" in sent[0][1], str(sent))
        check("ask: the row is marked told out of turn and delivered, so the tick does not repeat it",
              row["delivered_at"] and row["told_out_of_turn"], str(dict(row)))
        orcamod.typeable_now = lambda h: True
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("ask: and the tick then types nothing more about it", len(sent) == 1, str(sent))
        check("ask: no pending row is left behind once it is typed", db.list_pending(con, "backend") == [])
    finally:
        con.close()


def test_an_ask_at_a_blocked_screen_waits_as_a_pending_row_and_is_typed_once_clear(home: Path) -> None:
    sent = _world(home)
    orcamod.screen_blocker = lambda h: types.SimpleNamespace(reason=lambda: "a permission prompt")
    reply = _verb({"verb": "ask", "requester": "product", "speaker": "product",
                   "target": "backend", "body": "when you are free"})
    con = db.connect()
    try:
        check("ask: a blocked screen is not typed at, and is not an error",
              reply["ok"] and sent == [], str((reply, sent)))
        check("ask: it is held for the daemon, not lost", len(db.list_pending(con, "backend")) == 1)
        orcamod.screen_blocker = lambda h: None
        dmn_messaging.deliver_pending(con, "backend", lambda _m: None)
        check("ask: typed once the screen clears, and only once",
              len(sent) == 1 and "when you are free" in sent[0][1]
              and db.list_pending(con, "backend") == [], str(sent))
    finally:
        con.close()


def test_a_task_kind_ask_keeps_the_one_at_a_time_rule(home: Path) -> None:
    sent = _world(home)
    hand_edit_team(DESIGN_TEAM.replace(
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async }',
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async, kind: task }'))
    reply = _verb({"verb": "ask", "requester": "product", "speaker": "product",
                   "target": "backend", "body": "a piece of work"})
    check("ask: a task-kind ask is accepted", reply["ok"], str(reply))
    check("ask: but not typed at once - it waits for an agent that is free", sent == [], str(sent))


def test_who_is_asking_comes_from_the_caller_not_the_daemon(home: Path) -> None:
    sent = _world(home)
    for payload in ({"requester": "human", "speaker": "product"},   # an agent claiming to be the founder
                    {"speaker": "product"}):                        # nobody named
        reply = _verb({"verb": "ask", "target": "backend", "body": "hello", **payload})
        check(f"ask: {payload} is refused", not reply["ok"], str(reply))
    con = db.connect()
    try:
        check("ask: and nothing was written or typed", sent == []
              and con.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"] == 0)
    finally:
        con.close()


def test_a_say_is_typed_at_once_and_says_how_to_answer(home: Path) -> None:
    sent = _world(home)
    reply = _verb({"verb": "say", "requester": "product", "speaker": "product",
                   "target": "backend", "body": "which branch is it on?"})
    check("say: accepted, and the reply carries the way to answer",
          reply["ok"] and "ocp say" in reply["said"], str(reply))
    check("say: typed at once", len(sent) == 1 and "which branch is it on?" in sent[0][1], str(sent))


def test_with_the_daemon_down_ask_and_say_send_nothing(home: Path) -> None:
    _world(home)
    saved = cli_ask.dmn_client
    cli_ask.dmn_client = types.SimpleNamespace(send=lambda payload, timeout=None: None)
    try:
        rc, out = _cli(["ask", "backend", "hello"], as_agent="product")
        rc2, out2 = _cli(["say", "backend", "hello"], as_agent="product")
    finally:
        cli_ask.dmn_client = saved
    con = db.connect()
    try:
        check("daemon down: ask says nothing was sent and exits 1",
              rc == 1 and "daemon not running, nothing sent" in out, out)
        check("daemon down: so does say", rc2 == 1 and "daemon not running, nothing sent" in out2, out2)
        check("daemon down: and neither wrote a row - there is no other path",
              con.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"] == 0)
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_an_ask_is_written_by_the_daemon_and_typed_at_once_even_mid_turn(Path(td) / "s1"))
        run(lambda: test_an_ask_at_a_blocked_screen_waits_as_a_pending_row_and_is_typed_once_clear(Path(td) / "s2"))
        run(lambda: test_a_task_kind_ask_keeps_the_one_at_a_time_rule(Path(td) / "s3"))
        run(lambda: test_who_is_asking_comes_from_the_caller_not_the_daemon(Path(td) / "s4"))
        run(lambda: test_a_say_is_typed_at_once_and_says_how_to_answer(Path(td) / "s5"))
        run(lambda: test_with_the_daemon_down_ask_and_say_send_nothing(Path(td) / "s6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
