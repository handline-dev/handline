"""T223: a permission prompt is recognized by the vendor's adapter, listed as a
pending confirmation, and answered only when a person says Yes or No."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from ocp import actions  # noqa: E402
from ocp.actions import confirmations  # noqa: E402


def _pending(con, agent: str = "backend"):
    """What `confirmations.describe` makes of the prompt the agent's screen shows now, or None."""
    row = db.get_agent_state(con, agent)
    handle = row["terminal"] if row and row["terminal"] and not row["closed_at"] else None
    found = orcamod.confirmation_on_screen(handle) if handle else None
    return confirmations.describe(agent, found, None) if found else None

from ocp.vendors.claude import Claude  # noqa: E402
from ocp.vendors.opencode import Opencode  # noqa: E402

RULE = "▔" * 60
PROMPT = ["  earlier output", RULE, "   Bash command", "     rm -rf build",
          "     Remove the build directory", "   Do you want to proceed?", "   ❯ 1. Yes",
          "     2. Yes, and don't ask again for rm commands",
          "     3. No, and tell Claude what to do differently (esc)",
          "   Esc to cancel · Tab to amend"]


def _dialog(tail):
    return orcamod.parse_dialog(tail, orcamod._footer_row(tail))


class _Term:
    """A terminal showing `tail` until a digit is sent, then the cleared screen (or not)."""

    def __init__(self, tail, ignores_key=False):
        self.tail, self.ignores_key, self.sent = list(tail), ignores_key, []
        orcamod._screen = lambda h: support._screen_of(self.tail)
        orcamod.send = self.send

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if not self.ignores_key:
            self.tail = list(CLEARED_SCREEN)
        return True, ""


def _swap(tail, old, new):
    return [ln.replace(old, new) for ln in tail]


def test_the_claude_adapter_recognizes_only_the_one_time_permission_prompt(home: Path) -> None:
    drv = Claude()
    found = drv.confirmation(_dialog(PROMPT))
    check("recognize: title, the lines saying what is asked, and the Yes and No numbers",
          found and found.title == "Bash command"
          and found.lines == ("rm -rf build", "Remove the build directory")
          and (found.yes, found.no) == (1, 3), str(found))
    for label, tail in (
            ("no question", _swap(PROMPT, "Do you want to proceed?", "Allow it?")),
            ("nothing above the question", PROMPT[:3] + PROMPT[5:]),
            ("a footer that does not say Esc cancels", _swap(PROMPT, "Esc to cancel", "Enter to confirm")),
            ("two plain Yes options", _swap(PROMPT, "2. Yes, and don't ask again for rm commands", "2. Yes")),
            ("no No option", _swap(PROMPT, "3. No, and tell Claude what to do differently (esc)",
                                   "3. Maybe later (esc)")),
            ("a yes/no question", YES_NO_DIALOG),
            ("the trust dialog", TRUST_DIALOG)):
        check(f"recognize: not for {label}", drv.confirmation(_dialog(tail)) is None)
    check("recognize: a vendor with no such prompt has none", Opencode().confirmation(_dialog(PROMPT)) is None)


def test_a_confirmation_is_answered_with_the_plain_yes_or_no_and_nothing_else(home: Path) -> None:
    orcamod.PRESS_SETTLE_S = 0
    seen = ("rm -rf build", "Remove the build directory")
    term = _Term(PROMPT)
    out = orcamod.answer_confirmation("term_x", True, seen)
    check("answer: Yes is the plain Yes's digit, never the 'don't ask again' one",
          term.sent == [("1", False)] and out[0] == orcamod.CLEARED, str((term.sent, out)))
    term = _Term(PROMPT)
    out = orcamod.answer_confirmation("term_x", False, seen)
    check("answer: No is the No option's digit", term.sent == [("3", False)] and out[0] == orcamod.CLEARED,
          str((term.sent, out)))
    term = _Term(_swap(PROMPT, "rm -rf build", "rm -rf /"))
    out = orcamod.answer_confirmation("term_x", True, seen)
    check("answer: a prompt that is not the one answered is not pressed, and says why",
          not term.sent and out[0] == orcamod.NOT_PRESSED and "not the one" in out[2], str((term.sent, out)))
    term = _Term(YES_NO_DIALOG)
    out = orcamod.answer_confirmation("term_x", True, seen)
    check("answer: a dialog that is not a permission prompt is not pressed",
          not term.sent and out[0] == orcamod.NOT_PRESSED and "recognizes" in out[2], str((term.sent, out)))
    term = _Term(PROMPT, ignores_key=True)
    out = orcamod.answer_confirmation("term_x", True, seen)
    check("answer: a key that leaves the prompt open is reported, not repeated",
          out[0] == orcamod.STILL_OPEN and len(term.sent) == 1, str((term.sent, out)))


