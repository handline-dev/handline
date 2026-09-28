"""T395: the daemon's screen watch keeps one row per agent for the prompt its screen shows, and
the bridge answers through that row - never through a screen read of its own.

Run on its own: python3 tests/daemon/test_prompt_blocks.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from ocp import actions  # noqa: E402
from ocp.daemon import prompts  # noqa: E402
from ocp.team import dialog_rules  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "session"))
from test_ask_question import SINGLE_DEFAULT  # noqa: E402


class _Term:
    """A terminal showing `tail` (a screen taller than one read is read from its bottom rows) until a key is sent."""

    def __init__(self, tail, ignores_key=False):
        self.tail, self.ignores_key, self.sent, self.reads = list(tail), ignores_key, [], 0
        orcamod.PRESS_SETTLE_S = 0
        orcamod._screen = self.screen
        orcamod.send = self.send

    def screen(self, handle, limit=40):
        self.reads += 1
        return support._screen_of(self.tail[-limit:])

    def send(self, handle, text, enter=True):
        self.sent.append(text)
        if not self.ignores_key:
            self.tail = list(CLEARED_SCREEN)
        return True, ""


def _pass(con) -> None:
    prompts.watch_once(con, lambda m: None)


def _open(con):
    return db.open_prompt(con, "backend")


def test_one_prompt_is_one_block_whatever_recognizers_see_it(home: Path) -> None:
    """The overlap case: a permission prompt is read by the permission AND the dialog
    recognizer. One pass gives one row, kind permission - and no chat page is open anywhere."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        term = _Term(PERMISSION_PROMPT)
        _pass(con)
        rows = db.open_prompts(con)
        check("overlap: exactly one open block for the one prompt", len(rows) == 1, str([dict(r) for r in rows]))
        row = rows[0]
        check("overlap: it is judged a permission prompt, not a dialog", row["kind"] == "permission", row["kind"])
        check("overlap: it carries the hook's wait id and is answerable", row["wait_id"] == 7 and row["answerable"] == 1,
              str((row["wait_id"], row["answerable"])))
        check("overlap: the idle-cost read count is one for the whole pass", term.reads == 1, str(term.reads))
    finally:
        con.close()


