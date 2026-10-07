"""Textual key events as the byte tokens `tui.py`'s key tables expect."""
from __future__ import annotations

import time

from textual import events

from ..view import tui

# Textual hands a key event as a symbolic name (`event.key`) plus, for a
# printable one, the character itself (`event.character`). tui.py's own key
# handling is byte-token driven (arrow keys as their raw escape sequences,
# Enter as "\r", ...) because that is what a raw terminal read produces - and
# reusing it, rather than re-deriving what every key does a second time, is
# the whole point of porting the interaction layer and not the logic under
# it. This is the one place that translates between the two.
KEY_TOKENS = {
    "enter": "\r", "return": "\r",
    "escape": "\033",
    "tab": "\t", "shift+tab": "\033[Z",
    "up": "\033[A", "down": "\033[B", "left": "\033[D", "right": "\033[C",
    "home": "\033[H", "end": "\033[F",
    "pageup": "\033[5~", "pagedown": "\033[6~",
    "backspace": "\x7f", "delete": "\033[3~",
    "ctrl+c": "\x03", "ctrl+g": "\x07", "ctrl+u": "\x15", "ctrl+y": "\x19",
    "space": " ",
    "alt+enter": "\033\r",
}


def key_token(event: events.Key) -> str | None:
    """The byte token tui.py's key tables and `Composer.feed` expect, or None
    for a key with nothing to say (a bare modifier, a function key nothing
    binds)."""
    if event.key in KEY_TOKENS:
        return KEY_TOKENS[event.key]
    if event.character and event.character.isprintable():
        return event.character
    return None
