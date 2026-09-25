"""The stats screen (T65b): the stats table on a page of its own, opened with S from home."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen

from ..components.stats_view import StatsView, cells, short_tokens  # noqa: F401


class StatsScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "back"),
                Binding("q,ctrl+c", "app.quit", "quit", show=False)]

    def compose(self) -> ComposeResult:
        yield StatsView(back=True, id="stats_view")

    def on_mount(self) -> None:
        self.query_one("#stats_table").focus()
