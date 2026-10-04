"""A yes/no question in a box over the current page."""
from __future__ import annotations

from typing import Callable

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ...infra import textsafe
from .dialog import Dialog


def confirm_answer(key: str, character: str | None) -> bool | None:
    """True for y, False for n, Escape or Ctrl+C, None for any other key: a wheel notch
    arrives as an arrow and must leave the question open."""
    if character in ("y", "Y"):
        return True
    if character in ("n", "N") or key in ("escape", "ctrl+c"):
        return False
    return None


class Confirm(Dialog):
    """A yes/no question in a box in the middle of the screen, over what is
    under it. `question` is the one line asked; `detail` is what answering yes
    will do, in as many lines as it takes; `yes` and `no` label the buttons
    (the key each answers to is shown beside it); `danger` paints Yes red for
    something that cannot be undone.

    Only a typed y (or a click on Yes) confirms, and only y, n or Escape (or a
    click) answers; every other key leaves the question open, so a wheel notch
    or a stray arrow never answers it. Dismisses with True or False."""

    DEFAULT_CSS = """
    Confirm #dialog { width: 64; }
    Confirm #dialog.danger { border: thick $error; }
    Confirm #question { text-style: bold; }
    Confirm #detail { margin-top: 1; color: $text-muted; }
    Confirm #buttons { height: 1; margin-top: 1; align-horizontal: right; }
    Confirm Button { margin-left: 2; }
    """

    def __init__(self, question: str, detail: str = "", yes: str = "Yes", no: str = "No",
                 danger: bool = False) -> None:
        super().__init__()
        self.question, self.detail, self.yes, self.no, self.danger = question, detail, yes, no, danger

    @classmethod
    def ask(cls, app, on_yes: Callable[[], None], *args, **kw) -> None:
        """Push the question and run `on_yes` when answered yes. The answer arrives on the pump of
        the widget that asked, which `on_yes` may remove (a screen pop): it runs from the app's."""
        app.push_screen(cls(*args, **kw), lambda yes: yes and app.call_later(on_yes))

    def compose(self) -> ComposeResult:
        asked = self.question if self.question.rstrip().endswith(("?", ".", ":")) else self.question + "?"
        with Vertical(id="dialog", classes="danger" if self.danger else ""):
            yield Static(Text(textsafe.oneline(asked)), id="question")
            if self.detail:
                yield Static(Text("\n".join(textsafe.drawable(self.detail))), id="detail")
            with Horizontal(id="buttons"):
                yield Button(f"{self.no}  n", id="no", compact=True)
                yield Button(f"{self.yes}  y", id="yes", compact=True,
                             variant="error" if self.danger else "warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.finish(event.button.id == "yes")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        answer = confirm_answer(event.key, event.character)
        if answer is not None:
            self.finish(answer)


def leave_or_ask(app, problems: list[str]) -> None:
    """Go back from a settings page. Nothing is asked unless a field still holds a value that
    could not be saved (`problems` says why): leaving would drop it."""
    if not problems:
        app.pop_screen()
        return
    Confirm.ask(app, app.pop_screen, "Go back without saving", "Not saved: " + "; ".join(problems),
                yes="Go back", no="Stay")
