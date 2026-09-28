"""Claude Code's AskUserQuestion picker (T304), recognized for a founder to
answer from the bridge instead of switching to the real terminal.

Measured against four real screens the founder captured on 2026-09-27 (a
single-select question, the same one with "Other" typed over its "Type
something." line, a multiSelect question, and its own "Review your answers"
screen) - not reconstructed from wording the way claude_confirm.py's Yes/No
prompt was, since this shape is distinctive enough (an extra "Chat about
this" line below a second rule) that guessing it wrong risked pressing a key
against a screen this never was.

`parse_dialog` cannot read this screen: it takes the rule closest to the
footer as the panel's own top, which here is the second rule, above only
"Chat about this" - one line that itself matches the option-label pattern, so
`parse_dialog` refuses it as a title with no body. This walks `tail` directly
from the footer upward instead, through the fixed shape both captures share:
footer, blank, "N. Chat about this", blank, a rule, the numbered options
(each optionally an indented description line below it), the question, the
header, a rule.

Scope (v1): a lone question, single-select or multiSelect, "Other" on either.
A multiSelect question's own "Other" sends the single-select key pattern
followed by Right/Submit - untested against a real screen as of 2026-09-28,
so a founder capture of it going through (or not) is still owed. A call with
more than one question adds a tab per
question before "Submit", and reading that bar past the two tabs a lone
question shows is not measured here - `recognize` returns None rather than
guess at it, the same as it does for a multiSelect question's own
intermediate "Review your answers" screen: `orca.answer_question` sends that
whole key sequence blind (measured by github.com/stupart/conch's
`claudeQuestionKeys`, Claude Code 2.1.280) and confirms it worked by checking
the question screen itself is gone, never by reading the Review screen along
the way.
"""
from __future__ import annotations

import re

from .base import Question, QuestionOption

FOOTER_MARKER = "Esc to cancel"
_RULE = re.compile(r"^[─]{8,}$")
_CHAT_ABOUT_THIS = re.compile(r"^(\d+)\.\s+chat about this$", re.IGNORECASE)
# The header line of a lone question: " ☐ <header>" (single-select) or
# "←  ☒ <header>  ✔ Submit  →" (multiSelect, which always has its own
# "Submit" step even with one question).
_SINGLE_HEADER = re.compile(r"^☐\s+(.+)$")
_TAB_HEADER = re.compile(r"^←\s+(.+)→$")
_TAB_ICON = re.compile(r"^[☐☒✅✔]\s*")
_OPTION = re.compile(r"^(❯)?\s*(\d+)\.\s+(?:\[(.)\]\s*)?(\S.*)$")
_CHECKED = ("✔", "x", "X")
TYPE_SOMETHING = "type something"
# Right arrow: a multiSelect question's own way off it, onto its "Submit"
# tab (github.com/stupart/conch's `claudeQuestionKeys`, measured on Claude
# Code 2.1.280 - a real session driven in a pseudo-terminal, not this
# codebase's own capture, which never answered one this way).
RIGHT = "\x1b[C"
DOWN = "\x1b[B"
ENTER = "enter"


def question_keys(q: Question, choices: tuple[int, ...] = (), text: str | None = None) -> list[str] | None:
    """The keys that answer `q`, sent in order by `orca.answer_question`, or
    None when this combination is not sent here.

    `choices` are 0-based indexes into `q.options`; `text` is free text for
    `q.other_number`. A multiSelect question's Other does not open its text
    field on its own number the way single-select's does - that number only
    toggles its checkbox, same as any other row - so typing into it takes
    moving the cursor there first: Down from `q.cursor` to `q.other_number`,
    then the text, then one more Down onto "Submit" and Enter (founder
    capture, T322 follow-up, 2026-09-28, after two earlier guesses at this -
    a trailing Right/Submit, and toggling boxes in the same breath as
    answering Other - both went wrong on the real screen in different ways).
    `choices` alongside multiSelect's Other is still refused: toggling a box
    moves the cursor too, in some way this has not been measured against a
    real screen, so where it lands before the Downs above matters and is not
    guessed at here.

    A single-select question submits on its one number - conch's own words,
    "a lone single-choice question ... a number submits outright". A
    multiSelect question's numbers only TOGGLE a checkbox, never set it, so
    `choices` (the final checked set the founder wants) is compared against
    `q.options[i].checked` (what the screen just read shows now) and only
    the options that differ are pressed - an option already checked and
    still wanted is left alone, not pressed and un-checked by mistake. RIGHT
    then moves onto the question's own "Submit" tab, whose one worthwhile
    option ("1. Submit answers", the other being Cancel) is never read back
    to confirm - `orca.answer_question` checks the question card itself is
    gone instead.
    """
    if choices and any(c < 0 or c >= len(q.options) for c in choices):
        return None
    if text is not None:
        if q.other_number is None or choices:
            return None
        if not q.multi_select:
            return [str(q.other_number), text, ENTER]
        downs = q.other_number - q.cursor
        if downs < 0:
            return None
        # Enter on "Submit" opens the same Review-your-answers screen the
        # checked-boxes-only path reaches through Right - its "1. Submit
        # answers" is what actually finishes it (founder capture).
        return [DOWN] * downs + [text, DOWN, ENTER, "1"]
    if not q.multi_select:
        return [str(choices[0] + 1)] if len(choices) == 1 else None
    # Empty choices is a real answer on a multiSelect question - nothing
    # picked, not "no answer at all" - so it still submits, unchecking
    # whatever the screen showed checked.
    return _toggle_keys(q, choices) + [RIGHT, "1"]


