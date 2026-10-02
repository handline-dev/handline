"""T399: the automatic texts typed at an agent about a task handoff say what the agent must do,
show the note whatever the title's length, and use task verbs - never `ocp resolve`.

Run on its own: python3 tests/daemon/test_message_texts.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

TEAM = """
agents:
  manager: {}
  worker: {}
  x.helper: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
"""
NOTE = "Fix the two things in the report: the cap over the note, and the resolve verb. Then hand it back."


def _world(home: Path, title_len: int = 640):
    """A task manager opened, worker owns, handed to the auditor with NOTE; returns (con, tid, request)."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    for a in ("manager", "worker", "x.helper"):
        agents.register(a)
    con = db.connect()
    title = ("Task timeout: every task gets a default " * 20)[:title_len]
    ok, msg, tid = actions.task_new("manager", "worker", title, title)
    check("setup: the task opens", ok, msg)
    ok, msg = actions.task_handoff(con, "worker", tid, "x.helper", NOTE)
    check("setup: the owner hands it on", ok, msg)
    return con, tid, db.head_of_queue(con, "x.helper"), title


def test_a_handoff_notice_shows_the_note_and_cuts_only_the_title(home: Path) -> None:
    con, tid, head, title = _world(home)
    try:
        text = dmn_delivery.tell_text(con, head, 1)
        check("notice: a 640-char title still shows the whole note first", NOTE in text, text)
        check("notice: the title is cut to a short tail", title[:60].rstrip() in text and title[:120] not in text, text)
        check("notice: it names the task", f"T{tid} handed to you" in text, text)
        check("notice: it stays inside the three typed lines", text.count("\n") <= 2, text)
        long_note = "x" * 500
        db.edit_task(con, tid, "worker", owner="x.helper")
        body = f"T{tid} handed to you by worker: short title\n\n{long_note}"
        con.execute("UPDATE requests SET body = ? WHERE id = ?", (body, head["id"]))
        cut = dmn_delivery.tell_text(con, db.get_request(con, head["id"]), 1)
        check("notice: a note past 400 characters is cut, with the title still shown after it",
              "x" * 400 + "…" in cut and "Title: short title" in cut, cut)
    finally:
        con.close()


def test_a_task_is_told_to_hand_on_or_finish_never_to_resolve(home: Path) -> None:
    con, tid, head, _ = _world(home)
    try:
        text = dmn_delivery.tell_text(con, head, 1)
        check("verb: a task handoff is not told to resolve", "ocp resolve" not in text, text)
        check("verb: it says the task is now the receiver's",
              f"you own T{tid} now" in text and f"`ocp task done T{tid}`" in text, text)
        waiting = dmn_delivery.tell_text(con, head, 3)
        check("queue: a task's queue moves on when it is handed on or finished",
              "[2 more waiting - the next comes when you hand this on or finish it.]" in waiting, waiting)
        ok, _, sid = askmod.say("worker", "is the deploy done?", requester="manager")
        say_text = dmn_delivery.tell_text(con, db.get_request(con, sid), 3)
        check("queue: a message carries no 'more waiting' line at all", "more waiting" not in say_text, say_text)
        ok, _, rid = askmod.ask("worker", "please look", requester="manager")
        ask_text = dmn_delivery.tell_text(con, db.get_request(con, rid), 3)
        check("queue: an ask says its queue moves on when it is answered, naming neither resolve nor dismiss",
              "when you answer this one" in ask_text and "resolve" not in ask_text
              and "dismiss" not in ask_text, ask_text)
        rc, out = support._cli(["inbox"], as_agent="x.helper")
        check("inbox: the same task verb and queue wording, no resolve",
              f"you own T{tid} now" in out and "ocp resolve" not in out, out)
    finally:
        con.close()


def test_the_answer_line_of_a_handoff_says_whose_handoff_it_was(home: Path) -> None:
    con, tid, head, _ = _world(home)
    try:
        askmod.resolve(int(head["id"]), "PASS accepted; merged", notify=False)
        line, _ = dmn_delivery.answer_line(db.get_request(con, head["id"]))
        check("answer line: a task row reads as your handoff, not as something you were handed",
              f"(your handoff of T{tid} to x.helper)" in line and "you asked" not in line, line)
    finally:
        con.close()


