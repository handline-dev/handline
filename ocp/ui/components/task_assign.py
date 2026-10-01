from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static, TextArea

from ... import actions


class TaskAssign(ModalScreen["tuple[str, str] | None"]):
    """The founder handing a task on from its page: the agent and the note that goes with it.
    Dismisses with (to, text), or None."""

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

    def __init__(self, ref: str, holder: str, agents: list[str], recent=()) -> None:
        super().__init__()
        self.ref, self.holder = ref, holder
        first, team, rest = actions.assign_targets(holder, agents, recent)
        self.options = ([(f"{a}  (recent)", a) for a in first] + [(a, a) for a in team]
                        + [(f"{a}  (other team)" if actions.has_team(holder) else a, a) for a in rest])
        self.others = [a for _, a in self.options]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(f"Assign {self.ref}", style="bold"), id="title")
            with Horizontal(classes="frow", id="ta_to_row"):
                yield Static("to", classes="flabel")
                yield Select(self.options, id="ta_to", allow_blank=False, compact=True,
                             value=self.others[0]) if self.others else Static("no other agent")
            yield TextArea(id="ta_text")
            with Horizontal(id="ta_buttons"):
                yield Button("Send", id="ta_send", variant="primary", compact=True)
                yield Button("Cancel", id="ta_cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#ta_text").focus()

    def result(self) -> "tuple[str, str]":
        to = self.query_one("#ta_to", Select).value if self.others else self.holder
        return str(to), self.query_one("#ta_text", TextArea).text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.result() if event.button.id == "ta_send" else None)
