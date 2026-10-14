"""Tests: `ocp ask` and `ocp say` from the caller's side: a task keeps the one-at-a-time rule, who is asking comes
from the caller, the daemon's old close notes leave the chat once, and with the daemon down nothing is sent.
Run on its own: python3 tests/daemon/test_sends.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.cli import ask as cli_ask  # noqa: E402


def _world(home: Path) -> list:
    """backend can be typed at: a terminal, a live session, a clear screen."""
    _daemon_home(home, 20)
    sessions_are("backend")
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    termmod.screen_blocker = lambda h: None
    termmod.typeable_now = lambda h: False    # only an immediate telling can type
    termmod.live_terminals = lambda: {"term_x"}
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    termmod.send = send
    return sent


def test_a_task_kind_ask_keeps_the_one_at_a_time_rule(home: Path) -> None:
    sent = _world(home)
    hand_edit_team(DESIGN_TEAM.replace(
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async }',
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async, kind: task }'))
    reply = _ask_row({"verb": "ask", "requester": "product", "speaker": "product",
                   "target": "backend", "body": "a piece of work"})
    check("ask: a task-kind ask is accepted", reply["ok"], str(reply))
    check("ask: but not typed at once - it waits for an agent that is free", sent == [], str(sent))


def test_who_is_asking_comes_from_the_caller_not_the_daemon(home: Path) -> None:
    sent = _world(home)
    for payload in ({"requester": "human", "speaker": "product"},   # an agent claiming to be the founder
                    {"speaker": "product"}):                        # nobody named
        reply = _ask_row({"verb": "ask", "target": "backend", "body": "hello", **payload})
        check(f"ask: {payload} is refused", not reply["ok"], str(reply))
    con = db.connect()
    try:
        check("ask: and nothing was written or typed", sent == []
              and con.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"] == 0)
    finally:
        con.close()


def _drawn_replies(con, target: str) -> list[str]:
    """What the founder's chat with `target` shows as a reply: the verdict of each message the founder sent it."""
    rows = con.execute("SELECT id FROM requests WHERE requester = ? AND target = ? AND settles IS NULL ORDER BY id",
                       (actions.HUMAN, target)).fetchall()
    return [v for r in rows if (v := db.shown_verdict(db.get_request(con, r["id"])))]


def test_the_daemons_old_close_notes_are_taken_out_of_the_chat_once(home: Path) -> None:
    """T441: says closed before this carry "typed" (or the old-say note) as a verdict and an answer
    message of its own. Both go, whichever shape the answer has; a real answer to a former ask stays."""
    _world(home)
    con = db.connect()
    support.legacy_shape(con)
    try:
        told = [db.add_request(con, actions.HUMAN, "backend", f"say {i}", None, "peer", "async", "say", 0, None,
                               verdict=note)
                for i, note in enumerate(("typed", "closed: told before says were typed at once"))]
        for t, note in zip(told, ("typed", "closed: told before says were typed at once")):
            con.execute("UPDATE requests SET state = 'done', verdict = ? WHERE id = ?", (note, t))      # the old shape
        fakes = [db.add_answer(con, t, db.shown_verdict(db.get_request(con, t))) for t in told]
        ask = db.add_request(con, actions.HUMAN, "backend", "a former ask", None, "peer", "async", "say", 0, None,
                             verdict="PASS, nothing to change")
        con.execute("UPDATE requests SET state = 'done', verdict = 'PASS, nothing to change' WHERE id = ?", (ask,))
        real = db.add_answer(con, ask, "PASS, nothing to change")
        old_ask = db.add_request(con, actions.HUMAN, "backend", "before answers were says", None, "peer", "async",
                                 None, 0, None, verdict="typed")
        con.execute("UPDATE requests SET state = 'done', verdict = 'typed' WHERE id = ?", (old_ask,))
        old_fake = db.add_answer(con, old_ask, "typed")
        con.execute("UPDATE requests SET kind = 'answer', settles = NULL WHERE id = ?", (fakes[0],))   # the old shape
        db.set_cursor(con, "migrated:close_notes", 0)
        con.close()
        con = support.upgrade()
        gone = [con.execute("SELECT 1 FROM requests WHERE id = ?", (f,)).fetchone() is None for f in fakes]
        check("close notes: the messages made of them are gone, as an answer row or as a say", all(gone), str(gone))
        check("close notes: and the verdicts, so nothing is drawn from the say either",
              all(db.shown_verdict(db.get_request(con, t)) is None for t in told))
        check("close notes: a real answer to a former ask stays, drawn once",
              con.execute("SELECT 1 FROM requests WHERE id = ?", (real,)).fetchone() is not None
              and _drawn_replies(con, "backend").count("PASS, nothing to change") == 1,
              str(_drawn_replies(con, "backend")))
        check("close notes: an ask that was not a say when it was answered is left alone",
              db.shown_verdict(db.get_request(con, old_ask)) == "typed"
              and con.execute("SELECT 1 FROM requests WHERE id = ?", (old_fake,)).fetchone() is not None)
        check("close notes: the old-say note is drawn as nobody's reply",
              "closed: told before says were typed at once" not in _drawn_replies(con, "backend"),
              str(_drawn_replies(con, "backend")))
    finally:
        con.close()


def test_with_the_daemon_down_say_sends_nothing(home: Path) -> None:
    _world(home)
    saved = cli_ask.dmn_client
    cli_ask.dmn_client = types.SimpleNamespace(send=lambda payload, timeout=None: None)
    try:
        rc2, out2 = _cli(["say", "backend", "hello"], as_agent="product")
    finally:
        cli_ask.dmn_client = saved
    con = db.connect()
    try:
        check("daemon down: say says nothing was sent and exits 1",
              rc2 == 1 and "daemon not running, nothing sent" in out2, out2)
        check("daemon down: and it wrote no row - there is no other path",
              con.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"] == 0)
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_task_kind_ask_keeps_the_one_at_a_time_rule(Path(td) / "s3"))
        run(lambda: test_who_is_asking_comes_from_the_caller_not_the_daemon(Path(td) / "s4"))
        run(lambda: test_the_daemons_old_close_notes_are_taken_out_of_the_chat_once(Path(td) / 'closenotes'))
        run(lambda: test_with_the_daemon_down_say_sends_nothing(Path(td) / "s6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