def test_a_notice_rides_on_the_next_delivery_and_never_wakes_or_types_alone(home: Path) -> None:
    """T935: a task-board notice ("T<n> done by X") is born landed - owed nothing, never a head, never
    a reason to wake - and the next typed delivery to its target carries it."""
    from ocp.daemon import lifecycle
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    for a in ("manager", "worker", "x.helper"):
        agents.register(a)
    con = db.connect()
    try:
        rids = [db.add_request(con, db.NOTICE_REQUESTER, "worker", f"T{n} done by x.helper: PASS {n}", None, None,
                               "fire_and_forget", "say", 0, None, notice=True) for n in range(1, 6)]
        row = db.get_request(con, rids[0])
        check("notice: born landed and undelivered", row["state"] == "landed" and row["delivered_at"] is None
              and row["landed_at"] is not None, str(dict(row)))
        check("notice: owed nothing - not queued, not pending, not undelivered, so nothing wakes for it",
              not db.queued_for(con, "worker") and not db.send_now_pending(con, "worker")
              and not db.undelivered_for(con, "worker") and not lifecycle.waiting_for(con, "worker"))
        ok, _, rid = askmod.ask("worker", "please look", requester="manager")
        head = db.get_request(con, rid)
        folded = dmn_delivery.folded_notices(con, "worker")
        text = dmn_delivery.tell_text(con, head, 1, folded=folded)
        check("notice: a real delivery carries the first three, oldest first, on its own line",
              "Also: T1 done by x.helper: PASS 1 | T2 done by x.helper: PASS 2 | T3 done by x.helper: PASS 3" in text
              and "T4 done" not in text and text.count("\n") <= 2, text)
        check("notice: and says how many more wait, and where", "(+2 more: `ocp inbox --all`)" in text, text)
        check("notice: a delivery with nothing waiting says nothing of notices",
              "Also:" not in dmn_delivery.tell_text(con, head, 1), "")
        db.mark_notices_shown(con, [int(r["id"]) for r in folded], "sess")
        left = db.pending_notices(con, "worker")
        check("notice: those shown are marked, the rest wait for the next delivery",
              [r["id"] for r in left] == rids[3:] and db.get_request(con, rids[0])["typed_at"] is not None,
              str([r["id"] for r in left]))
        text = dmn_delivery.tell_text(con, head, 1, folded=dmn_delivery.folded_notices(con, "worker"))
        check("notice: the next delivery carries them, with no 'more' tail", "T4 done" in text and "T5 done" in text
              and "more:" not in text, text)
        # the typed path: what went out carries the notices and only then are they marked shown
        sent = []
        real_send = dmn_delivery._send
        dmn_delivery._send = lambda handle, text: (sent.append(text) or (True, ""))
        try:
            ok, _, rid2 = askmod.ask("worker", "and this", requester="manager")
            told = dmn_delivery._tell(con, "worker", db.get_request(con, rid2), 1, "h", lambda _m: None)
        finally:
            dmn_delivery._send = real_send
        check("notice: a delivery typed by the daemon carries them, and marks them shown once sent",
              told and "Also: T4 done" in sent[0] and not db.pending_notices(con, "worker"), str(sent))
        db.add_request(con, db.NOTICE_REQUESTER, "worker", "T9 done by x.helper: late", None, None,
                       "fire_and_forget", "say", 0, None, notice=True)
        dmn_delivery._send = lambda handle, text: (False, "no")
        try:
            ok, _, rid3 = askmod.ask("worker", "a third", requester="manager")
            dmn_delivery._tell(con, "worker", db.get_request(con, rid3), 1, "h", lambda _m: None)
        finally:
            dmn_delivery._send = real_send
        check("notice: a send that failed leaves them unshown", len(db.pending_notices(con, "worker")) == 1)
        # the waiting_prompt of a parked agent is such a delivery too
        prompt = dmn_delivery.waiting_prompt(con, "worker", lambda _m: None)
        check("notice: a resume's first prompt carries them as well", prompt is not None
              and "Also: T9 done" in prompt[0], str(prompt))
        prompt[1]("sess2")
        check("notice: and marks them shown once the session is up", not db.pending_notices(con, "worker"))
    finally:
        con.close()


def test_task_board_says_from_before_the_notice_column_are_never_carried(home: Path) -> None:
    """T949: the upgrade adds `notice` and backfills nothing, so a weeks-old "done by" say that was
    landed and undelivered when T935 shipped does not drip into every later delivery."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    for a in ("manager", "worker", "x.helper"):
        agents.register(a)
    con = db.connect()
    try:
        old = db.add_request(con, db.NOTICE_REQUESTER, "worker", "T7 done by x.helper: weeks ago", None, None,
                             "fire_and_forget", "say", 0, None, state="done")
        new = db.add_request(con, db.NOTICE_REQUESTER, "worker", "T8 done by x.helper: just now", None, None,
                             "fire_and_forget", "say", 0, None, notice=True)
        con.execute("UPDATE requests SET notice = NULL WHERE id = ?", (old,))
        check("setup: the old say is landed and undelivered, like a notice", db.get_request(con, old)["state"] == "landed"
              and db.get_request(con, old)["delivered_at"] is None)
        check("old rows: only the row born as a notice is carried", [r["id"] for r in db.pending_notices(con, "worker")] == [new])
        # an old database: the column is gone, with such a row in it
        con.commit()
        con.execute("ALTER TABLE requests DROP COLUMN notice")
        con.commit()
    finally:
        con.close()
    con = db.connect()
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(requests)")]
        check("upgrade: connecting to an old database adds the column", "notice" in cols, str(cols))
        check("upgrade: and backfills nothing - the row that was there before is not carried",
              db.pending_notices(con, "worker") == [])
        later = db.add_request(con, db.NOTICE_REQUESTER, "worker", "T9 done by x.helper: after", None, None,
                               "fire_and_forget", "say", 0, None, notice=True)
        check("upgrade: a notice written after it is carried, and a second connect adds nothing twice",
              [r["id"] for r in db.pending_notices(con, "worker")] == [later])
    finally:
        con.close()
    con = db.connect()
    try:
        check("upgrade: reconnecting keeps the column and the row", [r["id"] for r in db.pending_notices(con, "worker")] == [later])
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_handoff_notice_shows_the_note_and_cuts_only_the_title(Path(td) / 'm0'))
        run(lambda: test_a_task_is_told_to_hand_on_or_finish_never_to_resolve(Path(td) / 'm1'))
        run(lambda: test_the_answer_line_of_a_handoff_says_whose_handoff_it_was(Path(td) / 'm2'))
        run(lambda: test_a_notice_rides_on_the_next_delivery_and_never_wakes_or_types_alone(Path(td) / 'm3'))
        run(lambda: test_task_board_says_from_before_the_notice_column_are_never_carried(Path(td) / 'm4'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
