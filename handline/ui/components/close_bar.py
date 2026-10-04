"""The one visible way to close a box: a row at its top with a close button, for every modal."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button


class CloseBar(Horizontal):
    """`✕ close  esc` at the top right of a box. A press asks the screen it is on to `close()`: the
    same answer Escape gives."""

    DEFAULT_CSS = "CloseBar { height: 1; align-horizontal: right; }"

    def compose(self) -> ComposeResult:
        yield Button("✕ close  esc", id="close_x", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.screen.close()
