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
from handline import actions  # noqa: E402
from handline.actions import conversation  # noqa: E402
from handline.daemon import landing  # noqa: E402

SESSION = "sess-1"


def _world(home: Path) -> list:
    _daemon_home(home, 20)
    sessions_are("backend")
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    termmod.screen_blocker = lambda h: None
    termmod.typeable_now = lambda h: False
    termmod.live_terminals = lambda: {"term_x"}
    landing._typed_at.clear()
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    termmod.send = send
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
    termmod.typeable_now = lambda h: True
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
    termmod.typeable_now = lambda h: True
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
        check("founder: a say to him is landed at once, and unread", ok and row["state"] == "landed"
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
    support.legacy_shape(con)
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
              r["state"] == "landed" and r["landed_at"] and r["read_at"] is None, str(dict(r)))
        check("migration: what he had already dealt with reads as read, when it was closed",
              db.get_request(con, old_done)["read_at"] == 77)
        c = db.get_request(con, card)
        check("migration: a task he holds stays a task, queued", c["state"] == "queued" and c["read_at"] is None)
    finally:
        con.close()


def test_an_fyi_report_before_the_upgrade_becomes_a_say(home: Path) -> None:
    """T835: the fyi flag is gone; a row that carries it is a say, unread while it was queued."""
    _daemon_home(home, 20)
    con = db.connect()
    support.legacy_shape(con)
    try:
        old_q = db.add_request(con, "backend", "human", "report queued", None, None, None, "report", 0, None)
        con.execute('UPDATE requests SET fyi = 1, "state" = \'queued\', landed_at = NULL WHERE id = ?', (old_q,))
        old_done = db.add_request(con, "backend", "human", "report read", None, None, None, None, 0, None)
        con.execute('UPDATE requests SET fyi = 1, "state" = \'landed\', resolved_at = 77, read_at = NULL '
                    'WHERE id = ?', (old_done,))
        plain = db.add_request(con, "backend", "frontend", "a plain ask", None, None, None, "task", 0, None)
        con.execute("DELETE FROM cursors WHERE name = 'migrated:fyi_are_says'")
    finally:
        con.close()
    con = db.connect()
    try:
        r = db.get_request(con, old_q)
        check("migration: a queued fyi is a say, landed and unread",
              r["kind"] == "say" and r["state"] == "landed" and r["landed_at"] and r["read_at"] is None, str(dict(r)))
        d = db.get_request(con, old_done)
        check("migration: a closed fyi is a say that reads as read", d["kind"] == "say" and d["read_at"] == 77)
        check("migration: a row without the flag is untouched", db.get_request(con, plain)["kind"] == "task")
    finally:
        con.close()


def test_an_answer_is_told_on_its_say_and_an_old_untold_one_gets_a_say(home: Path) -> None:
    """T835 step 3: the asker is told of an answer when its say is shown; a refusal is told the
    same way; the upgrade moves the old per-request mark onto the say."""
    _daemon_home(home, 20)
    con = db.connect()
    support.legacy_shape(con)
    try:
        asked = db.add_request(con, "backend", "frontend", "q1", None, None, None, None, 0, None)
        db.close_if_queued(con, asked, "done", "yes")
        refused = db.add_request(con, "backend", "frontend", "q2", None, None, None, None, 0, None)
        db.close_if_queued(con, refused, "rejected", "no, not that")
        check("told: an answer and a refusal both wait, each as a say of its own",
              {r["id"] for r in db.unannounced_answers(con, "backend")} == {asked, refused}
              and db.answer_row_of(con, refused)["body"] == "no, not that")
        db.mark_announced(con, [asked])
        check("told: marking one shows its say and leaves the other",
              db.answer_row_of(con, asked)["delivered_at"] is not None
              and [r["id"] for r in db.unannounced_answers(con, "backend")] == [refused])
        # As the old build left them: told by a mark on the request, or untold with no say at all.
        told_old = db.add_request(con, "backend", "frontend", "old told", None, None, None, None, 0, None)
        db.close_if_queued(con, told_old, "done", "ok")
        con.execute("UPDATE requests SET announced_at = 55 WHERE id = ?", (told_old,))
        con.execute("UPDATE requests SET delivered_at = NULL WHERE settles = ?", (told_old,))
        untold_old = db.add_request(con, "backend", "frontend", "old untold", None, None, None, None, 0, None)
        con.execute('UPDATE requests SET "state" = \'landed\', verdict = \'late\', resolved_at = 60, announced_at = NULL '
                    'WHERE id = ?', (untold_old,))
        con.execute("DELETE FROM cursors WHERE name = 'migrated:answers_told_on_the_say'")
    finally:
        con.close()
    con = db.connect()
    try:
        check("migration: an answer told by the old mark has its say shown",
              db.answer_row_of(con, told_old)["delivered_at"] == 55)
        said = db.answer_row_of(con, untold_old)
        check("migration: an untold one gets a say carrying its verdict, still to be told",
              said is not None and said["body"] == "late" and said["delivered_at"] is None
              and untold_old in [r["id"] for r in db.unannounced_answers(con, "backend")])
    finally:
        con.close()


