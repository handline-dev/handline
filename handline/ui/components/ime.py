"""Widgets that keep a syllable an IME commits with the Enter that ends it."""
from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import Input, TextArea

from ...infra import textsafe


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


class LineBox(ImeInput):
    """A one-line box that keeps a multi-line paste as one line: each line
    break is a space, where Input alone would keep only the first line. A paste
    goes in as `textsafe.drawable` text: a widget draws a control character raw."""

    def on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        self.insert_text_at_cursor(" ".join(textsafe.drawable(event.text)))


class TextBox(TextArea):
    """A several-line box: Enter sends, Alt/Shift+Enter or Ctrl+J is a new line.
    A paste goes in as `textsafe.drawable` text: a widget draws a control
    character raw."""

    class Send(Message):
        pass

    def on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        self.insert("\n".join(textsafe.drawable(event.text, tabs=None)))

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            if ch := committed(event):
                self.insert(ch)
            event.prevent_default()
            event.stop()
            self.post_message(self.Send())
            return
        if event.key in ("alt+enter", "shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)
