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

`?` on an AGENTS/STUCK row goes through the same `tui.agent_help_line` curses
uses, and a row opened read-only (`/knows`, `/done`, ACCOUNTS) is a `ReadOnly`
page of Textual widgets. ACCOUNTS' "next" tick-mark needs
nothing of its own: it is computed inside `tui.build`, which `tui.frame`
already calls unconditionally.

Not yet ported: opening an ACCOUNTS row into a page of its own with actions
on it, beyond the read-only summary above.
"""
from __future__ import annotations

import subprocess
import sys

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen, Screen
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Footer, Input, Static, TabbedContent, TabPane, TextArea

from . import accounts, actions, db, jobs as jobsmod, load as loadmod, orca, textsafe, tui

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
        yield Static(Text(f"{self.question}? y / n", style="yellow"))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        ch = event.character or ""
        self.dismiss(ch in ("y", "Y"))


class _LineBox(Input):
    """A one-line box that keeps a multi-line paste as one line: each line
    break is a space, where Input alone would keep only the first line. A paste
    goes in as `textsafe.drawable` text: a widget draws a control character raw."""

    def on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        self.insert_text_at_cursor(" ".join(textsafe.drawable(event.text)))


class _TextBox(TextArea):
    """A several-line box: Enter sends, Alt/Shift+Enter or Ctrl+J is a new line.
    A paste goes in as `textsafe.drawable` text: a widget draws a control
    character raw."""

    class Send(Message):
        pass

    def on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        self.insert("\n".join(textsafe.drawable(event.text, tabs=None)))

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Send())
            return
        if event.key in ("alt+enter", "shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


class TextPrompt(ModalScreen[str | None]):
    """One line (or, `multi`, several) read with the screen still up, in a
    Textual `Input` or `TextArea`. Enter sends and Esc backs out (None); a paste
    is kept whole, minus anything a terminal would obey."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"),
                Binding("ctrl+c", "dismiss(None)", "cancel", show=False)]
    DEFAULT_CSS = """
    TextPrompt #prompt { dock: top; height: auto; }
    """

    def __init__(self, prompt: str, multi: bool = False) -> None:
        super().__init__()
        self.prompt, self.multi = prompt, multi

    def compose(self) -> ComposeResult:
        yield Static(Text(self.prompt, style="bold"), id="prompt")
        yield _TextBox(id="box") if self.multi else _LineBox(id="box")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#box").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def on__text_box_send(self, event: "_TextBox.Send") -> None:
        event.stop()
        self.dismiss(self.query_one("#box", TextArea).text)


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

    DEFAULT_CSS = """
    Dashboard #header { height: auto; }
    Dashboard #accounts-box { height: auto; max-height: 40%; border: round $panel; }
    Dashboard #accounts-box.focused { border: round cyan; }
    Dashboard #accounts { height: auto; }
    Dashboard #acct-notes, Dashboard #agent-notes, Dashboard #message-notes {
        height: auto; padding: 0 1; }
    Dashboard #messages-box { height: auto; max-height: 40%; border: round $panel; }
    Dashboard #messages-box.focused { border: round cyan; }
    Dashboard #messages { height: auto; }
    Dashboard #agents-box { height: auto; max-height: 50%; border: round $panel; }
    Dashboard #agents-box.focused { border: round cyan; }
    Dashboard #agents { height: auto; }
    Dashboard #rest { height: 1fr; }
    Dashboard #foot { height: auto; }
    """
    _ACCT_COLS = [k for k in tui.ACCT_W if k != "name"]

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Vertical(id="accounts-box") as box:
            box.border_title = "ACCOUNTS"
            yield DataTable(id="accounts", cursor_type="row", show_cursor=False)
            yield Static(id="acct-notes")
        with Vertical(id="agents-box") as box:
            box.border_title = "AGENTS"
            yield DataTable(id="agents", cursor_type="row", show_cursor=False)
            yield Static(id="agent-notes")
        with Vertical(id="messages-box"):
            yield DataTable(id="messages", cursor_type="row", show_cursor=False)
            yield Static(id="message-notes")
        yield Static(id="rest")
        yield Static(id="foot")

    def on_mount(self) -> None:
        table = self.query_one("#accounts", DataTable)
        table.can_focus = False
        table.add_columns("account", *(tui.ACCT_HEAD[k] for k in self._ACCT_COLS), "")
        agents_table = self.query_one("#agents", DataTable)
        agents_table.can_focus = False
        agents_table.add_columns("agent", "acct", "kind", "state", "ctx", "in", "")
        messages_table = self.query_one("#messages", DataTable)
        messages_table.can_focus = False
        messages_table.add_columns("", "", "#", "from", "kind", "message")
        self._retime()
        self._redraw()

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        size = self.size
        width, height = max(20, size.width), max(6, size.height)
        header, panels, footer_keys = tui.build(width, self.sel, self.panel,
                                                wait_budget=height - 4 - tui.PANEL_MIN)
        self.query_one("#header", Static).update(Text.from_ansi("\n".join(header)))
        self._draw_accounts()
        self._draw_agents()
        self._draw_messages()
        rest = panels[3:]
        room = max(1, self.query_one("#rest").size.height)
        limits, hidden = tui._layout([0, 0, 0] + [len(p.rows) for p in rest], room)
        body: list[str] = []
        for p, lim in zip(rest, limits[3:]):
            if lim != 0:
                body += p.draw(limit=lim)
        self.query_one("#rest", Static).update(Text.from_ansi("\n".join(body)))
        # task #72: `b` is a Textual-only global (tui.py's shared keybar
        # doesn't know it), so the hint is appended here rather than in tui.keybar.
        keys = tui.keybar(self.panel, width, hidden, footer_keys)
        hint = f" {tui.DIM}·{tui.R} {tui.BOLD}b{tui.R} {tui.DIM}checks{tui.R}"
        if tui.vlen(keys + hint) <= width:
            keys += hint
        foot = _job_lines_ansi(self.jobs, width)
        foot.append(tui.fit(self.flash, width) if self.flash else "")
        foot.append(keys)
        self.query_one("#foot", Static).update(Text.from_ansi("\n".join(foot)))

    def _draw_accounts(self) -> None:
        con = db.connect()
        try:
            rows, notes = tui.accounts_data(con)
        finally:
            con.close()
        table = self.query_one("#accounts", DataTable)
        table.clear()
        for r in rows:
            table.add_row(Text.from_ansi(f"{tui.BOLD}{textsafe.oneline(r.name)}{tui.R}"),
                          *(Text.from_ansi(r.cells[k]) for k in self._ACCT_COLS),
                          Text.from_ansi(r.mark + r.next), key=r.name)
        self._sync_cursor(table, "#accounts-box", tui.ACCOUNTS, [r.name for r in rows])
        self._notes("#acct-notes", notes)

    def _sync_cursor(self, table: DataTable, box: str, panel: int, names: list) -> None:
        """A panel's table is display only: its row cursor is the dashboard's own
        selection, and is drawn only while that panel is the one the keys act on."""
        focused = self.panel == panel
        table.show_cursor = focused
        if focused and self.sel.get(panel) in names:
            table.move_cursor(row=names.index(self.sel[panel]))
        self.query_one(box).set_class(focused, "focused")

    def _notes(self, widget: str, notes: list[str]) -> None:
        w = self.query_one(widget, Static)
        w.update(Text.from_ansi("\n".join(f"{tui.DIM}{n}{tui.R}" for n in notes)))
        w.display = bool(notes)

    def _draw_messages(self) -> None:
        con = db.connect()
        try:
            title, rows, notes = tui.messages_data(con)
        finally:
            con.close()
        self.query_one("#messages-box").border_title = title
        table = self.query_one("#messages", DataTable)
        table.clear()
        for r in rows:
            glyph, _ = tui.message_mark(r.flavour)
            table.add_row(Text(r.when, style="dim"), Text.from_ansi(glyph),
                          Text(f"#{r.id}", style="cyan"),
                          Text(r.sender, style="dim" if r.flavour == "read" else "bold"),
                          Text(r.kind), Text.from_ansi(r.first + r.behind), key=r.key)
        self._sync_cursor(table, "#messages-box", tui.MESSAGES, [r.key for r in rows])
        self._notes("#message-notes", notes)

    def _draw_agents(self) -> None:
        con = db.connect()
        try:
            rows, notes = tui.agents_data(con)
        finally:
            con.close()
        table = self.query_one("#agents", DataTable)
        table.clear()
        for r in rows:
            name = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}"
                                  + tui.fold_suffix(r.under, r.urgency))
            if r.error:
                table.add_row(name, "", "", Text(f"config error: {textsafe.oneline(r.error)}", style="red"),
                              "", "", "", key=r.name)
                continue
            wall = ""
            if r.wall:
                who = f"{r.wall.account}{r.wall.mark} " if r.wall.elsewhere else ""
                wall = f"{tui.YEL}⏳ {who}{r.wall.when}{r.wall.mark if not who else ''}{tui.R}"
            table.add_row(name, Text(r.acct), Text(r.kind, style="dim"),
                          Text.from_ansi(tui.state_cell(r.state, r.until)),
                          Text.from_ansi(r.ctx), Text(str(r.inbox) if r.inbox else "·"),
                          Text.from_ansi(r.fire or wall), key=r.name)
        self._sync_cursor(table, "#agents-box", tui.AGENTS, [r.name for r in rows])
        self._notes("#agent-notes", notes)

    def on_key(self, event: events.Key) -> None:
        k = key_token(event)
        if k is None:
            return
        event.stop()
        if k in ("q", "Q", "\x03"):
            self.app.exit()
            return
        if k == "b":     # task #72: "done - waiting for check" - free on every panel
            self.app.push_screen(TaskChecks())
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
            await self.app.push_screen_wait(
                ReadOnly(f"account {name}", sections))
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
        if key == "?":
            # The keybar (tui.keybar, drawn by tui.frame under this screen
            # unchanged) folds fold/move out of a filtered row's own footer -
            # `?` is the one place every AGENT_KEYS verb is named, curses or not.
            self.flash = tui.agent_help_line()
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
    whatever was open, in `tui.page()`'s shape - a title per section, then its
    rows. A single flat list is one section with no heading, and several
    sections are tabs. Textual's own scroll container and tab strip do the
    scrolling and the tab keys; a page has nothing to act on, so Esc closes it."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "back"),
        Binding("left", "tab(-1)", "prev tab", show=False),
        Binding("right,tab", "tab(1)", "next tab", show=False),
        Binding("j,down", "scroll(1)", "down", show=False),
        Binding("k,up", "scroll(-1)", "up", show=False),
        Binding("space", "page(1)", "page", show=False),
        Binding("b", "page(-1)", "page up", show=False),
        Binding("g", "edge(0)", "top", show=False),
        Binding("G", "edge(1)", "bottom", show=False),
        Binding("q,ctrl+c", "app.quit", "quit", show=False),
    ]
    DEFAULT_CSS = """
    ReadOnly #title { dock: top; height: 1; }
    ReadOnly TabbedContent { height: 1fr; }
    """

    def __init__(self, title: str, sections: list) -> None:
        super().__init__()
        if sections and not isinstance(sections[0], tuple):
            sections = [("", sections)]
        self.title_text = title
        self.sections = [(str(h), [str(r) for r in rows]) for h, rows in sections]

    def _body(self, rows: list[str]) -> VerticalScroll:
        lines = [ln for r in rows for ln in textsafe.drawable(r)]
        return VerticalScroll(Static(Text("\n".join(lines))))

    def compose(self) -> ComposeResult:
        yield Static(Text(self.title_text, style="bold"), id="title")
        if len(self.sections) == 1 and not self.sections[0][0]:
            yield self._body(self.sections[0][1])
        else:
            with TabbedContent():
                for i, (head, rows) in enumerate(self.sections):
                    with TabPane(head or "-", id=f"sec{i}"):
                        yield self._body(rows)
        yield Footer()

    def _pane(self) -> VerticalScroll:
        tabs = self.query(TabbedContent)
        if tabs:
            return self.query_one(f"#{tabs.first().active} VerticalScroll", VerticalScroll)
        return self.query_one(VerticalScroll)

    def action_tab(self, step: int) -> None:
        tabs = list(self.query(TabbedContent))
        if tabs:
            n = len(self.sections)
            cur = int(tabs[0].active.removeprefix("sec") or 0)
            tabs[0].active = f"sec{(cur + step) % n}"

    def action_scroll(self, step: int) -> None:
        self._pane().scroll_relative(y=step, animate=False)

    def action_page(self, step: int) -> None:
        pane = self._pane()
        (pane.scroll_page_down if step > 0 else pane.scroll_page_up)(animate=False)

    def action_edge(self, end: int) -> None:
        pane = self._pane()
        (pane.scroll_end if end else pane.scroll_home)(animate=False)