def test_a_closed_request_has_left_its_queue_and_a_held_one_has_not(home: Path) -> None:
    """T835 step 4b: `queue_order` NULL is what says a request is out of its target's queue - closed,
    or its task hold ended; the upgrade clears it on every row that was resolved."""
    _daemon_home(home, 20)
    con = db.connect()
    support.legacy_shape(con)
    try:
        held = db.add_request(con, "orcapipe", "backend", "task one", None, None, None, "task", 0, None)
        db.mark_landed(con, held)
        gone = db.add_request(con, "orcapipe", "backend", "task two", None, None, None, "task", 0, None)
        db.close_if_queued(con, gone, "done", "ok")
        check("closing a request takes it out of the queue", db.get_request(con, gone)["queue_order"] is None)
        check("a landed one still has its place", db.get_request(con, held)["queue_order"] is not None)
        old = db.add_request(con, "orcapipe", "backend", "task three", None, None, None, "task", 0, None)
        con.execute('UPDATE requests SET "state" = \'landed\', resolved_at = 50 WHERE id = ?', (old,))
        con.execute("DELETE FROM cursors WHERE name = 'migrated:closed_rows_leave_the_queue'")
    finally:
        con.close()
    con = db.connect()
    try:
        check("migration: a resolved row loses its place, an unresolved one keeps it",
              db.get_request(con, old)["queue_order"] is None and db.get_request(con, held)["queue_order"] is not None)
    finally:
        con.close()


