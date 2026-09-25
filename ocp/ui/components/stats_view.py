"""The stats table as a widget (T65b), so the stats screen and a home tab share it: each agent's
use of each account over 5h, 24h or 7d - tokens, cost at list price, its share of the
account, and an estimate of the plan percent it used. What is measured and estimated is
`usage.stats.token_share`'s; this draws it."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ... import actions, db
from ...infra import textsafe
from ...usage import stats
from .key_guide import Key, KeyGuide, KeyHelp

WINDOWS = tuple(stats.SHARE_WINDOWS)
KEYS = [Key("w", "window", 1, "step through the windows: 5h, 24h, 7d")]


def short_tokens(n: int) -> str:
    """12345678 as 12.3M, 1400 as 1.4k."""
    for div, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def pct(x: float | None, approx: bool = False) -> str:
    return "-" if x is None else f"{'~' if approx else ''}{x * 100:.0f}%"


def cells(s: stats.Share) -> tuple[str, ...]:
    """One Share as the table's cells. `est_pct` is already a percent, not a fraction."""
    est = "-" if s.est_pct is None else f"~{s.est_pct:.0f}%"
    return (textsafe.oneline(s.agent), s.account, short_tokens(s.tokens), f"${s.cost:.2f}",
            pct(s.share), pct(s.token_share), est)


class StatsView(Vertical):
    BINDINGS = [Binding("w", "window", "window"), Binding("question_mark", "key_help", "keys")]
    DEFAULT_CSS = """
    StatsView { height: 1fr; }
    StatsView #stats_table { height: 1fr; }
    """

    def __init__(self, back: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.back = back
        self.window = "7d"
        self.rows: list[stats.Share] = []

    def _keys(self) -> list[Key]:
        return [*KEYS, *([Key("esc", "back", 1, "back")] if self.back else [])]

    def compose(self) -> ComposeResult:
        yield Static(id="stats_title")
        yield DataTable(id="stats_table", zebra_stripes=True, cursor_type="row")
        yield Static("cost is at list price; ~ is an estimate: the agent's share of the account "
                     "times the plan percent it reports", id="stats_note")
        yield KeyGuide(self._keys(), id="stats_foot")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("agent", "account", "tokens", "cost", "share",
                                              "tokens%", "est %")
        self.set_interval(10.0, self.refresh_rows)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if not self.display:
            return
        con = db.connect()
        try:
            self.rows = actions.usage_shares(con, int(time.time()), self.window)
        finally:
            con.close()
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for s in self.rows:
            table.add_row(*(Text(c) for c in cells(s)))
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        i = WINDOWS.index(self.window)
        self.query_one("#stats_title", Static).update(
            f"STATS  {self.window}  (w: {WINDOWS[(i + 1) % len(WINDOWS)]})"
            + ("" if self.rows else "  - nothing logged in this window"))

    def action_window(self) -> None:
        self.window = WINDOWS[(WINDOWS.index(self.window) + 1) % len(WINDOWS)]
        self.refresh_rows()

    def action_key_help(self) -> None:
        self.app.push_screen(KeyHelp("STATS keys", self._keys()))
