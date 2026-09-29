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
        check("queue: an ask still says it moves on when resolved or dismissed",
              "resolve or dismiss" in ask_text, ask_text)
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


def main() -> int:
    def tests(td):
        run(lambda: test_a_handoff_notice_shows_the_note_and_cuts_only_the_title(Path(td) / 'm0'))
        run(lambda: test_a_task_is_told_to_hand_on_or_finish_never_to_resolve(Path(td) / 'm1'))
        run(lambda: test_the_answer_line_of_a_handoff_says_whose_handoff_it_was(Path(td) / 'm2'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
