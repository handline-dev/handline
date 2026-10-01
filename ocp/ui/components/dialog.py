"""What every box over the page shares: the frame, Escape, one way out, and where focus goes."""
from __future__ import annotations

from textual.binding import Binding
from textual.screen import ModalScreen


class Dialog(ModalScreen):
    """A box in the middle of the screen over what is under it. A subclass draws its content in a
    `Vertical(id="dialog")`, names the widget to focus when it opens in `FOCUS` (a selector), and
    leaves through `finish`; Escape finishes with None. What is under it takes focus back by
    itself when the box goes."""

    FOCUS: str | None = None
    BINDINGS = [Binding("escape", "finish(None)", "cancel")]
    DEFAULT_CSS = """
    Dialog { align: center middle; background: $background 60%; }
    Dialog #dialog { width: 72; max-width: 90%; height: auto; padding: 1 2;
                     border: thick $primary; background: $panel; }
    """

    _finished = False

    def on_mount(self) -> None:
        if self.FOCUS:
            self.query_one(self.FOCUS).focus()

    def finish(self, result=None) -> None:
        """The one way out: the first answer counts, a second (a double click, Enter after a click)
        is dropped instead of dismissing a screen that is already gone."""
        if not self._finished:
            self._finished = True
            self.dismiss(result)

    def action_finish(self, result=None) -> None:
        self.finish(result)
