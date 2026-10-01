"""Tests (T418): a request is delivered once the receiving session's transcript shows
it, not once it was typed - a line the transcript never shows is typed again, as often
as it takes (T766: no count, no "lost"), and the words on screen say which it is.

Run on its own: python3 tests/daemon/test_landing.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp import actions  # noqa: E402
from ocp.actions import conversation  # noqa: E402
from ocp.daemon import landing  # noqa: E402

SESSION = "sess-1"


def _world(home: Path) -> list:
    _daemon_home(home, 20)
    sessions_are("backend")
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.screen_blocker = lambda h: None
    orcamod.typeable_now = lambda h: False
    orcamod.live_terminals = lambda: {"term_x"}
    landing._typed_at.clear()
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    orcamod.send = send
    return sent


def _transcript(*entries: dict) -> Path:
    f = vendors.config_dir("alpha") / "projects" / "-wt" / f"{SESSION}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return f


def _typed_row(sent: list, ago: int = 0, body: str = "look at the retry loop"):
    """A request typed at backend by the daemon `ago` seconds back, into SESSION."""
    reply = _ask_row(
        {"verb": "ask", "requester": "product", "speaker": "product", "target": "backend", "body": body},
        lambda _m: None)
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM requests WHERE target = 'backend'").fetchone()
        con.execute("UPDATE requests SET typed_session = ?, typed_at = typed_at - ? WHERE id = ?",
                    (SESSION, ago, row["id"]))
        landing._typed_at[int(row["id"])] = int(time.time()) - ago      # what the daemon remembers
        return reply, db.get_request(con, int(row["id"]))
    finally:
        con.close()


def _row(rid: int):
    con = db.connect()
    try:
        return db.get_request(con, rid)
    finally:
        con.close()


def _sweep() -> None:
    con = db.connect()
    try:
        landing.sweep(con, "backend", lambda _m: None)
    finally:
        con.close()


def _header(rid: int) -> str:
    return f"#{rid} from product, waiting for you:\nlook at the retry loop\n"


def test_only_a_line_a_person_or_the_daemon_typed_counts_as_shown(home: Path) -> None:
    f = _transcript(
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": _header(11)}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": _header(12)}]}},
        {"type": "user", "message": {"role": "user", "content": _header(13)}},
        {"type": "queue-operation", "operation": "enqueue", "content": _header(14)},
        {"type": "queue-operation", "operation": "remove", "content": _header(15)},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": _header(16)}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": _header(17)}]}})
    check("shown: a tool result printing the header is not the line", not landing.shown_in(f, 11))
    check("shown: the agent quoting it is not the line", not landing.shown_in(f, 12))
    check("shown: a user entry is", landing.shown_in(f, 13))
    check("shown: a line queued mid-turn is", landing.shown_in(f, 14))
    check("shown: its removal from the queue alone is not (the attachment is what read it)",
          not landing.shown_in(f, 15))
    check("shown: the queued command the turn then read is", landing.shown_in(f, 16))
    check("shown: a text block of a user entry is", landing.shown_in(f, 17))
    check("shown: another request's id is not #1", not landing.shown_in(f, 1))
    check("shown: a transcript that is not there shows nothing", not landing.shown_in(f.parent / "no.jsonl", 13))


def test_a_typed_request_is_delivered_only_once_its_transcript_shows_it(home: Path) -> None:
    sent = _world(home)
    reply, row = _typed_row(sent)
    rid = int(row["id"])
    check("landing: typed, so the daemon marked it, but it is not delivered", reply["ok"]
          and row["delivered_at"] and row["typed_at"] and not row["landed_at"]
          and landing.state(row) == "typed", str(dict(row)))
    _sweep()
    check("landing: no transcript yet - still only typed, and not typed again",
          landing.state(_row(rid)) == "typed" and len(sent) == 1)
    _transcript({"type": "queue-operation", "operation": "enqueue", "content": _header(rid)})
    _sweep()
    row = _row(rid)
    check("landing: the transcript shows it, and now it is delivered",
          row["landed_at"] and landing.state(row) == "delivered", str(dict(row)))
    con = db.connect()
    try:
        check("landing: the landing is on the record",
              db.last_event_ts(con, "backend", "landed") is not None)
    finally:
        con.close()


def _age(rid: int, by: int) -> None:
    """The daemon last typed `rid` `by` seconds ago."""
    landing._typed_at[rid] = int(time.time()) - by


def _tell_undelivered() -> None:
    con = db.connect()
    try:
        dmn_messaging.tell_undelivered(con, "backend", lambda _m: None)
        con.execute("UPDATE requests SET typed_session = ? WHERE typed_at IS NOT NULL AND landed_at IS NULL",
                    (SESSION,))               # the test has no live session to record
    finally:
        con.close()


def test_a_line_the_transcript_never_shows_is_typed_again_without_end(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent, ago=landing.LANDING_WAIT_S - 30)
    rid = int(row["id"])
    _transcript({"type": "user", "message": {"role": "user", "content": "something else"}})
    con = db.connect()
    try:
        con.execute("UPDATE requests SET requester = 'human' WHERE id = ?", (rid,))    # told without a terminal
    finally:
        con.close()
    _sweep()
    row = _row(rid)
    check("retry: inside the window it is not owed again, and not typed again",
          row["typed_at"] and len(sent) == 1 and landing.state(row) == "typed", str(dict(row)))
    _age(rid, landing.LANDING_WAIT_S + 5)
    _sweep()
    row = _row(rid)
    check("retry: after the window it is owed again", not row["delivered_at"] and not row["typed_at"]
          and landing.state(row) == "queued", str(dict(row)))
    _tell_undelivered()
    check("retry: the tick types it again, once more", len(sent) == 2 and _row(rid)["typed_at"]
          and "look at the retry loop" in sent[1][1], str(sent))
    _sweep()
    _tell_undelivered()
    check("retry: and the window starts over - nothing more inside it", len(sent) == 2, str(sent))
    for n in range(3, 7):                    # no count, no giving up
        _age(rid, landing.LANDING_WAIT_S + 5)
        _sweep()
        _tell_undelivered()
    row = _row(rid)
    check("retry: typed again each time the window passes, a sixth time too", len(sent) == 6
          and row["state"] == "queued" and not row["landed_at"], f"{len(sent)} {dict(row)}")
    con = db.connect()
    try:
        _age(rid, landing.LANDING_WAIT_S + 5)
        con.execute("UPDATE requests SET ts = ts - 3600 WHERE id = ?", (rid,))
        why = [s for s in actions.stuck(con) if s["what"].startswith(f"#{rid} ")]
        check("never lands: it stays queued and is on the stuck list", bool(why), str(why))
        check("never lands: and nothing is said as lost", con.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'landing_lost'").fetchone()[0] == 0)
    finally:
        con.close()


def test_a_daemon_restart_types_a_line_at_most_once_more(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent)
    rid = int(row["id"])
    _transcript({"type": "user", "message": {"role": "user", "content": "something else"}})
    con = db.connect()
    try:
        con.execute("UPDATE requests SET requester = 'human' WHERE id = ?", (rid,))
    finally:
        con.close()
    landing._typed_at.clear()               # the daemon restarted: it remembers nothing
    _sweep()
    _tell_undelivered()
    check("restart: the line the transcript does not hold is typed once more", len(sent) == 2, str(sent))
    _sweep()
    _tell_undelivered()
    check("restart: and not again inside the window", len(sent) == 2, str(sent))


def test_a_line_already_in_the_transcript_is_landed_without_typing(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent)
    rid = int(row["id"])
    con = db.connect()
    try:
        con.execute("UPDATE requests SET requester = 'human' WHERE id = ?", (rid,))
        db.owe_again(con, rid)                # as if the daemon had lost its record of the send
    finally:
        con.close()
    _transcript({"type": "queue-operation", "operation": "enqueue", "content": _header(rid)})
    real = dmn_delivery._live_session
    dmn_delivery._live_session = lambda con, agent: SESSION       # owe_again cleared the old one: only the live session is looked at
    try:
        _tell_undelivered()
    finally:
        dmn_delivery._live_session = real
    row = _row(rid)
    check("transcript first: the line is there, so it is landed and nothing is typed",
          row["landed_at"] and len(sent) == 1, f"{len(sent)} {dict(row)}")


def test_a_head_the_transcript_already_holds_is_landed_not_typed(home: Path) -> None:
    sent = _world(home)
    orcamod.typeable_now = lambda h: True
    hand_edit_team(DESIGN_TEAM.replace(
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async }',
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async, kind: task }'))
    _ask_row(
        {"verb": "ask", "requester": "product", "speaker": "product", "target": "backend",
         "body": "look at the retry loop"}, lambda _m: None)
    con = db.connect()
    real = dmn_delivery._live_session
    dmn_delivery._live_session = lambda con, agent: SESSION
    try:
        rid = int(con.execute("SELECT id FROM requests WHERE target = 'backend'").fetchone()["id"])
        typed_before = len(sent)
        db.owe_again(con, rid)                          # the daemon's record of the send is gone
        db.forget_told(con, "backend")
        _transcript({"type": "queue-operation", "operation": "enqueue", "content": _header(rid)})
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        row = db.get_request(con, rid)
        check("head: its line is already in the live session's transcript, so it is landed and not typed",
              row["landed_at"] and row["typed_at"] and len(sent) == typed_before, f"{sent} {dict(row)}")
        check("head: and the notify cursor moved on, so it is not told again",
              db.get_cursor(con, "notify:backend") == rid)
    finally:
        dmn_delivery._live_session = real
        con.close()


def test_a_line_typed_into_a_session_that_ended_is_owed_again_at_once(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent)
    rid = int(row["id"])
    con = db.connect()
    try:
        db.set_closed(con, "backend", SESSION)
    finally:
        con.close()
    _sweep()
    row = _row(rid)
    check("ended: nothing waits out the timer - it is owed again, and no try is used up",
          not row["delivered_at"] and not row["typed_at"], str(dict(row)))
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        check("ended: and a wake's first prompt now finds it undelivered",
              rid in [int(r["id"]) for r in db.undelivered_for(con, "backend")])
    finally:
        con.close()


def test_sessions_that_ended_do_not_make_a_later_miss_lost(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent)
    rid = int(row["id"])
    con = db.connect()
    try:
        db.set_closed(con, "backend", SESSION)
        for _ in range(2):                       # typed, and the session ends, twice
            landing.sweep(con, "backend", lambda _m: None)
            con.execute("UPDATE requests SET requester = 'human' WHERE id = ?", (rid,))
            con.execute("UPDATE requests SET delivered_at = 1, typed_at = ?, typed_session = ? WHERE id = ?",
                        (int(time.time()), SESSION, rid))
        landing.sweep(con, "backend", lambda _m: None)
        db.clear_closed(con, "backend")
        con.execute("UPDATE requests SET delivered_at = 1, typed_at = ?, typed_session = ? WHERE id = ?",
                    (int(time.time()) - landing.LANDING_WAIT_S - 5, SESSION, rid))
        _age(rid, landing.LANDING_WAIT_S + 5)
    finally:
        con.close()
    _transcript({"type": "user", "message": {"role": "user", "content": "something else"}})
    _sweep()
    row = _row(rid)
    check("ended twice, then one miss in a live session: retyped",
          not row["typed_at"] and landing.state(row) == "queued", str(dict(row)))


def test_a_landing_further_back_than_the_tail_is_found_before_the_line_is_typed_again(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent, ago=landing.LANDING_WAIT_S + 5)
    rid = int(row["id"])
    filler = {"type": "user", "message": {"role": "user", "content": "x" * 200}}
    _transcript({"type": "queue-operation", "operation": "enqueue", "content": _header(rid)},
                *[filler] * 200)
    real = landing._TAIL_BYTES
    landing._TAIL_BYTES = 1000
    try:
        check("tail: not in the tail alone", not landing.shown_in(vendors.config_dir("alpha") / "projects" / "-wt"
                                                                   / f"{SESSION}.jsonl", rid))
        _sweep()
    finally:
        landing._TAIL_BYTES = real
    row = _row(rid)
    check("tail: the whole transcript is read before a line is called never shown",
          row["landed_at"] and len(sent) == 1, str(dict(row)))


def test_a_task_head_is_told_again_when_it_never_landed(home: Path) -> None:
    sent = _world(home)
    orcamod.typeable_now = lambda h: True
    hand_edit_team(DESIGN_TEAM.replace(
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async }',
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async, kind: task }'))
    _ask_row(
        {"verb": "ask", "requester": "product", "speaker": "product", "target": "backend",
         "body": "a piece of work"}, lambda _m: None)
    con = db.connect()
    try:
        rid = int(con.execute("SELECT id FROM requests WHERE target = 'backend'").fetchone()["id"])
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        row = db.get_request(con, rid)
        told = db.get_cursor(con, "notify:backend") == rid
        con.execute("UPDATE requests SET typed_session = ?, typed_at = typed_at - ? WHERE id = ?",
                    (SESSION, landing.LANDING_WAIT_S + 5, rid))
        _age(rid, landing.LANDING_WAIT_S + 5)
        landing.sweep(con, "backend", lambda _m: None)
        check("task: it was told once, and the sweep voids the cursor so it is told again",
              row["typed_at"] and told and db.get_cursor(con, "notify:backend") != rid, str(dict(row)))
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("task: the tick types it a second time", len(sent) == 2, str(sent))
    finally:
        con.close()


def test_a_say_to_the_founder_is_landed_and_unread_and_never_typed(home: Path) -> None:
    sent = _world(home)
    ok, msg, rid = askmod.say(askmod.HUMAN, "status: the retry loop is fixed", requester="backend")
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        check("founder: a say to him is landed at once, and unread", ok and row["state"] == "done"
              and row["landed_at"] and row["read_at"] is None, f"{msg} {dict(row)}")
        check("founder: and it is in For you", rid in [m["id"] for m in actions.mailbox_rows(con)])
        dmn_delivery.deliver_queued(con, askmod.HUMAN, lambda _m: None)
        dmn_messaging.tell_undelivered(con, askmod.HUMAN, lambda _m: None)
        landing.sweep(con, askmod.HUMAN, lambda _m: None)
        check("founder: the daemon never types it", not sent and not db.get_request(con, rid)["typed_at"], str(sent))
        actions.opened_by_human(con, rid)
        first = db.get_request(con, rid)["read_at"]
        check("founder: opening it sets read_at, and it leaves For you", first is not None
              and rid not in [m["id"] for m in actions.mailbox_rows(con)])
        con.execute("UPDATE requests SET read_at = read_at - 50 WHERE id = ?", (rid,))
        actions.opened_by_human(con, rid)
        check("founder: reading it again does not move read_at", db.get_request(con, rid)["read_at"] == first - 50)
        ok, _, rid2 = askmod.say(askmod.HUMAN, "second", requester="backend")
        ok, _, rid3 = askmod.say(askmod.HUMAN, "third", requester="backend")
        actions.for_you_seen(con, "backend", rid3)
        check("founder: a chat that has drawn them reads them - one rule, the same read_at",
              all(db.get_request(con, r)["read_at"] is not None for r in (rid2, rid3)))
    finally:
        con.close()


def test_a_say_queued_for_the_founder_before_the_upgrade_becomes_landed_and_unread(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        old_q = db.add_request(con, "backend", "human", "queued before", None, None, None, None, 0, None)
        con.execute("UPDATE requests SET kind = 'say' WHERE id = ?", (old_q,))      # as the old writers left it: queued
        con.execute("UPDATE requests SET state = 'queued', landed_at = NULL, resolved_at = NULL, announced_at = NULL "
                    "WHERE id = ?", (old_q,))
        old_done = db.add_request(con, "backend", "human", "dealt with", None, None, None, "say", 0, None)
        con.execute("UPDATE requests SET read_at = NULL, resolved_at = 77 WHERE id = ?", (old_done,))
        card = db.add_request(con, "backend", "human", "decide this", None, None, None, "task", 0, None)
        con.execute("DELETE FROM cursors WHERE name = 'migrated:founder_says_landed'")
    finally:
        con.close()
    con = db.connect()
    try:
        r = db.get_request(con, old_q)
        check("migration: a say still queued for the founder is landed and unread",
              r["state"] == "done" and r["landed_at"] and r["read_at"] is None, str(dict(r)))
        check("migration: what he had already dealt with reads as read, when it was closed",
              db.get_request(con, old_done)["read_at"] == 77)
        c = db.get_request(con, card)
        check("migration: a task he holds stays a task, queued", c["state"] == "queued" and c["read_at"] is None)
    finally:
        con.close()


def test_a_vendor_without_transcripts_takes_a_typed_line_as_landed(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent)
    rid = int(row["id"])
    real = landing._keeps_transcripts
    landing._keeps_transcripts = lambda acct: False
    try:
        _sweep()
    finally:
        landing._keeps_transcripts = real
    check("no transcript to read: typed counts as delivered", _row(rid)["landed_at"])


def test_the_conversation_says_where_a_request_is(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent)
    con = db.connect()
    try:
        con.execute("UPDATE requests SET requester = 'human' WHERE id = ?", (row["id"],))
        def tail():
            rows = conversation.conversation_and_drawn(con, "backend")[0]
            return " | ".join(getattr(r, "tail", "") for r in rows)
        check("conversation: typed, not delivered", "typed, not in its session yet" in tail(), tail()[:300])
        con.execute("UPDATE requests SET landed_at = ? WHERE id = ?", (int(time.time()), row["id"]))
        check("conversation: shown, delivered", "typed, not in its session yet" not in tail()
              and "delivered " in tail(), tail()[:300])
    finally:
        con.close()


def test_an_old_delivered_request_is_not_called_undelivered_after_the_upgrade(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        con.execute("INSERT INTO requests(ts, requester, target, state, body, kind, delivered_at) "
                    "VALUES(1, 'product', 'backend', 'queued', 'old', 'ask', 100)")
        for col in ("typed_at", "typed_session", "landed_at", "landing_tries"):
            con.execute(f"ALTER TABLE requests DROP COLUMN {col}")     # the table as it was before
    finally:
        con.close()
    con = db.connect()          # connecting migrates
    try:
        row = con.execute("SELECT * FROM requests WHERE body = 'old'").fetchone()
        check("migration: a request told before landings were checked is delivered as it was",
              row["landed_at"] == 100 and landing.state(row) == "delivered", str(dict(row)))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_only_a_line_a_person_or_the_daemon_typed_counts_as_shown(Path(td) / "l1"))
        run(lambda: test_a_typed_request_is_delivered_only_once_its_transcript_shows_it(Path(td) / "l2"))
        run(lambda: test_a_line_the_transcript_never_shows_is_typed_again_without_end(Path(td) / "l3"))
        run(lambda: test_a_daemon_restart_types_a_line_at_most_once_more(Path(td) / "l12"))
        run(lambda: test_a_line_already_in_the_transcript_is_landed_without_typing(Path(td) / "l13"))
        run(lambda: test_a_head_the_transcript_already_holds_is_landed_not_typed(Path(td) / "l14"))
        run(lambda: test_a_line_typed_into_a_session_that_ended_is_owed_again_at_once(Path(td) / "l4"))
        run(lambda: test_a_task_head_is_told_again_when_it_never_landed(Path(td) / "l5"))
        run(lambda: test_sessions_that_ended_do_not_make_a_later_miss_lost(Path(td) / "l9"))
        run(lambda: test_a_landing_further_back_than_the_tail_is_found_before_the_line_is_typed_again(Path(td) / "l10"))
        run(lambda: test_a_say_to_the_founder_is_landed_and_unread_and_never_typed(Path(td) / "l15"))
        run(lambda: test_a_say_queued_for_the_founder_before_the_upgrade_becomes_landed_and_unread(Path(td) / "l16"))
        run(lambda: test_a_vendor_without_transcripts_takes_a_typed_line_as_landed(Path(td) / "l6"))
        run(lambda: test_the_conversation_says_where_a_request_is(Path(td) / "l7"))
        run(lambda: test_an_old_delivered_request_is_not_called_undelivered_after_the_upgrade(Path(td) / "l8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
