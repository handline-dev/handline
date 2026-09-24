"""The bridge's home page: Home, HR and Settings tabs under the page header.

`Dashboard` is the previous home page and stays as it was; this page inherits its
selection, jobs and row actions and draws its own panels, so nothing here changes the
Dashboard. What only this page shows is in `ui.components` (`gauge`, `agent_rows`,
`summary`, `proposals`)."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.errors import NoWidget
from textual.widgets import DataTable, Static, Tabs
from textual.widgets._data_table import CellDoesNotExist

from ... import db, status, tui
from ..components import agent_rows, gauge
from ..components.page_header import page_header
from ..components.proposals import ProposalsView, open_proposals
from ..components.summary import clock, daemon_running, marks_on_screen, summarize, summary_text
from ..keys import key_token
from .dashboard import _LEVEL_STYLE, Dashboard

_PANEL_OF_TABLE = {"accounts": tui.ACCOUNTS, "agents": tui.AGENTS,
                   "messages": tui.MESSAGES, "stuck": tui.STUCK}
# Each tab's widgets: what it shows and the others hide. Home's are the Dashboard's own.
_TAB_WIDGETS = {
    "home": ("#header", "#accounts-box", "#agents-box", "#messages-box", "#foot"),
    "hr": ("#hr_view",),
    "settings": ("#settings_view",),
}
# The ACCOUNTS columns from the richest layout to the barest; the first that fits the
# width is drawn. The bars carry a `│` for how far through the window we are, so a
# narrow layout drops the reset countdowns, then the percentages.
_ACCT_TIERS = (
    (("five", "reset5", "agents", "seven", "reset7", "runway"), True),
    (("five", "agents", "seven", "runway"), True),
    (("five", "agents", "seven", "runway"), False),
)
_AGENT_HEADS = ("agent", "acct", "model", "state", "cache", "ctx", "in", "")
_MESSAGE_HEADS = ("", "", "from", "message")


def _set_columns(table: DataTable, heads: tuple) -> None:
    """Give a table these columns, once: Dashboard's own are replaced on the first draw."""
    if getattr(table, "_heads", None) != heads:
        table.clear(columns=True)
        table.add_columns(*heads)
        table._heads = heads


