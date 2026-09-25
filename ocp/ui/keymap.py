"""Shortcuts that work in any keyboard layout. A terminal reports the key by the
character the layout types, so Ctrl+V in Korean input is `ctrl+ㅍ` and a binding
on `ctrl+v` never fires. `canonical` names a key by the Latin letter on the same
physical key, and `KeymapApp` does that to every key before a binding sees it.
The text a key types is its `character`, which is left as typed."""
from __future__ import annotations

from textual import events
from textual.app import App

_JAMO = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t", "ㅛ": "y", "ㅕ": "u", "ㅑ": "i",
    "ㅐ": "o", "ㅔ": "p", "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f", "ㅎ": "g", "ㅗ": "h",
    "ㅓ": "j", "ㅏ": "k", "ㅣ": "l", "ㅋ": "z", "ㅌ": "x", "ㅊ": "c", "ㅍ": "v", "ㅠ": "b",
    "ㅜ": "n", "ㅡ": "m",
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T", "ㅒ": "O", "ㅖ": "P",
}


def canonical(key: str) -> str:
    """`key` with a layout's letter replaced by the Latin one on the same key:
    'ctrl+ㅍ' is 'ctrl+v', 'ㅁ' is 'a'."""
    head, sep, last = key.rpartition("+")
    latin = _JAMO.get(last)
    if latin is None:
        return key
    if sep:
        latin = latin.lower()
    return head + sep + latin


class KeymapApp(App):
    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key):
            event.key = canonical(event.key)
        await super().on_event(event)
