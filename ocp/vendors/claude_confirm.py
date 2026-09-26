"""Claude Code's one-time permission prompt, recognized for a person to answer.

The prompt for one tool call - "Do you want to proceed?", a plain "Yes", a "Yes,
and don't ask again ...", a "No ..." - reconstructed from Claude Code's wording,
not measured on a live screen (see claude.py's note on the browser prompt): the
match is as narrow as that wording allows, so a layout that differs is not
recognized and stays for the terminal. Nothing here presses anything; the
number a founder's answer names is read off the screen by `orca.answer_confirmation`.
"""
from __future__ import annotations

from .base import Confirmation

QUESTION = "do you want to proceed?"


def recognize(dialog) -> Confirmation | None:
    from ..session import orca  # late: orca looks drivers up
    if "Esc to cancel" not in dialog.footer:
        return None
    prose = [ln for ln in dialog.body if not orca._OPTION_LABEL.match(ln)]
    asked = [i for i, ln in enumerate(prose) if ln.strip().lower() == QUESTION]
    if len(asked) != 1 or asked[0] < 2:
        return None      # no question, or nothing above it saying what is asked
    yes = [n for n, label in dialog.options if orca._norm_label(label) == "yes"]
    no = [n for n, label in dialog.options if orca._norm_label(label).split(",")[0] == "no"]
    if len(yes) != 1 or len(no) != 1:
        return None
    return Confirmation(prose[0], tuple(prose[1:asked[0]]), yes[0], no[0])
