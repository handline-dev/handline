"""A dialog an agent's terminal is showing, offered to the founder (T187): its title and each
option as a button (or its number key), and a box that makes the pick the standing answer.
What is pressed is sent through `actions.request_block_answer`; the box saves a standing answer."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Static

from ...infra import textsafe
from ...session import orca
from .dialog import Dialog


class DialogModal(Dialog):
    """Dismisses with (option number, always), or None when closed: the terminal is left as it is."""

    DEFAULT_CSS = """
    DialogModal #who { color: $text-muted; }
    DialogModal #title { margin: 1 0; text-style: bold; }
    DialogModal Button { width: 100%; margin-bottom: 1; }
    DialogModal #body { margin-bottom: 1; }
    DialogModal #hint { color: $text-muted; }
    """

    def __init__(self, dialog: dict) -> None:
        super().__init__()
        self.dialog = dialog

    def compose(self) -> ComposeResult:
        d = self.dialog
        with Vertical(id="dialog"):
            yield Static(Text(f"{textsafe.oneline(d['agent'])} is asking", style="dim"), id="who")
            yield Static(Text(textsafe.oneline(d["title"])), id="title")
            if d.get("body"):
                yield Static(Text("\n".join(textsafe.oneline(l) for l in d["body"]), style="dim"), id="body")
            for number, label in d["options"]:
                yield Button(Text(f"{number}. {textsafe.oneline(label)}"), id=f"opt_{number}", compact=True)
            if d["title"] != orca.TRUNCATED_TITLE:      # no rule can be kept for it (dialogs._remember)
                yield Checkbox("always choose the one I pick", id="always")
            yield Static("click an option or press its number · esc closes (the terminal is left as it is)", id="hint")

    def _always(self) -> bool:
        boxes = self.query("#always")
        return bool(boxes) and boxes.first(Checkbox).value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.finish((int(event.button.id.split("_")[1]), self._always()))

    def on_key(self, event) -> None:
        if event.character and event.character.isdigit() and any(n == int(event.character) for n, _ in self.dialog["options"]):
            event.stop()
            self.finish((int(event.character), self._always()))
