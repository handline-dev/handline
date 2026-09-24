"""The previous home page: four panels (ACCOUNTS, AGENTS, FOR YOU, STUCK) fed by the
`tui` data functions. `HomePage` replaces it; it stays as the unchanged fallback."""
from __future__ import annotations

import subprocess
import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ... import accounts, actions, db, load as loadmod, orca, status, textsafe, tui
from ..keys import key_token
from ..components.text_prompt import TextPrompt
from ..components.jobs_aware import JobsAware, dispatch_agent_verb
from ..components.read_only import ReadOnly
from .task_checks import TaskChecks


_LEVEL_STYLE = {"normal": "dim", "slow": "yellow", "danger": "red"}


def _header_text(head: tui.HeaderData) -> Text:
    """The bar and, under it, what is stopped until the founder acts."""
    out = Text()
    out.append("ocp", "bold")
    out.append(" · daemon ", "dim")
    out.append(*(("running", "green") if head.running else ("NOT RUNNING", "red")))
    out.append(f" · {head.clock}", "dim")
    if head.legend:
        out.append(f" · {status.SOURCE_LEGEND_SHORT}", "dim")
    if head.waits:
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
    PANELS = tui.PANELS   # the panels ←→ and the row keys walk

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
        agents_table.add_columns("agent", "acct", "kind", "state", "ctx", "in", "")
        messages_table = self.query_one("#messages", DataTable)
        messages_table.can_focus = False
        messages_table.add_columns("", "", "#", "from", "kind", "message")
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
        self.query_one("#header", Static).update(_header_text(head))
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
        from ...chat_page import ChatPage   # chat_page imports the components this page uses
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
