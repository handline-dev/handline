"""A Claude permission prompt an agent's terminal is showing, offered to the
founder from its chat page (T224): the tool, its command and the reason it was
asked, with Yes and No only - never "always", since a permission prompt has no
standing rule to save. `answered` is `actions.answer_confirmation`'s to send;
`seen` (the dict `actions.pending_confirmation` returned) is passed back to it
unchanged, since what is pressed is checked against a fresh screen read, not
against what this modal drew."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ...infra import textsafe


class PermissionModal(ModalScreen["bool | None"]):
    """Dismisses with True (Yes), False (No), or None when closed by hand: the
    terminal is left as it is either way."""

    BINDINGS = [Binding("escape", "dismiss(None)", "close")]
    DEFAULT_CSS = """
    PermissionModal { align: center middle; background: $background 60%; }
    PermissionModal #dialog { width: 72; max-width: 90%; height: auto; padding: 1 2;
                              border: thick $warning; background: $panel; }
    PermissionModal #title { margin-bottom: 1; text-style: bold; }
    PermissionModal #tool { color: $text-muted; }
    PermissionModal #command { margin-top: 1; }
    PermissionModal #reason { margin-top: 1; color: $text-muted; }
    PermissionModal #buttons { height: 1; margin-top: 1; align-horizontal: right; }
    PermissionModal Button { margin-left: 2; }
    PermissionModal #hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, seen: dict) -> None:
        super().__init__()
        self.seen = seen

    def compose(self) -> ComposeResult:
        d = self.seen
        with Vertical(id="dialog"):
            yield Static(Text(f"{textsafe.oneline(d['agent'])} needs permission"), id="title")
            if d.get("tool"):
                yield Static(Text(textsafe.oneline(d["tool"])), id="tool")
            if d.get("command"):
                yield Static(Text("\n".join(textsafe.drawable(d["command"]))), id="command")
            if d.get("reason"):
                yield Static(Text(f"reason: {textsafe.oneline(d['reason'])}"), id="reason")
            with Horizontal(id="buttons"):
                yield Button("No  n", id="no", compact=True)
                yield Button("Yes  y", id="yes", compact=True, variant="warning")
            yield Static("y / n keys work too · esc closes (the terminal is left as it is)", id="hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "yes")

    def on_key(self, event) -> None:
        if event.character in ("y", "Y"):
            event.stop()
            self.dismiss(True)
        elif event.character in ("n", "N"):
            event.stop()
            self.dismiss(False)