def test_a_dismissal_is_an_event_and_an_old_one_gets_one(home: Path) -> None:
    """T835 step 4c: who dismissed a request and why is an event naming it; the row's own columns say
    nothing any reader trusts. The upgrade writes the event for a row that has only the columns."""
    _daemon_home(home, 20)
    con = db.connect()
    support.legacy_shape(con)
    try:
        rid = db.add_request(con, "backend", "frontend", "q", None, None, None, "task", 0, None)
        db.close_if_queued(con, rid, "done", None, dismissed_by="frontend", dismissed_reason="not mine")
        ev = con.execute("SELECT payload FROM events WHERE kind = 'dismissed'").fetchall()
        check("dismiss: one event naming the request, who and why", len(ev) == 1 and
              json.loads(ev[0]["payload"]) == {"request_id": rid, "by": "frontend", "reason": "not mine"}, str(ev))
        con.execute("UPDATE requests SET dismissed_by = NULL, dismissed_reason = NULL WHERE id = ?", (rid,))
        check("dismiss: the row reads as dismissed from the event alone",
              db.dismissal(db.get_request(con, rid)) == (True, "frontend", "not mine")
              and db.shown_verdict(db.get_request(con, rid)) == "dismissed by frontend - not mine")
        old = db.add_request(con, "backend", "frontend", "q2", None, None, None, "task", 0, None)
        con.execute("UPDATE requests SET dismissed_by = 'frontend', dismissed_reason = NULL WHERE id = ?", (old,))
        con.execute("DELETE FROM cursors WHERE name = 'migrated:dismissals_are_events'")
    finally:
        con.close()
    con = db.connect()
    try:
        check("migration: an old dismissal gets its event, and a read dismissal is not doubled",
              db.dismissal(db.get_request(con, old)) == (True, "frontend", None)
              and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'dismissed'").fetchone()[0] == 2)
    finally:
        con.close()


def test_why_a_request_closed_without_an_answer_is_an_event(home: Path) -> None:
    """T835 step 4d: a denial or a withdrawal carries its reason as an event naming the request; the
    row reads it from there, and the upgrade writes one for a row that has only a verdict."""
    _daemon_home(home, 20)
    con = db.connect()
    support.legacy_shape(con)
    try:
        denied = db.add_request(con, "backend", "frontend", "q", None, None, None, "task", 0, None,
                                state="denied", verdict="no such route")
        taken = db.add_request(con, "backend", "frontend", "q2", None, None, None, "task", 0, None)
        db.retire_if_undelivered(con, taken, "withdrawn", "withdrawn: changed my mind")
        con.execute("UPDATE requests SET verdict = NULL WHERE id IN (?, ?)", (denied, taken))
        check("reason: a denial and a withdrawal read their reason from the event, the verdict gone",
              db.shown_verdict(db.get_request(con, denied)) == "no such route"
              and db.shown_verdict(db.get_request(con, taken)) == "withdrawn: changed my mind")
        moved = db.add_request(con, "backend", "frontend", "q4", None, None, None, "task", 0, None)
        db.close_if_queued(con, moved, "withdrawn", "moved to someone", announced=True, settle=False)
        con.execute("UPDATE requests SET verdict = NULL WHERE id = ?", (moved,))
        check("reason: a close without an answer (a task moved on) keeps why, and is not a blank retirement",
              db.shown_verdict(db.get_request(con, moved)) == "moved to someone"
              and con.execute("SELECT COUNT(*) FROM requests WHERE state = 'withdrawn' AND id = ? AND "
                              + db.CLOSING_SQL + " IS NULL", (moved,)).fetchone()[0] == 0)
        old = db.add_request(con, "backend", "frontend", "q3", None, None, None, "task", 0, None)
        con.execute('UPDATE requests SET "state" = \'withdrawn\', verdict = \'withdrawn: long ago\' WHERE id = ?', (old,))
        con.execute("DELETE FROM cursors WHERE name = 'migrated:reasons_are_events'")
    finally:
        con.close()
    con = db.connect()
    try:
        ev = [json.loads(r["payload"])["request_id"] for r in
              con.execute("SELECT payload FROM events WHERE kind = 'request_reason'").fetchall()]
        check("migration: the old verdict is an event now, and the others are not doubled",
              ev.count(old) == 1 and len(ev) == len(set(ev)), str(ev))
    finally:
        con.close()


def test_the_legacy_request_columns_are_gone_from_a_new_database_and_dropped_by_the_doctor(home: Path) -> None:
    """T835 step 7d: a database made now has none of the legacy request columns and never grows them;
    on an older one `hl doctor --drop-dead-columns` first moves what a process on older code closed since
    the upgrade (an answer, a reason, a dismissal, a hold) to where the readers look, then drops them
    and marks the database, so a connect neither re-adds them nor runs the migrations that read them."""
    from handline.db import schema as dbschema
    _daemon_home(home, 20)
    legacy = set(dbschema._LEGACY_REQUEST_COLUMNS)

    def cols() -> set:
        c = db.connect()
        try:
            return {r[1] for r in c.execute("PRAGMA table_info(requests)")}
        finally:
            c.close()
    check("new database: no legacy column, and it is marked so", not legacy & cols())
    con = db.connect()
    try:
        support.legacy_shape(con)
        answered = db.add_request(con, "backend", "frontend", "q1", None, None, None, "task", 0, None)
        con.execute("UPDATE requests SET \"state\" = 'landed', verdict = 'done by old code', resolved_at = 900 WHERE id = ?",
                    (answered,))
        taken = db.add_request(con, "backend", "frontend", "q2", None, None, None, "task", 0, None)
        con.execute("UPDATE requests SET \"state\" = 'withdrawn', verdict = 'withdrawn: old code', resolved_at = 901 "
                    "WHERE id = ?", (taken,))
        dismissed = db.add_request(con, "backend", "frontend", "q3", None, None, None, "task", 0, None)
        con.execute("UPDATE requests SET \"state\" = 'landed', dismissed_by = 'frontend', dismissed_reason = 'no', "
                    "resolved_at = 902 WHERE id = ?", (dismissed,))
        for name in ("answers_told_on_the_say", "closed_rows_leave_the_queue", "dismissals_are_events",
                     "reasons_are_events"):
            con.execute("DELETE FROM cursors WHERE name = ?", (f"migrated:{name}",))
    finally:
        con.close()
    con = db.connect()
    try:
        con.execute("DELETE FROM events WHERE kind IN ('dismissed', 'request_reason')")
        con.execute("DELETE FROM requests WHERE settles IS NOT NULL")
        gone = dbschema.drop_unread_request_columns(con)
    finally:
        con.close()
    check("doctor: the legacy columns are dropped", legacy <= set(gone) and not legacy & cols(), str(gone))
    con = db.connect()
    try:
        check("doctor: first, what old code closed is where the readers look",
              db.shown_verdict(db.get_request(con, answered)) == "done by old code"
              and db.shown_verdict(db.get_request(con, taken)) == "withdrawn: old code"
              and db.dismissal(db.get_request(con, dismissed)) == (True, "frontend", "no"))
        check("doctor: and a connect after it grows no column back", not legacy & cols())
    finally:
        con.close()


def test_a_database_dropped_of_the_legacy_columns_without_the_mark_still_connects(home: Path) -> None:
    """A database whose legacy request columns are gone but which was never marked (dropped by a
    doctor from before the mark) runs none of the migrations that read them, and is marked at connect."""
    from handline.db import schema as dbschema
    _daemon_home(home, 20)
    con = db.connect()
    try:
        con.execute("DELETE FROM cursors WHERE name IN (?, 'migrated:answer_rows')", (dbschema.LEGACY_DROPPED,))
    finally:
        con.close()
    con = db.connect()
    try:
        check("unmarked and column-less: connect does not crash, and marks it",
              dbschema._legacy_columns_dropped(con))
    finally:
        con.close()


def test_the_requests_table_has_one_state_and_one_time_after_the_upgrade(home: Path) -> None:
    """T797: done becomes landed, rejected becomes landed when it was delivered and withdrawn when not,
    every row gets a state_at; ledgered, a second connect changes nothing."""
    _daemon_home(home, 20)
    con = db.connect()
    support.legacy_shape(con)
    try:
        def row(state, **cols):
            rid = db.add_request(con, "product", "backend", f"old {state}", None, None, None, "say", 0, None)
            sets = "".join(f"{k} = ?, " for k in cols)
            con.execute(f"UPDATE requests SET {sets}state = ?, state_at = NULL WHERE id = ?",
                        (*cols.values(), state, rid))
            return rid
        done_landed = row("done", landed_at=500, typed_at=400, delivered_at=300, resolved_at=900)
        done_typed = row("done", typed_at=400, delivered_at=300, resolved_at=900)
        done_only = row("done", resolved_at=900)
        rej_delivered = row("rejected", delivered_at=300, resolved_at=900)
        rej_never = row("rejected", resolved_at=900)
        gone = [row(s, resolved_at=800) for s in ("withdrawn", "replaced", "denied")]
        waiting = row("queued")
        con.execute("DELETE FROM cursors WHERE name = 'migrated:requests_one_state'")
    finally:
        con.close()
    con = db.connect()
    try:
        got = {r["id"]: (r["state"], r["state_at"]) for r in con.execute("SELECT id, state, state_at FROM requests")}
        check("one state: done becomes landed, stamped with when it landed (else typed, delivered, resolved)",
              got[done_landed] == ("landed", 500) and got[done_typed] == ("landed", 400)
              and got[done_only] == ("landed", 900), str(got))
        check("one state: rejected is landed if it had been delivered, withdrawn if not",
              got[rej_delivered] == ("landed", 300) and got[rej_never] == ("withdrawn", 900), str(got))
        check("one state: withdrawn, replaced and denied stay and take the time they ended; queued takes its ts",
              [got[g] for g in gone] == [("withdrawn", 800), ("replaced", 800), ("denied", 800)]
              and got[waiting][0] == "queued" and got[waiting][1] == db.get_request(con, waiting)["ts"], str(got))
        check("one state: a row that ended before sending is marked told (never an answer); a refusal keeps what it had",
              all(con.execute("SELECT announced_at FROM requests WHERE id = ?", (g,)).fetchone()[0] is not None for g in gone)
              and con.execute("SELECT announced_at FROM requests WHERE id = ?", (rej_never,)).fetchone()[0] is None)
        check("one state: nothing is left in the old words",
              con.execute("SELECT COUNT(*) FROM requests WHERE state IN ('done', 'rejected')").fetchone()[0] == 0)
    finally:
        con.close()
    con = db.connect()
    try:
        again = {r["id"]: (r["state"], r["state_at"]) for r in con.execute("SELECT id, state, state_at FROM requests")}
        check("one state: a second connect changes nothing", again == got)
        # A daemon still on the old code writes the old words after the migration has run.
        stray = row("done", delivered_at=300, resolved_at=900)
    finally:
        con.close()
    con = db.connect()
    try:
        r = db.get_request(con, stray)
        check("one state: a row written in an old state after the migration is mapped at the next connect",
              (r["state"], r["state_at"]) == ("landed", 300), str(dict(r)))
    finally:
        con.close()


def test_the_unread_request_columns_are_dropped_only_by_the_doctor_and_racing_drops_are_safe(home: Path) -> None:
    """T797 branch 7 / T830: a connect never drops a column (an older process still names them, and
    re-adds them when it connects); `hl doctor --drop-dead-columns` does, and two of them at once,
    or one that read the columns a moment too early, do not crash."""
    import threading
    from handline.db import schema as dbschema
    _daemon_home(home, 20)
    dead = ("timebox_s", "timebox_noticed_at", "landing_tries", "told_out_of_turn")

    def cols() -> set:
        c = db.connect()
        try:
            return {r[1] for r in c.execute("PRAGMA table_info(requests)")}
        finally:
            c.close()
    con = db.connect()
    try:
        for col in dead:
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} INTEGER")
        kept = db.add_request(con, "product", "backend", "kept", None, None, None, "say", 0, None)
    finally:
        con.close()
    check("dead columns: connecting does not drop them - an older process may still use them",
          set(dead) <= cols() and set(dead) <= cols())
    errors: list = []

    def drop():
        c = db.connect()
        try:
            dbschema.drop_unread_request_columns(c)
        except Exception as e:      # noqa: BLE001 - the point of the test
            errors.append(repr(e))
        finally:
            c.close()
    threads = [threading.Thread(target=drop) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("dead columns: four drops racing all finish without an error", not errors, str(errors))
    check("dead columns: and the columns are gone, the rows untouched",
          not set(dead) & cols() and db.get_request(db.connect(), kept)["body"] == "kept")
    con = db.connect()
    real = dbschema._cols
    try:
        for col in dead:
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} INTEGER")
        con.execute(f"ALTER TABLE requests DROP COLUMN timebox_noticed_at")       # gone since it was read
        dbschema._cols = lambda c, t: set(dead) | {"id"}                            # a stale reading
        try:
            got = dbschema.drop_unread_request_columns(con)
        finally:
            dbschema._cols = real
        check("dead columns: a column that vanished after it was read is not an error",
              "timebox_noticed_at" not in got and not set(dead) & cols(), str(got))
    finally:
        con.close()


