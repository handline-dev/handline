"""What a terminal would obey, and how to show text without it.

Text that someone else wrote - a request body, a verdict, an agent's state
detail, a handoff - is drawn on the founder's terminal by every front end: the
bridge's screen and the CLI's output alike. A terminal acts on some bytes
instead of drawing them: an OSC sequence (window title, clipboard write, link),
a CSI sequence (cursor moves, erase, colour), a lone ESC, any other C0 control
(BEL, backspace, carriage return), DEL, and the C1 range (0x9b is CSI, 0x9d is
OSC). This module is the one rule for taking those out, and it imports nothing
of any front end, so a new one reaches it without reaching a screen.

It does not style, truncate or draw: those belong to the front end that owns the
terminal. `wrap` is here although it breaks lines to a width, because it cannot
be done apart from the rule: text is sanitised before it is measured, and a
wrap that measured first would count a control as a column.
"""

from __future__ import annotations

import json
import re
import unicodedata

# OSC (title, clipboard, links) up to its terminator or the end of the text;
# CSI with any parameter and intermediate bytes; then every byte left that is a
# control - a lone ESC, C0, DEL and C1.
_OSC = re.compile(r"\033\][^\a\033]*(?:\a|\033\\)?")
_CSI = re.compile(r"\033\[[0-?]*[ -/]*[@-~]")

# The single-byte controls a terminal obeys instead of draws, as a set any
# other module can test a character against - C0 (tab and newline excluded;
# line-oriented callers handle those separately, as `drawable` does below)
# plus DEL plus C1 (0x9b is CSI, 0x9d is OSC, one byte instead of two). A
# caller that also cares about tab and newline - a config scalar, never a
# display line - adds "\t\n" itself rather than this module deciding that
# for it.
CONTROL_CHARS = frozenset(chr(c) for c in range(0x00, 0x09)) \
    | frozenset(chr(c) for c in range(0x0b, 0x20)) \
    | frozenset(chr(c) for c in range(0x7f, 0xa0))
_CONTROL = re.compile("[" + "".join(CONTROL_CHARS) + "]")


def drawable(text: str, tabs: int | None = 4) -> list[str]:
    """`text` as lines of plain text and nothing a terminal would obey.

    A line break is anything `str.splitlines` breaks on, which includes the
    ones a terminal would obey (carriage return, vertical tab, form feed): they
    become line breaks here rather than being overwritten in place. A tab
    becomes `tabs` spaces, since it is the one control whose width is not one;
    `None` leaves it, for output that is read as text and not laid out.
    """
    text = _CSI.sub("", _OSC.sub("", text))
    return [_CONTROL.sub("", ln.expandtabs(tabs) if tabs else ln)
            for ln in text.splitlines() or [""]]


def text(value: str) -> str:
    """`value` as one string that can be printed as it is: `drawable`'s lines,
    joined by newlines, tabs kept. For a command that writes into someone's
    shell, where the text is read - by a person or by an agent - and not laid
    out, so a tab in a patch must stay a tab."""
    return "\n".join(drawable(value, tabs=None))


def oneline(value: str) -> str:
    """`value` as a single line of printable text: `drawable`, a newline being
    a space, and anything else unprintable blanked."""
    return "".join(c if c.isprintable() else " " for c in " ".join(drawable(value)))


def json_dumps(obj, **kw) -> str:
    """`json.dumps(obj, ensure_ascii=False)` for a terminal: DEL and C1 are the
    controls JSON leaves as they are, so they are written as \\uXXXX escapes,
    which is still the same JSON."""
    return re.sub(r"[\x7f-\x9f]", lambda m: f"\\u{ord(m.group()):04x}",
                  json.dumps(obj, ensure_ascii=False, **kw))


def cwidth(ch: str) -> int:
    """How many columns one character takes.

    A Korean or Japanese glyph is drawn two columns wide, and counting it as
    one put every box border a column short per glyph: a handoff written in
    Korean tore the panel it was drawn in. Combining marks take none.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def wrap(lines: list[str], width: int) -> list[str]:
    """Break long lines to the terminal's width, at spaces where there are any.

    A message body arrives as whatever the sender typed, and an agent's report
    is often one paragraph with no newline in it at all. Fitting that to the
    width shows its first sentence and drops the rest - which is not reading
    the message. Every line goes through `drawable` first.
    """
    width = max(1, width)
    out: list[str] = []
    for raw in lines:
        for line in drawable(raw):
            line = line.rstrip()
            if not line:
                out.append("")
                continue
            while sum(map(cwidth, line)) > width:
                # Cut on width, not on characters: with wide glyphs the two are
                # not the same number, and cutting on the second wraps a line
                # that is already too long back onto itself.
                room, cut = 0, 0
                for i, ch in enumerate(line):
                    if room + cwidth(ch) > width:
                        break
                    room += cwidth(ch)
                    cut = i + 1
                space = line.rfind(" ", 0, cut + 1)
                if space > 0:
                    cut = space
                out.append(line[:cut].rstrip())
                line = line[cut:].lstrip()
            out.append(line)
    return out
