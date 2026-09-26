"""Widgets that keep a syllable an IME commits with the Enter that ends it."""
from __future__ import annotations

from textual import events
from textual.widgets import Input


def committed(event: events.Key) -> str:
    """This terminal sends the committing Enter as one event, key 'enter' with the
    syllable as its character, and Textual would drop it."""
    ch = event.character
    return ch if ch and ch.isprintable() else ""


class ImeInput(Input):
    """An `Input` whose Enter takes the syllable it commits into the value first."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter" and (ch := committed(event)):
            event.prevent_default()
            event.stop()
            self.insert_text_at_cursor(ch)
            await self.action_submit()
            return
        await super()._on_key(event)