def test_the_dead_columns_are_not_dropped_under_a_process_on_older_code(home: Path) -> None:
    import argparse, io, contextlib
    from handline.cli import health
    from handline.db import schema as dbschema
    from handline.daemon import devmode, locks as dmn_locks
    from handline.ui import stall
    _daemon_home(home, 20)
    code = max(f.stat().st_mtime for f in devmode._watched_files(paths.repo_root()) if f.exists())
    real_pid, real_daemon = stall.running_pid, dmn_locks.running_elsewhere
    stall.running_pid, dmn_locks.running_elsewhere = lambda: 4242, lambda: None
    try:
        check("stale: a screen that started before the newest code is named",
              health.stale_long_lived(lambda pid, rec: code - 60) == ["screen (pid 4242)"])
        check("stale: one that started after it is not", health.stale_long_lived(lambda pid, rec: code + 60) == [])
        check("stale: a live process whose start cannot be read is refused, not passed",
              health.stale_long_lived(lambda pid, rec: None) == ["screen (pid 4242; cannot tell when it started)"])
        dmn_locks.running_elsewhere = lambda: 77
        check("stale: the daemon is named too",
              health.stale_long_lived(lambda pid, rec: code - 60) == ["screen (pid 4242)", "daemon (pid 77)"])
        import os, subprocess
        rec = home / "rec.pid"
        rec.write_text("1\n")
        os.utime(rec, (1000.0, 1000.0))
        real_run = subprocess.run
        asked = []
        def table(*a, **k):
            asked.append(a)
            return subprocess.CompletedProcess(a, 0, stdout="Mon Jan  1 00:00:00 2001\n")
        subprocess.run = table
        try:
            check("stale: a pid file newer than the fork is the start - an exec keeps the pid, and ps its old time",
                  health._started_at(1, rec) == 1000.0 and not asked)
            check("stale: with no pid file the process table is asked",
                  health._started_at(1, home / "none") == time.mktime(time.strptime("2001-01-01", "%Y-%m-%d")) and asked)
            def no_ps(*a, **k):
                raise PermissionError("operation not permitted")
            subprocess.run = no_ps
            check("stale: no pid file and ps denied is unknown", health._started_at(1, home / "none") is None)
        finally:
            subprocess.run = real_run
        con = db.connect()
        dead = [c for c in dbschema._LEGACY_REQUEST_COLUMNS]
        try:
            for col in dead:
                if col not in dbschema._cols(con, "requests"):
                    con.execute(f"ALTER TABLE requests ADD COLUMN {col} INTEGER")
        finally:
            con.close()
        real_started = health._started_at
        args = argparse.Namespace(settle_leftovers=False, drop_dead_columns=True, dry_run=False, no_color=True)
        err = io.StringIO()
        health._started_at = lambda pid, rec: code - 60
        try:
            with contextlib.redirect_stderr(err):
                rc = health.cmd_doctor(args)
        finally:
            health._started_at = real_started
        con = db.connect()
        try:
            left = dbschema._cols(con, "requests")
        finally:
            con.close()
        check("stale: the drop refuses naming the process and drops nothing",
              rc == 1 and "screen (pid 4242)" in err.getvalue() and set(dead) <= left, err.getvalue())
    finally:
        stall.running_pid, dmn_locks.running_elsewhere = real_pid, real_daemon


