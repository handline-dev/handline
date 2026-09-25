"""The tasks screen (T69): all tasks, opened with T from the home page."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen

from ..components.tasks_view import TasksView


class TasksScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "back"),
                Binding("q,ctrl+c", "app.quit", "quit", show=False)]

    def compose(self) -> ComposeResult:
        yield TasksView(back=True, id="tasks_view")

    def on_mount(self) -> None:
        self.query_one("#tasks_table").focus()
