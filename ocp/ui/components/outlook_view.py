"""The weekly headroom graph (founder #824), so the stats screen and a home tab share it: the
week left of all accounts together, the last 24h as recorded and the next 24h as a guess.
What is measured and what is guessed is `usage.outlook`'s; this draws it."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Select, Static

from ... import actions, db
from ...infra import textsafe
from ...usage.outlook import Outlook
from ..chat_page import TaskTable
from .key_guide import Key, KeyGuide, KeyHelp

RANGES = (("12h", 12), ("24h", 24), ("3d", 72), ("7d", 168), ("14d", 336))
BLOCKS = " ▁▂▃▄▅▆▇█"


def short_tokens(n: int) -> str:
    """12345678 as 12.3M, 1400 as 1.4k."""
    for div, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)
AXIS_W = 6
HEIGHT = 12


def _grid(vals: list[float | None], fut: list[bool], top: float, ylabels: tuple[str, str, str],
          height: int, axis: str) -> Text:
    """Rows of eighth-blocks, one column per value scaled to `top`, `ylabels` (top, middle, zero)
    at the left, and the `axis` line below; a value in the guess is drawn in its own colour."""
    out = Text()
    for row in range(height - 1, -1, -1):
        label = {height - 1: ylabels[0], (height - 1) // 2: ylabels[1], 0: ylabels[2]}.get(row, "")
        out.append(label.rjust(AXIS_W - 1)[-(AXIS_W - 1):] + "│", style="dim")
        for v, f in zip(vals, fut):
            eighths = 0 if v is None or top <= 0 else round(v / top * height * 8) - row * 8
            out.append(BLOCKS[max(0, min(8, eighths))], style="yellow" if f else "cyan")
        out.append("\n")
    out.append(" " * (AXIS_W - 1) + "└" + axis, style="dim")
    return out


def _axis(cols: int, marks: list[tuple[int, str]]) -> str:
    axis = [" "] * cols
    for c, text in marks:
        for k, x in enumerate(text):
            if 0 <= c + k < cols:
                axis[c + k] = x
    return "".join(axis)


def _resample(n: int, cols: int) -> list[int]:
    return [min(n - 1, c * n // cols) for c in range(cols)]


def chart(o: Outlook, width: int, height: int = HEIGHT, span: str = "24h") -> Text:
    """The weekly headroom: the past in one colour, the guess in another, `now` marked."""
    points = o.past + o.future
    if not points:
        return Text("no weekly readings yet", style="dim")
    cols = max(10, width - AXIS_W)
    idx = _resample(len(points), cols)
    fut = [i >= len(o.past) for i in idx]
    now_col = next((c for c, f in enumerate(fut) if f), cols)
    axis = _axis(cols, [(0, f"-{span}"), (now_col - 2, "now"), (cols - 1 - len(span), f"+{span}")])
    return _grid([points[i][1] for i in idx], fut, 100, ("100%", "50%", "0%"), height, axis)


def bars(series: list[tuple[int, float]], width: int, height: int, span: str) -> Text:
    """One agent's cost per time slot over the last `span`, scaled to its busiest slot."""
    if not series:
        return Text("nothing logged", style="dim")
    cols = max(10, width - AXIS_W)
    idx = _resample(len(series), cols)
    vals = [series[i][1] for i in idx]
    top = max(vals)
    axis = _axis(cols, [(0, f"-{span}"), (cols - 3, "now")])
    return _grid(vals, [False] * cols, top, (f"${top:.2f}", f"${top / 2:.2f}", "$0"), height, axis)