def test_a_trust_proposal_for_a_root_already_declared_is_moot(home: Path) -> None:
    _daemon_home(home, 20)
    from handline.team import trust as trustmod
    ok, msg = trustmod.propose(str(home / "repos" / "moot"), "needs it", requester="backend")
    pid = trustmod.listing()[0]["id"]
    ok, msg = trustmod.enact(str(home / "repos"), "the founder declares the parent")
    check("moot: the founder declares a root above it", ok, msg)
    settled = trustmod.settle_moot()
    check("moot: settling moots withdraws it, never approves it",
          settled == [pid] and trustmod.load_proposal(pid)["state"] == "withdrawn", str(settled))
    ok, msg = trustmod.propose(str(home / "repos" / "second"), "again", requester="backend")
    check("moot: a new proposal for a declared root is refused outright", not ok, msg)


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
        for col in ("typed_at", "typed_session", "landed_at"):
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
        run(lambda: test_an_fyi_report_before_the_upgrade_becomes_a_say(Path(td) / "l16f"))
        run(lambda: test_an_answer_is_told_on_its_say_and_an_old_untold_one_gets_a_say(Path(td) / "l16t"))
        run(lambda: test_a_closed_request_has_left_its_queue_and_a_held_one_has_not(Path(td) / "l16q"))
        run(lambda: test_a_dismissal_is_an_event_and_an_old_one_gets_one(Path(td) / "l16d"))
        run(lambda: test_why_a_request_closed_without_an_answer_is_an_event(Path(td) / "l16r"))
        run(lambda: test_the_legacy_request_columns_are_gone_from_a_new_database_and_dropped_by_the_doctor(Path(td) / "l16x"))
        run(lambda: test_a_database_dropped_of_the_legacy_columns_without_the_mark_still_connects(Path(td) / "l16y"))
        run(lambda: test_the_requests_table_has_one_state_and_one_time_after_the_upgrade(Path(td) / "l17"))
        run(lambda: test_the_dead_columns_are_not_dropped_under_a_process_on_older_code(Path(td) / "l16s"))
        run(lambda: test_a_trust_proposal_for_a_root_already_declared_is_moot(Path(td) / "l18"))
        run(lambda: test_the_unread_request_columns_are_dropped_only_by_the_doctor_and_racing_drops_are_safe(Path(td) / 'dropcols'))
        run(lambda: test_a_vendor_without_transcripts_takes_a_typed_line_as_landed(Path(td) / "l6"))
        run(lambda: test_the_conversation_says_where_a_request_is(Path(td) / "l7"))
        run(lambda: test_an_old_delivered_request_is_not_called_undelivered_after_the_upgrade(Path(td) / "l8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