class HomePage(Dashboard):
    AUTO_FOCUS = ""     # the keys act on the dashboard until the tab bar takes the focus
    # The row cursor is the selection. DataTable's hover highlight restarts at row 0
    # each redraw, so it reads as a second, wrong selection.
    DEFAULT_CSS = """
    HomePage DataTable > .datatable--hover { background: transparent; }
    HomePage DataTable { overflow-x: hidden; }
    HomePage #stuck-box { display: none; }
    HomePage #messages-box { height: 1fr; max-height: 100%; }
    """
    # What is stuck is said on the agent's row, on the account's mark and in the header's
    # daemon word, so the STUCK panel is not drawn and the keys skip it.
    PANELS = (tui.ACCOUNTS, tui.AGENTS, tui.MESSAGES)
    BLINK_S = 0.6

    def __init__(self) -> None:
        super().__init__()
        self.tab = "home"
        self._hr_dot = False            # whether the HR tab wears its dot
        self._on = True                 # the blink phase
        self._blinking: dict = {}       # agent -> (its AgentRow, its Extra), for the rows that blink

    def compose(self) -> ComposeResult:
        yield from page_header([("Home", "home"), ("HR", "hr"), ("Settings", "settings")])
        yield from super().compose()
        yield ProposalsView(id="hr_view")
        yield Static("Settings", id="settings_view")

    def on_mount(self) -> None:
        self.sub_title = "home"
        self.set_interval(self.BLINK_S, self._blink)
        self.query_one("#hr_view").display = False
        self.query_one("#settings_view").display = False

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.tab = event.tab.id
        for tab, widgets in _TAB_WIDGETS.items():
            for sel in widgets:
                self.query_one(sel).display = tab == self.tab
        if self.tab == "hr":
            self.query_one("#hr_view", ProposalsView).refresh_rows()

    # ------------------------------------------------------------------ drawing

    def _redraw(self) -> None:
        con = db.connect()
        try:
            target = self.sel.get(self.panel)
            agent_keys = (tui.agent_footer_keys(con, str(target) if target is not None else None)
                          if self.panel == tui.AGENTS else None)
            summary = summarize(con)
        finally:
            con.close()
        self.query_one("#header", Static).update(summary_text(daemon_running(), clock(), summary))
        self._draw_accounts()
        self._draw_agents()
        self._draw_messages()
        self.query_one("#accounts-box").border_subtitle = " · ".join(
            ["│ time passed in the window"] + ([status.SOURCE_LEGEND_SHORT] if marks_on_screen() else []))
        foot = Text()
        for jl in self.jobs.lines():
            foot.append(jl.text + "\n", _LEVEL_STYLE.get(jl.level, ""))
        if self.flash:
            foot.append(tui.oneline(self.flash) + "\n")
        foot.append(f"{tui.PANEL_NAME[self.panel]}: ", "dim")
        for i, (k, label) in enumerate(tui.panel_keys(self.panel, agent_keys)):
            foot.append(" · " if i else "", "dim")
            foot.append(k, "bold")
            foot.append(f" {label}", "dim")
        foot.append(" · ←→ panel ↑↓ row · q quit · ", "dim")
        foot.append("b", "bold")
        foot.append(" checks", "dim")
        self.query_one("#foot", Static).update(foot)
        self._count_hr()
        if self.tab == "hr":
            self.query_one("#hr_view", ProposalsView).refresh_rows()

    def _draw_accounts(self) -> None:
        con = db.connect()
        try:
            rows, notes = tui.accounts_data(con)
        finally:
            con.close()
        table = self.query_one("#accounts", DataTable)
        meters = {r.name: gauge.usage_cells(status.read(r.name), True) for r in rows}
        room = table.scrollable_content_region.width or self.size.width - 4
        for cols, with_pct in _ACCT_TIERS:
            heads = ("account", *(tui.ACCT_HEAD[c] for c in cols), "")
            body = []
            for r in rows:
                cells = dict(r.cells)
                if meters[r.name]:
                    five, seven = gauge.usage_cells(status.read(r.name), with_pct)
                    cells["five"], cells["seven"] = five, seven
                body.append([Text.from_ansi(f"{tui.BOLD}{tui.oneline(r.name)}{tui.R}"),
                             *(Text.from_ansi(cells[c]) for c in cols),
                             Text.from_ansi(r.mark + r.next)])
            widths = [max(len(h), *(c.cell_len for c in col)) + 2 for h, col in zip(heads, zip(*body))] \
                if body else [0]
            if sum(widths) <= room:
                break
        _set_columns(table, heads)
        table.clear()
        for r, cells in zip(rows, body):
            table.add_row(*cells, key=r.name)
        self._sync_cursor(table, "#accounts-box", tui.ACCOUNTS, [r.name for r in rows])
        self._notes("#acct-notes", notes)

    def _draw_agents(self) -> None:
        con = db.connect()
        try:
            rows, notes = tui.agents_data(con)
            extra = agent_rows.collect(con, rows)
        finally:
            con.close()
        table = self.query_one("#agents", DataTable)
        _set_columns(table, _AGENT_HEADS)
        table.clear()
        self._blinking = {}
        for r in rows:
            x = extra.get(r.name)
            if x is None:       # a config error: no cells beyond the message
                name = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}"
                                      + tui.fold_suffix(r.under, r.urgency))
                table.add_row(name, "", "", Text(f"config error: {tui.oneline(r.error or '')}", style="red"),
                              "", "", "", "", key=r.name)
                continue
            wall = ""
            if r.wall:
                who = f"{r.wall.account}{r.wall.mark} " if r.wall.elsewhere else ""
                wall = f"{tui.YEL}⏳ {who}{r.wall.when}{r.wall.mark if not who else ''}{tui.R}"
            table.add_row(self._name_cell(r, x, self._on), Text(r.acct), Text(x.kind_short, style="dim"),
                          self._state_cell(r, x, self._on),
                          Text.from_ansi(agent_rows.cache_cell(x.cache_left)),
                          Text.from_ansi(r.ctx), Text(str(r.inbox) if r.inbox else "·"),
                          Text.from_ansi(r.fire or wall), key=r.name)
            if self._alarm(r, x) or self._fold_errs(r, x):
                self._blinking[r.name] = (r, x)
        self._sync_cursor(table, "#agents-box", tui.AGENTS, [r.name for r in rows])
        self._notes("#agent-notes", notes)

    def _draw_messages(self) -> None:
        con = db.connect()
        try:
            title, rows, notes = tui.messages_data(con)
        finally:
            con.close()
        # The glyph already says what kind of message a row is (⚠ decide, ◷ asked for a
        # reply, ↩ came back, · to read), so there is no kind column and no id; `refused`,
        # the one kind the glyph shares with `answer`, is said in the text.
        self.query_one("#messages-box").border_title = title
        table = self.query_one("#messages", DataTable)
        _set_columns(table, _MESSAGE_HEADS)
        table.clear()
        for r in rows:
            glyph, _ = tui.message_mark(r.flavour)
            text = Text.from_ansi(r.first + r.behind)
            if r.kind == "refused":
                text = Text.assemble(("refused ", "red"), text)
            text.no_wrap, text.overflow = True, "ellipsis"
            table.add_row(Text(r.when, style="dim"), Text.from_ansi(glyph),
                          Text(r.sender, style="dim" if r.flavour == "read" else "bold"), text,
                          key=r.key)
        self._sync_cursor(table, "#messages-box", tui.MESSAGES, [r.key for r in rows])
        self._notes("#message-notes", notes)
        self.call_after_refresh(self._fit, "#messages", 3)

    def on_resize(self) -> None:
        super().on_resize()
        self._fit("#messages", 3)

    def _fit(self, table_id: str, col: int) -> None:
        """Give column `col` of a table the width the others leave, so its text is
        cut with an ellipsis instead of scrolling the table sideways."""
        table = self.query_one(table_id, DataTable)
        cols = table.ordered_columns
        if col >= len(cols):
            return
        taken = sum(c.get_render_width(table) for i, c in enumerate(cols) if i != col)
        width = max(10, table.scrollable_content_region.width - taken - 2 * table.cell_padding)
        if cols[col].auto_width or cols[col].width != width:
            cols[col].auto_width, cols[col].width = False, width
            table._require_update_dimensions = True
            table.refresh(layout=True)

    @staticmethod
    def _alarm(r, x) -> bool:
        """Does this row need the founder: a dialog or a fatal state, or a block."""
        return r.state in tui.NEEDS_A_HUMAN or bool(x.block)

    @staticmethod
    def _fold_errs(r, x) -> bool:
        """Does the fold on this row hide an error?"""
        return bool(r.under and x.urgency and x.urgency[0] == "!" and x.urgency[2] == tui.RED)

    def _name_cell(self, r, x, on: bool) -> Text:
        """The name, its fold count, and a warning icon that blinks: on the row itself
        when it needs the founder, on the count when the reports it hides do."""
        out = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}")
        if r.under:
            out.append(f" +{r.under}", style="dim")
            if x.urgency:
                glyph, n, colour = x.urgency
                if glyph == "!" and colour == tui.RED:      # hides an error: blinks
                    out.append(f" ⚠{n}" if on else " " * (len(str(n)) + 2), style="bold red")
                elif glyph == "!":                          # hides a limit or a delay
                    out.append(f" ⚠{n}", style="yellow")
                else:                                       # ✉ asks of the founder, ▶ running
                    out.append(f" {glyph}{n}", style="yellow" if glyph == "✉" else "green")
        if x.asks:
            out.append(f" ✉{x.asks}", style="yellow")
        if self._alarm(r, x):
            out.append(" ⚠" if on else "  ", style="bold red")
        return out

    def _state_cell(self, r, x, on: bool) -> Text:
        cell = Text.from_ansi(tui.state_cell(r.state, r.until))
        if x.block and r.state not in tui.NEEDS_A_HUMAN:
            cell.append(f" {x.block}", style="red")
        elif x.stuck:
            cell.append(f" ⚠ {x.stuck}", style="yellow")
        if self._alarm(r, x) and on:
            cell.stylize("reverse")
        return cell

    def _blink(self) -> None:
        """Flip the phase and repaint only the cells that blink; a redraw would read
        every agent again."""
        self._on = not self._on
        table = self.query_one("#agents", DataTable)
        keys = [c.key for c in table.ordered_columns]
        for name, (r, x) in self._blinking.items():
            try:
                table.update_cell(name, keys[0], self._name_cell(r, x, self._on))
                table.update_cell(name, keys[3], self._state_cell(r, x, self._on))
            except CellDoesNotExist:
                pass

    def _count_hr(self) -> None:
        """The HR tab wears a red dot while a proposal waits on the founder, so it is
        seen from the other tabs."""
        con = db.connect()
        try:
            n = len(open_proposals(con))
        finally:
            con.close()
        if (n > 0) != self._hr_dot:
            self._hr_dot = n > 0
            self.query_one("#scope", Tabs).get_tab("hr").label = (
                Text.assemble("HR", (" ●", "bold red")) if n else Text("HR"))

    # --------------------------------------------------------------------- keys

    def on_key(self, event: events.Key) -> None:
        """↑ from the first row of a tab's content puts the focus on the tab bar, where
        ←→ change tab and ↓ goes back down. With the bar focused, or off the Home tab,
        only `q` is the page's: the Dashboard's row keys would act on rows that are not
        on screen, and a tab's own keys are its widgets'."""
        k = key_token(event)
        if isinstance(self.focused, Tabs):
            event.prevent_default()
            if k in tui.NEXT_ROW:
                event.stop()
                self._enter_tab()
            elif k in ("q", "Q", "\x03"):
                event.stop()
                self.app.exit()
            return
        if k in tui.PREV_ROW and self._on_top_row():
            event.prevent_default()
            event.stop()
            self.query_one("#scope", Tabs).focus()
            return
        if self.tab == "home":
            return
        event.prevent_default()
        if k in ("q", "Q", "\x03"):
            event.stop()
            self.app.exit()

    def _on_top_row(self) -> bool:
        """Is the cursor on the first row of the tab, so that ↑ has nowhere to go?"""
        if self.tab == "hr":
            return self.query_one("#hr_table", DataTable).cursor_row == 0
        if self.tab != "home":
            return True
        first = next((p for p in self.PANELS if tui._keys_of(p)), None)
        keys = tui._keys_of(first) if first is not None else []
        return first is None or (self.panel == first and self.sel.get(first) == keys[0])

    def _enter_tab(self) -> None:
        """Take the focus from the tab bar into the tab's content."""
        if self.tab == "hr":
            self.query_one("#hr_table").focus()
        else:
            self.set_focus(None)

    def _row_key(self, table: DataTable, panel: int, row: int):
        """The selection key of a table row, or None for a row the cursor cannot
        sit on. The stuck table's rows carry no key: its cursor is on an agent."""
        if panel == tui.STUCK:
            con = db.connect()
            try:
                _title, rows = tui.stuck_data(con)
            finally:
                con.close()
            return rows[row].agent or None if 0 <= row < len(rows) else None
        if not 0 <= row < table.row_count:
            return None
        return table.coordinate_to_cell_key((row, 0)).row_key.value

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Pressing on a row puts the cursor there, as the arrow keys would. Not on
        Click: the focused panel's DataTable moves its own cursor on Click and stops
        the event, so the screen never sees it and the next redraw undoes the move."""
        row = event.style.meta.get("row")
        # A real MouseDown carries no widget (only a Click does): ask the screen.
        try:
            table = self.get_widget_at(event.screen_x, event.screen_y)[0]
        except NoWidget:
            return
        table = next((w for w in table.ancestors_with_self if isinstance(w, DataTable)), None)
        panel = _PANEL_OF_TABLE.get(table.id) if table is not None else None
        if panel is None or row is None:
            return
        key = self._row_key(table, panel, row)
        if key is None:
            return
        event.stop()
        self.set_focus(None)
        self.panel = panel
        self.sel[panel] = key
        self._redraw()
