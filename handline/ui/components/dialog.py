"""What every box over the page shares: the frame, Escape, one way out, and where focus goes."""
from __future__ import annotations

from textual.binding import Binding
from textual.screen import ModalScreen

from .. import stall
from .close_bar import CloseCorner


class Dialog(CloseCorner, ModalScreen):
    """A box in the middle of the screen over what is under it. A subclass draws its content in a
    `Vertical(id="dialog")`, names the widget to focus when it opens in `FOCUS` (a selector), and
    leaves through `finish`; Escape finishes with None. What is under it takes focus back by
    itself when the box goes."""

    FOCUS: str | None = None
    CLOSE_RESULT = None     # what the close button and Escape answer
    BINDINGS = [Binding("escape", "close", "cancel")]
    DEFAULT_CSS = """
    Dialog { align: center middle; background: $background 60%; }
    Dialog #dialog { width: 72; max-width: 90%; height: auto; padding: 1 2;
                     border: thick $primary; background: $panel; }
    """

    _finished = False

    def on_mount(self) -> None:
        self.mark_close_corner()
        if self.FOCUS:
            self.query_one(self.FOCUS).focus()

    def finish(self, result=None) -> None:
        """The one way out: the first answer counts, a second (a double click, Enter after a click)
        is dropped instead of dismissing a screen that is already gone."""
        stall.crumb(f"{type(self).__name__}: finish{'' if not self._finished else ' dropped (already finished)'}")
        if not self._finished:
            self._finished = True
            self.dismiss(result)

    def close(self) -> None:
        self.finish(self.CLOSE_RESULT)

    def action_close(self) -> None:
        self.close()

    def action_finish(self, result=None) -> None:
        self.finish(result)
