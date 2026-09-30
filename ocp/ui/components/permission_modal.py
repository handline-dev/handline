"""A Claude permission prompt an agent's terminal is showing, offered to the
founder from its chat page (T224): the tool, its command and the reason it was
asked, with Yes and No only - never "always", since a permission prompt has no
standing rule to save. What is pressed is sent by the chat page through
`actions.request_block_answer`; Esc closes it with no pick, which is sent as a cancel."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ...infra import textsafe


def panel_text(d: dict) -> str:
    """What the terminal's prompt says about the call - the whole command, the reason and
    Claude's warning line, as drawn there - without the auto-mode tip; the one-line
    `command` when the panel's lines were not read."""
    lines = [ln for ln in d.get("lines") or () if not ln.strip().lower().startswith("tip:")]
    return "\n".join(textsafe.drawable("\n".join(lines) if lines else d.get("command") or ""))


class PermissionModal(ModalScreen["bool | None"]):
    """Dismisses with True (Yes), False (No), or None when closed by hand: the
    terminal is left as it is either way."""

    BINDINGS = [Binding("escape", "dismiss(None)", "close")]
    DEFAULT_CSS = """
    PermissionModal { align: center middle; background: $background 60%; }
    PermissionModal #dialog { width: 72; max-width: 90%; height: auto; max-height: 95%; padding: 1 2;
                              border: thick $warning; background: $panel; }
    PermissionModal #title { margin-bottom: 1; text-style: bold; }
    PermissionModal #tool { color: $text-muted; }
    PermissionModal #command { margin-top: 1; height: auto; max-height: 20; border: round $primary-darken-1; padding: 0 1; }
    PermissionModal #command Static { width: auto; }
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
            shown = panel_text(d)
            if shown:
                with VerticalScroll(id="command"):
                    yield Static(Text(shown))
            elif d.get("reason"):
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
