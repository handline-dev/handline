"""One guard for a widget whose action writes on a thread: a second action while one is writing
is refused with a note, so the first is never cancelled before it has reported."""
from __future__ import annotations

from contextlib import contextmanager


class WriteGuard:
    def __init__(self) -> None:
        self.tasks = 0

    def refuse(self, widget) -> bool:
        """True, with a note on `widget`, while a write is running."""
        if self.tasks:
            widget.notify(f"still changing {self.tasks} task{'s' if self.tasks != 1 else ''}", severity="warning")
        return bool(self.tasks)

    @contextmanager
    def running(self, tasks: int):
        self.tasks = tasks
        try:
            yield
        finally:
            self.tasks = 0
