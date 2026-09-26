"""The stats screen (T65b, founder #824): the weekly headroom graph on a page of its own, opened with S from home."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen

from ..components.outlook_view import OutlookView


class StatsScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "back"),
                Binding("q,ctrl+c", "app.quit", "quit", show=False)]

    def compose(self) -> ComposeResult:
        yield OutlookView(back=True, id="stats_view")

    def on_mount(self) -> None:
        self.query_one("#stats_view").focus()
