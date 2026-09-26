"""The weekly headroom graph (founder #824), so the stats screen and a home tab share it: the
week left of all accounts together, the last 24h as recorded and the next 24h as a guess.
What is measured and what is guessed is `usage.outlook`'s; this draws it."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Static

from ... import actions, db
from ...usage.outlook import Outlook
from .key_guide import Key, KeyGuide, KeyHelp

BLOCKS = " ▁▂▃▄▅▆▇█"
AXIS_W = 6
HEIGHT = 12


def chart(o: Outlook, width: int, height: int = HEIGHT) -> Text:
    """The graph as text: a column of eighth-blocks per time slot, the past in one colour, the
    guess in another, a `now` mark under the point between them."""
    points = o.past + o.future
    cols = max(10, width - AXIS_W)
    out = Text()
    if not points:
        return Text("no weekly readings yet", style="dim")
    idx = [min(len(points) - 1, c * len(points) // cols) for c in range(cols)]
    vals = [points[i][1] for i in idx]
    is_future = [i >= len(o.past) for i in idx]
    for row in range(height - 1, -1, -1):
        label = {height - 1: "100%", (height - 1) // 2: " 50%", 0: "  0%"}.get(row, "")
        out.append(label.rjust(AXIS_W - 1) + "│", style="dim")
        for v, fut in zip(vals, is_future):
            eighths = 0 if v is None else round(v / 100 * height * 8) - row * 8
            ch = BLOCKS[max(0, min(8, eighths))]
            out.append(ch, style="yellow" if fut else "cyan")
        out.append("\n")
    now_col = next((c for c, f in enumerate(is_future) if f), cols)
    axis = [" "] * cols
    for c, ch in ((0, "-24h"), (max(0, now_col - 2), "now"), (max(0, cols - 4), "+24h")):
        for k, x in enumerate(ch):
            if c + k < cols:
                axis[c + k] = x
    out.append(" " * (AXIS_W - 1) + "└" + "".join(axis), style="dim")
    return out


class OutlookView(Vertical):
    BINDINGS = [Binding("question_mark", "key_help", "keys")]
    DEFAULT_CSS = """
    OutlookView { height: 1fr; padding: 0 1; }
    OutlookView #outlook_chart { height: 1fr; }
    OutlookView #outlook_note { height: auto; color: $text-muted; margin-top: 1; }
    """

    def __init__(self, back: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.back = back
        self.outlook = Outlook()

    def _keys(self) -> list[Key]:
        return [*([Key("esc", "back", 1, "back")] if self.back else [])]

    def compose(self) -> ComposeResult:
        yield Static(id="outlook_title")
        yield Static(id="outlook_chart")
        yield Static(id="outlook_note")
        yield KeyGuide(self._keys(), id="outlook_foot")

    def on_mount(self) -> None:
        self.set_interval(30.0, self.refresh_rows)
        self.refresh_rows()

    def on_resize(self) -> None:
        self._draw()

    def refresh_rows(self) -> None:
        if not self.display:
            return
        con = db.connect()
        try:
            self.outlook = actions.weekly_outlook(con, int(time.time()))
        finally:
            con.close()
        self._draw()

    def _draw(self) -> None:
        o = self.outlook
        self.query_one("#outlook_title", Static).update(Text.assemble(
            ("WEEK LEFT  ", "bold"), (f"{o.accounts} account{'s' if o.accounts != 1 else ''} together", "dim")))
        width = self.size.width - 2
        self.query_one("#outlook_chart", Static).update(
            chart(o, width, max(4, min(HEIGHT, self.size.height - 5))) if width > 0 else Text(""))
        self.query_one("#outlook_note", Static).update(
            "cyan: recorded, yellow: a guess - each account keeps its pace of the last day, and when its "
            "week resets what it had used comes back")

    def action_key_help(self) -> None:
        self.app.push_screen(KeyHelp("WEEK LEFT keys", self._keys()))
