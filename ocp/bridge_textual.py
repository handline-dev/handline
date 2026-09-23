"""ocp bridge, ported to Textual (task #45).

The first screen ported: the dashboard (`tui.build`'s four panels - ACCOUNTS,
AGENTS, MESSAGES, STUCK - the same screen `tui.frame` draws for curses). It
draws from the same data functions curses does (`tui.build`, `tui.navigate`,
`tui.step`, `actions.can`, ...) - "it draws; it does not decide" (tui.py's own
rule) holds here too, so the two screens cannot disagree about what a row
means. What changes is the interaction layer: raw termios reads and writes
become Textual's event loop, and a key that acts no longer blocks it.

That is `jobs.py`, taken over from the unmerged `buddyron/bridge-working`
branch (task #16 / #1615) as a requirement of this port: `w`, `c`, `p`, `f`,
`v`, `t`, `m`, `e` and the MESSAGES panel's `a`/`e`/`x` all hand their verb to
`ocp.actrun` and return at once, so the dashboard keeps redrawing and reading
keys while an action is in flight, with a line saying what is running and for
how long. The agent detail page (chat-box, `buddyron/chat-box-is-the-composer`)
is the next screen; this one does not open it yet.
"""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from . import actions, db, jobs as jobsmod, tui

# Textual hands a key event as a symbolic name (`event.key`) plus, for a
# printable one, the character itself (`event.character`). tui.py's own key
# handling is byte-token driven (arrow keys as their raw escape sequences,
# Enter as "\r", ...) because that is what a raw terminal read produces - and
# reusing it, rather than re-deriving what every key does a second time, is
# the whole point of porting the interaction layer and not the logic under
# it. This is the one place that translates between the two.
KEY_TOKENS = {
    "enter": "\r", "return": "\r",
    "escape": "\033",
    "tab": "\t", "shift+tab": "\033[Z",
    "up": "\033[A", "down": "\033[B", "left": "\033[D", "right": "\033[C",
    "home": "\033[H", "end": "\033[F",
    "pageup": "\033[5~", "pagedown": "\033[6~",
    "backspace": "\x7f", "delete": "\033[3~",
    "ctrl+c": "\x03", "ctrl+g": "\x07", "ctrl+u": "\x15", "ctrl+y": "\x19",
    "space": " ",
    "alt+enter": "\033\r",
}


def key_token(event: events.Key) -> str | None:
    """The byte token tui.py's key tables and `Composer.feed` expect, or None
    for a key with nothing to say (a bare modifier, a function key nothing
    binds)."""
    if event.key in KEY_TOKENS:
        return KEY_TOKENS[event.key]
    if event.character and event.character.isprintable():
        return event.character
    return None


_LEVEL_COLOR = {"normal": tui.DIM, "slow": tui.YEL, "danger": tui.RED}


def _job_lines_ansi(jobs: jobsmod.Jobs, width: int) -> list[str]:
    return [tui.fit(f"{_LEVEL_COLOR.get(jl.level, '')}{jl.text}{tui.R}", width)
            for jl in jobs.lines()]


class Confirm(ModalScreen[bool]):
    """Yes or no, over the screen under it - only a typed y confirms, the same
    rule `tui._confirm` uses, so a destructive key asks the same question
    however it is drawn."""

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        yield Static(id="confirm")

    def on_mount(self) -> None:
        w = max(20, self.app.size.width)
        text = f"{self.question}? " + "y / n"
        self.query_one("#confirm", Static).update(
            Text.from_ansi(tui.fit(f"{tui.YEL}{text}{tui.R}", w)))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        ch = event.character or ""
        self.dismiss(ch in ("y", "Y"))


