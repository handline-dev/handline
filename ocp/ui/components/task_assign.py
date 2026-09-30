from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static, TextArea

from ... import actions

_HOWS = (("send a note to whoever holds it", "note"), ("hand it to another agent", "handoff"))


class TaskAssign(ModalScreen["tuple[str, str, str] | None"]):
    """The founder answering from a task's page: a note to its holder, or a handoff to another
    agent with the note. The agent is asked for only when handing it on. Dismisses with
    (how, to, text), or None."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]
    DEFAULT_CSS = """
    TaskAssign { align: center middle; background: $background 60%; }
    TaskAssign #dialog { width: 72; max-width: 90%; height: auto; padding: 1 2;
                         border: thick $primary; background: $panel; }
    TaskAssign #title { margin-bottom: 1; }
    TaskAssign .frow { height: 1; margin-bottom: 1; }
    TaskAssign .flabel { width: 8; color: $text-muted; }
    TaskAssign .frow Select { width: 1fr; }
    TaskAssign #ta_text { height: 6; margin-bottom: 1; }
    TaskAssign #ta_buttons { height: 1; }
    TaskAssign #ta_buttons Button { width: auto; min-width: 0; margin-right: 2; }
    """

    def __init__(self, ref: str, holder: str, agents: list[str]) -> None:
        super().__init__()
        self.ref, self.holder = ref, holder
        self.others = [a for a in agents if a != holder]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(f"Assign {self.ref}", style="bold"), id="title")
            with Horizontal(classes="frow"):
                yield Static("how", classes="flabel")
                yield Select(_HOWS, id="ta_how", allow_blank=False, compact=True, value="note")
            with Horizontal(classes="frow", id="ta_to_row"):
                yield Static("to", classes="flabel")
                yield Select([(a, a) for a in self.others], id="ta_to", allow_blank=False, compact=True,
                             value=self.others[0]) if self.others else Static("no other agent")
            yield TextArea(id="ta_text")
            with Horizontal(id="ta_buttons"):
                yield Button("Send", id="ta_send", variant="primary", compact=True)
                yield Button("Cancel", id="ta_cancel", compact=True)

    def on_mount(self) -> None:
        self._show()
        self.query_one("#ta_text").focus()

    def _show(self) -> None:
        self.query_one("#ta_to_row").display = self.query_one("#ta_how", Select).value == "handoff"

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        self._show()

    def result(self) -> "tuple[str, str, str]":
        how = str(self.query_one("#ta_how", Select).value)
        to = self.query_one("#ta_to", Select).value if how == "handoff" and self.others else self.holder
        return how, str(to), self.query_one("#ta_text", TextArea).text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.result() if event.button.id == "ta_send" else None)
