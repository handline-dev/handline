"""Tests: pressing an option of a dialog on an agent's terminal (T187).

Run on its own: python3 tests/session/test_press_option.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.actions import confirmations, dialogs  # noqa: E402
from ocp.team import dialog_rules  # noqa: E402

DIALOG = ["  earlier output", "▔" * 60, "   Do you want to proceed?", "   ❯ 1. Yes",
          "     2. Yes, and don't ask again", "     3. No", "   Esc to cancel"]
TEN = DIALOG[:2] + ["   Pick one", "   ❯ 1. one"] + [f"     {n}. option {n}" for n in range(2, 11)] + ["   Esc to cancel"]


class _KeyTerm:
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


def test_press_option_sends_one_digit_and_refuses_what_it_cannot_send(home: Path) -> None:
    orcamod.PRESS_SETTLE_S = 0
    term = _KeyTerm(DIALOG)
    out = orcamod.press_option("term_x", 2)
    check("press: the listed option's digit is sent without Enter, and the dialog is gone",
          term.sent == [("2", False)] and out[0] == orcamod.CLEARED, str((term.sent, out)))
    term = _KeyTerm(DIALOG)
    out = orcamod.press_option("term_x", 5)
    check("press: an option the dialog does not list sends nothing", not term.sent and out[0] == orcamod.NOT_PRESSED, str(out))
    term = _KeyTerm(DIALOG, ignores_key=True)
    out = orcamod.press_option("term_x", 1)
    check("press: a dialog still on screen after the key is still open", out[0] == orcamod.STILL_OPEN, str(out))
    term = _KeyTerm(TEN)
    out = orcamod.press_option("term_x", 10)
    check("press: option 10 is refused, not sent as '1' then '0'", not term.sent and out[0] == orcamod.NOT_PRESSED, str((term.sent, out)))
    term = _KeyTerm(CLEARED_SCREEN)
    out = orcamod.press_option("term_x", 1)
    check("press: a screen that is not a dialog sends nothing", not term.sent and out[0] == orcamod.NOT_PRESSED, str(out))
    check("press: and dialog_on_screen reads it as None", orcamod.dialog_on_screen("term_x") is None)
    _KeyTerm(DIALOG)
    d = orcamod.dialog_on_screen("term_x")
    check("press: dialog_on_screen reads a dialog's title and options", d and d.title.startswith("Do you want") and len(d.options) == 3, str(d))


def _wide_screen(full_tail: list[str], calls: list[int] | None = None):
    """A `_screen` stand-in for a terminal whose whole buffer is `full_tail` -
    `--limit N` is really the last N rows of it, the way Orca's own read
    works. `calls` (if given) records each `limit` asked for, so a test can
    check how many reads a case actually cost."""
    def screen(h, limit=40):
        if calls is not None:
            calls.append(limit)
        return support._screen_of(full_tail[-limit:])
    return screen


def _heredoc_dialog(body_lines: int) -> list[str]:
    """A permission prompt whose command preview is `body_lines` long - a
    heredoc script, say - built the same shape as PERMISSION_PROMPT."""
    return (
        ["  earlier output", _RULE_ROW, "   Bash command"]
        + [f"   line_{i} = do_something({i})" for i in range(body_lines)]
        + ["   Do you want to proceed?", "   ❯ 1. Yes",
           "     2. Yes, and don't ask again for python3 commands",
           "     3. No, and tell Claude what to do differently (esc)",
           "   Esc to cancel · Tab to amend"]
    )


class _WideKeyTerm:
    """Like `_KeyTerm`, but the whole buffer can be longer than one read -
    `_screen(h, limit)` returns the last `limit` rows of it, and pressing a
    key (unless `ignores_key`) clears the whole thing, not just the visible
    slice of it."""

    def __init__(self, full_tail, ignores_key=False):
        self.full_tail, self.ignores_key, self.sent = list(full_tail), ignores_key, []
        orcamod._screen = lambda h, limit=40: support._screen_of(self.full_tail[-limit:])
        orcamod.send = self.send

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if not self.ignores_key:
            self.full_tail = list(CLEARED_SCREEN)
        return True, ""


def test_answer_dialog_never_remembers_a_press_that_failed(home: Path) -> None:
    """T383 (auditor finding): dialogs.press_choice used to call _remember
    before orca.press_option, so 'always' could write a standing rule even
    when the press itself then failed - especially bad under a truncated
    dialog's shared placeholder title (T383), which would wrongly answer
    every other long dialog like it too. Now the rule is written only once
    the press has actually landed."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        # A dialog that never clears when pressed - press_option reports
        # STILL_OPEN, never CLEARED.
        _KeyTerm(DIALOG, ignores_key=True)
        ok, said = dialogs.press_choice(con, "backend", "Do you want to proceed?", 1, always=True)
        check("press_choice: a press that does not clear the dialog is not ok", not ok, said)
        check("press_choice: and nothing was remembered from it",
              dialog_rules.list_rules() == [], str(dialog_rules.list_rules()))

        # The same dialog, but this time it actually clears.
        _KeyTerm(DIALOG)
        ok, said = dialogs.press_choice(con, "backend", "Do you want to proceed?", 1, always=True)
        check("press_choice: a press that clears the dialog is ok", ok, said)
        check("press_choice: and this time it is remembered",
              len(dialog_rules.list_rules()) == 1, str(dialog_rules.list_rules()))
    finally:
        con.close()


