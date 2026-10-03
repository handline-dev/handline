from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class SectionHead(Vertical):
    """The title of one section of a settings page: a rule above it, the title in bold and, under it, the
    line that says what the section holds."""

    DEFAULT_CSS = """
    SectionHead { height: auto; margin-top: 1; border-top: solid $accent; }
    SectionHead .hint { color: $text-muted; }
    """

    def __init__(self, title: str, hint: str = "", **kw) -> None:
        super().__init__(**kw)
        self.title, self.hint = title, hint

    def compose(self) -> ComposeResult:
        yield Static(Text(self.title, style="bold"))
        if self.hint:
            yield Static(self.hint, classes="hint")