def _world(home: Path, tail, **kw):
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    con = db.connect()
    db.set_terminal(con, "backend", "term_x", "wt")
    db.set_agent_state(con, "backend", dmn_states.RUNNING)
    return con, _Term(tail, **kw)


def test_a_confirmation_carries_the_command_and_reason_the_screen_has(home: Path) -> None:
    orcamod.PRESS_SETTLE_S = 0
    con, term = _world(home, PROMPT)
    try:
        waiting.note("backend", "Bash", {"command": "rm -rf build", "description": "Remove the build directory"}, 7)
        p = _pending(con)
        check("pending: read from the screen once the hook says the agent waits",
              p is not None and p["agent"] == "backend", str(p))
        check("pending: with the tool, the command and the reason the tool call gave",
              (p["tool"], p["command"], p["reason"]) == ("Bash", "rm -rf build", "Remove the build directory"),
              str(p))
        check("pending: and the panel's own lines", p["lines"] == ["rm -rf build", "Remove the build directory"]
              and p["title"] == "Bash command", str(p))
        check("pending: reading it presses nothing, and it carries the wait record's id",
              term.sent == [] and p["wait"] == 7, str((term.sent, p)))

        waiting.note("backend", "Bash", {"command": "rm -rf build", "description": "something the screen lacks"}, 8)
        p = _pending(con)
        check("pending: a reason the screen does not carry is dropped", p["reason"] == "", str(p))
        waiting.note("backend", "Bash", {"command": "ls"}, 9)
        p = _pending(con)
        check("pending: a command the screen does not carry falls back to its first line",
              p["command"] == "rm -rf build", str(p))

        check("pending: a blank command has no reason to show",
              waiting.reason({"command": "  ", "description": "why"}) == ""
              and waiting.reason({"command": "ls", "description": "why"}) == "why")
        term.tail = list(CLEARED_SCREEN)
        check("pending: a screen with no prompt has none", _pending(con) is None)
        check("pending: and a closed terminal has none",
              (db.set_closed(con, "backend", "s") or True) and _pending(con) is None)
    finally:
        con.close()


def test_askuserquestion_carries_its_raw_input_beside_the_summary(home: Path) -> None:
    """T304: ui renders the founder's own questions and options, not the one-line
    summary() every other tool's wait record gets - added only for this tool."""
    q = {"questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]}
    waiting.note("backend", "AskUserQuestion", q, 11)
    w = waiting.read("backend")
    check("question_data: carries the whole call, untouched",
          w["question_data"] == q, str(w))
    check("question_data: the flat summary is still there too",
          w["command"] and w["tool"] == "AskUserQuestion", str(w))
    waiting.note("backend", "Bash", {"command": "ls"}, 12)
    check("question_data: absent for every other tool",
          "question_data" not in waiting.read("backend"))


# The founder's paste of a live prompt (T277): the panel opens with a U+2500 rule,
# the command is boxed with "│", a description and a reason line follow.
LIVE_PROMPT = ["─" * 120, " Bash command", "",
               " │ ocp ask human 'T239 (#1328) is blocked. Need one of: (a) approve that edit'",
               " │ 2>&1 | tail -3", "   Raise the block to the founder", "",
               " │ ocp names a path that is computed at run time, which cannot be checked against "
               "the read block (permissions.blockReadsOutsideWorkingDirectories)", "",
               " Do you want to proceed?", " ❯ 1. Yes", "   2. No", "",
               " Esc to cancel · Tab to amend"]


def test_the_live_prompt_with_a_boxed_command_and_a_reason_line_is_recognized(home: Path) -> None:
    found = Claude().confirmation(_dialog(["earlier output"] + LIVE_PROMPT))
    check("recognize: the live prompt's title, three lines, and Yes/No numbers",
          found and found.title == "Bash command" and len(found.lines) == 4
          and found.lines[-1].startswith("│ ocp names a path") and (found.yes, found.no) == (1, 2), str(found))


def main() -> int:
    def tests(td):
        run(lambda: test_the_claude_adapter_recognizes_only_the_one_time_permission_prompt(Path(td) / 'c0'))
        run(lambda: test_a_confirmation_is_answered_with_the_plain_yes_or_no_and_nothing_else(Path(td) / 'c1'))
        run(lambda: test_a_confirmation_carries_the_command_and_reason_the_screen_has(Path(td) / 'c2'))
        run(lambda: test_askuserquestion_carries_its_raw_input_beside_the_summary(Path(td) / 'c2b'))
        run(lambda: test_the_live_prompt_with_a_boxed_command_and_a_reason_line_is_recognized(Path(td) / 'c4'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
