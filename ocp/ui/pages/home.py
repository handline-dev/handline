"""The bridge's home page. `bridge_textual.Dashboard` is the previous home page,
kept unchanged; `HomePage` is the one `BridgeApp` opens, so edits here never
touch the Dashboard. Its header is the chat page's (`page_header`)."""
from __future__ import annotations

from textual import events
from rich.text import Text
from textual.app import ComposeResult
from textual.errors import NoWidget
from textual.widgets._data_table import CellDoesNotExist
from textual.widgets import DataTable, Static, Tabs

from ... import db, tui
from ...bridge_textual import Dashboard, key_token
from ..components.page_header import page_header
from ..components.proposals import ProposalsView, open_proposals

_PANEL_OF_TABLE = {"accounts": tui.ACCOUNTS, "agents": tui.AGENTS,
                   "messages": tui.MESSAGES, "stuck": tui.STUCK}
# Each tab's widgets: what it shows and the others hide. Home's are the Dashboard's own.
_TAB_WIDGETS = {
    "home": ("#header", "#accounts-box", "#agents-box", "#messages-box", "#foot"),
    "hr": ("#hr_view",),
    "settings": ("#settings_view",),
}


class HomePage(Dashboard):
    AUTO_FOCUS = ""     # the keys act on the dashboard until a tab is clicked
    # The row cursor is the selection. DataTable's hover highlight restarts at row 0
    # each redraw, so it reads as a second, wrong selection.
    DEFAULT_CSS = """
    HomePage DataTable > .datatable--hover { background: transparent; }
    HomePage DataTable { overflow-x: hidden; }
    HomePage #stuck-box { display: none; }
    HomePage #messages-box { height: 1fr; max-height: 100%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.tab = "home"
        self._on = True                 # the blink phase
        self._blinking: dict = {}       # agent -> its AgentRow, for the rows that blink

    AGENT_COLUMNS = ("agent", "acct", "model", "state", "cache", "ctx", "in", "")

    # The glyph already says what kind of message a row is (⚠ decide, ◷ asked for a
    # reply, ↩ came back, · to read), so the kind column is left out, and the id too; `refused`, the
    # one kind the glyph shares with `answer`, is said in the text.
    MESSAGE_COLUMNS = ("", "", "from", "message")
    # What is stuck is said on the agent's row, on the account's mark and in the header's
    # daemon word, so the STUCK panel is not drawn and the keys skip it.
    PANELS = (tui.ACCOUNTS, tui.AGENTS, tui.MESSAGES)
    SHOW_WAITS = False   # what waits on the founder shows on the rows it is about
    FOUNDER_VIEW = True
    BLINK_S = 0.6

    def _message_cells(self, r) -> list:
        cells = super()._message_cells(r)
        del cells[4], cells[2]      # the kind, and the id
        if r.kind == "refused":
            cells[3] = Text.assemble(("refused ", "red"), cells[3])
        cells[3].no_wrap, cells[3].overflow = True, "ellipsis"
        return cells

    def _draw_stuck(self) -> None:
        pass

    def _draw_messages(self) -> None:
        super()._draw_messages()
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
    def _alarm(r) -> bool:
        """Does this row need the founder: a dialog or a fatal state, or a block."""
        return not r.error and (r.state in tui.NEEDS_A_HUMAN or bool(r.block))

    def _name_cell(self, r, on: bool) -> Text:
        """The name, its fold count, and a warning icon that blinks: on the row itself
        when it needs the founder, on the count when the reports it hides do."""
        out = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}")
        if r.under:
            out.append(f" +{r.under}", style="dim")
            if r.urgency:
                glyph, n, colour = r.urgency
                if glyph == "!" and colour == tui.RED:      # hides an error: blinks
                    out.append(f" ⚠{n}" if on else " " * (len(str(n)) + 2), style="bold red")
                elif glyph == "!":                          # hides a limit or a delay
                    out.append(f" ⚠{n}", style="yellow")
                else:                                       # ✉ asks of the founder, ▶ running
                    out.append(f" {glyph}{n}", style="yellow" if glyph == "✉" else "green")
        if r.asks:
            out.append(f" ✉{r.asks}", style="yellow")
        if self._alarm(r):
            out.append(" ⚠" if on else "  ", style="bold red")
        return out

    def _state_cell(self, r, on: bool) -> Text:
        cell = Text.from_ansi(tui.state_cell(r.state, r.until))
        if r.block and r.state not in tui.NEEDS_A_HUMAN:
            cell.append(f" {r.block}", style="red")
        elif r.stuck:
            cell.append(f" ⚠ {r.stuck}", style="yellow")
        if self._alarm(r) and on:
            cell.stylize("reverse")
        return cell

    def _blinks(self, r) -> bool:
        return self._alarm(r) or (not r.error and r.under and r.urgency
                                  and r.urgency[0] == "!" and r.urgency[2] == tui.RED)

    def _agent_cells(self, r, name) -> list:
        """The Dashboard's cells with the short model and the prompt-cache time left after
        the state. The full model and vendor are on the agent's chat page, Settings tab."""
        cells = super()._agent_cells(r, name)
        if r.error:
            cells.insert(4, "")
            return cells
        cells[0] = self._name_cell(r, self._on)
        cells[2] = Text(r.kind_short, style="dim")
        cells[3] = self._state_cell(r, self._on)
        cells.insert(4, Text.from_ansi(tui.cache_cell(r.cache_left)))
        if self._blinks(r):
            self._blinking[r.name] = r
        return cells

    def _draw_agents(self) -> None:
        self._blinking: dict = {}
        super()._draw_agents()

    def _count_hr(self) -> None:
        """The HR tab says how many proposals wait on the founder, so it is seen from
        the other tabs."""
        con = db.connect()
        try:
            n = len(open_proposals(con))
        finally:
            con.close()
        label = f"HR ({n})" if n else "HR"
        tab = self.query_one("#scope", Tabs).get_tab("hr")
        if str(tab.label) != label:
            tab.label = label

    def _blink(self) -> None:
        """Flip the phase and repaint only the cells that blink; a redraw would read
        every agent again."""
        self._on = not self._on
        table = self.query_one("#agents", DataTable)
        keys = [c.key for c in table.ordered_columns]
        for name, r in self._blinking.items():
            try:
                table.update_cell(name, keys[0], self._name_cell(r, self._on))
                table.update_cell(name, keys[3], self._state_cell(r, self._on))
            except CellDoesNotExist:
                pass

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
            self.query_one("#hr_table").focus()
        else:
            self.set_focus(None)

    def _redraw(self) -> None:
        super()._redraw()
        self._count_hr()
        if self.tab == "hr":
            self.query_one("#hr_view", ProposalsView).refresh_rows()

    def on_key(self, event: events.Key) -> None:
        """Off the Home tab only `q` is the page's: the Dashboard's row keys would act
        on rows that are not on screen, and the tab's own keys are its widgets'."""
        if self.tab == "home":
            return
        event.prevent_default()
        if key_token(event) in ("q", "Q", "\x03"):
            event.stop()
            self.app.exit()

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
