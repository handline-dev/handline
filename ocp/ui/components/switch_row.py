"""A settings row with an on/off switch: a label, the switch, and a muted line saying what it does.
The page styles `.frow`, `.flabel` and `.fhint`, so the row looks like the rows around it."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static, Switch


class SwitchRow(Horizontal):
    def __init__(self, label: str, hint: str, switch_id: str, value: bool = False) -> None:
        super().__init__(classes="frow")
        self.label, self.hint, self.switch_id, self.initial = label, hint, switch_id, value

    def compose(self) -> ComposeResult:
        yield Static(self.label, classes="flabel")
        yield Switch(id=self.switch_id, value=self.initial)
        yield Static("  " + self.hint, classes="fhint")
