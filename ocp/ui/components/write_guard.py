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

    def take(self, tasks: int) -> None:
        """A write is owed from now: taken where it is decided, not where it starts, so a second
        request in between is refused. `release` when it ends."""
        self.tasks = tasks

    def release(self) -> None:
        self.tasks = 0

    @contextmanager
    def running(self, tasks: int):
        self.take(tasks)
        try:
            yield
        finally:
            self.release()
