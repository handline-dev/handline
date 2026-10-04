"""The weekly headroom graph (founder #824), so the stats screen and a home tab share it: the
week left of all accounts together, the range before now as recorded and the same after it as a guess.
What is measured and what is guessed is `usage.outlook`'s; this draws it."""
from __future__ import annotations

import datetime as dt
import time

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Select, Static

from ... import actions, db
from ...infra import textsafe
from ...usage import stats
from ...usage.outlook import Outlook
from ..chat_page import TaskTable
from .key_guide import Key, KeyGuide, KeyHelp
from .table_sync import sync_rows

RANGES = (("12h", 12), ("24h", 24), ("3d", 72), ("7d", 168), ("14d", 336))
DAYS = (("7d", 7), ("14d", 14), ("30d", 30))
# Mid-lightness, colour-blind-safe (Okabe-Ito without its yellow), so a stack reads on a light and a dark
# terminal alike; the agents past the top seven are one grey.
PALETTE = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#7B5EA7")
OTHER_COLOUR = "#8a8a8a"
BLOCKS = " ▁▂▃▄▅▆▇█"


AXIS_W = 6
HEIGHT = 12


def short_tokens(n: int) -> str:
    """12345678 as 12.3M, 1400 as 1.4k."""
    for div, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def _grid(vals: list[float | None], fut: list[bool], over: list[float], ylabels: tuple[str, str, str],
          height: int, axis: str) -> Text:
    """Rows of eighth-blocks, one column per value (0 to 100), `ylabels` (top, middle, zero) at the
    left, and the `axis` line below; a value in the guess has its own colour. `over` is drawn in
    front, from the bottom, in magenta: what the picked agent ate, with the headroom behind it."""
    out = Text()
    for row in range(height - 1, -1, -1):
        label = {height - 1: ylabels[0], (height - 1) // 2: ylabels[1], 0: ylabels[2]}.get(row, "")
        out.append(label.rjust(AXIS_W - 1)[-(AXIS_W - 1):] + "│", style="dim")
        for v, f, o in zip(vals, fut, over):
            base = 0 if v is None else round(v / 100 * height * 8) - row * 8
            mine = round(o / 100 * height * 8) if o > 0 else 0
            mine = max(1, mine) - row * 8 if o > 0 and v is not None else 0
            colour = "yellow" if f else "cyan"
            if mine >= 8:
                out.append("█", style="magenta")
            elif mine > 0:
                out.append(BLOCKS[mine], style="magenta on " + colour if base >= 8 else "magenta")
            else:
                out.append(BLOCKS[max(0, min(8, base))], style=colour)
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


def chart(o: Outlook, width: int, height: int = HEIGHT, span: str = "24h",
          over: list[float] | None = None) -> Text:
    """The weekly headroom: the past in one colour, the guess in another, `now` marked;
    `over` (one value per past point) is drawn on top of it."""
    points = o.past + o.future
    if not points:
        return Text("no weekly readings yet", style="dim")
    cols = max(10, width - AXIS_W)
    idx = _resample(len(points), cols)
    fut = [i >= len(o.past) for i in idx]
    now_col = next((c for c, f in enumerate(fut) if f), cols)
    axis = _axis(cols, [(0, f"-{span}"), (now_col - 2, "now"), (cols - 1 - len(span), f"+{span}")])
    full = [*(over or [0.0] * len(o.past)), *[0.0] * len(o.future)]
    return _grid([points[i][1] for i in idx], fut, [full[i] for i in idx], ("100%", "50%", "0%"), height, axis)


def colours_of(labels: list[str]) -> dict[str, str]:
    """A colour for each of the top agents in order, and the grey for `stats.OTHER`."""
    return {l: OTHER_COLOUR if l == stats.OTHER else PALETTE[i % len(PALETTE)] for i, l in enumerate(labels)}


def _cell(a: int, h: int, segs: list[tuple[int, int, str]]) -> tuple[str, Style]:
    """One cell of a stacked bar covering eighths [a, a+8) of the height, the bar `h` eighths tall made
    of `segs` (start, end, colour) from the bottom. A boundary inside the cell is a block of the lower
    colour on the upper colour's background."""
    top = min(h, a + 8)
    if top <= a:
        return " ", Style()
    inside = [s for s in segs if s[1] > a and s[0] < top]
    if len(inside) == 1 or top - a < 8:
        colour = inside[-1][2]
        return ("█" if top - a >= 8 else BLOCKS[top - a]), Style(color=colour)
    low, up = inside[0], inside[1]
    return BLOCKS[max(1, min(7, low[1] - a))], Style(color=low[2], bgcolor=up[2])


def cost_chart(days: list[str], stacked: dict[str, dict[str, float]], labels: list[str],
               colours: dict[str, str], width: int, height: int = HEIGHT) -> Text:
    """One stacked bar per day (oldest left), each agent's part of the day's cost in its own colour,
    the scale in dollars at the left and the days' dates under the bars."""
    totals = [sum(stacked.get(d, {}).values()) for d in days]
    top = max(totals, default=0.0)
    if not days or top <= 0:
        return Text("no cost recorded in this range", style="dim")
    cols = max(10, width - AXIS_W)
    bar = max(1, min(6, cols // len(days) - 1))
    unit = bar + 1 if cols // len(days) > 1 else 1
    scale = height * 8 / top
    bars = []
    for d in days:
        edge, segs = 0, []
        for l in labels:
            c = stacked.get(d, {}).get(l, 0.0)
            if c <= 0:
                continue
            nxt = edge + c * scale
            segs.append((round(edge), max(round(nxt), round(edge) + 1), colours[l]))
            edge = nxt
        bars.append((round(edge), segs))
    out = Text()
    for row in range(height - 1, -1, -1):
        label = {height - 1: f"${top:.0f}" if top >= 10 else f"${top:.2f}", (height - 1) // 2: "", 0: "$0"}.get(row, "")
        out.append(label.rjust(AXIS_W - 1)[-(AXIS_W - 1):] + "│", style="dim")
        for h, segs in bars:
            ch, style = _cell(row * 8, h, segs)
            out.append(ch * bar, style=style)
            out.append(" " * (unit - bar))
        out.append("\n")
    step = max(1, -(-6 // unit))        # a date is five cells: not under every bar when they are narrow
    marks = [(i * unit, d[5:]) for i, d in enumerate(days) if i % step == 0]
    out.append(" " * (AXIS_W - 1) + "└" + _axis(cols, marks), style="dim")
    return out


class OutlookView(Vertical):
    can_focus = True
    BINDINGS = [Binding("r", "range", "range"), Binding("v", "view", "view"),
                Binding("backspace", "cost_back", "teams", show=False),
                Binding("question_mark", "key_help", "keys")]
    DEFAULT_CSS = """
    OutlookView { height: 1fr; padding: 0 1; }
    OutlookView #outlook_chart { height: auto; }
    OutlookView #outlook_note { height: auto; color: $text-muted; }
    OutlookView #outlook_table { height: 1fr; margin-top: 1; }
    OutlookView #cost_table { height: 1fr; margin-top: 1; }
    OutlookView #outlook_legend { height: auto; }
    """

    def __init__(self, back: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.back = back
        self.outlook = Outlook()
        self.uses: list[stats.UseRow] = []
        self.picked: str | None = None
        self.over: list[float] = []
        self.range = "24h"
        self.mode = "left"                  # "left": the week left of the accounts; "cost": what the agents cost per day
        self.days = "14d"
        self.per_day: dict[str, dict[str, float]] = {}
        self.cost_pick: str | None = None
        self.cost_team: str | None = None      # the team the bars are drilled into, None for the team level
        self._now = int(time.time())

    def _keys(self) -> list[Key]:
        return [Key("v", "view", 1, "switch between the session left and the cost per day"),
                Key("backspace", "teams", 2, "cost view: back from a team's agents to the teams"),
                Key("r", "range", 1, "step through the date range: 12h, 24h, 3d, 7d, 14d (cost view: 7d, 14d, 30d)"),
                *([Key("esc", "back", 1, "back")] if self.back else [])]

    def compose(self) -> ComposeResult:
        yield Static(id="outlook_title")
        yield Select([("view: session left", "left"), ("view: cost per day", "cost")], id="o_view", value=self.mode,
                     allow_blank=False, compact=True)
        yield Select([(f"range: ±{name}", name) for name, _ in RANGES], id="o_range", value=self.range,
                     allow_blank=False, compact=True)
        yield Select([(f"range: {name}", name) for name, _ in DAYS], id="o_days", value=self.days,
                     allow_blank=False, compact=True)
        yield Static(id="outlook_chart")
        yield Static(id="outlook_legend")
        yield Static(id="outlook_note")
        yield TaskTable(id="outlook_table", cursor_type="row", zebra_stripes=True)
        yield TaskTable(id="cost_table", cursor_type="row", zebra_stripes=True)
        yield KeyGuide(self._keys(), id="outlook_foot")

    def on_mount(self) -> None:
        self.query_one("#outlook_table", DataTable).add_columns("agent", "tokens", "cost", "share", "~ week %")
        self.query_one("#cost_table", DataTable).add_columns("agent", "cost", "share")
        self._show_mode()
        self.set_interval(30.0, self.refresh_rows)
        self.refresh_rows()

    def _show_mode(self) -> None:
        cost = self.mode == "cost"
        for sel, shown in (("#o_range", not cost), ("#o_days", cost), ("#outlook_table", not cost),
                           ("#cost_table", cost), ("#outlook_legend", cost)):
            self.query_one(sel).display = shown

    def on_resize(self) -> None:
        self._draw()

    def _load(self):
        """What the database says (usage files are read and parsed: hundreds of ms), read on a
        thread so the page moves while it works."""
        con = db.connect()
        try:
            now, hours = int(time.time()), dict(RANGES)[self.range]
            if self.mode == "cost":
                return actions.daily_agent_cost(con, now, dict(DAYS)[self.days]), now
            outlook = actions.weekly_outlook(con, now, hours)
            uses = stats.group_uses(actions.agent_use(con, now, hours))
            picked = self.picked if self.picked in {r.key for r in uses} else None
            use = next((r.use for r in uses if r.key == picked), None)
            over = (actions.agent_overlay(con, use, now, hours, [t for t, _ in outlook.past])
                    if use else [])
        finally:
            con.close()
        return outlook, uses, picked, over

    def refresh_rows(self) -> None:
        if not self.display:
            return

        def work() -> None:
            data = self._load()
            self.app.call_from_thread(self._apply_cost if self.mode == "cost" else self._apply, *data)
        self.run_worker(work, thread=True, exclusive=True, group="outlook", exit_on_error=False)

    def _apply(self, outlook, uses, picked, over) -> None:
        self.outlook, self.uses, self.picked, self.over = outlook, uses, picked, over
        table = self.query_one("#outlook_table", DataTable)
        keep = table.cursor_row
        out = []
        for r in self.uses:
            u = r.use
            name = Text(("└ " if r.depth else "") + textsafe.oneline(r.label), style="" if r.depth else "bold")
            out.append((r.key, (name, Text(short_tokens(u.tokens), justify="right"),
                                Text(f"${u.cost:.2f}", justify="right"), Text(f"{u.share * 100:.0f}%", justify="right"),
                                Text("-" if u.est_pct is None else f"~{u.est_pct:.1f}%", justify="right"))))
        if sync_rows(table, out) and self.uses:
            table.move_cursor(row=min(keep, len(self.uses) - 1), scroll=False)
        self._draw()

    def _series(self) -> dict[str, dict[str, float]]:
        """The days' cost by what the bars stack: the teams, or the agents of the team drilled into."""
        return stats.by_team(self.per_day, self.cost_team)

    def _colour(self, key: str) -> str | None:
        """The colour a table row shows: a team's at the team level, an agent's inside its drilled team,
        and a leaf's own at the team level is its team's."""
        top = colours_of(stats.stack_agents(self._series())[0])
        if key.startswith("grp:"):
            return None if self.cost_team == key[4:] else top.get(key[4:], OTHER_COLOUR)
        team = stats.team_of(key)
        if self.cost_team is not None:
            return top.get(key, OTHER_COLOUR) if team == self.cost_team else None
        return top.get(team, OTHER_COLOUR)

    def _apply_cost(self, per_day: dict, now: int) -> None:
        self.per_day = per_day
        self._now = now
        tree = stats.cost_tree(per_day)
        everyone = sum(c for key, _, depth, c in tree if depth == 0)
        if self.cost_team not in {k[4:] for k, *_ in tree if k.startswith("grp:")}:
            self.cost_team = None
        table = self.query_one("#cost_table", DataTable)
        keep = table.cursor_row
        rows = []
        for key, label, depth, cost in tree:
            colour = self._colour(key)
            name = Text("  └ " if depth else "", style="dim")
            if colour:
                name.append("■ ", style=colour)
            name.append(textsafe.oneline(label), style="" if depth else "bold")
            rows.append((key, (name, Text(f"${cost:.2f}", justify="right"),
                               Text(f"{cost / everyone * 100:.0f}%" if everyone else "-", justify="right"))))
        if self.cost_pick not in {k for k, *_ in tree}:
            self.cost_pick = None
        if sync_rows(table, rows) and rows:
            table.move_cursor(row=min(keep, len(rows) - 1), scroll=False)
        self._draw()

    def action_cost_back(self) -> None:
        if self.mode == "cost" and (self.cost_team or self.cost_pick):
            self.cost_team = self.cost_pick = None
            self._apply_cost(self.per_day, self._now)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if event.row_key.value is not None and event.data_table.id == "cost_table":
            key = str(event.row_key.value)
            if key.startswith("grp:"):       # a team: its agents become the bars; again, back to the teams
                self.cost_team = None if self.cost_team == key[4:] else key[4:]
                self.cost_pick = None
            else:
                self.cost_pick = None if self.cost_pick == key else key
            self._apply_cost(self.per_day, self._now)
        elif event.row_key.value is not None:
            agent = str(event.row_key.value)
            self.picked = None if self.picked == agent else agent
            self.refresh_rows()

    def _days(self) -> list[str]:
        n = dict(DAYS)[self.days]
        today = dt.date.fromtimestamp(self._now)
        return [(today - dt.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]

    def _draw_cost(self) -> None:
        width = self.size.width - 2
        height = max(4, min(HEIGHT, (self.size.height - 11) // 2))
        series = self._series()
        labels, stacked = stats.stack_agents(series)
        colours = colours_of(labels)
        days = self._days()
        total = sum(sum(v.values()) for v in series.values())
        title = Text.assemble(("COST PER DAY  ", "bold"), (f"${total:.2f} at list price over {self.days}", "dim"))
        if self.cost_team:
            title.append_text(Text.assemble(("   ", ""), (textsafe.oneline(self.cost_team), "bold"),
                                            (" - its agents (backspace: the teams)", "dim")))
        if self.cost_pick:
            own = self.cost_pick
            shown = stats.team_of(own) if self.cost_team is None else own
            mine = {d: {own: per.get(own, 0.0)} for d, per in self.per_day.items()}
            colour = colours.get(shown, OTHER_COLOUR)
            stacked, labels, colours = mine, [own], {own: colour}
            title.append_text(Text.assemble(("   ", ""), (textsafe.oneline(own), colour + " bold"),
                                            (f" ${sum(m[own] for m in mine.values()):.2f}", "dim")))
        legend = Text()
        for l in labels:
            legend.append("■ ", style=colours[l])
            legend.append(textsafe.oneline(l) + "   ")
        self.query_one("#outlook_title", Static).update(title)
        self.query_one("#outlook_chart", Static).update(
            cost_chart(days, stacked, labels, colours, width, height) if width > 0 else Text(""))
        self.query_one("#outlook_legend", Static).update(legend)
        self.query_one("#outlook_note", Static).update(
            "one bar per day, one colour per team (the top seven, the rest grey 'other') · Enter on a team: its agents, "
            "on an agent: its days · cost is the list price of the token log, not what the plan charged")

    def _draw(self) -> None:
        if self.mode == "cost":
            return self._draw_cost()
        o = self.outlook
        width = self.size.width - 2
        height = max(4, min(HEIGHT, (self.size.height - 9) // 2))
        title = Text.assemble(("WEEK LEFT  ", "bold"),
                              (f"{o.accounts} account{'s' if o.accounts != 1 else ''} together", "dim"))
        if self.picked:
            title.append_text(Text.assemble(("   magenta: ", "dim"), (f"{self.picked.removeprefix('grp:')}", "magenta bold"),
                                            (f" ate{self._picked_pct()}", "dim")))
        self.query_one("#outlook_title", Static).update(title)
        self.query_one("#outlook_chart", Static).update(
            chart(o, width, height, self.range, self.over if self.picked else None) if width > 0 else Text(""))
        self.query_one("#outlook_note", Static).update(
            "cyan: recorded · yellow: guess · Enter on an agent: what it used (magenta) · ~ is an estimate")

    def _picked_pct(self) -> str:
        u = next((r.use for r in self.uses if r.key == self.picked), None)
        return f" about {u.est_pct:.1f}% of the week in this range" if u and u.est_pct is not None else ""

    def action_range(self) -> None:
        cost = self.mode == "cost"
        names = [n for n, _ in (DAYS if cost else RANGES)]
        now = self.days if cost else self.range
        self.query_one("#o_days" if cost else "#o_range", Select).value = names[(names.index(now) + 1) % len(names)]

    def action_view(self) -> None:
        self.query_one("#o_view", Select).value = "left" if self.mode == "cost" else "cost"

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        if event.select.id == "o_view":
            if event.value != self.mode:
                self.mode = str(event.value)
                self._show_mode()
                self.refresh_rows()
        elif event.select.id == "o_days":
            if event.value != self.days:
                self.days = str(event.value)
                self.refresh_rows()
        elif event.value != self.range:
            self.range = str(event.value)
            self.refresh_rows()

    def action_key_help(self) -> None:
        self.app.push_screen(KeyHelp("WEEK LEFT keys", self._keys()))
