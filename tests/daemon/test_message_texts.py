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
        check("verb: it names the ways to end or pass it on, never a verdict flag",
              f'`ocp task review T{tid} <sha> "<note>"`' in text and f'`ocp task handoff T{tid} <agent> "<note>"`' in text
              and "--pass" not in text, text)
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


TEAM2 = """
agents:
  p.manager: {}
  p.worker: {}
  p.auditor: {}
  p.x.auditor: {}
  p.merger: {}
  q.auditor: {}
  q.merger: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
"""


def test_a_review_and_a_merge_are_told_what_to_close_them_with(home: Path) -> None:
    """The auditor and the merger are told the exact close for the small task they hold;
    the owner is told the next step for the kind of task it holds. Only the sender's own
    project's auditor or merger is ever the receiver."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM2)
    for a in ("p.manager", "p.worker", "p.auditor", "p.x.auditor", "p.merger", "q.auditor", "q.merger"):
        agents.register(a)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("p.manager", "p.worker", "ship it", "ship it")
        check("setup: the task opens", ok, msg)
        text = actions.finish_command(con, db.get_task(con, tid), "p.worker")
        check("owner: told it can ask for a review and keeps the task",
              f'`ocp task review T{tid} <sha> "<note>"`' in text and "you keep" in text, text)
        ok, msg = actions.task_review(con, "p.worker", tid, "abc1234", "branch b: fixed it")
        check("setup: the review opens", ok, msg)
        kid = next(k for k in db.open_child_tasks(con, tid))
        check("the review goes to the sender's own project's auditor, first by name",
              kid["owner"] == "p.auditor" and kid["title"] == f"review T{tid} @abc1234", dict(kid))
        text = actions.finish_command(con, kid, "p.auditor")
        check("auditor: PASS or FAIL with notes, and nothing about resolving",
              '"PASS: <notes>"' in text and '"FAIL: <what fails>"' in text and "resolve" not in text, text)
        rc, out = support._cli(["inbox"], as_agent="p.auditor")
        check("auditor inbox: says what closes the review", f"you own T{kid['id']} now" in out and "PASS" in out, out)
        ok, msg = actions.mark_task_done(con, kid["id"], "p.auditor", reason="PASS: green")
        check("setup: the auditor passes it", ok, msg)
        ok, msg = actions.task_land(con, "p.worker", tid, "abc1234", "merge b")
        check("setup: the merge opens", ok, msg)
        kid = next(k for k in db.open_child_tasks(con, tid))
        text = actions.finish_command(con, kid, "p.merger")
        check("merger: the merged commit id, or not merged with the reason",
              f"`ocp task done T{kid['id']} --sha <merged commit>`" in text and "not merged: <reason>" in text, text)
        ok, msg = actions.task_review(con, "zz.worker", actions.task_new("p.manager", "p.worker", "y", "y")[2],
                                      "abc1234", "x")
        check("no auditor in the sender's project: refused, another project's is not used",
              not ok, msg)
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_handoff_notice_shows_the_note_and_cuts_only_the_title(Path(td) / 'm0'))
        run(lambda: test_a_task_is_told_to_hand_on_or_finish_never_to_resolve(Path(td) / 'm1'))
        run(lambda: test_a_review_and_a_merge_are_told_what_to_close_them_with(Path(td) / 'm3'))
        run(lambda: test_the_answer_line_of_a_handoff_says_whose_handoff_it_was(Path(td) / 'm2'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