class TextPrompt(ModalScreen[str | None]):
    """One line (or, `multi`, several) read with the screen still up, through
    the same `Composer` the curses composer uses - so word-left, paste, and
    backspace behave exactly as they always have. None if the human backs out."""

    def __init__(self, prompt: str, multi: bool = False) -> None:
        super().__init__()
        self.prompt, self.box = prompt, tui.Composer(multi=multi)

    def compose(self) -> ComposeResult:
        yield Static(id="prompt")

    def on_mount(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        w, h = max(20, self.app.size.width), max(3, self.app.size.height - 2)
        rows = self.box.shown(self.prompt, w, h)
        self.query_one("#prompt", Static).update(
            Text.from_ansi("\n".join(tui.fit(r, w) for r in rows)))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        k = key_token(event)
        if k is None:
            return
        done = self.box.feed(k)
        if done == "send":
            self.dismiss(self.box.text)
        elif done == "cancel":
            self.dismiss(None)
        else:
            self._redraw()

    def on_paste(self, event: events.Paste) -> None:
        # Textual delivers a paste as its own event, not as keys - `Composer`
        # still needs it wrapped in its own bracketed-paste markers to tell it
        # from typing (a pasted "y" must not confirm, a pasted newline must
        # not send).
        event.stop()
        self.box.feed(tui.Composer.START + event.text + tui.Composer.END)
        self._redraw()


class Dashboard(Screen):
    """The main screen: `tui.build`'s four panels, tab- and arrow-navigable,
    with the row actions dispatched through `jobs.Jobs` rather than run in
    place."""

    REFRESH_S = 2.0        # matches curses `run`'s own default interval
    BUSY_REFRESH_S = 0.25  # while a job is running, quick enough to notice it end

    def __init__(self) -> None:
        super().__init__()
        self.panel = tui.AGENTS
        self.sel: dict = {}
        self.flash = ""
        self.jobs = jobsmod.Jobs()
        tui._enter(self.panel, self.sel)

    def compose(self) -> ComposeResult:
        yield Static(id="canvas")

    def on_mount(self) -> None:
        self._retime()
        self._redraw()

    def _retime(self) -> None:
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self._timer = self.set_interval(
            self.BUSY_REFRESH_S if self.jobs.running else self.REFRESH_S, self._tick)

    def _tick(self) -> None:
        said = self.jobs.reap()
        if said:
            self.flash = "  ·  ".join(said)
            self._retime()
        self._redraw()

    def _redraw(self) -> None:
        size = self.size
        width, height = max(20, size.width), max(6, size.height)
        job_lines = _job_lines_ansi(self.jobs, width)
        text = tui.frame(width, max(1, height - len(job_lines)), self.sel, self.panel, self.flash)
        text = text.replace(tui.CLEAR_EOL, "")
        if job_lines:
            lines = text.split("\n")
            text = "\n".join(lines[:-2] + job_lines + lines[-2:])
        self.query_one("#canvas", Static).update(Text.from_ansi(text))

    def on_key(self, event: events.Key) -> None:
        k = key_token(event)
        if k is None:
            return
        event.stop()
        if k in ("q", "Q", "\x03"):
            self.app.exit()
            return
        moved = tui.navigate(self.panel, self.sel, k)
        if moved is not None:
            self.panel = moved
            self._redraw()
            return
        self.flash = ""
        # `_act` can open a modal (`push_screen_wait`), which Textual only
        # allows from inside a worker - not straight from an event handler.
        self.run_worker(self._act_and_redraw(k), exclusive=True, group="act")

    async def _act_and_redraw(self, key: str) -> None:
        await self._act(key)
        self._redraw()

    # ---------------------------------------------------------- row actions

    async def _act(self, key: str) -> None:
        """One keypress on the selected row - `tui._act_on`'s decisions
        (which key does what, what asks first, what needs a prompt), `jobs.Jobs`
        for running it. Kept deliberately smaller than `_act_on` for now:
        AGENTS, STUCK and MESSAGES are ported; ACCOUNTS (login, add, cap,
        remove) and opening a row into its own page are not yet - `ocp
        account`/`ocp hr` cover the former from the command line meanwhile."""
        panel, sel = self.panel, self.sel
        keys = tui._keys_of(panel)
        target = sel.get(panel)
        if not keys or target not in keys:
            return
        con = db.connect()
        try:
            if panel in (tui.AGENTS, tui.STUCK):
                await self._act_agent(con, str(target), key)
            elif panel == tui.MESSAGES:
                await self._act_message(con, target, key)
        finally:
            con.close()

    async def _act_agent(self, con, name: str, key: str) -> None:
        label = dict(tui.AGENT_KEYS).get(key)
        if not label or label == "open":
            return          # "open" is the next screen (the agent's own page)
        allowed, why = actions.can(con, name, label)
        if not allowed:
            self.flash = why
            return
        if label in ("message", "editor"):
            # "editor" is curses's $EDITOR spawn - one more way to write the same
            # message, not a different action (`actions.can` has no case for it
            # either). The modal composer stands in for both here.
            body = await self.app.push_screen_wait(TextPrompt(f"message {name}:", multi=True))
            if body is None:
                self.flash = "nothing sent: e or m again to retry"
                return
            self.flash = self._start("message", name, f"messaging {name}", (name, body))
            return
        if label == "terminal" and tui._needs_a_terminal(con, name):
            repo = await self.app.push_screen_wait(
                TextPrompt(f"{name} has nowhere to run - repo to start it in:"))
            if not repo:
                self.flash = "not started"
                return
            self.flash = self._start("terminal", name, f"starting {name}", (name, repo.strip()))
            return
        if label == "move":
            dest = await self.app.push_screen_wait(TextPrompt(f"move {name} to which account:"))
            if not dest:
                self.flash = "not moved"
                return
            dest = dest.strip()
            if not await self.app.push_screen_wait(
                    Confirm(f"move {name} to {dest} - records its handoff, ends its session, "
                            f"and makes {dest} its preferred account")):
                self.flash = "left alone"
                return
            self.flash = self._start("move", name, f"moving {name} to {dest}", (name, dest))
            return
        if tui._needs_confirm(con, name, label):
            if not await self.app.push_screen_wait(Confirm(f"{label} {name}")):
                self.flash = "left alone"
                return
            ok, why = tui._confirmed_can(con, name, label)
            if not ok:
                self.flash = why
                return
        self.flash = self._start(label, name, f"{jobsmod.GERUND.get(label, label)} {name}", (name,))

    async def _act_message(self, con, target, key: str) -> None:
        # `target` is the row's stable key, not its id: a folded row's id is
        # its latest thing, which can have changed since the row was
        # selected, so it is looked up fresh rather than trusted from then
        # (curses's own `_act_on` does the same).
        row_m = next((m for m in actions.messages(con) if m["key"] == target), None)
        if row_m is None:
            self.flash = f"{target} is gone"
            return
        rid = row_m["id"]
        row = con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
        if not row:
            self.flash = f"#{rid} is gone"
            return
        if key == "x":
            self.flash = self._start("dismiss", f"#{rid}", f"dismissing #{rid}", (rid,),
                                     {"me": actions.HUMAN})
            return
        if key in ("a", "e"):
            text = await self.app.push_screen_wait(TextPrompt(f"answer #{rid}:", multi=key == "e"))
            if not text:
                self.flash = "nothing sent: a or e again to retry"
                return
            self.flash = self._start("answer", f"#{rid}", f"answering #{rid}", (rid, text))

    def _start(self, verb: str, target: str, what: str, args: tuple, kwargs: dict | None = None) -> str:
        said = self.jobs.start(verb, target, what, args, kwargs)
        self._retime()
        return said


class BridgeApp(App):
    """The Textual bridge (`ocp bridge --textual`, task #45). One screen so
    far: the dashboard - see `Dashboard`."""

    TITLE = "ocp bridge"

    def on_mount(self) -> None:
        self.push_screen(Dashboard())


def run() -> int:
    BridgeApp().run()
    return 0
