"""Tests: the requests table's upgrades (a queued say, an fyi, an answer, a dismissal and a closing reason become
what the chat reads), the legacy columns the doctor drops, and a trust proposal for a root already declared.
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

SESSION = "sess-1"


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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
    try:
        con.execute("DELETE FROM events WHERE kind IN ('dismissed', 'request_reason')")
        con.execute("DELETE FROM requests WHERE settles IS NOT NULL")
        gone = dbschema.drop_unread_request_columns(con)
    finally:
        con.close()
    check("doctor: the legacy columns are dropped", legacy <= set(gone) and not legacy & cols(), str(gone))
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
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
    con = support.upgrade()
    try:
        again = {r["id"]: (r["state"], r["state_at"]) for r in con.execute("SELECT id, state, state_at FROM requests")}
        check("one state: a second connect changes nothing", again == got)
        # A daemon still on the old code writes the old words after the migration has run.
        stray = row("done", delivered_at=300, resolved_at=900)
    finally:
        con.close()
    con = support.upgrade()
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
    con = support.upgrade()
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


def main() -> int:
    def tests(td):
        run(lambda: test_a_say_queued_for_the_founder_before_the_upgrade_becomes_landed_and_unread(Path(td) / "l16"))
        run(lambda: test_an_fyi_report_before_the_upgrade_becomes_a_say(Path(td) / "l16f"))
        run(lambda: test_an_answer_is_told_on_its_say_and_an_old_untold_one_gets_a_say(Path(td) / "l16t"))
        run(lambda: test_a_closed_request_has_left_its_queue_and_a_held_one_has_not(Path(td) / "l16q"))
        run(lambda: test_a_dismissal_is_an_event_and_an_old_one_gets_one(Path(td) / "l16d"))
        run(lambda: test_why_a_request_closed_without_an_answer_is_an_event(Path(td) / "l16r"))
        run(lambda: test_the_legacy_request_columns_are_gone_from_a_new_database_and_dropped_by_the_doctor(Path(td) / "l16x"))
        run(lambda: test_a_database_dropped_of_the_legacy_columns_without_the_mark_still_connects(Path(td) / "l16y"))
        run(lambda: test_the_requests_table_has_one_state_and_one_time_after_the_upgrade(Path(td) / "l17"))
        run(lambda: test_a_trust_proposal_for_a_root_already_declared_is_moot(Path(td) / "l18"))
        run(lambda: test_the_unread_request_columns_are_dropped_only_by_the_doctor_and_racing_drops_are_safe(Path(td) / 'dropcols'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
