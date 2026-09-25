"""ocp bridge: the Textual app the team is run from.

The app opens on the home page (`ui.pages.home.HomePage`), from which Enter opens an agent's chat page
(`chat_page.ChatPage`); the founder's "done - waiting for check" list is
`ui.pages.task_checks.TaskChecks`. Pages draw what the data functions in `tui.py`
and `actions` say and decide nothing themselves, so a row or a page means the same
here as in `ocp status` and the daemon. The widgets and modals pages share are in
`ui.components`.

Every verb a key or a slash command can run hands its work to `ocp.ui.actrun`
through `jobs.Jobs` and returns at once, so a screen keeps redrawing and reading
keys while an action is in flight, with a line saying what is running and for
how long.

Run once or into a pipe, `ocp bridge` is `tui.snapshot`, plain text, and does not
start this app.
"""
from __future__ import annotations

import sys
from pathlib import Path

from textual import events
from textual.app import App

from ..infra import clipboard
from . import jobs as jobsmod, tui
from .keymap import KeymapApp


class ClipboardApp(KeymapApp):
    """An app whose copy also reaches the system clipboard: Textual's own copy
    is OSC 52, which iTerm2 and macOS Terminal drop unless told otherwise. A
    drag-selection is copied when the mouse is let go, as Cmd+C never reaches
    the app."""

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.call_after_refresh(self._copy_selection)

    def _copy_selection(self) -> None:
        if text := self.screen.get_selected_text():
            self.copy_to_clipboard(text)

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        if clipboard.copy(text):
            self.notify(f"copied {len(text)} chars", timeout=2)


class BridgeApp(ClipboardApp):
    """The Textual bridge: the home page, and from it an agent's chat page - see
    `HomePage`, `chat_page.ChatPage`."""

    TITLE = "ocp bridge"
    CSS_PATH = Path(__file__).with_name("chat_page.tcss")

    def __init__(self) -> None:
        super().__init__()
        self.jobs = jobsmod.Jobs()

    def on_mount(self) -> None:
        from .pages.home import HomePage
        self.push_screen(HomePage())


def run(once: bool = False) -> int:
    """`once`, or stdout not a tty (a script or a pipe): `tui.snapshot`, plain
    text, not a Textual app."""
    if once or not sys.stdout.isatty():
        print(tui.snapshot())
        return 0
    BridgeApp().run()
    return 0