def test_a_long_heredoc_dialog_can_actually_be_pressed(home: Path) -> None:
    """dialog_on_screen widening to find a long command's dialog is only
    half the fix - _press_dialog (press_option, decline_dialog,
    answer_dialog, ...) must resolve the same dialog the same way, or the
    founder sees the prompt and every press on it still fails. Both the
    recoverable (45-line) and the truncated (450-line, past
    _DIALOG_READ_MAX) cases must actually press."""
    for body_lines in (45, 450):
        term = _WideKeyTerm(_heredoc_dialog(body_lines))
        out = orcamod.press_option("term_x", 1)
        check(f"heredoc press ({body_lines} lines): the option is actually sent",
              term.sent == [("1", False)] and out[0] == orcamod.CLEARED,
              str((body_lines, term.sent, out)))


def test_a_long_heredoc_commands_dialog_is_found_by_widening_the_read(home: Path) -> None:
    """T383: a command long enough to push the panel's own top rule and
    title past the ordinary 40-line read is still a real, answerable
    dialog - the read widens instead of the founder never seeing it."""
    calls: list[int] = []
    orcamod._screen = _wide_screen(_heredoc_dialog(45), calls)
    d = orcamod.dialog_on_screen("term_x")
    check("heredoc: the dialog is found once the read is wide enough",
          d is not None and d.title == "Bash command" and len(d.options) == 3, str(d))
    check("heredoc: the read widened past the default 40 to find it",
          calls == [40, 80], str(calls))


def test_a_command_past_the_widest_read_still_shows_a_truncated_dialog(home: Path) -> None:
    """T383: even past `_DIALOG_READ_MAX`, a real dialog is offered - marked
    as truncated - rather than the founder seeing nothing at all."""
    orcamod._screen = _wide_screen(_heredoc_dialog(450))
    d = orcamod.dialog_on_screen("term_x")
    check("heredoc: past the widest read, the title says so rather than guessing",
          d is not None and d.title == orcamod.TRUNCATED_TITLE, str(d))
    check("heredoc: the options are still read correctly",
          d is not None and len(d.options) == 3
          and [n for n, _ in d.options] == [1, 2, 3], str(d))


def test_an_ordinary_short_dialog_costs_only_the_one_read(home: Path) -> None:
    """Control case (T383): a dialog whose top rule is already inside the
    first 40 lines never triggers a second, wider read."""
    calls: list[int] = []
    orcamod._screen = _wide_screen(PERMISSION_PROMPT, calls)
    d = orcamod.dialog_on_screen("term_x")
    check("short command: found on the first read", d is not None and len(d.options) == 3, str(d))
    check("short command: never widened past the default", calls == [40], str(calls))


def test_a_truncated_dialogs_title_is_stable_across_reads(home: Path) -> None:
    """dialogs.press_choice refuses a press when a re-read's title differs
    from what was shown (T383's concern about the title recheck): a
    truncated dialog's title is the fixed TRUNCATED_TITLE both times, from
    the same still-long command, so a real answer is not refused as 'the
    dialog changed'."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        orcamod._screen = _wide_screen(_heredoc_dialog(450))
        first = actions.dialog_of(con, "backend")
        second = actions.dialog_of(con, "backend")
        check("truncated title: stable across two reads of the same screen",
              bool(first) and bool(second) and first["title"] == second["title"] == orcamod.TRUNCATED_TITLE,
              str((first, second)))
    finally:
        con.close()


def test_a_heredoc_permission_prompt_is_read_from_the_screen(home: Path) -> None:
    """The actual founder report (#2254/#2255): a Bash permission prompt for
    a long (heredoc) command never reached the bridge, because
    confirmation_on_screen (built on dialog_on_screen) found nothing to
    answer. With the read widened, the prompt is found and described."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        waiting.note("backend", "Bash", {"command": "git checkout -q x; python3 - <<'EOF'",
                                         "description": ""}, after_event_id=1)
        orcamod._screen = _wide_screen(_heredoc_dialog(45))
        prompt = orcamod.confirmation_on_screen("term_x")
        found = confirmations.describe("backend", prompt, None) if prompt else None
        check("heredoc confirm: the prompt is now found, with its wait record",
              found is not None and found["wait"] == 1, str(found))
    finally:
        con.close()


def test_always_on_a_truncated_dialog_presses_but_saves_no_rule(home: Path) -> None:
    """T383 (auditor #2322): every long command shares TRUNCATED_TITLE, so a
    standing rule saved under it would answer them all. The press lands, the
    rule is refused, and the founder is told it was not remembered."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        _WideKeyTerm(_heredoc_dialog(450))
        ok, said = dialogs.press_choice(con, "backend", orcamod.TRUNCATED_TITLE, 1, always=True)
        check("truncated always: the press itself still lands", ok and "chose 1" in said, said)
        check("truncated always: the founder is told it was not remembered",
              "not remembered" in said and "and will again" not in said, said)
        check("truncated always: no rule was saved",
              dialog_rules.list_rules() == [], str(dialog_rules.list_rules()))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_press_option_sends_one_digit_and_refuses_what_it_cannot_send(Path(td) / 'po0'))
        run(lambda: test_answer_dialog_never_remembers_a_press_that_failed(Path(td) / 'po0a'))
        run(lambda: test_a_long_heredoc_dialog_can_actually_be_pressed(Path(td) / 'po0b'))
        run(lambda: test_always_on_a_truncated_dialog_presses_but_saves_no_rule(Path(td) / 'po0c'))
        run(lambda: test_a_long_heredoc_commands_dialog_is_found_by_widening_the_read(Path(td) / 'po1'))
        run(lambda: test_a_command_past_the_widest_read_still_shows_a_truncated_dialog(Path(td) / 'po2'))
        run(lambda: test_an_ordinary_short_dialog_costs_only_the_one_read(Path(td) / 'po3'))
        run(lambda: test_a_truncated_dialogs_title_is_stable_across_reads(Path(td) / 'po4'))
        run(lambda: test_a_heredoc_permission_prompt_is_read_from_the_screen(Path(td) / 'po5'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
