"""Tests (T418): a request is delivered once the receiving session's transcript shows
it, not once it was typed - a line the transcript never shows is typed again, then
reported lost, and the words on screen say which of those it is.

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
    reply = dmn_socket_server._handle_request(
        {"verb": "ask", "requester": "product", "speaker": "product", "target": "backend", "body": body},
        lambda _m: None)
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM requests WHERE target = 'backend'").fetchone()
        con.execute("UPDATE requests SET typed_session = ?, typed_at = typed_at - ? WHERE id = ?",
                    (SESSION, ago, row["id"]))
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


def test_a_line_the_transcript_never_shows_is_typed_again_then_lost(home: Path) -> None:
    sent = _world(home)
    _, row = _typed_row(sent, ago=landing.LANDING_WAIT_S + 5)
    rid = int(row["id"])
    _transcript({"type": "user", "message": {"role": "user", "content": "something else"}})
    con = db.connect()
    try:
        con.execute("UPDATE requests SET requester = 'human' WHERE id = ?", (rid,))    # told without a terminal
    finally:
        con.close()
    _sweep()
    row = _row(rid)
    check("retry: after the wait it is owed again, counted", not row["delivered_at"] and not row["typed_at"]
          and row["landing_tries"] == 1 and landing.state(row) == "queued", str(dict(row)))
    con = db.connect()
    try:
        dmn_messaging.tell_undelivered(con, "backend", lambda _m: None)
    finally:
        con.close()
    row = _row(rid)
    check("retry: the tick types it again, once more", len(sent) == 2 and row["typed_at"]
          and "look at the retry loop" in sent[1][1], str(sent))
    con = db.connect()
    try:
        con.execute("UPDATE requests SET typed_session = ?, typed_at = typed_at - ? WHERE id = ?",
                    (SESSION, landing.LANDING_WAIT_S + 5, rid))
    finally:
        con.close()
    _sweep()
    con = db.connect()
    try:
        dmn_messaging.tell_undelivered(con, "backend", lambda _m: None)
        con.execute("UPDATE requests SET typed_session = ?, typed_at = typed_at - ? WHERE id = ?",
                    (SESSION, landing.LANDING_WAIT_S + 5, rid))
    finally:
        con.close()
    check("retry: a third telling went out", len(sent) == 3, str(sent))
    _sweep()
    _sweep()
    row = _row(rid)
    check("lost: typed MAX_TRIES times and never shown, it is lost and not typed a fourth time",
          landing.state(row) == "lost" and len(sent) == 3, f"{landing.state(row)} {len(sent)}")
    con = db.connect()
    try:
        check("lost: said once", con.execute("SELECT COUNT(*) FROM events WHERE kind = 'landing_lost'"
                                             ).fetchone()[0] == 1)
        why = [s for s in actions.stuck(con) if s["what"].startswith(f"#{rid} ")]
        check("lost: it is on the stuck list, and says it was not read",
              why and "never showed it" in why[0]["why"], str(why))
    finally:
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
    check("ended: nothing waits out the timer - it is owed again", not row["delivered_at"]
          and not row["typed_at"] and row["landing_tries"] == 1, str(dict(row)))
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        check("ended: and a wake's first prompt now finds it undelivered",
              rid in [int(r["id"]) for r in db.undelivered_for(con, "backend")])
    finally:
        con.close()


def test_a_task_head_is_told_again_when_it_never_landed(home: Path) -> None:
    sent = _world(home)
    orcamod.typeable_now = lambda h: True
    hand_edit_team(DESIGN_TEAM.replace(
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async }',
        '- { from: "product",   to: "eng.*",        relation: peer,  response: async, kind: task }'))
    dmn_socket_server._handle_request(
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
        landing.sweep(con, "backend", lambda _m: None)
        check("task: it was told once, and the sweep voids the cursor so it is told again",
              row["typed_at"] and told and db.get_cursor(con, "notify:backend") != rid, str(dict(row)))
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("task: the tick types it a second time", len(sent) == 2, str(sent))
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
        run(lambda: test_a_line_the_transcript_never_shows_is_typed_again_then_lost(Path(td) / "l3"))
        run(lambda: test_a_line_typed_into_a_session_that_ended_is_owed_again_at_once(Path(td) / "l4"))
        run(lambda: test_a_task_head_is_told_again_when_it_never_landed(Path(td) / "l5"))
        run(lambda: test_a_vendor_without_transcripts_takes_a_typed_line_as_landed(Path(td) / "l6"))
        run(lambda: test_the_conversation_says_where_a_request_is(Path(td) / "l7"))
        run(lambda: test_an_old_delivered_request_is_not_called_undelivered_after_the_upgrade(Path(td) / "l8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
