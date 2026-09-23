"""ocp bridge, ported to Textual (task #45).

Two screens so far: the dashboard (`tui.build`'s four panels - ACCOUNTS,
AGENTS, MESSAGES, STUCK - the same screen `tui.frame` draws for curses) and an
agent's own page (its conversation and the box chat-box put there). Both draw
from the same data functions curses does (`tui.build`, `tui.navigate`,
`tui._agent_page`, `tui.lines_of`, `actions.can`, ...) - "it draws; it does
not decide" (tui.py's own rule) holds here too, so the two front ends cannot
disagree about what a row or a page means. What changes is the interaction
layer: raw termios reads and writes become Textual's event loop, and a key
that acts no longer blocks it.

That is `jobs.py`, taken over from the unmerged `buddyron/bridge-working`
branch (task #16 / #1615) as a requirement of this port: every verb a key or a
slash command can run hands its verb to `ocp.actrun` and returns at once, so a
screen keeps redrawing and reading keys while an action is in flight, with a
line saying what is running and for how long.

The agent's page's box is `buddyron/chat-box-is-the-composer` (task #16),
taken over the same way: typing lands in it, Enter sends, and a message
starting with '/' is a command - `/knows`, `/done`, `/cancel <id>`, and every
`AGENT_KEYS` verb the row keys still bind on the dashboard.

ACCOUNTS' login and add hand the whole terminal to another program - a human
typing a login code into claude's own screen, same as curses's `_suspend` -
through Textual's own `App.suspend()` rather than `jobs.Jobs`: it is
interactive, not something to run and report on later.

Not yet ported: opening an ACCOUNTS row into a page of its own beyond a
read-only summary, and ACCOUNTS' "next" tick-mark bookkeeping the dashboard
already draws from `tui.build` regardless.
"""
from __future__ import annotations

import subprocess

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from . import accounts, actions, db, jobs as jobsmod, load as loadmod, orca, tui

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


class JobsAware:
    """What a screen needs to run a verb through `jobs.Jobs` and keep showing
    it: one `Jobs` per app (`self.app.jobs`), shared across screens so an
    action started from the dashboard is still there, still running, if the
    human moves to an agent's own page while it goes."""

    REFRESH_S = 2.0        # matches curses `run`'s own default interval
    BUSY_REFRESH_S = 0.25  # while a job is running, quick enough to notice it end
    flash = ""

    @property
    def jobs(self) -> jobsmod.Jobs:
        return self.app.jobs

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

    def _start(self, verb: str, target: str, what: str, args: tuple, kwargs: dict | None = None,
               on_result=None) -> str:
        said = self.jobs.start(verb, target, what, args, kwargs, on_result)
        self._retime()
        return said


async def dispatch_agent_verb(screen: JobsAware, con, name: str, label: str) -> None:
    """Run one `AGENT_KEYS` verb against `name`, whichever screen it was
    pressed or typed on - `tui._dispatch_agent_verb`'s decisions (what asks
    first, what needs a prompt), a modal and `jobs.Jobs` in place of curses's
    blocking prompt and in-place call."""
    allowed, why = actions.can(con, name, label)
    if not allowed:
        screen.flash = why
        return
    if label in ("message", "editor"):
        # "editor" is curses's $EDITOR spawn - one more way to write the same
        # message, not a different action (`actions.can` has no case for it
        # either). The modal composer stands in for both here.
        body = await screen.app.push_screen_wait(TextPrompt(f"message {name}:", multi=True))
        if body is None:
            screen.flash = "nothing sent: try again to retry"
            return
        screen.flash = screen._start("message", name, f"messaging {name}", (name, body))
        return
    if label == "terminal" and tui._needs_a_terminal(con, name):
        repo = await screen.app.push_screen_wait(
            TextPrompt(f"{name} has nowhere to run - repo to start it in:"))
        if not repo:
            screen.flash = "not started"
            return
        screen.flash = screen._start("terminal", name, f"starting {name}", (name, repo.strip()))
        return
    if label == "move":
        dest = await screen.app.push_screen_wait(TextPrompt(f"move {name} to which account:"))
        if not dest:
            screen.flash = "not moved"
            return
        dest = dest.strip()
        if not await screen.app.push_screen_wait(
                Confirm(f"move {name} to {dest} - records its handoff, ends its session, "
                        f"and makes {dest} its preferred account")):
            screen.flash = "left alone"
            return
        screen.flash = screen._start("move", name, f"moving {name} to {dest}", (name, dest))
        return
    if tui._needs_confirm(con, name, label):
        if not await screen.app.push_screen_wait(Confirm(f"{label} {name}")):
            screen.flash = "left alone"
            return
        ok, why = tui._confirmed_can(con, name, label)
        if not ok:
            screen.flash = why
            return
    screen.flash = screen._start(label, name, f"{jobsmod.GERUND.get(label, label)} {name}", (name,))


