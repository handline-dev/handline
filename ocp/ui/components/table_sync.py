"""Bring a DataTable up to date without clearing it when only cell text changed.

`DataTable.clear()` drops the scroll offset and cursor along with the rows, so a
table redrawn on a timer jumped back to the top every tick (founder #1975/T309)."""
from __future__ import annotations

from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import DataTable


def _same(a, b) -> bool:
    """Cells are the same when they look the same: `Text.__eq__` ignores the base
    style, so a cell that only turned dim, bold or yellow would look unchanged."""
    if isinstance(a, Text) and isinstance(b, Text):
        return (a.plain, str(a.style), a.spans, a.justify) == (b.plain, str(b.style), b.spans, b.justify)
    return type(a) is type(b) and a == b


def sync_rows(table: DataTable, rows: list[tuple[str, tuple]]) -> bool:
    """Make `table` show `rows`, a list of (row key, cells). Same keys in the same
    order: only the cells that differ are rewritten, and the scroll and cursor are
    untouched. Otherwise the table is rebuilt and its scroll offset put back once
    layout has resized it. Returns True when it was rebuilt."""
    keys = [k for k, _ in rows]
    shown = getattr(table, "_synced", None)
    if shown is not None and [r.key.value for r in table.ordered_rows] == keys \
            and [k for k, _ in shown] == keys:
        for i, ((_, old), (_, new)) in enumerate(zip(shown, rows)):
            for j, (a, b) in enumerate(zip(old, new)):
                if not _same(a, b):
                    table.update_cell_at(Coordinate(i, j), b, update_width=True)
        table._synced = rows
        return False
    scroll = table.scroll_y
    table.clear()
    for key, cells in rows:
        table.add_row(*cells, key=key)
    table._synced = rows
    table.call_after_refresh(lambda: table.scroll_to(y=scroll, animate=False))
    return True
