"""Tests: pressing an option of a dialog on an agent's terminal (T187).

Run on its own: python3 tests/session/test_press_option.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

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


def main() -> int:
    def tests(td):
        run(lambda: test_press_option_sends_one_digit_and_refuses_what_it_cannot_send(Path(td) / 'po0'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
