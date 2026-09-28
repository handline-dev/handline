"""T304: Claude Code's AskUserQuestion prompt, recognized off the real screen it draws (the
founder's own captures, 2026-09-27: a single-select question, the same one with "Other" typed
over its "Type something.", and a multiSelect question), listed as a pending question, and
answered only with the keys that pick what was shown."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from ocp import actions  # noqa: E402
from ocp.actions import ask_question  # noqa: E402
from ocp.vendors.claude import Claude  # noqa: E402
from ocp.vendors.claude_ask import DOWN, ENTER, RIGHT, question_keys  # noqa: E402
from ocp.vendors.opencode import Opencode  # noqa: E402

RULE = "─" * 60

SINGLE_DEFAULT = [
    RULE, " ☐ Library", "",
    "Which library should we use for dates?", "",
    "❯ 1. date-fns", "     small, tree-shakeable",
    "  2. dayjs", "     momentjs-compatible API",
    "  3. luxon", "     built-in timezone support",
    "  4. Type something.",
    RULE, "  5. Chat about this", "",
    "Enter to select · ↑/↓ to navigate · Esc to cancel",
]

SINGLE_OTHER = [
    RULE, " ☐ Library", "",
    "Which library should we use for dates?", "",
    "  1. date-fns", "     small, tree-shakeable",
    "  2. dayjs", "     momentjs-compatible API",
    "  3. luxon", "     built-in timezone support",
    "❯ 4. hihi",
    RULE, "  5. Chat about this", "",
    "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel",
]

MULTI_SELECT = [
    RULE, "←  ☒ Features  ✔ Submit  →", "",
    "Which features should we enable?", "",
    "  1. [✔] dark mode", "         system-wide dark theme",
    "  2. [ ] notifications", "         push alerts",
    "  3. [✔] offline sync", "         works without network",
    "❯ 4. [ ] Type something", "     Submit",
    RULE, "  5. Chat about this", "",
    "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel",
]

# The founder's own real capture (2026-09-28): cursor fresh on row 1, four
# real options, Other at row 5 - the shape that showed a number toggles
# Other's checkbox but does not open its text field, only Down does.
MULTI_SELECT_CURSOR_AT_TOP = [
    RULE, "←  ☐ Needed  ✔ Submit  →", "",
    "Which features should we enable?", "",
    "❯ 1. [ ] logging", "         request/response logs",
    "  2. [ ] metrics", "         latency, error rate",
    "  3. [ ] alerts", "         notify on anomaly",
    "  4. [ ] user admin", "         roles/accounts screen",
    "  5. [ ] Type something", "     Submit",
    RULE, "  6. Chat about this", "",
    "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel",
]

PERMISSION_PROMPT = ["earlier output", RULE, "   Bash command", "     rm -rf build",
                     "   Do you want to proceed?", "   ❯ 1. Yes", "     2. No",
                     "   Esc to cancel · Tab to amend"]


def test_the_claude_adapter_recognizes_the_askuserquestion_prompt(home: Path) -> None:
    drv = Claude()
    q = drv.ask_question(SINGLE_DEFAULT)
    check("recognize: header, question and the three real options",
          q and q.header == "Library" and q.question == "Which library should we use for dates?"
          and [o.label for o in q.options] == ["date-fns", "dayjs", "luxon"]
          and q.options[0].description == "small, tree-shakeable"
          and not q.multi_select and q.other_number == 4 and q.other_text is None, str(q))

    q2 = drv.ask_question(SINGLE_OTHER)
    check("recognize: text already typed over 'Type something.' is other_text",
          q2 and q2.other_text == "hihi" and len(q2.options) == 3, str(q2))

    q3 = drv.ask_question(MULTI_SELECT)
    check("recognize: a multiSelect question's checkboxes and header",
          q3 and q3.multi_select and q3.header == "Features"
          and [o.checked for o in q3.options] == [True, False, True], str(q3))
    check("recognize: the cursor row the screen's own ❯ marks",
          q3.cursor == q3.other_number == 4, str(q3))

    check("recognize: not a plain permission prompt (parse_dialog's own shape)",
          drv.ask_question(PERMISSION_PROMPT) is None)
    check("recognize: a vendor with no such prompt has none", Opencode().ask_question(SINGLE_DEFAULT) is None)


def test_question_keys_match_the_measured_key_sequence(home: Path) -> None:
    q = Claude().ask_question(SINGLE_DEFAULT)
    check("keys: a lone single-select question submits on its own number",
          question_keys(q, choices=(1,)) == ["2"])
    check("keys: Other types the words and presses Enter",
          question_keys(q, text="my-lib") == ["4", "my-lib", ENTER])
    check("keys: an out-of-range choice is refused", question_keys(q, choices=(9,)) is None)

    # dark mode (0) and offline sync (2) are already checked on this real
    # capture - a number TOGGLES a checkbox, never sets it, so wanting
    # exactly what is already checked presses nothing before Right/Submit.
    m = Claude().ask_question(MULTI_SELECT)
    check("keys: an option already checked and still wanted is not pressed",
          question_keys(m, choices=(0, 2)) == [RIGHT, "1"])
    check("keys: only the options that must change are toggled",
          question_keys(m, choices=(1,)) == ["1", "2", "3", RIGHT, "1"])
    check("keys: wanting nothing checked on a multiSelect question still submits, unchecking what was",
          question_keys(m, choices=()) == ["1", "3", RIGHT, "1"])
    check("keys: wanting nothing at all on a single-select question is refused, not a submit",
          question_keys(q, choices=()) is None)

    # MULTI_SELECT's own cursor already sits on Other's row (founder capture) -
    # a number toggles Other's checkbox same as any other, it does not open
    # its text field, so this is Down's to reach it, not `other_number`'s own key.
    check("keys: Other on a multiSelect question, cursor already there, types and submits",
          question_keys(m, text="anything") == ["anything", DOWN, ENTER, "1"])
    check("keys: Other alongside checked-box changes is refused - toggling moves the "
          "cursor too, in a way this has not measured against a real screen",
          question_keys(m, choices=(1,), text="anything") is None)

    top = Claude().ask_question(MULTI_SELECT_CURSOR_AT_TOP)
    check("recognize: cursor fresh on row 1, Other four rows down",
          top and top.cursor == 1 and top.other_number == 5, str(top))
    check("keys: Other reached by Down from wherever the cursor is, then one more onto Submit",
          question_keys(top, text="a new one") == [DOWN, DOWN, DOWN, DOWN, "a new one", DOWN, ENTER, "1"])


class _Term:
    """A terminal showing `tail` until any key is sent, then CLEARED_SCREEN."""

    def __init__(self, tail):
        self.tail, self.sent = list(tail), []
        orcamod._screen = lambda h: support._screen_of(self.tail)
        orcamod.send = self.send

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        self.tail = list(CLEARED_SCREEN)
        return True, ""


# MULTI_SELECT_CURSOR_AT_TOP after toggling rows 1 and 3 (options 0 and 2) -
# the cursor lands on the last one pressed, row 3, same as a real toggle key
# leaves it. Written out by hand rather than computed, to keep the fixture
# and the test reading it equally plain (founder #2013 follow-up: prefer the
# simpler failure mode over a small renderer that could itself be wrong).
MULTI_SELECT_AFTER_TOGGLING_1_AND_3 = [
    RULE, "←  ☐ Needed  ✔ Submit  →", "",
    "Which features should we enable?", "",
    "  1. [✔] logging", "         request/response logs",
    "  2. [ ] metrics", "         latency, error rate",
    "❯ 3. [✔] alerts", "         notify on anomaly",
    "  4. [ ] user admin", "         roles/accounts screen",
    "  5. [ ] Type something", "     Submit",
    RULE, "  6. Chat about this", "",
    "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel",
]


class _TwoStageTerm:
    """A multiSelect card: `before` until `toggle_count` keys have been sent
    (the toggles), then `after_toggle` until every key after that has landed
    too, then CLEARED_SCREEN - the same two-stage shape
    `orca._answer_question_toggle_and_other` sends and re-reads between
    (T322/T336 follow-up)."""

    def __init__(self, before: list[str], toggle_count: int, after_toggle: list[str]):
        self.sent: list[tuple[str, bool]] = []
        self._before, self._after_toggle = before, after_toggle
        self._toggle_count = toggle_count
        orcamod._screen = lambda h: support._screen_of(self.tail)
        orcamod.send = self.send

    @property
    def tail(self) -> list[str]:
        if len(self.sent) < self._toggle_count:
            return self._before
        if len(self.sent) == self._toggle_count:
            return self._after_toggle
        return list(CLEARED_SCREEN)

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        return True, ""


def test_multiselect_boxes_and_other_together_toggle_then_reach_other(home: Path) -> None:
    """T322/T336 follow-up (founder #2013, live 2026-09-28): checking boxes and
    typing Other together used to be refused outright. It is sent now, in two
    stages - the toggles, a re-read for where they actually left the cursor,
    then Other from there - never as one sequence computed blind."""
    orcamod.PRESS_SETTLE_S = 0
    q = Claude().ask_question(MULTI_SELECT_CURSOR_AT_TOP)
    term = _TwoStageTerm(MULTI_SELECT_CURSOR_AT_TOP, toggle_count=2,
                         after_toggle=MULTI_SELECT_AFTER_TOGGLING_1_AND_3)
    out = orcamod.answer_question("term_x", q.lines, choices=(0, 2), text="좋아")
    check("answer: the toggles land first, options 0 and 2 in ascending order",
          term.sent[:2] == [("1", False), ("3", False)], str(term.sent))
    check("answer: Other is reached from the re-read cursor (row 3), not the one first shown",
          term.sent[2:] == [(DOWN, False), (DOWN, False), ("좋아", False),
                            (DOWN, False), ("", True), ("1", False)], str(term.sent))
    check("answer: confirmed cleared", out[0] == orcamod.CLEARED, str(out))

    # The re-read after toggling disagrees with what was asked for (a stray
    # key landed differently on the real screen) - refused, nothing further sent.
    term = _TwoStageTerm(MULTI_SELECT_CURSOR_AT_TOP, toggle_count=2,
                         after_toggle=MULTI_SELECT_CURSOR_AT_TOP)      # still unchecked
    out = orcamod.answer_question("term_x", q.lines, choices=(0, 2), text="좋아")
    check("answer: a re-read that does not show what was toggled stops there",
          term.sent == [("1", False), ("3", False)] and out[0] == orcamod.STILL_OPEN
          and "not what was toggled" in out[2], str((term.sent, out)))


def test_answer_question_sends_the_keys_and_confirms_it_cleared(home: Path) -> None:
    orcamod.PRESS_SETTLE_S = 0
    q = Claude().ask_question(SINGLE_DEFAULT)
    term = _Term(SINGLE_DEFAULT)
    out = orcamod.answer_question("term_x", q.lines, choices=(1,))
    check("answer: a single choice sends its number and confirms cleared",
          term.sent == [("2", False)] and out[0] == orcamod.CLEARED, str((term.sent, out)))

    term = _Term(SINGLE_OTHER)
    out = orcamod.answer_question("term_x", ("stale",), choices=(0,))
    check("answer: a question that is not the one shown is not pressed",
          not term.sent and out[0] == orcamod.NOT_PRESSED, str(out))


def test_a_pending_question_is_listed_and_answered_through_actions(home: Path) -> None:
    orcamod.PRESS_SETTLE_S = 0
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        check("pending: none without a hook record naming AskUserQuestion",
              ask_question.pending_all(con) == [])
        waiting.note("backend", "AskUserQuestion", {"questions": [{"header": "Library"}]}, 7)
        term = _Term(SINGLE_DEFAULT)
        found = ask_question.pending_all(con)
        check("pending: listed once the hook says the agent waits on AskUserQuestion",
              len(found) == 1 and found[0]["header"] == "Library"
              and [o["label"] for o in found[0]["options"]] == ["date-fns", "dayjs", "luxon"], str(found))
        check("pending: the actions package exposes it for the UI",
              actions.pending_questions(con) == found and actions.pending_question(con, "backend") == found[0])

        ok, said = ask_question.answer(con, "backend", found[0], choices=(0,))
        check("answer: the founder's pick reaches the terminal and is confirmed",
              ok and term.sent == [("1", False)], str((ok, said, term.sent)))
    finally:
        con.close()


def test_question_data_is_preferred_only_when_it_still_matches_the_screen(home: Path) -> None:
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        _Term(SINGLE_DEFAULT)
        waiting.note("backend", "AskUserQuestion",
                     {"questions": [{"header": "Dates", "question": "Pick a date library",
                                    "options": [{"label": "date-fns", "description": "the one to use"},
                                                {"label": "dayjs", "description": ""},
                                                {"label": "luxon", "description": ""}],
                                    "multiSelect": False}]}, 7)
        found = ask_question.pending(con, "backend")
        check("pending: question_data's own words are shown when its shape still matches the screen",
              found["header"] == "Dates" and found["question"] == "Pick a date library"
              and found["options"][0]["description"] == "the one to use", str(found))

        waiting.note("backend", "AskUserQuestion",
                     {"questions": [{"header": "Stale", "question": "a question already answered",
                                    "options": [{"label": "X"}, {"label": "Y"}], "multiSelect": False}]}, 8)
        found = ask_question.pending(con, "backend")
        check("pending: a stale question_data (wrong option count) falls back to the screen's own words",
              found["header"] == "Library" and [o["label"] for o in found["options"]]
              == ["date-fns", "dayjs", "luxon"], str(found))
    finally:
        con.close()


def test_a_permission_prompt_the_screen_shows_wins_over_a_question_reading(home: Path) -> None:
    """T336, founder #1977: a live screen where both detectors agreed on a
    plain Bash permission prompt showed both modals for it. `pending` now
    defers to `confirmations.pending` whenever the same terminal is also a
    recognized permission prompt, whatever the AskUserQuestion reading says."""
    from ocp.vendors.base import Confirmation
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        _Term(SINGLE_DEFAULT)
        waiting.note("backend", "AskUserQuestion", {"questions": [{"header": "Library"}]}, 7)
        check("pending: a real AskUserQuestion screen is listed on its own",
              ask_question.pending(con, "backend") is not None)

        real_confirmation_on_screen = orcamod.confirmation_on_screen
        orcamod.confirmation_on_screen = lambda h: Confirmation("Bash command", (), 1, 2)
        try:
            check("pending: the same screen is dropped once a permission prompt also reads it",
                  ask_question.pending(con, "backend") is None)
        finally:
            orcamod.confirmation_on_screen = real_confirmation_on_screen
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_claude_adapter_recognizes_the_askuserquestion_prompt(Path(td) / 'q0'))
        run(lambda: test_question_keys_match_the_measured_key_sequence(Path(td) / 'q1'))
        run(lambda: test_multiselect_boxes_and_other_together_toggle_then_reach_other(Path(td) / 'q1b'))
        run(lambda: test_answer_question_sends_the_keys_and_confirms_it_cleared(Path(td) / 'q2'))
        run(lambda: test_a_pending_question_is_listed_and_answered_through_actions(Path(td) / 'q3'))
        run(lambda: test_question_data_is_preferred_only_when_it_still_matches_the_screen(Path(td) / 'q4'))
        run(lambda: test_a_permission_prompt_the_screen_shows_wins_over_a_question_reading(Path(td) / 'q5'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
