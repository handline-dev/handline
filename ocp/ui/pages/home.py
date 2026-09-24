"""The bridge's home page. `bridge_textual.Dashboard` is the previous home page,
kept unchanged; `HomePage` is the one `BridgeApp` opens, so edits here never
touch the Dashboard. Its header is the chat page's (`page_header`)."""
from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.errors import NoWidget
from textual.widgets import DataTable, Static, Tabs

from ... import db, tui
from ...bridge_textual import Dashboard, key_token
from ..components.page_header import page_header

_PANEL_OF_TABLE = {"accounts": tui.ACCOUNTS, "agents": tui.AGENTS,
                   "messages": tui.MESSAGES, "stuck": tui.STUCK}
# The Dashboard's own widgets: what the Home tab shows and the Settings tab hides.
_HOME_WIDGETS = ("#header", "#accounts-box", "#agents-box", "#messages-box", "#stuck-box", "#foot")


class HomePage(Dashboard):
    AUTO_FOCUS = ""     # the keys act on the dashboard until a tab is clicked
    # The row cursor is the selection. DataTable's hover highlight restarts at row 0
    # each redraw, so it reads as a second, wrong selection.
    DEFAULT_CSS = """
    HomePage DataTable > .datatable--hover { background: transparent; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.showing_settings = False

    def compose(self) -> ComposeResult:
        yield from page_header([("Home", "home"), ("Settings", "settings")])
        yield from super().compose()
        yield Static("Settings", id="settings_view")

    def on_mount(self) -> None:
        self.sub_title = "home"
        self.query_one("#settings_view").display = False

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.showing_settings = event.tab.id == "settings"
        for sel in _HOME_WIDGETS:
            self.query_one(sel).display = not self.showing_settings
        self.query_one("#settings_view").display = self.showing_settings
        self.set_focus(None)

    def on_key(self, event: events.Key) -> None:
        """On Settings only `q` does anything; the Dashboard's row keys would act on
        rows that are not on screen."""
        if not self.showing_settings:
            return
        event.prevent_default()
        event.stop()
        if key_token(event) in ("q", "Q", "\x03"):
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
