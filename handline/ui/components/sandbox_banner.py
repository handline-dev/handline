from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from ... import actions


class SandboxBanner(Horizontal):
    """The line above a chat while the agent runs without its sandbox, with the one button that ends it.
    It is only drawn: whether the sandbox is off, and putting it back, are `actions`."""

    DEFAULT_CSS = """
    SandboxBanner { height: 1; padding: 0 2; background: $error 25%; }
    SandboxBanner Static { width: 1fr; }
    SandboxBanner Button { min-width: 0; width: auto; }
    """

    def __init__(self, agent: str, **kw) -> None:
        super().__init__(**kw)
        self.agent = agent
        self.display = False

    def compose(self) -> ComposeResult:
        yield Static("Sandbox is off for this agent, until you turn it on", id="sandbox_text")
        yield Button("Turn it on", id="sandbox_on", compact=True)

    def refresh_state(self, agent: str, show: bool) -> None:
        self.agent = agent
        self.display = show and actions.sandbox_is_off(self.agent)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        ok, said = actions.sandbox_back_on(self.agent)
        self.app.notify(said, severity="information" if ok else "error", markup=False)
        self.refresh_state(self.agent, True)
