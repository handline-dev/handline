"""ocp bridge: the Textual app the team is run from.

The app opens on the home page (`ui.pages.home.HomePage`; `ui.pages.dashboard.Dashboard`
is the previous one), from which Enter opens an agent's chat page
(`chat_page.ChatPage`); the founder's "done - waiting for check" list is
`ui.pages.task_checks.TaskChecks`. Pages draw what the data functions in `tui.py`
and `actions` say and decide nothing themselves, so a row or a page means the same
here as in `ocp status` and the daemon. The widgets and modals pages share are in
`ui.components`.

Every verb a key or a slash command can run hands its work to `ocp.actrun`
through `jobs.Jobs` and returns at once, so a screen keeps redrawing and reading
keys while an action is in flight, with a line saying what is running and for
how long.

Run once or into a pipe, `ocp bridge` is `tui.snapshot`, plain text, and does not
start this app.
"""
from __future__ import annotations

import sys
from pathlib import Path

from textual.app import App

from . import jobs as jobsmod, tui
# The names other modules and the tests reach through here.
from .ui.components.confirm import Confirm, confirm_answer  # noqa: F401
from .ui.components.jobs_aware import JobsAware, dispatch_agent_verb  # noqa: F401
from .ui.components.read_only import ReadOnly  # noqa: F401
from .ui.components.text_prompt import TextPrompt, _LineBox, _TextBox  # noqa: F401
from .ui.keys import KEY_TOKENS, key_token  # noqa: F401
from .ui.pages.dashboard import Dashboard  # noqa: F401
from .ui.pages.task_checks import TaskChecks  # noqa: F401


class BridgeApp(App):
    """The Textual bridge (`ocp bridge --textual`, task #45): the dashboard, and
    from it an agent's chat page - see `Dashboard`, `chat_page.ChatPage`."""

    TITLE = "ocp bridge"
    CSS_PATH = Path(__file__).with_name("chat_page.tcss")

    def __init__(self) -> None:
        super().__init__()
        self.jobs = jobsmod.Jobs()

    def on_mount(self) -> None:
        from .ui.pages.home import HomePage
        self.push_screen(HomePage())


def run(once: bool = False) -> int:
    """`once`, or stdout not a tty (a script or a pipe): `tui.snapshot`, plain
    text, not a Textual app."""
    if once or not sys.stdout.isatty():
        print(tui.snapshot())
        return 0
    BridgeApp().run()
    return 0