class TaskChecks(JobsAware, Screen):
    """Task #72 (founder #2154/#2155): the founder's own 'done - waiting for
    check' list (#71) - `db.tasks_needing_close` filtered to the ones
    `actions.task_asker` says are his, the same set `cmd_task_board` prints
    under that heading for `me == HUMAN` (the bridge always acts as the
    founder - the same `actions.HUMAN` the MESSAGES panel already answers,
    dismisses and delivers as). `c` closes the row under the cursor, `r`
    reopens it with a typed one-line reason (#71's own rule for any undo),
    and space/`C` mark and then close several at once, for a day with more
    of these than are worth doing one at a time."""

    BINDINGS = [
        Binding("c", "act('c')", "close"),
        Binding("r", "act('r')", "reopen"),
        Binding("space", "check", "check"),
        Binding("C", "act('C')", "close checked"),
        Binding("escape", "app.pop_screen", "back"),
        Binding("q,ctrl+c", "app.quit", "quit", show=False),
    ]
    _STATUS_STYLE = {"normal": "dim", "slow": "yellow", "danger": "red"}

    def __init__(self) -> None:
        super().__init__()
        self.checked: set[int] = set()
        self.flash = ""
        self.rows: list = []

    def compose(self) -> ComposeResult:
        yield Static("done - waiting for check", id="title")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "#", "task", "owner", "done")
        table.focus()
        self._retime()
        self._redraw()

    @property
    def cur(self) -> int:
        return self.query_one(DataTable).cursor_row

    def _rows(self, con) -> list:
        return [t for t in db.tasks_needing_close(con)
                if actions.task_asker(con, t) == actions.HUMAN]

    def _redraw(self) -> None:
        con = db.connect()
        try:
            self.rows = self._rows(con)
        finally:
            con.close()
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for t in self.rows:
            parent = f" (under #{t['parent_task']})" if t["parent_task"] else ""
            cells = ("[x]" if t["id"] in self.checked else "[ ]", f"#{t['id']}",
                     textsafe.oneline(t["title"]) + parent, t["owner"],
                     textsafe.oneline(t["done_evidence"]))
            table.add_row(*(Text(c) for c in cells), key=str(t["id"]))
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        status = Text(no_wrap=True, overflow="ellipsis")
        for jl in self.jobs.lines():
            status.append(jl.text + "\n", self._STATUS_STYLE.get(jl.level, ""))
        if self.flash:
            status.append(self.flash, "yellow")
        self.query_one("#status", Static).update(status)

    def action_check(self) -> None:
        if self.cur < len(self.rows):
            self.checked.symmetric_difference_update({self.rows[self.cur]["id"]})
        self._redraw()

    def action_act(self, key: str) -> None:
        self.run_worker(self._act(key), exclusive=True, group="act")

    async def _act(self, key: str) -> None:
        if key == "C":
            await self._close_checked()
        else:
            tid = self.rows[self.cur]["id"] if self.cur < len(self.rows) else None
            if tid is None:
                self.flash = "nothing waiting"
            elif key == "c":
                await self._close(tid)
            else:
                await self._reopen(tid)
        self._redraw()

    async def _close(self, tid: int) -> None:
        con = db.connect()
        try:
            ok, msg = actions.close_task(con, tid, actions.HUMAN)
        finally:
            con.close()
        self.flash = msg
        self.checked.discard(tid)

    async def _reopen(self, tid: int) -> None:
        reason = await self.app.push_screen_wait(TextPrompt(f"reopen #{tid} - why:"))
        if reason is None or not reason.strip():
            self.flash = "left alone"
            return
        con = db.connect()
        try:
            ok, msg = actions.reopen_task(con, tid, actions.HUMAN, reason)
        finally:
            con.close()
        self.flash = msg
        self.checked.discard(tid)

    async def _close_checked(self) -> None:
        if not self.checked:
            self.flash = "nothing checked - space marks a row"
            return
        ids = sorted(self.checked)
        said = []
        con = db.connect()
        try:
            for tid in ids:
                ok, msg = actions.close_task(con, tid, actions.HUMAN)
                said.append(msg)
        finally:
            con.close()
        self.flash = "  ·  ".join(said)
        self.checked.clear()


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