class OutlookView(Vertical):
    can_focus = True
    BINDINGS = [Binding("r", "range", "range"), Binding("question_mark", "key_help", "keys")]
    DEFAULT_CSS = """
    OutlookView { height: 1fr; padding: 0 1; }
    OutlookView #outlook_chart { height: auto; }
    OutlookView #outlook_note { height: auto; color: $text-muted; }
    OutlookView #outlook_table { height: 1fr; margin-top: 1; }
    """

    def __init__(self, back: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.back = back
        self.outlook = Outlook()
        self.uses: list = []
        self.picked: str | None = None
        self.series: list[tuple[int, float]] = []
        self.range = "24h"

    def _keys(self) -> list[Key]:
        return [Key("r", "range", 1, "step through the date range: 12h, 24h, 3d, 7d, 14d"),
                *([Key("esc", "back", 1, "back")] if self.back else [])]

    def compose(self) -> ComposeResult:
        yield Static(id="outlook_title")
        yield Select([(f"range: ±{name}", name) for name, _ in RANGES], id="o_range", value=self.range,
                     allow_blank=False, compact=True)
        yield Static(id="outlook_chart")
        yield Static(id="outlook_note")
        yield TaskTable(id="outlook_table", cursor_type="row", zebra_stripes=True)
        yield KeyGuide(self._keys(), id="outlook_foot")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("agent", "tokens", "cost", "share", "~ week %")
        self.set_interval(30.0, self.refresh_rows)
        self.refresh_rows()

    def on_resize(self) -> None:
        self._draw()

    def refresh_rows(self) -> None:
        if not self.display:
            return
        con = db.connect()
        try:
            self.outlook = actions.weekly_outlook(con, int(time.time()), dict(RANGES)[self.range])
            now, hours = int(time.time()), dict(RANGES)[self.range]
            self.uses = actions.agent_use(con, now, hours)
            if self.picked not in {u.agent for u in self.uses}:
                self.picked = None
            self.series = actions.agent_use_series(con, self.picked, now, hours) if self.picked else []
        finally:
            con.close()
        table = self.query_one(DataTable)
        keep = table.cursor_row
        table.clear()
        for u in self.uses:
            table.add_row(Text(textsafe.oneline(u.agent)), Text(short_tokens(u.tokens), justify="right"),
                          Text(f"${u.cost:.2f}", justify="right"), Text(f"{u.share * 100:.0f}%", justify="right"),
                          Text("-" if u.est_pct is None else f"~{u.est_pct:.1f}%", justify="right"), key=u.agent)
        if self.uses:
            table.move_cursor(row=min(keep, len(self.uses) - 1))
        self._draw()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if event.row_key.value is not None:
            agent = str(event.row_key.value)
            self.picked = None if self.picked == agent else agent
            self.refresh_rows()

    def _draw(self) -> None:
        o = self.outlook
        width = self.size.width - 2
        height = max(4, min(HEIGHT, (self.size.height - 9) // 2))
        if self.picked:
            total = sum(c for _, c in self.series)
            self.query_one("#outlook_title", Static).update(Text.assemble(
                (f"{self.picked}  ", "bold"),
                (f"cost at list price per slot, last {self.range}: ${total:.2f} in all"
                 f"{self._picked_pct()}", "dim")))
            drawn = bars(self.series, width, height, self.range) if width > 0 else Text("")
            note = "enter or a click on the same row goes back to the week left"
        else:
            self.query_one("#outlook_title", Static).update(Text.assemble(
                ("WEEK LEFT  ", "bold"), (f"{o.accounts} account{'s' if o.accounts != 1 else ''} together", "dim")))
            drawn = chart(o, width, height, self.range) if width > 0 else Text("")
            note = ("cyan: recorded, yellow: a guess - each account keeps its pace of the last day, and when its "
                    "week resets what it had used comes back. Enter or a click on an agent draws its use here. "
                    "~ week % is an estimate: the agent's cost share of each account times the points of the "
                    "week that account used in the range, averaged over the accounts")
        self.query_one("#outlook_chart", Static).update(drawn)
        self.query_one("#outlook_note", Static).update(note)

    def _picked_pct(self) -> str:
        u = next((u for u in self.uses if u.agent == self.picked), None)
        return f", about {u.est_pct:.1f}% of the week" if u and u.est_pct is not None else ""

    def action_range(self) -> None:
        names = [n for n, _ in RANGES]
        self.query_one("#o_range", Select).value = names[(names.index(self.range) + 1) % len(names)]

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        if event.value != self.range:
            self.range = str(event.value)
            self.refresh_rows()

    def action_key_help(self) -> None:
        self.app.push_screen(KeyHelp("WEEK LEFT keys", self._keys()))
