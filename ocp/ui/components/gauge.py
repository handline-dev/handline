"""A usage bar with a `│` for how far through its window we are, and the figures'
cells for one account. ANSI strings, in the shape `tui` draws its own bars."""
from __future__ import annotations

import time

from ...usage import status
from .. import tui

BAR_W = 10


def elapsed_fraction(reset: int | None, window_s: int, now: float | None = None) -> float | None:
    """How far through a usage window we are, from when it resets; None when the
    reset is unknown or already passed, where the window on the figure is gone."""
    left = (reset or 0) - (time.time() if now is None else now)
    return None if not reset or left <= 0 else max(0.0, 1 - left / window_s)


def bar(pct: int | None, width: int, last_seen: bool = False, elapsed: float | None = None) -> str:
    """`last_seen` draws the fill at the value without colour: the figure is what an
    account was last seen at, not a gauge anyone is reading now. `elapsed` puts a `│`
    at that fraction of the bar: a fill short of it is under pace, past it ahead."""
    if pct is None:
        return tui.DIM + "·" * width + tui.R
    filled = max(0, min(width, round(pct / 100 * width)))
    if last_seen:
        head, cells = tui.DIM, ["█"] * filled + ["░"] * (width - filled)
    else:
        head = tui.RED if pct >= 85 else (tui.YEL if pct >= 60 else tui.GRN)
        cells = ["█"] * filled + [tui.DIM + "░"] * (width - filled)
    if elapsed is not None:
        at = max(0, min(width - 1, int(elapsed * width)))
        cells[at] = f"{tui.R}{tui.BOLD}│{tui.R}{head if at < filled else tui.DIM}"
    return head + "".join(cells) + tui.R


def gauge(value: int | None, src: str, elapsed: float | None, show_pct: bool = True) -> str:
    """The bar and, unless `show_pct` is off, its figure with the mark for where it came from."""
    last_seen = src == status.IDLE_MARK
    drawn = bar(value, BAR_W, last_seen, elapsed)
    if not show_pct:
        return drawn
    figure = f"{tui.pct(value)}%{src}"
    return f"{drawn} {tui.DIM + figure + tui.R if last_seen else figure}"


def usage_cells(st, show_pct: bool = True, now: float | None = None) -> tuple[str, str] | None:
    """The 5h and 7d cells of an account, or None for one with no meter to draw."""
    if st.unmetered:
        return None
    shown, src = st.shown_pct_5h()
    return (gauge(shown, src, elapsed_fraction(st.reset_5h, 5 * 3600, now), show_pct),
            gauge(st.trusted_pct_7d, status.mark(st.pct_7d_source),
                  elapsed_fraction(st.reset_7d, 7 * 86400, now), show_pct))