def _conversation_lines(con, name: str, width: int) -> tuple[list[str], int]:
    """The conversation, formatted and wrapped exactly as curses draws it
    (`tui._agent_page`, `tui.lines_of` - the tested turn-by-turn layout), and
    where it ends - `conv_end`, the newest line of the conversation itself,
    not of whatever `agent_detail` lists under it (queued-for-it rows,
    untold answers). Pinning to `len(body)` pins to the bottom of that list
    instead, on any agent with something queued (auditor #2146 BLOCKING).
    `hint=False`: this screen's own box already says where a message goes."""
    detail = tui._agent_page(con, name, tui.AGENTS, hint=False)
    body = tui.lines_of(detail, width)
    return body, detail.get("conv_end", len(body))


class AgentDetail(JobsAware, Screen):
    """An agent's own page: its conversation, and the box chat-box put there -
    typing lands in it, Enter sends, '/' opens its commands in place. Escape
    goes back to the dashboard, keeping the draft."""

    def __init__(self, agent: str) -> None:
        super().__init__()
        # `top`: None means pinned to the end of the conversation (`_redraw`'s
        # `pin`, not the bottom of the page - what agent_detail lists under
        # the conversation is not it, auditor #2146). The page opens there
        # and follows it as replies arrive, until a scroll away from it sets
        # an explicit row and un-pins it (task #54, #2124).
        self.agent, self.top, self.flash = agent, None, ""
        self._top_now = 0
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
            body, conv_end = _conversation_lines(con, self.agent, width)
            cmds = tui.agent_commands(con, self.agent)
        finally:
            con.close()
        self.commands = cmds
        job_lines = _job_lines_ansi(self.jobs, width)
        room = self._room(height) - len(job_lines)
        max_top = max(0, len(body) - room)
        # `tui._detail_frame`'s own pin: the end of the conversation, not of
        # whatever `agent_detail` lists under it (auditor #2146 BLOCKING).
        pin = min(max_top, max(0, conv_end - room))
        top = pin if self.top is None else max(0, min(self.top, max_top))
        self._top_now = top
        # `== pin`, not `>= pin` (auditor #2171 BLOCKING 1): curses re-pins
        # only on the exact spot (`tui._detail_frame`: `end = top == pin`) -
        # `>=` re-pinned on any scroll past it too, so a look at what is
        # queued under the conversation snapped back to `pin` on the very
        # next redraw, a timer tick away.
        self.top = None if top == pin else top
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
            self.top = max(0, self._top_now + (1 if k == "\033[B" else -1))
            self._redraw()
            return
        if k in ("\033[5~", "\033[6~"):
            room = self._room(max(10, self.size.height))
            self.top = max(0, self._top_now + room * (1 if k == "\033[6~" else -1))
            self._redraw()
            return
        if k in ("\033[H", "\033[F") and not box.text:
            self.top = 0 if k == "\033[H" else None    # None: pinned to the conversation's end
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
        line saying it did not go. Restored ahead of whatever is in the box
        by then, not only when it is empty: the human may already be typing
        the next thing by the time a refusal comes back, and that draft is
        no reason to drop the one that did not go."""
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
                    if ok:
                        return
                    prefix = body if not self.box.text else body + "\n"
                    self.box.text = prefix + self.box.text
                    self.box.pos += len(prefix)

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


def run(once: bool = False) -> int:
    """`once`, or stdout not a tty (a script or a pipe): `tui.run`'s own
    non-interactive snapshot, not a Textual app - the same one `ocp bridge
    --curses --once` prints, so the two front ends cannot show a different
    picture of the same state."""
    if once or not sys.stdout.isatty():
        return tui.run(once=True)
    BridgeApp().run()
    return 0
