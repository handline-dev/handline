"""What the agent and account rows hold as data, shared by the CLI, the daemon's row checks and the tests."""
from __future__ import annotations

import re
import time

from ..team import agents
from ..usage import status
from ..infra import timefmt


R = "\033[0m"

DIM = "\033[2m"

RED, YEL, GRN, CYA = "\033[31m", "\033[33m", "\033[32m", "\033[36m"

_ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")

STATE_COLOR = {
    "RUNNING": GRN, "IDLE": DIM, "PENDING": CYA, "MOVING": YEL,
    "DIALOG": RED, "FATAL": RED,
}

# What an agent reads as while it sits at a prompt only the founder can answer.
# Its state underneath is still RUNNING; this is drawn over it. Not one of
# SCREEN_WORDS: it is the pinned block's own heading, never a row's state cell
# (that says DIALOG - see screen_state()).
WAITING_ON_YOU = "WAITING ON YOU"

def bar(pct: int | None, width: int = 10, last_seen: bool = False) -> str:
    """`last_seen` draws the fill at the value but with no colour: the figure is
    what an account was last seen at, not a gauge anyone is reading now."""
    if pct is None:
        return DIM + "·" * width + R
    filled = max(0, min(width, round(pct / 100 * width)))
    if last_seen:
        return DIM + "█" * filled + "░" * (width - filled) + R
    color = RED if pct >= 85 else (YEL if pct >= 60 else GRN)
    return color + "█" * filled + DIM + "░" * (width - filled) + R

# The ACCOUNTS panel's columns, in the order they are drawn: the heading, every
# row and the overflow test read these, so a width or a position changed here
# moves all three and one written anywhere else is a bug. Each bounded cell
# must stay strictly narrower than its column. Order is also priority: see
# `acct_cols`.
ACCT_W = {"name": 10, "five": 17, "reset5": 7, "agents": 11, "seven": 12, "reset7": 7,
          "runway": 15}

ACCT_HEAD = {"name": "account", "five": "5h usage", "reset5": "reset", "agents": "agents ▶",
             "seven": "7d", "reset7": "7d rst", "runway": "runway (est.)"}

ACCT_BAR_W = {"five": 10, "seven": 5}

def _gauge(value: int | None, width: int, src: str) -> str:
    """A bar and its figure, where `src` is the mark saying where it came from."""
    if src == status.IDLE_MARK:
        return f"{bar(value, width, last_seen=True)} {DIM}{pct(value)}%{src}{R}"
    return f"{bar(value, width)} {pct(value)}%{src}"

def agents_cell(counts: tuple[int, int] | None, unplaced: int = 0,
                at_cap: bool = False) -> str:
    """Agents assigned to an account and how many of them are running: "3 ▶1".
    The figures are `load.roster`'s. None is a roster not read, which is not
    the same as none: "?" against a dim "0". With agents that could not be
    placed anywhere each figure is a floor and says so with a "?". The running
    figure is yellow while the account has no slot left under its cap, which is
    one reason the tick beside the account is red, said without a column.

    The colour and the digits are two counts: at_cap is `Load.active >= cap`,
    which counts an agent that has just moved and not yet run, while ▶ is
    `roster`'s running and unclosed. A cap of 2 with one running and one moved
    agent draws "2 ▶1" with the ▶1 yellow; the account's page says why."""
    if counts is None:
        return f"{DIM}?{R}"
    assigned, running = counts
    q = "?" if unplaced else ""
    if not assigned and not unplaced:
        return f"{DIM}0{R}"
    colour = YEL if at_cap else (STATE_COLOR["RUNNING"] if running else DIM)
    run = f"{colour}▶{running}{q}{R}"
    return f"{assigned}{q} {run}"

def account_cells(name: str, st, now: float | None = None,
                  counts: tuple[int, int] | None = None,
                  unplaced: int = 0, at_cap: bool = False, ld=None) -> dict[str, str]:
    """The bounded cells of an ACCOUNTS row, unpadded and keyed like ACCT_W.
    The name is free text and is cut to fit where it is drawn. `counts` is the
    account's (assigned, running) from `load.roster`, None where it was not read;
    `at_cap` whether the pick has no slot left on it (`load.Load.at_cap`); `ld`
    the account's `load.Load`, which the runway needs to judge the week by the
    pick's own figure."""
    if st.unmetered:
        return {"agents": agents_cell(counts, unplaced, at_cap), "five": f"{DIM}unmetered{R}",
                "reset5": "", "seven": "", "reset7": "", "runway": ""}
    runway_text = status.describe_runway(status.runway(st, status.read_burn(name), now, ld))
    # One definition of the figure and its mark, shared with `hl account list`.
    # A last-seen figure is drawn at its value, dimmed and uncoloured, with its
    # "?", so it cannot be read as a live gauge at the same number and is not
    # drawn as an unknown either. Display only.
    shown, src = st.shown_short()
    five = _gauge(shown, ACCT_BAR_W["five"], src)
    return {
        "agents": agents_cell(counts, unplaced, at_cap),
        "five": five,
        "reset5": countdown(st.short.reset),
        # The weekly figure carries its provenance like the 5h one. Drawn
        # bare, a number from a stale snapshot - or from a week that has since
        # rolled over - reads as this week's measurement.
        "seven": _gauge(st.long_trusted, ACCT_BAR_W["seven"],
                        status.mark(st.long_source)),
        # The deadline the ordering spends against: when this window's
        # headroom is lost if nobody uses it.
        "reset7": countdown(st.long.reset),
        "runway": runway_text,
    }

def pct(v: int | None) -> str:
    return "  ?" if v is None else f"{v:>3}"

def countdown(ts: int | None) -> str:
    if not ts:
        return "   -"
    left = ts - int(time.time())
    if left <= 0:
        return " due"
    return timefmt.span(left, precise=True)
