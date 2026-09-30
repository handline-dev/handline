"""A one-line or multi-line text prompt over the current page."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from ...infra import textsafe
from .ime import ImeInput, committed


class _LineBox(ImeInput):
    """A one-line box that keeps a multi-line paste as one line: each line
    break is a space, where Input alone would keep only the first line. A paste
    goes in as `textsafe.drawable` text: a widget draws a control character raw."""

    def on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        self.insert_text_at_cursor(" ".join(textsafe.drawable(event.text)))


class _TextBox(TextArea):
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


class TextPrompt(ModalScreen[str | None]):
    """One line (or, `multi`, several) read with the screen still up, in a
    Textual `Input` or `TextArea`. Enter sends and Esc backs out (None); a paste
    is kept whole, minus anything a terminal would obey."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"),
                Binding("ctrl+c", "dismiss(None)", "cancel", show=False)]
    DEFAULT_CSS = """
    TextPrompt { align: center middle; background: $background 60%; }
    TextPrompt #dialog { width: 72; max-width: 90%; height: auto; padding: 1 2;
                         border: thick $primary; background: $panel; }
    TextPrompt #prompt { height: auto; margin-bottom: 1; }
    TextPrompt #box { height: auto; min-height: 3; max-height: 12; }
    TextPrompt #hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, prompt: str, multi: bool = False, initial: str = "") -> None:
        super().__init__()
        self.prompt, self.multi, self.initial = prompt, multi, initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(textsafe.oneline(self.prompt), style="bold"), id="prompt")
            yield _TextBox(id="box") if self.multi else _LineBox(id="box")
            yield Static("enter sends" + (" · shift+enter new line" if self.multi else "")
                         + " · esc cancels", id="hint")

    def on_mount(self) -> None:
        box = self.query_one("#box")
        if self.initial and self.multi:
            box.text = self.initial
            box.move_cursor(box.document.end)
        elif self.initial:
            box.value = self.initial
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def on__text_box_send(self, event: "_TextBox.Send") -> None:
        event.stop()
        self.dismiss(self.query_one("#box", TextArea).text)