def test_the_three_kinds_and_their_priority(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        _Term(SINGLE_DEFAULT)
        _pass(con)
        check("question screen: a question block", _open(con)["kind"] == "question", str(dict(_open(con))))
        _Term(YES_NO_DIALOG)
        _pass(con)
        rows = db.open_prompts(con)
        check("a different prompt closes the old block and opens one new one",
              len(rows) == 1 and rows[0]["kind"] == "dialog", str([dict(r) for r in rows]))
        check("and the old one is kept, closed",
              con.execute("SELECT COUNT(*) FROM prompt_blocks WHERE gone_at IS NOT NULL").fetchone()[0] == 1)
    finally:
        con.close()


def test_the_row_follows_the_screen_and_fails_closed(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        _Term(PERMISSION_PROMPT)
        _pass(con)
        first = _open(con)
        time.sleep(1.1)
        _pass(con)
        again = _open(con)
        check("the same prompt is the same row, refreshed", again["id"] == first["id"] and again["last_seen"] > first["last_seen"],
              str((first["id"], again["id"])))
        orcamod._screen = lambda h, limit=40: None
        _pass(con)
        check("an unreadable screen changes nothing", _open(con) is not None and _open(con)["last_seen"] == again["last_seen"])
        check("a row the watch just refreshed is shown to the bridge", len(actions.open_blocks(con)) == 1)
        con.execute("UPDATE prompt_blocks SET last_seen = last_seen - 60")
        check("and a row nobody refreshed for STALE_S is not shown", actions.open_blocks(con) == [],
              str(actions.open_blocks(con)))
        _Term(CLEARED_SCREEN)
        _pass(con)
        check("a screen with no prompt closes the row", _open(con) is None)
    finally:
        con.close()


def test_a_permission_prompt_with_no_hook_record_is_left_to_the_terminal(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        term = _Term(PERMISSION_PROMPT)
        _pass(con)
        row = _open(con)
        check("no hook record: the block exists but is not answerable", row and row["answerable"] == 0, str(dict(row or {})))
        ok, said = actions.answer_block(con, row["id"], {"yes": True})
        check("no hook record: an answer is refused and nothing is sent", not ok and not term.sent, str((ok, said, term.sent)))
    finally:
        con.close()


def test_an_answer_needs_the_very_prompt_the_row_names(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        term = _Term(PERMISSION_PROMPT)
        _pass(con)
        row = _open(con)
        # The screen moves on to another prompt before the founder answers: a stale fingerprint.
        term.tail = list(YES_NO_DIALOG)
        ok, said = actions.answer_block(con, row["id"], {"yes": True})
        check("stale fingerprint: refused, nothing sent", not ok and not term.sent, str((ok, said, term.sent)))
        check("stale fingerprint: the refusal is recorded on the row",
              db.get_prompt(con, row["id"])["answer_ok"] == 0 and db.get_prompt(con, row["id"])["answer_said"], said)
        # Same words, but the hook now waits on a later call: not the prompt that was shown.
        term.tail = list(PERMISSION_PROMPT)
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=9)
        ok, said = actions.answer_block(con, row["id"], {"yes": True})
        check("a changed wait id: refused, nothing sent", not ok and not term.sent, str((ok, said, term.sent)))
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        ok, said = actions.answer_block(con, row["id"], {"yes": True})
        check("the same prompt: the plain Yes is pressed", ok and term.sent == ["1"], str((ok, said, term.sent)))
        check("the result is recorded", db.get_prompt(con, row["id"])["answer_ok"] == 1)
    finally:
        con.close()


def test_a_question_and_a_dialog_are_answered_from_their_blocks(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        term = _Term(SINGLE_DEFAULT)
        _pass(con)
        ok, said = actions.answer_block(con, _open(con)["id"], {"choices": [1]})
        check("question: the pick is sent through the block", ok and term.sent, str((ok, said, term.sent)))
        term = _Term(YES_NO_DIALOG)
        _pass(con)
        ok, said = actions.answer_block(con, _open(con)["id"], {"number": 1, "always": True})
        check("dialog: the pick is pressed and 'always' saves the rule", ok and term.sent == ["1"]
              and len(dialog_rules.list_rules()) == 1, str((ok, said, term.sent, dialog_rules.list_rules())))
    finally:
        con.close()


def test_always_is_never_saved_under_a_truncated_dialogs_placeholder(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        # A dialog whose top rule never shows: no vendor recognizer claims it, so it is a plain dialog.
        long_dialog = ["  earlier output"] + [f"   line {i}" for i in range(450)] + [
            "   Overwrite the file?", "   ❯ 1. Yes", "     2. No", "   Enter to confirm · Esc to cancel"]
        term = _Term(long_dialog)
        _pass(con)
        row = _open(con)
        check("truncated: found by growing the read, and marked as truncated",
              row and row["kind"] == "dialog" and row["title"] == orcamod.TRUNCATED_TITLE, str(dict(row or {})))
        ok, said = actions.answer_block(con, row["id"], {"number": 1, "always": True})
        check("truncated: the press lands", ok and term.sent == ["1"], str((ok, said, term.sent)))
        check("truncated: no rule was saved, and the founder is told", dialog_rules.list_rules() == []
              and "not remembered" in said, str((said, dialog_rules.list_rules())))
    finally:
        con.close()


def test_the_answer_is_a_socket_verb(home: Path) -> None:
    from ocp.daemon import socket_server
    check("answer_block is a verb of the daemon's socket", "answer_block" in socket_server.VERBS)


def test_cancel_sends_esc_after_the_same_checks_and_records_it(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        term = _Term(PERMISSION_PROMPT)
        _pass(con)
        row = _open(con)
        ok, said = actions.answer_block(con, row["id"], {"cancel": True})
        check("cancel: a permission prompt gets Esc and it counts as No",
              ok and term.sent == ["\x1b"] and "No" in said, str((ok, said, term.sent)))
        check("cancel: the result is recorded on the row",
              db.get_prompt(con, row["id"])["answer_ok"] == 1 and "Esc" in db.get_prompt(con, row["id"])["answer_said"])
        term = _Term(SINGLE_DEFAULT)
        _pass(con)
        ok, said = actions.answer_block(con, _open(con)["id"], {"cancel": True})
        check("cancel: a question gets Esc", ok and term.sent == ["\x1b"], str((ok, said, term.sent)))
        term = _Term(YES_NO_DIALOG)
        _pass(con)
        ok, said = actions.answer_block(con, _open(con)["id"], {"cancel": True})
        check("cancel: a dialog gets Esc, and no rule is saved by it",
              ok and term.sent == ["\x1b"] and dialog_rules.list_rules() == [], str((ok, said, term.sent)))
    finally:
        con.close()


def test_cancel_is_refused_on_a_stale_prompt_or_one_that_stays(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        term = _Term(PERMISSION_PROMPT)
        _pass(con)
        row = _open(con)
        term.tail = list(YES_NO_DIALOG)         # the screen moved on to another prompt
        ok, said = actions.answer_block(con, row["id"], {"cancel": True})
        check("cancel: a stale fingerprint is refused and no Esc is sent", not ok and not term.sent, str((ok, said, term.sent)))
        term = _Term(PERMISSION_PROMPT, ignores_key=True)
        waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
        ok, said = actions.answer_block(con, row["id"], {"cancel": True})
        check("cancel: Esc that leaves the prompt on screen is reported, not counted as done",
              not ok and term.sent == ["\x1b"] and "still on screen" in said, str((ok, said, term.sent)))
        db.prompt_close(con, row["id"], int(time.time()))
        ok, said = actions.answer_block(con, row["id"], {"cancel": True})
        check("cancel: a prompt that is already gone is refused", not ok and "gone" in said, said)
    finally:
        con.close()


def test_cancel_never_presses_esc_where_esc_leaves_the_session(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        exit_dialog = ["  earlier output", support._RULE_ROW,
                       "   Background tasks are running. Exiting will stop tasks (esc)",
                       "   ❯ 1. Keep working", "     2. Exit now", "   Enter to confirm · Esc to cancel"]
        term = _Term(exit_dialog)
        _pass(con)
        row = _open(con)
        check("exit dialog: it is an answerable dialog block", row and row["kind"] == "dialog" and row["answerable"] == 1,
              str(dict(row or {})))
        ok, said = actions.answer_block(con, row["id"], {"cancel": True})
        check("exit dialog: cancel is refused with the reason and sends nothing",
              not ok and not term.sent and "not cancelled" in said and "(esc)" in said, str((ok, said, term.sent)))
        check("exit dialog: the refusal is recorded", db.get_prompt(con, row["id"])["answer_ok"] == 0)
        term = _Term(["  earlier output"] + [f"   line {i}" for i in range(450)] + [
            "   Overwrite the file?", "   ❯ 1. Yes", "     2. No", "   Enter to confirm · Esc to cancel"])
        _pass(con)
        ok, said = actions.answer_block(con, _open(con)["id"], {"cancel": True})
        check("truncated dialog: cancel is refused, ocp does not choose Esc on what it could not read whole",
              not ok and not term.sent, str((ok, said, term.sent)))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_one_prompt_is_one_block_whatever_recognizers_see_it(Path(td) / 'b0'))
        run(lambda: test_the_three_kinds_and_their_priority(Path(td) / 'b1'))
        run(lambda: test_the_row_follows_the_screen_and_fails_closed(Path(td) / 'b2'))
        run(lambda: test_a_permission_prompt_with_no_hook_record_is_left_to_the_terminal(Path(td) / 'b3'))
        run(lambda: test_an_answer_needs_the_very_prompt_the_row_names(Path(td) / 'b4'))
        run(lambda: test_a_question_and_a_dialog_are_answered_from_their_blocks(Path(td) / 'b5'))
        run(lambda: test_cancel_sends_esc_after_the_same_checks_and_records_it(Path(td) / 'b8'))
        run(lambda: test_cancel_is_refused_on_a_stale_prompt_or_one_that_stays(Path(td) / 'b9'))
        run(lambda: test_cancel_never_presses_esc_where_esc_leaves_the_session(Path(td) / 'b10'))
        run(lambda: test_the_answer_is_a_socket_verb(Path(td) / 'b7'))
        run(lambda: test_always_is_never_saved_under_a_truncated_dialogs_placeholder(Path(td) / 'b6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