def _toggle_keys(q: Question, choices: tuple[int, ...]) -> list[str]:
    desired = set(choices)
    current = {i for i, o in enumerate(q.options) if o.checked}
    return [str(i + 1) for i in sorted(desired ^ current)]


def recognize(tail: list[str]) -> Question | None:
    """`Driver.ask_question`'s contract, on Claude Code's own screen text."""
    footer = _footer_row(tail)
    if footer is None:
        return None
    lines = [str(x).rstrip() for x in tail[:footer]]
    i = _last_nonblank(lines, len(lines) - 1)
    chat = _CHAT_ABOUT_THIS.match(lines[i].strip()) if i >= 0 else None
    if not chat:
        return None
    chat_number = int(chat[1])
    mid_rule = _last_nonblank(lines, i - 1)
    if mid_rule < 0 or not _RULE.match(lines[mid_rule].strip()):
        return None
    top_rule = next((k for k in range(mid_rule - 1, -1, -1) if _RULE.match(lines[k].strip())), None)
    if top_rule is None:
        return None
    body = [ln for ln in lines[top_rule + 1:mid_rule] if ln.strip()]
    if len(body) < 3:
        return None
    header, multi_select = _header(body[0].strip())
    if header is None:
        return None
    question = body[1].strip()
    options, other_number, other_text, cursor = _options(body[2:], multi_select)
    if other_number is None or other_number + 1 != chat_number:
        return None
    return Question(header, question, tuple(options), multi_select, other_number,
                    other_text, tuple(body), cursor)


def _footer_row(tail: list[str]) -> int | None:
    for i in range(len(tail) - 1, -1, -1):
        if FOOTER_MARKER in str(tail[i]):
            return i
    return None


def _last_nonblank(lines: list[str], start: int) -> int:
    i = start
    while i >= 0 and not lines[i].strip():
        i -= 1
    return i


def _header(line: str) -> tuple[str | None, bool]:
    m = _SINGLE_HEADER.match(line)
    if m:
        return m[1].strip(), False
    m = _TAB_HEADER.match(line)
    if not m:
        return None, False
    parts = [_TAB_ICON.sub("", p).strip() for p in re.split(r"\s{2,}", m[1].strip()) if p.strip()]
    # A real second question adds a third tab before "Submit" - not this
    # scan's to read.
    if len(parts) != 2 or parts[1].lower() != "submit":
        return None, False
    return parts[0], True


def _options(lines: list[str], multi_select: bool) -> tuple[list[QuestionOption], int | None, str | None, int]:
    options: list[QuestionOption] = []
    other_number, other_text, cursor = None, None, 1
    i, n = 0, 1
    while i < len(lines):
        m = _OPTION.match(lines[i].strip())
        if not m or int(m[2]) != n:
            return [], None, None, 1
        if m[1]:
            cursor = n
        box, label = m[3], m[4].strip()
        i += 1
        desc: list[str] = []
        while i < len(lines) and not _OPTION.match(lines[i].strip()):
            text = lines[i].strip()
            # A multiSelect header's own wide tab bar sometimes wraps its
            # "Submit" tab onto the row under the last option; that is the
            # tab bar's text, never this option's description.
            if not (multi_select and i == len(lines) - 1 and text == "Submit"):
                desc.append(text)
            i += 1
        is_last = i == len(lines)
        if label.rstrip(".").lower() == TYPE_SOMETHING:
            other_number = n
        elif is_last and other_number is None:
            other_number, other_text = n, label
        else:
            options.append(QuestionOption(label, " ".join(desc), box in _CHECKED))
        n += 1
    return options, other_number, other_text, cursor
