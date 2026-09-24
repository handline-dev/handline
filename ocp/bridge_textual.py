"""ocp bridge: the Textual app the team is run from.

Three screens: the dashboard (four DataTables - ACCOUNTS, AGENTS, FOR YOU,
STUCK - fed by `tui.accounts_data`, `agents_data`, `messages_data` and
`stuck_data`), an agent's chat page (`chat_page.ChatPage`, opened with Enter on
an agent or a message), and the founder's "done - waiting for check" list. They draw what the data
functions in `tui.py` and `actions` say and decide nothing themselves, so a row
or a page means the same here as in `ocp status` and the daemon.

Every verb a key or a slash command can run hands its work to `ocp.actrun`
through `jobs.Jobs` and returns at once, so a screen keeps redrawing and reading
keys while an action is in flight, with a line saying what is running and for
how long.

Every `AGENT_KEYS` verb is a row key on the dashboard; the chat page is for
talking to the agent, not for running them.

ACCOUNTS' login and add hand the whole terminal to another program - a human
typing a login code into claude's own screen - through Textual's own
`App.suspend()` rather than `jobs.Jobs`: it is interactive, not something to
run and report on later.

`?` on an AGENTS/STUCK row names every `AGENT_KEYS` verb (`tui.agent_help_line`),
and a row opened read-only (an ACCOUNTS row, a message with no agent) is a `ReadOnly` page.
Run once or into a pipe, `ocp bridge` is `tui.snapshot`, plain text, and does not
start this app.

Not yet built: opening an ACCOUNTS row into a page of its own with actions on
it, beyond the read-only summary above.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen, Screen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, DataTable, Footer, Input, Static, TabbedContent, TabPane, TextArea

from . import accounts, actions, db, jobs as jobsmod, load as loadmod, orca, status, textsafe, tui

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


_LEVEL_STYLE = {"normal": "dim", "slow": "yellow", "danger": "red"}


def _header_text(head: tui.HeaderData, show_waits: bool = True, show_legend: bool = True) -> Text:
    """The bar and, under it, what is stopped until the founder acts."""
    out = Text()
    out.append("ocp", "bold")
    out.append(" · daemon ", "dim")
    out.append(*(("running", "green") if head.running else ("NOT RUNNING", "red")))
    out.append(f" · {head.clock}", "dim")
    if head.legend and show_legend:
        out.append(f" · {status.SOURCE_LEGEND_SHORT}", "dim")
    if head.waits and show_waits:
        now = time.time()
        out.append(f"\n{tui.WAITING_ON_YOU}", "bold red")
        out.append(f" ({len(head.waits)})", "dim")
        shown = head.waits if len(head.waits) <= tui.WAITS_SHOWN + 1 else head.waits[:tui.WAITS_SHOWN]
        for w in shown:
            out.append(f"\n  {textsafe.oneline(w['who'])} · {textsafe.oneline(w['what'])} · "
                       f"{tui.age(now - w['since'])}", "red" if w["red"] else "yellow")
        if len(shown) < len(head.waits):
            out.append(f"\n  … {len(head.waits) - len(shown)} more", "dim")
    return out


def confirm_answer(key: str, character: str | None) -> bool | None:
    """True for y, False for n, Escape or Ctrl+C, None for any other key: a wheel notch
    arrives as an arrow and must leave the question open."""
    if character in ("y", "Y"):
        return True
    if character in ("n", "N") or key in ("escape", "ctrl+c"):
        return False
    return None


class Confirm(ModalScreen[bool]):
    """A yes/no question in a box in the middle of the screen, over what is
    under it. `question` is the one line asked; `detail` is what answering yes
    will do, in as many lines as it takes; `yes` and `no` label the buttons
    (the key each answers to is shown beside it); `danger` paints Yes red for
    something that cannot be undone.

    Only a typed y (or a click on Yes) confirms, and only y, n or Escape (or a
    click) answers; every other key leaves the question open, so a wheel notch
    or a stray arrow never answers it. Dismisses with True or False."""

    DEFAULT_CSS = """
    Confirm { align: center middle; background: $background 60%; }
    Confirm #dialog { width: 64; max-width: 90%; height: auto; padding: 1 2;
                      border: thick $primary; background: $panel; }
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
        self.dismiss(event.button.id == "yes")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        answer = confirm_answer(event.key, event.character)
        if answer is not None:
            self.dismiss(answer)


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
    TextPrompt { align: center middle; background: $background 60%; }
    TextPrompt #dialog { width: 72; max-width: 90%; height: auto; padding: 1 2;
                         border: thick $primary; background: $panel; }
    TextPrompt #prompt { height: auto; margin-bottom: 1; }
    TextPrompt #box { height: auto; min-height: 3; max-height: 12; }
    TextPrompt #hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, prompt: str, multi: bool = False) -> None:
        super().__init__()
        self.prompt, self.multi = prompt, multi

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(textsafe.oneline(self.prompt), style="bold"), id="prompt")
            yield _TextBox(id="box") if self.multi else _LineBox(id="box")
            yield Static("enter sends" + (" · shift+enter new line" if self.multi else "")
                         + " · esc cancels", id="hint")

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
    human moves to another screen while it goes."""

    REFRESH_S = 2.0
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
    pressed or typed on: what asks first and what needs a prompt is decided
    here, the work goes through `jobs.Jobs`."""
    allowed, why = actions.can(con, name, label)
    if not allowed:
        screen.flash = why
        return
    if label in ("message", "editor"):
        # "editor" is one more way to write the same message, not a different
        # action (`actions.can` has no case for it either): the modal composer
        # stands in for both.
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
    """The main screen: four panels, tab- and arrow-navigable, with the row
    actions dispatched through `jobs.Jobs` rather than run in place."""

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
    Dashboard #stuck-box { height: 1fr; border: round $panel; }
    Dashboard #stuck-box.focused { border: round cyan; }
    Dashboard #foot { height: auto; }
    """
    _ACCT_COLS = [k for k in tui.ACCT_W if k != "name"]
    SEVEN_W = tui.ACCT_BAR_W["seven"]   # the 7d bar's width
    PACE = False          # whether the usage bars mark how far through the window we are
    AGENT_COLUMNS = ("agent", "acct", "kind", "state", "ctx", "in", "")
    MESSAGE_COLUMNS = ("", "", "#", "from", "kind", "message")
    PANELS = tui.PANELS   # the panels ←→ and the row keys walk
    SHOW_LEGEND = True    # the key to the figures' marks, in the header
    SHOW_WAITS = True     # the WAITING ON YOU list under the header
    FOUNDER_VIEW = False   # whether an agent's delivery block counts on its AGENTS row

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
        with Vertical(id="stuck-box"):
            yield DataTable(id="stuck", cursor_type="row", show_cursor=False)
        yield Static(id="foot")

    def on_mount(self) -> None:
        table = self.query_one("#accounts", DataTable)
        table.can_focus = False
        table.add_columns("account", *(tui.ACCT_HEAD[k] for k in self._ACCT_COLS), "")
        agents_table = self.query_one("#agents", DataTable)
        agents_table.can_focus = False
        agents_table.add_columns(*self.AGENT_COLUMNS)
        messages_table = self.query_one("#messages", DataTable)
        messages_table.can_focus = False
        messages_table.add_columns(*self.MESSAGE_COLUMNS)
        stuck_table = self.query_one("#stuck", DataTable)
        stuck_table.can_focus = False
        stuck_table.add_columns("what", "for", "why")
        self._retime()
        self._redraw()

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        con = db.connect()
        try:
            head = tui.header_data(con)
            target = self.sel.get(self.panel)
            agent_keys = (tui.agent_footer_keys(con, str(target) if target is not None else None)
                          if self.panel in (tui.AGENTS, tui.STUCK) else None)
        finally:
            con.close()
        self.query_one("#header", Static).update(_header_text(head, self.SHOW_WAITS, self.SHOW_LEGEND))
        self.head = head
        self._draw_accounts()
        self._draw_agents()
        self._draw_messages()
        self._draw_stuck()
        foot = Text()
        for jl in self.jobs.lines():
            foot.append(jl.text + "\n", _LEVEL_STYLE.get(jl.level, ""))
        if self.flash:
            foot.append(textsafe.oneline(self.flash) + "\n")
        foot.append(f"{tui.PANEL_NAME[self.panel]}: ", "dim")
        for i, (k, label) in enumerate(tui.panel_keys(self.panel, agent_keys)):
            foot.append(" · " if i else "", "dim")
            foot.append(k, "bold")
            foot.append(f" {label}", "dim")
        # task #72: `b` is a Textual-only global, so its hint is appended here.
        foot.append(" · ←→ panel ↑↓ row · q quit · ", "dim")
        foot.append("b", "bold")
        foot.append(" checks", "dim")
        self.query_one("#foot", Static).update(foot)

    def _draw_accounts(self) -> None:
        con = db.connect()
        try:
            rows, notes = tui.accounts_data(con, self.PACE, self.SEVEN_W)
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
        want = self.sel.get(panel)
        if focused and want is not None and want in names:
            table.move_cursor(row=names.index(want))
        self.query_one(box).set_class(focused, "focused")

    def _notes(self, widget: str, notes: list[str]) -> None:
        w = self.query_one(widget, Static)
        w.update(Text.from_ansi("\n".join(f"{tui.DIM}{n}{tui.R}" for n in notes)))
        w.display = bool(notes)

    def _draw_stuck(self) -> None:
        con = db.connect()
        try:
            title, rows = tui.stuck_data(con)
        finally:
            con.close()
        self.query_one("#stuck-box").border_title = title
        table = self.query_one("#stuck", DataTable)
        table.clear()
        for r in rows:
            table.add_row(Text(r.what, style="yellow"), Text(r.age, style="dim"),
                          Text(r.why, style="dim"))
        self._sync_cursor(table, "#stuck-box", tui.STUCK, [r.agent for r in rows])

    def _message_cells(self, r: tui.MessageRow) -> list:
        """One FOR YOU row, a cell per `MESSAGE_COLUMNS`."""
        glyph, _ = tui.message_mark(r.flavour)
        return [Text(r.when, style="dim"), Text.from_ansi(glyph),
                Text(f"#{r.id}", style="cyan"),
                Text(r.sender, style="dim" if r.flavour == "read" else "bold"),
                Text(r.kind), Text.from_ansi(r.first + r.behind)]

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
            table.add_row(*self._message_cells(r), key=r.key)
        self._sync_cursor(table, "#messages-box", tui.MESSAGES, [r.key for r in rows])
        self._notes("#message-notes", notes)

    def _agent_cells(self, r: tui.AgentRow, name: Text) -> list:
        """One AGENTS row, a cell per `AGENT_COLUMNS`."""
        if r.error:
            return [name, "", "", Text(f"config error: {textsafe.oneline(r.error)}", style="red"),
                    "", "", ""]
        wall = ""
        if r.wall:
            who = f"{r.wall.account}{r.wall.mark} " if r.wall.elsewhere else ""
            wall = f"{tui.YEL}⏳ {who}{r.wall.when}{r.wall.mark if not who else ''}{tui.R}"
        return [name, Text(r.acct), Text(r.kind, style="dim"),
                Text.from_ansi(tui.state_cell(r.state, r.until)),
                Text.from_ansi(r.ctx), Text(str(r.inbox) if r.inbox else "·"),
                Text.from_ansi(r.fire or wall)]

    def _draw_agents(self) -> None:
        con = db.connect()
        try:
            rows, notes = tui.agents_data(con, self.FOUNDER_VIEW)
        finally:
            con.close()
        table = self.query_one("#agents", DataTable)
        table.clear()
        for r in rows:
            name = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}"
                                  + tui.fold_suffix(r.under, r.urgency))
            table.add_row(*self._agent_cells(r, name), key=r.name)
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
        moved = tui.navigate(self.panel, self.sel, k, self.PANELS)
        if moved is not None:
            self.panel = moved
            self._redraw()
            return
        self.flash = ""
        # `_act` can open a modal (`push_screen_wait`), which Textual only
        # allows from inside a worker - not straight from an event handler.
        self.run_worker(self._act_and_redraw(k), exclusive=True, group="act")

    def open_chat(self, agent: str) -> None:
        from .chat_page import ChatPage   # chat_page imports this module
        self.app.push_screen(ChatPage(agent))

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
        # `remove_plan` first, the same order the CLI uses: an
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
            self.open_chat(name)
            return
        if key == "?":
            # The footer (`tui.panel_keys`) folds fold/move out of a filtered
            # row's own keys - `?` is the one place every AGENT_KEYS verb is named.
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
        # (the row's id is what the action is given).
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
                self.open_chat(place[0])
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
            parent = f" (under {actions.task_ref(t['parent_task'])})" if t["parent_task"] else ""
            cells = ("[x]" if t["id"] in self.checked else "[ ]", actions.task_ref(t['id']),
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
        reason = await self.app.push_screen_wait(TextPrompt(f"reopen {actions.task_ref(tid)} - why:"))
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


class BridgeApp(App):
    """The Textual bridge (`ocp bridge --textual`, task #45): the dashboard, and
    from it an agent's chat page - see `Dashboard`, `chat_page.ChatPage`."""

    TITLE = "ocp bridge"
    CSS_PATH = Path(__file__).with_name("chat_page.tcss")

    def __init__(self) -> None:
        super().__init__()
        self.jobs = jobsmod.Jobs()

    def on_mount(self) -> None:
        from .ui.pages.home import HomePage   # the page imports this module
        self.push_screen(HomePage())


def run(once: bool = False) -> int:
    """`once`, or stdout not a tty (a script or a pipe): `tui.snapshot`, plain
    text, not a Textual app."""
    if once or not sys.stdout.isatty():
        print(tui.snapshot())
        return 0
    BridgeApp().run()
    return 0
