"""The founder's editor for any one agent's settings, opened from the HR tab: the
same form as the Settings tab of the agent's chat page."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from ...infra import textsafe
from .agent_settings import AgentSettings


class AgentSettingsScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "back")]
    DEFAULT_CSS = "AgentSettingsScreen #as_title { height: 1; margin: 1 3 0 3; text-style: bold; }"

    def __init__(self, agent: str) -> None:
        super().__init__()
        self.agent = agent

    def compose(self) -> ComposeResult:
        yield Static(f"settings of {textsafe.oneline(self.agent)}   ·   esc to go back", id="as_title", markup=False)
        yield AgentSettings(self.agent, id="settings_form")

    def on_mount(self) -> None:
        self.query_one("#settings_form", AgentSettings).load()