class Dashboard(JobsAware, Screen):
    """The main screen: `tui.build`'s four panels, tab- and arrow-navigable,
    with the row actions dispatched through `jobs.Jobs` rather than run in
    place."""

    def __init__(self) -> None:
        super().__init__()
        self.panel = tui.AGENTS
        self.sel: dict = {}
        self.flash = ""
        tui._enter(self.panel, self.sel)

    def compose(self) -> ComposeResult:
        yield Static(id="canvas")

    def on_mount(self) -> None:
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
        for running it (or, for ACCOUNTS' login/add, `App.suspend()`: those
        hand the whole terminal to another program)."""
        panel, sel = self.panel, self.sel
        if panel == tui.ACCOUNTS and key == "a":
            # Adding needs no row under the cursor - the first account is
            # added to an empty panel, so this comes before the "no rows" guard.
            await self._account_add()
            return
        keys = tui._keys_of(panel)
        target = sel.get(panel)
        if not keys or target not in keys:
            return
        con = db.connect()
        try:
            if panel == tui.ACCOUNTS:
                await self._act_account(con, str(target), key)
            elif panel in (tui.AGENTS, tui.STUCK):
                await self._act_agent(con, str(target), key)
            elif panel == tui.MESSAGES:
                await self._act_message(con, target, key)
        finally:
            con.close()

    async def _act_account(self, con, name: str, key: str) -> None:
        if key in ("\r", "\n"):
            sections = actions.account_detail(con, name, accounts.logged_in)
            await self.app.push_screen_wait(ReadOnly(f"account {name}", sections))
            return
        if key == "l":
            await self._account_login(name)
            return
        if key == "c":
            await self._account_cap(name)
            return
        if key == "x":
            await self._account_remove(name)
            return

    def _run_suspended(self, argv: list[str], env: dict) -> tuple[int | None, str]:
        """`tui._suspend`'s own contract, through `App.suspend()`: hand the
        whole terminal to `argv` (a login is a human typing a code into
        claude's own screen - nothing about that can happen behind Textual's
        screen), then take it back."""
        try:
            with self.app.suspend():
                r = subprocess.run(argv, env=env)
                return r.returncode, f"{argv[0]} exited {r.returncode}"
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"could not run {argv[0]}: {e}"

    async def _account_add(self) -> None:
        name = await self.app.push_screen_wait(TextPrompt("new account name:"))
        if not name or not name.strip():
            self.flash = "not added"
            return
        name = name.strip()
        ok, msg = accounts.add_account(name, lambda argv, env: self._run_suspended(argv, env)[0])
        self.flash = msg
        if not ok or not orca.available():
            return
        # 156 item 1c: the CLI and this screen share the one trusting sequence.
        declared, skipped = actions.eligible_worktrees()
        if not declared:
            return
        listed = ", ".join(f"{a} ({w})" for a, w in declared)
        prompt = f"trust {listed}? y/N" + (f" (skipping {len(skipped)} outside declared roots)"
                                           if skipped else "")
        yn = await self.app.push_screen_wait(TextPrompt(prompt))
        if (yn or "").strip().lower() in ("y", "yes"):
            self.flash = msg + " · " + "; ".join(actions.trust_worktrees(name, declared))

    async def _account_login(self, name: str) -> None:
        line, argv, env = actions.login_argv(name)
        if not argv:
            self.flash = line
            return
        _, self.flash = self._run_suspended(argv, env)

    async def _account_cap(self, name: str) -> None:
        text = await self.app.push_screen_wait(
            TextPrompt(f"most agents mid-turn on {name} at once [{loadmod.cap_of(name)}]:"))
        if not text or not text.strip():
            self.flash = "cap unchanged"
            return
        try:
            loadmod.set_cap(name, int(text.strip()))
        except ValueError:
            self.flash = "a cap is a whole number, at least 1"
            return
        self.flash = f"{name}: at most {text.strip()} agents mid-turn"

    async def _account_remove(self, name: str) -> None:
        # `remove_plan` first, the same order the CLI and curses use: an
        # account in use is refused before anyone is asked to type its name,
        # not after - `remove_account` checks it again post-confirm, since
        # that wait is on a person and an agent can start in the meantime.
        con = db.connect()
        try:
            ok, why = accounts.remove_plan(name, con)
        finally:
            con.close()
        if not ok:
            self.flash = why
            return
        typed = await self.app.push_screen_wait(
            TextPrompt(f"remove '{name}'? type its name to confirm:"))
        confirmed = typed == name
        con = db.connect()
        try:
            ok, msg = accounts.remove_account(
                name, lambda: confirmed,
                lambda argv, env: self._run_suspended(argv, env)[0], con)
        finally:
            con.close()
        self.flash = msg

    async def _act_agent(self, con, name: str, key: str) -> None:
        if key in ("\r", "\n"):    # "↵" in AGENT_KEYS is a display label, never a lookup key
            self.app.push_screen(AgentDetail(name))
            return
        label = dict(tui.AGENT_KEYS).get(key)
        if not label or label == "open":
            return
        await dispatch_agent_verb(self, con, name, label)

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
        if key in ("\r", "\n"):
            actions.opened_by_human(con, rid)
            place = actions.message_place(con, rid)
            if place is not None:
                d = tui._agent_page(con, place[0], tui.AGENTS)
                if actions.CHAT_TAB in tui._tab_stems(d):    # not a page that could not be built
                    self.app.push_screen(AgentDetail(place[0]))
                    return
            detail = actions.message_detail(con, rid)
            if detail is None:
                self.flash = f"#{rid} is gone"
                return
            title, sections = detail
            await self.app.push_screen_wait(ReadOnly(title, sections))
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


class ReadOnly(ModalScreen[None]):
    """`/knows`, `/done`, and an ACCOUNTS row opened: a page of text over
    whatever was open, in `page()`'s own shape - a title per section, then its
    rows. A single flat list is one section with no heading. Any key closes
    it - there is nothing on it to act on."""

    def __init__(self, title: str, sections: list) -> None:
        super().__init__()
        self.title_ = title
        if sections and not isinstance(sections[0], tuple):
            sections = [("", sections)]
        self.sections = [(str(h), [str(r) for r in rows]) for h, rows in sections]

    def compose(self) -> ComposeResult:
        yield Static(id="readonly")

    def on_mount(self) -> None:
        w = max(20, self.app.size.width)
        lines = [tui.fit(f"{tui.BOLD}{self.title_}{tui.R}", w), ""]
        for heading, rows in self.sections:
            if heading:
                lines.append(tui.fit(f"{tui.DIM}{heading}{tui.R}", w))
            lines += [tui.fit(r, w) for r in rows] + [""]
        self.query_one("#readonly", Static).update(Text.from_ansi("\n".join(lines)))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)


class AgentDraft(tui.Composer):
    """What is typed in an agent's own box (chat-box, `buddyron/chat-box-is-
    the-composer`, task #16 - taken over as a requirement here). One per
    agent, kept in `_DRAFTS` for the session: it is not part of the screen,
    which is rebuilt from the database every tick, and leaving the page keeps
    it."""

    def __init__(self) -> None:
        super().__init__(multi=True)
        self.matches: list[str] = []
        self.sel, self.picked = 0, False
        self.cleared = ""


_DRAFTS: dict[str, AgentDraft] = {}


def _draft(agent: str) -> AgentDraft:
    return _DRAFTS.setdefault(agent, AgentDraft())


def _sync_matches(box: AgentDraft, cmds: list[tuple[str, str]]) -> list[str]:
    """The commands the box's text names. The highlight survives only typing
    that leaves the same list - clamping alone would retarget it to another
    command."""
    new = ([n for n, _ in cmds if n.startswith(box.text[1:])]
           if box.text.startswith("/") else [])
    if new != box.matches:
        box.sel, box.picked = 0, False
    box.matches = new
    return new


def _resolved(box: AgentDraft) -> str:
    """What Enter sends: a command when exactly one is named or the human
    picked one, otherwise the text as typed - a real message may start with
    '/'."""
    if len(box.matches) == 1 or (box.matches and box.picked):
        return f"/{box.matches[box.sel]}"
    return box.text.strip("\n")


def _conversation_lines(con, name: str, width: int) -> list[str]:
    """The conversation, formatted and wrapped exactly as curses draws it
    (`tui._agent_page`, `tui.lines_of` - the tested turn-by-turn layout).
    `hint=False`: this screen's own box already says where a message goes."""
    detail = tui._agent_page(con, name, tui.AGENTS, hint=False)
    return tui.lines_of(detail, width)


class AgentDetail(JobsAware, Screen):
    """An agent's own page: its conversation, and the box chat-box put there -
    typing lands in it, Enter sends, '/' opens its commands in place. Escape
    goes back to the dashboard, keeping the draft."""

    def __init__(self, agent: str) -> None:
        super().__init__()
        self.agent, self.top, self.flash = agent, 0, ""
        self.commands: list[tuple[str, str]] = []

    @property
    def box(self) -> AgentDraft:
        return _draft(self.agent)

    def compose(self) -> ComposeResult:
        yield Static(id="canvas")

    def on_mount(self) -> None:
        self._retime()
        self._redraw()

    def _room(self, height: int) -> int:
        # title + blank, two rules, the box (at least one row), flash, keys.
        return max(1, height - 8)

    def _redraw(self) -> None:
        size = self.size
        width, height = max(20, size.width), max(10, size.height)
        con = db.connect()
        try:
            body = _conversation_lines(con, self.agent, width)
            cmds = tui.agent_commands(con, self.agent)
        finally:
            con.close()
        self.commands = cmds
        job_lines = _job_lines_ansi(self.jobs, width)
        room = self._room(height) - len(job_lines)
        top = max(0, min(self.top, max(0, len(body) - room)))
        self.top = top
        shown = body[top:top + room]
        while len(shown) < room:
            shown.append("")
        rule = f"{tui.DIM}{'─' * width}{tui.R}"
        box_text = self.box.shown(f"{tui.CYA}{tui.BOLD}❯{tui.R}", width, 3)
        _sync_matches(self.box, cmds)
        if self.box.matches:
            box_text = box_text + [tui.fit(f"{tui.DIM}  " + "  ".join(
                (f"{tui.R}▸{tui.DIM}" if i == self.box.sel else " ") + n
                for i, n in enumerate(self.box.matches)) + tui.R, width)]
        keys = "esc back · ↑↓ scroll · / commands · ↵ send"
        lines = ([tui.fit(f"{tui.BOLD}agent {self.agent}{tui.R}", width), ""]
                 + shown + [rule] + box_text + [rule] + job_lines
                 + [tui.fit(self.flash, width) if self.flash else "",
                    tui.fit(f"{tui.DIM}{keys}{tui.R}", width)])
        text = "\n".join(line for line in lines[:height])
        self.query_one("#canvas", Static).update(Text.from_ansi(text))

    def on_key(self, event: events.Key) -> None:
        k = key_token(event)
        if k is None:
            return
        event.stop()
        box = self.box
        if k in ("\033[A", "\033[B") and box.matches:
            box.sel = max(0, min(len(box.matches) - 1, box.sel + (1 if k == "\033[B" else -1)))
            box.picked = True
            self._redraw()
            return
        if k in ("\033[A", "\033[B"):
            self.top = max(0, self.top + (1 if k == "\033[B" else -1))
            self._redraw()
            return
        if k in ("\033[5~", "\033[6~"):
            room = self._room(max(10, self.size.height))
            self.top = max(0, self.top + room * (1 if k == "\033[6~" else -1))
            self._redraw()
            return
        if k in ("\033[H", "\033[F") and not box.text:
            self.top = 0 if k == "\033[H" else 1 << 30     # clamped in `_redraw`
            self._redraw()
            return
        if k == "\x15":         # ctrl+u: clear the draft, kept for ctrl+y
            if box.text:
                box.cleared = box.text
                box.text, box.pos, box.typed = "", 0, ""
            self._redraw()
            return
        if k == "\x19":         # ctrl+y: bring the cleared draft back
            if box.cleared:
                box.text = box.text[:box.pos] + box.cleared + box.text[box.pos:]
                box.pos += len(box.cleared)
                box.cleared = ""
            self._redraw()
            return
        done = box.feed(k)
        _sync_matches(box, self.commands)
        if done == "cancel":
            self.app.pop_screen()
            return
        if done == "send":
            body = _resolved(box)
            if body.strip():
                self.run_worker(self._deliver(body), exclusive=True, group="act")
            return
        self._redraw()

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        box = self.box
        box.feed(tui.Composer.START + event.text + tui.Composer.END)
        _sync_matches(box, self.commands)
        self._redraw()

    async def _deliver(self, body: str) -> None:
        """Send `body`, or run it when it names one of `self.commands` - a
        literal message that happens to type a real command's name runs it,
        the same tradeoff any chat command syntax makes. Dispatched through
        `jobs.Jobs` either way, so the box is free the moment it is handed
        off, not once an answer comes back.

        The box is cleared only once `chat_reply` is actually on its way, not
        on a refusal `_start` already caught (a busy target, `MAX_JOBS`), and
        is restored if the job's own answer turns out to be a refusal - a
        team routing decision, say - since typing it twice is worse than a
        line saying it did not go."""
        box = self.box
        con = db.connect()
        try:
            known = dict(self.commands)
            if body.startswith("/") and body[1:] in known:
                await self._run_command(con, body[1:], known[body[1:]])
                box.text, box.pos, box.typed, box.cleared = "", 0, "", ""
            else:
                agent = self.agent

                def restore_if_refused(ok: bool, _text: str) -> None:
                    if not ok and not self.box.text:
                        self.box.text, self.box.pos = body, len(body)

                refused = self._start("chat_reply", agent, f"messaging {agent}", (agent, body),
                                      on_result=restore_if_refused)
                self.flash = refused
                if not refused:
                    box.text, box.pos, box.typed, box.cleared = "", 0, "", ""
        finally:
            con.close()
        self._redraw()

    async def _run_command(self, con, cmd: str, kind: str) -> None:
        name = self.agent
        if kind == "info":
            _, self.flash = tui._run_info_command(con, {}, name, cmd)
            return
        if kind == "page":
            rows = actions.agent_knows(con, name) if cmd == "knows" else actions.agent_done(con, name)
            await self.app.push_screen_wait(ReadOnly(f"{cmd} · {name}", rows))
            return
        if kind == "cancel":
            await self._cancel(con, name, cmd.partition(" ")[2])
            return
        await dispatch_agent_verb(self, con, name, cmd)

    async def _cancel(self, con, name: str, arg: str) -> None:
        waiting = actions.waiting_ids(con, name)
        if not arg:
            if len(waiting) != 1:
                self.flash = ("nothing is waiting for it" if not waiting else
                              "several are waiting - " + " · ".join(f"/cancel {r}" for r in waiting))
                return
            rid = waiting[0]
        else:
            rid = int(arg)
            if rid not in waiting:
                self.flash = f"#{rid} is not waiting for {name}"
                return
        ok, plan = actions.cancel_plan(con, rid)
        if not ok:
            self.flash = plan
            return
        if not await self.app.push_screen_wait(Confirm(plan)):
            self.flash = "left alone"
            return
        self.flash = self._start("cancel", f"#{rid}", f"cancelling #{rid}", (rid,))


class BridgeApp(App):
    """The Textual bridge (`ocp bridge --textual`, task #45). Two screens so
    far: the dashboard and an agent's own page - see `Dashboard`, `AgentDetail`."""

    TITLE = "ocp bridge"

    def __init__(self) -> None:
        super().__init__()
        self.jobs = jobsmod.Jobs()

    def on_mount(self) -> None:
        self.push_screen(Dashboard())


def run() -> int:
    BridgeApp().run()
    return 0
