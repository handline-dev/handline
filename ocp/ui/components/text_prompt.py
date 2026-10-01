"""A one-line or multi-line text prompt over the current page."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from ...infra import textsafe
from .dialog import Dialog
from .ime import LineBox, TextBox


class TextPrompt(Dialog):
    """One line (or, `multi`, several) read with the screen still up, in a
    Textual `Input` or `TextArea`. Enter sends and Esc backs out (None); a paste
    is kept whole, minus anything a terminal would obey."""

    FOCUS = "#box"
    BINDINGS = [Binding("ctrl+c", "finish(None)", "cancel", show=False)]
    DEFAULT_CSS = """
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
            yield TextBox(id="box") if self.multi else LineBox(id="box")
            yield Static("enter sends" + (" · shift+enter new line" if self.multi else "")
                         + " · esc cancels", id="hint")

    def on_mount(self) -> None:
        box = self.query_one("#box")
        if self.initial and self.multi:
            box.text = self.initial
            box.move_cursor(box.document.end)
        elif self.initial:
            box.value = self.initial

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.finish(event.value)

    def on_text_box_send(self, event: "TextBox.Send") -> None:
        event.stop()
        self.finish(self.query_one("#box", TextArea).text)
