"""The short line beside a settings field: "saved" for a moment after a write, or the reason
the value was not saved, which stays until the field is right."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ...infra import textsafe

SAVED_FOR_S = 2.0


class FieldNote(Static):
    DEFAULT_CSS = "FieldNote { width: auto; padding-left: 2; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.problem = ""
        self._timer = None

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def saved(self, how: str = "saved") -> None:
        self.problem = ""
        self._stop()
        self.update(Text("✓ " + textsafe.oneline(how), style="green"))
        self._timer = self.set_timer(SAVED_FOR_S, self.clear_saved)

    def error(self, why: str) -> None:
        self.problem = why
        self._stop()
        self.update(Text("✗ " + textsafe.oneline(why), style="bold red"))

    def clear_saved(self) -> None:
        if not self.problem:
            self.update("")

    def clear(self) -> None:
        self.problem = ""
        self._stop()
        self.update("")
