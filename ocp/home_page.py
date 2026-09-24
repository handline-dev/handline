"""The bridge's home page. `bridge_textual.Dashboard` is the previous home page,
kept unchanged; `HomePage` starts as the same screen and is the one `BridgeApp`
opens, so edits here never touch the Dashboard."""
from __future__ import annotations

from textual import events
from textual.widgets import DataTable

from . import db, tui
from .bridge_textual import Dashboard

_PANEL_OF_TABLE = {"accounts": tui.ACCOUNTS, "agents": tui.AGENTS,
                   "messages": tui.MESSAGES, "stuck": tui.STUCK}


class HomePage(Dashboard):
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

    def on_click(self, event: events.Click) -> None:
        """A click on a row puts the cursor there, as the arrow keys would."""
        row = event.style.meta.get("row")
        table = next((w for w in event.widget.ancestors_with_self
                      if isinstance(w, DataTable)), None) if event.widget else None
        panel = _PANEL_OF_TABLE.get(table.id) if table is not None else None
        if panel is None or row is None:
            return
        key = self._row_key(table, panel, row)
        if key is None:
            return
        event.stop()
        self.panel = panel
        self.sel[panel] = key
        self._redraw()
