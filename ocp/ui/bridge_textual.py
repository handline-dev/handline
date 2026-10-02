"""ocp bridge: the Textual app the team is run from.

The app opens on the home page (`ui.pages.home.HomePage`), from which Enter opens an agent's chat page
(`chat_page.ChatPage`); the founder's tasks
are the home page's Tasks tab and a chat page's own (`components.tasks_view.TasksView`). Pages draw what the data functions in `tui.py`
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

import os
import sys
import time
from pathlib import Path

from ..daemon import devmode
from ..infra import service

from textual.app import App
from textual.screen import ModalScreen

from textual import events

from .. import actions
from . import jobs as jobsmod, stall, tui
from .clipboard_app import ClipboardApp
from .components.confirm import Confirm
from .components.dialog import Dialog
from .theme import follow_os_theme


CODE_CHECK_S = 3.0


class BridgeApp(ClipboardApp):
    """The Textual bridge: the home page, and from it an agent's chat page - see
    `HomePage`, `chat_page.ChatPage`."""

    TITLE = "ocp bridge"
    BINDINGS = [("f5", "restart", "restart")]
    CSS_PATH = Path(__file__).with_name("chat_page.tcss")

    def __init__(self) -> None:
        super().__init__()
        self.jobs = jobsmod.Jobs()
        self.restart_owed = False       # the code changed; waiting for a moment to ask
        self.restart_on_exit = False
        self._asking = False            # the restart question is on screen
        self._code_state: dict = {"fp": None, "warned": None, "seen": None}

    async def on_event(self, event) -> None:
        """While a box is over the page, the raw mouse and key events that reach the app, with what
        had focus and the mouse (a printable key is logged as "printable": what is typed is not logged)."""
        if isinstance(event, (events.MouseDown, events.MouseUp, events.Click, events.Key)) \
                and isinstance(self.screen, Dialog):
            what = (f"key {'printable' if event.is_printable else event.key}" if isinstance(event, events.Key)
                    else f"{type(event).__name__} at {event.screen_x},{event.screen_y}")
            stall.crumb(f"{type(self.screen).__name__}: {what} focus {self.focused!r} mouse {self.mouse_captured!r}")
        await super().on_event(event)

    def action_open_path(self, path: str) -> None:
        """A path in a message was clicked: open it in the default app."""
        ok, said = actions.open_path(path)
        self.notify(said, severity="information" if ok else "warning", timeout=3 if ok else 6)

    def on_mount(self) -> None:
        from .pages.home import HomePage
        follow_os_theme(self)
        self.title = f"ocp bridge · pid {stall.install(self)}"
        self.push_screen(HomePage())
        self.set_interval(CODE_CHECK_S, lambda: self.run_worker(
            self._check_code, thread=True, exclusive=True, group="code_check"))

    def _check_code(self) -> None:
        """With dev mode on, the daemon's own check: the code changed and still imports. The import
        check runs `ocp --help`, so this is a worker, not the UI thread. A restart found is owed
        until nothing on screen would be lost by asking (see `_idle`)."""
        try:
            self.restart_owed = devmode._dev_mode_should_restart(self._code_state, stall.crumb)
        except Exception as e:  # noqa: BLE001 - a tree mid-checkout must not take the bridge down
            stall.crumb(f"dev-mode check failed: {e}")
            return
        if self.restart_owed:
            self.call_from_thread(self._restart_if_idle)

    def _idle(self) -> bool:
        """No box is open and no page holds a draft or a send in flight - what `ChatPage.leave` asks about."""
        return not any(isinstance(sc, ModalScreen) or getattr(sc, "unsent", list)() for sc in self.screen_stack)

    def _restart_if_idle(self) -> None:
        if self._idle():
            self.ask_restart("ocp was updated. Restart the bridge now?", declined=self._decline_update)

    def _decline_update(self) -> None:
        """Stay on this version: this change is not asked about again, a newer one is."""
        self.restart_owed = False
        self._code_state["fp"] = self._code_state["seen"]

    def ask_restart(self, question: str, declined=lambda: None) -> None:
        """Ask once whether to restart the bridge; yes exits it and `run` execs the same entrypoint."""
        if self._asking:
            return
        self._asking = True

        def answered(yes: bool | None) -> None:
            self._asking = False
            if yes:
                self.restart_on_exit = True
                self.exit()
            else:
                declined()
        self.push_screen(Confirm(question, yes="Restart", no="Not now"), answered)

    def action_restart(self) -> None:
        lost = [w for sc in self.screen_stack for w in getattr(sc, "unsent", list)()]
        self.ask_restart("Restart the bridge now?" if not lost else "Restart the bridge now? That loses "
                         + " and ".join(lost) + ".")


def _one_bridge(ask=input, tty: bool | None = None) -> bool:
    """Another bridge is live: say which and since when, and ask to take over (close it, go on) or
    quit. True when this one may start."""
    pid = stall.running_pid()
    if pid is None or pid == os.getpid():
        return True
    if tty is None:
        tty = sys.stdin.isatty()
    since = stall.started_at()
    print(f"An ocp bridge is already running: pid {pid}"
          + (f", since {time.strftime('%a %b %d %H:%M', time.localtime(since))}" if since else "") + ".")
    if not tty:
        return False
    while True:
        try:
            choice = ask("[t] take over: close it and start here   [q] quit > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "q"
        if choice == "q":
            return False
        if choice == "t":
            if stall.close_bridge(pid):
                return True
            print(f"pid {pid} would not close; close that window yourself.")
            return False


def run(once: bool = False) -> int:
    """`once`, or stdout not a tty (a script or a pipe): `tui.snapshot`, plain
    text, not a Textual app."""
    if once or not sys.stdout.isatty():
        print(tui.snapshot())
        return 0
    if not _one_bridge():
        return 1
    try:                    # the lock is taken before the app starts: a bridge that lost the race says so
        held = stall._claim_pid_file(os.getpid())
    except OSError:
        held = True         # a pid file that cannot be written is no second bridge
    if not held:
        print("Another ocp bridge took the lock first.")
        return 1
    app = BridgeApp()
    app.run()
    if app.restart_on_exit:     # the terminal is restored by now: same entrypoint, same arguments
        exe = service.ocp_executable()
        os.execv(exe, [exe] + sys.argv[1:])
    return 0

