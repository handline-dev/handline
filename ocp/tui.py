"""ocp bridge - the screen the team is run from (design section 11).

A join view over the same state the daemon decides on, so the two cannot
disagree: if a number looks wrong here, the daemon is acting on that same wrong
number. It draws; it does not decide. What a key does is a call into `actions`,
which calls the same function the daemon or the CLI calls - so the screen can
act without ever holding an opinion of its own.

Two drawing rules are deliberate. Every figure carries a mark for where it came
from - inferred, carried, or carried past its bound - so an inference is never
drawn as a measurement, and what we genuinely do not know is drawn as "?" and
never as 0. The one unmarked case is a figure held at the window's high-water
mark: a real number reported by an earlier payload than the one on screen. And
an account's usage and an agent's context come from different files - rate
limits belong to the account, context to the session - so they are shown as
what they are rather than blended into one number.

It runs on the alternate screen, so quitting leaves the scrollback as it was -
and hands that screen back whole for anything a human has to type into, which
is why logging in works from here at all.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import termios
import textwrap
import time
import tty
from pathlib import Path

from . import (accounts, actions, agents, daemon, db, launch, load as loadmod, paths, status,
               triggers)
from . import waiting as promptwait
from .textsafe import cwidth, drawable, oneline, wrap

# Bracketed paste goes on and off with the screen: the terminal then marks
# where a paste begins and ends everywhere in the bridge, not only in a prompt,
# so a paste can be told from typing wherever it lands. The editor and `claude`
# are handed the terminal with it off.
PASTE_ON, PASTE_OFF = "\033[?2004h", "\033[?2004l"
ALT_ON, ALT_OFF = "\033[?1049h" + PASTE_ON, PASTE_OFF + "\033[?1049l"
CUR_OFF, CUR_ON = "\033[?25l", "\033[?25h"
HOME = "\033[H"
CLEAR_EOL = "\033[K"

R = "\033[0m"
DIM, BOLD = "\033[2m", "\033[1m"
RED, YEL, GRN, CYA, BLU = ("\033[31m", "\033[33m", "\033[32m", "\033[36m", "\033[34m")

_ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")

STATE_COLOR = {
    "RUNNING": GRN, "QUEUED": CYA, "IDLE": DIM, "DONE": DIM,
    "HANDOFF": YEL, "PARKED": YEL, "THROTTLED": YEL,
    "DEGRADED": RED, "STALLED": RED,
}

# What an agent reads as while it sits at a prompt only the founder can answer.
# Its state underneath is still RUNNING; this is drawn over it.
WAITING_ON_YOU = "WAITING ON YOU"
WAITS_SHOWN = 5   # rows of the pinned block; the rest are counted, not drawn

# The panels a cursor can sit in, in tab order. STUCK is one of them because a
# row there names the agent that is stuck, and the keys that unstick it are the
# agent keys - pressing them where the problem is read beats finding the same
# name again in the list above.
ACCOUNTS, AGENTS, MESSAGES, STUCK = 0, 1, 2, 3
NAME_W = 22   # fits `└ orcapipe-manager +2`
# The state cell carries the time an agent is waiting until ("PARKED·closed
# 12m05s"): park, a degraded retry and the await fallback all set one. Its width
# is measured per frame from what the rows print (see `_state_width`), not set
# for the longest thing it could ever print.
COUNTDOWN_W = 6   # `status.span(precise)`: 12m05s, 5h03m, 6d23h
# What an agent *is* - its model, its effort, and one day which tool it is at
# all - against what is happening to it now. The second wins a narrow screen:
# the kind of an agent is on its own page too and does not change while anyone
# is watching, while the wake cell's account has nowhere else to be said. So
# the column appears only where it costs that cell nothing (see `_kind_fits`).
KIND_W = 13
ACCT_COL = 8
CTX_COL, IN_COL = 7, 4
PANEL_BORDERS = 4   # the box's two edges and its gutter, as `room` below counts them
PANELS = (ACCOUNTS, AGENTS, MESSAGES, STUCK)
PANEL_NAME = {ACCOUNTS: "accounts", AGENTS: "agents", MESSAGES: "messages",
              STUCK: "stuck"}

# Keys, per panel. Anything that cannot be undone asks first; the rest happen
# on the keypress, because a confirmation nobody reads is just a second key.
# No `s start`. `t` goes to the agent and makes it somewhere to run on the way,
# so a second key for half of that is a second thing to know: an agent with no
# terminal and an agent with one are the same keypress now.
AGENT_KEYS = [
    ("↵", "open"), ("t", "terminal"), ("m", "message"), ("e", "editor"),
    ("w", "wake"), ("c", "close"), ("p", "park"), ("f", "fold"), ("u", "unstall"),
    ("v", "move"),
]
ACCOUNT_KEYS = [("↵", "open"), ("l", "login"), ("a", "add"), ("c", "cap"), ("x", "remove")]
MESSAGE_KEYS = [("↵", "read"), ("a", "answer"), ("e", "answer in $EDITOR"),
                ("x", "dismiss")]
# A stuck row is about an agent, so it answers to the agent keys.
STUCK_KEYS = AGENT_KEYS
CONFIRM = {"close", "park", "unstall", "fold", "cancel"}

# Kept off the footer even when they apply: reached for rarely enough that
# they cost more room on every row than they earn. Still bound - dispatch
# reads AGENT_KEYS whole - and named under the `?` the footer adds in their
# place.
AGENT_RARE = {"fold", "move"}


def agent_footer_keys(con, agent: str | None) -> list[tuple[str, str]]:
    """Which of AGENT_KEYS belong on the footer for `agent` right now.

    Filtered by `actions.can`, the same check the keypress itself makes, so
    the footer never offers a key the row would then refuse - and a state it
    does not name (`agent` is None: nothing is selected, or this is drawn for
    a page with no single row) falls back to the unfiltered common set rather
    than showing nothing. `open` is never checked: the panel handles it before
    consulting AGENT_KEYS at all, so `can` was never asked about it.
    """
    common = [(k, label) for k, label in AGENT_KEYS if label not in AGENT_RARE]
    if agent is None:
        return common
    return [(k, label) for k, label in common
            if label == "open" or actions.can(con, agent, label)[0]]


# The read-only slash commands: nothing to decide, so not gated by `can` the
# way an AGENT_KEYS verb is - the terminal handle and worktree path the head
# used to show, one keystroke away instead of on screen unasked (#1008).
# `terminal` itself names an AGENT_KEYS action (go to one) - naming the info
# getter the same thing would have one clobber the other in the command dict,
# so this reads the handle, not the terminal, and is named for that.
INFO_COMMANDS = ("where", "worktree")


def agent_commands(con, agent: str) -> list[tuple[str, str]]:
    """Every slash command `agent`'s conversation offers right now, and its
    kind: 'action' (an AGENT_KEYS verb, gated by the same `can` the keybinding
    itself checks) or 'info' (always offered, nothing to decide).

    Derived from AGENT_KEYS and INFO_COMMANDS, not a third, hand-kept table -
    the same reason `agent_footer_keys` reads AGENT_KEYS instead of keeping
    its own list. `open` is excluded: typing a command to open the page
    already open is not a thing, the same way the footer never offers it."""
    out = [(label, "action") for _, label in AGENT_KEYS
           if label != "open" and actions.can(con, agent, label)[0]]
    out += [(name, "info") for name in INFO_COMMANDS]
    return out


def agent_help_line() -> str:
    """The full AGENT_KEYS legend, for `?`: what the footer never shows at
    all (fold, move) and whatever a row's own state hid from it. One place
    for the three spots that print it, so they cannot drift into naming
    different keys - the failure the detail-page footer already had once."""
    return " · ".join(f"{k} {label}" for k, label in AGENT_KEYS if label != "open")


def vlen(s: str) -> int:
    """Visible width, ignoring the escape sequences."""
    return sum(cwidth(c) for c in _ANSI.sub("", s))


def fit(s: str, width: int) -> str:
    """Truncate to a visible width without cutting an escape sequence in half."""
    if vlen(s) <= width:
        return s
    out, seen = [], 0
    i = 0
    while i < len(s):
        m = _ANSI.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        w = cwidth(s[i])
        if seen + w > width - 1:
            break
        out.append(s[i])
        seen += w
        i += 1
    return "".join(out) + "…" + R


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - vlen(s))


def padfit(s: str, width: int) -> str:
    """pad(), but a value wider than the column is cut to fit first (with
    fit()'s ellipsis) instead of shoving whatever comes after it.

    A backstop for a free-text cell (an account name, a `why`), not licence
    for a bounded one to skip being measured - a value with a known worst
    case should never reach the ellipsis at all."""
    return pad(fit(s, width), width)


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
ACCT_INDENT = 2   # before the name, on the heading and every row
ACCT_TAIL = 6     # after the last column: the tick, and " next" on one row


def acct_cols(width: int) -> list[str]:
    """The ACCOUNTS columns drawn at `width`: ACCT_W's order, cut from the end,
    a whole column at a time, until the row fits with the indent and the tick
    and "next" after it inside the panel's borders. What is last in ACCT_W is
    what matters least, so where a column sits is also when it goes. The name
    stays. Heading and rows both draw what this returns, and a row pushed past
    the panel would be cut at its end, which is where the tick and "next" are."""
    cols = list(ACCT_W)
    while len(cols) > 1 and (ACCT_INDENT + sum(ACCT_W[k] for k in cols) + ACCT_TAIL
                             > width - PANEL_BORDERS):
        cols.pop()
    return cols


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
                  unplaced: int = 0, at_cap: bool = False) -> dict[str, str]:
    """The bounded cells of an ACCOUNTS row, unpadded and keyed like ACCT_W.
    The name is free text and is cut to fit where it is drawn. `counts` is the
    account's (assigned, running) from `load.roster`, None where it was not read;
    `at_cap` whether the pick has no slot left on it (`load.Load.at_cap`)."""
    runway_text = status.describe_runway(status.runway(st, status.read_burn(name), now))
    # One definition of the figure and its mark, shared with `ocp account list`.
    # A last-seen figure is drawn at its value, dimmed and uncoloured, with its
    # "?", so it cannot be read as a live gauge at the same number and is not
    # drawn as an unknown either. Display only.
    shown, src = st.shown_pct_5h()
    five = _gauge(shown, ACCT_BAR_W["five"], src)
    return {
        "agents": agents_cell(counts, unplaced, at_cap),
        "five": five,
        "reset5": countdown(st.reset_5h),
        # The weekly figure carries its provenance like the 5h one. Drawn
        # bare, a number from a stale snapshot - or from a week that has since
        # rolled over - reads as this week's measurement.
        "seven": _gauge(st.trusted_pct_7d, ACCT_BAR_W["seven"],
                        status.mark(st.pct_7d_source)),
        # The deadline the ordering spends against: when this window's
        # headroom is lost if nobody uses it.
        "reset7": countdown(st.reset_7d),
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
    return status.span(left, precise=True)


def age(secs: float | None) -> str:
    return status.ago(secs)


def hidden_urgency(con, names: list[str]) -> tuple[str, int, str] | None:
    """The most urgent thing among agents a fold hides: (glyph, how many share
    it, colour), or None when there is nothing to say.

    A folded row reads as its own state, and "IDLE" is true of a supervisor
    whose reports are all working. Three classes, most urgent first: something
    waiting on the human (an open ask addressed to them); stuck (a state only a
    person clears, or the usage wall); running. Idle reports add nothing.
    """
    found: dict[int, list[str]] = {}
    for name in names:
        row = db.get_agent_state(con, name)
        state = row["state"] if row else "IDLE"
        if con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? "
                       "AND state = 'queued'", (name, actions.HUMAN)).fetchone():
            found.setdefault(0, []).append(YEL)
        elif state in NEEDS_A_HUMAN:
            found.setdefault(1, []).append(STATE_COLOR[state])
        elif daemon.wall_reason(con, name, row):
            found.setdefault(1, []).append(YEL)
        elif loadmod.is_running(row):
            found.setdefault(2, []).append(STATE_COLOR["RUNNING"])
    if not found:
        return None
    worst = min(found)
    colours = found[worst]
    return ("▶" if worst == 2 else "!", len(colours), RED if RED in colours else colours[0])


def fold_marker(name_cell: str, under: int, urgency: tuple[str, int, str] | None) -> str:
    """The name and what its fold says: ` +3`, and ` ▶2` after it while the
    column has room. The count of what is hidden is never the thing that goes;
    the aggregate goes before it does, and the name is cut last."""
    if not under:
        return name_cell
    count = f"{DIM} +{under}{R}"
    if urgency:
        glyph, n, colour = urgency
        full = f"{count} {colour}{glyph}{n}{R}"
        if vlen(name_cell) + vlen(full) <= NAME_W:
            return name_cell + full
    return fit(name_cell, NAME_W - vlen(count)) + count


# Supervisors whose reports are drawn. Closed by default: the screen is for
# what needs you, and a team of twelve drawn in full is a list to scroll past.
EXPANDED: set[str] = set()


def _wants_a_human() -> set[str]:
    """Agents in a state only a person clears. They are never hidden."""
    con = db.connect()
    try:
        return {r["agent"] for r in db.all_agent_states(con)
                if r["state"] in NEEDS_A_HUMAN}
    except Exception:  # noqa: BLE001 - a folded row is not worth a blank screen
        return set()
    finally:
        con.close()


def _walk_tree() -> list[tuple[str, int, list[str]]]:
    """The team as drawn: (name, depth, the agents its fold hides, by name).

    One walk decides what is hidden, so the count on a fold and what its marker
    says about those agents cannot drift apart.
    """
    tree = actions.agent_tree()
    keep = _wants_a_human()
    rows: list[list] = []
    closed: list[tuple[int, int]] = []     # (depth, row) of each closed supervisor
    for i, (name, depth) in enumerate(tree):
        while closed and depth <= closed[-1][0]:
            closed.pop()
        if closed and name not in keep:
            rows[closed[-1][1]][2].append(name)
            continue
        under = 0
        for _n, d in tree[i + 1:]:
            if d <= depth:
                break
            under += 1
        rows.append([name, depth, []])
        if under and name not in EXPANDED:
            closed.append((depth, len(rows) - 1))
    return [(n, d, h) for n, d, h in rows]


def shown_names(drawn: list[tuple[str, int, list[str]]]) -> dict[str, str]:
    """How each drawn agent's name reads in the tree: without its parent's name.

    Display only: every key, send and lookup keeps the whole name. The prefix
    goes only when it is `<parent>.` for the agent this row reports to and that
    parent is drawn on the screen - never on the dot alone, and never where
    the parent is not there to be read from. Flat lists have no parent above
    a name and do not call this.
    """
    parent: dict[str, str | None] = {}
    stack: list[str] = []
    for name, depth in actions.agent_tree():
        del stack[depth:]
        parent[name] = stack[-1] if stack else None
        stack.append(name)
    on_screen = {n for n, _d, _h in drawn}
    out = {}
    for name, _d, _h in drawn:
        boss = parent.get(name)
        prefix = f"{boss}." if boss in on_screen else None
        out[name] = name[len(prefix):] if prefix and name.startswith(prefix) \
            and len(name) > len(prefix) else name
    return out


def visible_tree() -> list[tuple[str, int, int]]:
    """The team as drawn: (name, depth, how many under it are not drawn).

    The cursor walks the same list, so it never lands on a row nobody can see.
    A report that needs a person is drawn whether its supervisor is open or
    not - hiding the one row this screen exists for is the one thing folding
    must not do.
    """
    return [(n, d, len(h)) for n, d, h in _walk_tree()]


def _fold(sel: dict, k: str) -> bool:
    """→ opens the selected supervisor, ← closes it - or, on a report, closes
    the one it reports to and puts the cursor there. False when there was
    nothing to fold, so the key keeps its other meaning."""
    # Read off the whole tree, not the drawn one: whether an agent has reports
    # is a fact about the team, and the drawn list is what folding changes.
    tree = actions.agent_tree()
    at = next((i for i, r in enumerate(tree) if r[0] == sel.get(AGENTS)), None)
    if at is None:
        return False
    name, depth = tree[at]
    has_reports = at + 1 < len(tree) and tree[at + 1][1] > depth
    if k in NEXT_PANEL and has_reports and name not in EXPANDED:
        EXPANDED.add(name)
        return True
    if k in PREV_PANEL:
        if has_reports and name in EXPANDED:
            EXPANDED.discard(name)
            return True
        if depth:
            parent = next(n for n, d in reversed(tree[:at]) if d < depth)
            EXPANDED.discard(parent)
            sel[AGENTS] = parent
            return True
    return False


# Whether an account has credentials, asked at most this often. The check runs
# `security`, which is a subprocess with a ten-second timeout, and the screen
# redraws every two seconds and on every keypress - a slow keychain froze the
# dashboard. Logging in is a thing a person does by hand, so a minute-old
# answer is current.
LOGIN_TTL_S = 60
_LOGIN: dict[str, tuple[float, bool]] = {}

# The account the ACCOUNTS panel marked "next" last redraw. Fed back into
# pick_account as `prefer`: the panel redraws every couple of seconds, and
# without this the "next" mark would flicker between two close accounts as
# usage crept past a rounding boundary, tick after tick, with nothing having
# really changed.
_LAST_NEXT: str | None = None


def _logged_in(acct: str) -> bool:
    now = time.time()
    seen = _LOGIN.get(acct)
    if seen and now - seen[0] < LOGIN_TTL_S:
        return seen[1]
    ok = accounts.logged_in(acct)
    _LOGIN[acct] = (now, ok)
    return ok


class Panel:
    """A titled box that draws exactly the rows it is given.

    Rows carry a key when they can be selected - an agent's name, a request's
    id - and the cursor is drawn in a gutter every row shares, so selecting
    something never moves the columns.
    """

    def __init__(self, title: str, width: int, focused: bool = False,
                 plain_title: bool = False):
        self.title, self.width, self.focused = title, width, focused
        # A title that carries its own colours - a row of tabs, where one is
        # lit and the rest are not - is drawn as it was built.
        self.plain_title = plain_title
        self.rows: list[str] = []
        self.keys: list[object] = []
        self.headings: set[int] = set()
        self.selected: object = None

    def add(self, row: str = "", key: object = None, heading: bool = False) -> None:
        self.rows.append(row)
        self.keys.append(key)
        if heading:
            self.headings.add(len(self.rows) - 1)

    def draw(self, limit: int | None = None) -> list[str]:
        inner = self.width - 2
        body = list(zip(self.rows, self.keys))
        title = self.title
        held_back = 0
        if limit is not None and len(body) > limit:
            # The column header is a label, and the rows under it are the
            # point. Shortened, a panel spent its one line naming its columns
            # and then said "… 8 more" - a box that told the reader nothing
            # except that it was hiding something.
            body = [b for i, b in enumerate(body) if i not in self.headings]
            held_back = max(0, len(body) - limit)
            body = body[:limit]
        if held_back and limit is not None and limit >= 2:
            # The marker takes the last line it was given.
            body = body[:limit - 1]
            held_back += 1
        elif held_back:
            # There is no line to spare for a marker, so the count goes in the
            # title: one real row beats a box whose only content is its own
            # "and there is more".
            title = f"{title} · +{held_back}"
        # The title is data - it counts what is waiting - so it is fitted like
        # any other row. A head wider than the terminal wraps, and a wrapped
        # line is one row more than the frame promised, which is the one thing
        # that scrolls the alternate screen.
        # The focused panel is the one the keys act on, and nothing on the
        # screen said which it was: the reader pressed `a` at a panel they were
        # not in and read "does not apply". The title carries it.
        edge = f"{CYA}┌─{R}" if self.focused else f"{DIM}┌─{R}"
        name = "" if self.plain_title else (f"{CYA}{BOLD}" if self.focused else BOLD)
        head = f"{edge} {name}{fit(title, max(0, self.width - 6))}{R} "
        # Pad with the rule to exactly `width` visible columns. Mixing vlen()
        # with len() of an escape sequence is how this ended up one character
        # too long and truncating its own title.
        head += DIM + "─" * max(0, self.width - vlen(head) - 1) + "┐" + R
        out = [head]
        for row, key in body:
            # Only the focused panel draws a cursor. Every panel remembers
            # where its own was, so four of them drew one at once and the
            # reader had four arrows and no idea which their keys meant.
            here = self.focused and key is not None and key == self.selected
            gutter = f"{CYA}▸{R} " if here else "  "
            line = pad(fit(gutter + row, inner), inner)
            fill = getattr(row, "fill", "")
            if fill:
                # The fill runs from border to border. Every reset inside the
                # row ends it, so it is switched on again after each, and the
                # last reset is the line's own: nothing carries to the next line.
                line = fill + line.replace(R, R + fill) + R
            out.append(f"{DIM}│{R}" + line + f"{DIM}│{R}")
        if held_back and limit is not None and limit >= 2:
            more = f"{DIM}  … {held_back} more{R}"
            out.append(f"{DIM}│{R}" + pad(fit(more, inner), inner) + f"{DIM}│{R}")
        out.append(DIM + "└" + "─" * inner + "┘" + R)
        return out


# The states a person has to do something about. Everything else the daemon
# handles on its own, and saying so is saying nothing.
NEEDS_A_HUMAN = ("STALLED", "DEGRADED")


def _state_text(row) -> tuple[str, str]:
    """What the state cell says for an agent's row, and until when: ("PARKED·closed",
    "12m05s"). The width is decided from these before any row is drawn, and the
    row is drawn from the same two strings, so the two cannot disagree."""
    s = row["state"] if row else "IDLE"
    # A closed session holds no memory and answers no one; showing it as PARKED
    # hides the one difference that matters on this machine.
    if row and row["closed_at"]:
        s += "·closed"
    elif promptwait.of(row):
        s = WAITING_ON_YOU
    until = countdown(row["wake_at"] if row else None).strip()
    return s, "" if until == "-" else until


def _state_width(cells: list[tuple[str, str]]) -> int:
    """The state column: the longest state the rows print, room for a countdown
    when any row has one, and one column between it and ctx.

    Measured per frame rather than fixed at the longest state there is: the rows
    are aligned with each other, which is the drift that matters, and a column
    that is wide for the sake of a state nobody is in is a column pushing ctx
    off the screen. The countdown's width is its longest form, not its current
    one, so the column does not breathe as 9m59s becomes 10m00s.
    """
    return 1 + max(len("state"),
                   *(len(s) + (1 + COUNTDOWN_W if until else 0) for s, until in cells))


def _kind_fits(width: int, state_w: int) -> bool:
    """Is there room for the kind column without costing the wake cell.

    The row is the columns drawn plus that cell, which names the account it
    would wake on when it is not the agent's own: the longest account name is
    what must still fit. `width` is the panel's, which is the screen's.
    """
    longest = max((len(a) for a in paths.list_accounts()), default=0)
    wake = vlen(f"⏳ {'x' * longest}? 00:00")
    drawn = NAME_W + ACCT_COL + KIND_W + state_w + CTX_COL + IN_COL
    return width - PANEL_BORDERS - drawn >= wake


def _needs_you(row) -> str:
    """The last column: what this agent needs from a human, or nothing.

    It used to carry the daemon's own note - "unparked by hand: the
    usage_unknown that parked it is resolved", "woke from PARKED at
    reconciliation", "finished its turn". That is the daemon explaining itself
    to whoever debugs the daemon: it is on every row, it is never actionable,
    and a column that is always full of something you cannot act on is a column
    a reader learns to skip. What the row is doing is in the state column; why
    it got there is in `ocp events` and on the agent's own page.
    """
    if not row or row["state"] not in NEEDS_A_HUMAN:
        return ""
    return f"{RED}{row['state'].lower()}{R} {DIM}{oneline(row['detail'] or '')[:60]}{R}"


def wait_lines(waits: list[dict], width: int, budget: int | None = None) -> list[str]:
    """The block pinned above everything else: what is stopped until the founder
    acts. Nothing when nothing is.

    `budget` is the most lines it may take: the heading always shows, with the
    count, and what does not fit is counted rather than drawn - a short window
    keeps its mailbox.
    """
    if not waits:
        return []
    now = time.time()
    out = [f"{BOLD}{RED}{WAITING_ON_YOU}{R} {DIM}({len(waits)}){R}"]
    room = min(WAITS_SHOWN + 1, len(waits) if budget is None else max(0, budget - 1))
    shown = len(waits) if len(waits) <= room else max(0, room - 1)
    for w in waits[:shown]:
        colour = RED if w["red"] else YEL
        out.append(fit(f"{colour}  {oneline(w['who'])} · {oneline(w['what'])} · "
                       f"{age(now - w['since'])}{R}", width))
    if shown < len(waits) and room:
        out.append(f"{DIM}  … {len(waits) - shown} more{R}")
    return out


def keybar(panel: int, width: int, hidden: int = 0,
           agent_keys: list[tuple[str, str]] | None = None) -> str:
    """The keys this panel answers to, in as much room as there is.

    Half an action's name is worse than no name: `u u…` tells a reader nothing
    and looks like a bug. So the line sheds whole pieces - the navigation
    first, since it is the same on every panel, then keys from the end - and
    says how many it dropped.

    `agent_keys` is `agent_footer_keys`'s output for the row under the cursor,
    on AGENTS or STUCK: what `build` computed while it still had the row open,
    so this does not reopen the database to ask the same question again. A `?`
    is appended there for the keys that filtering never shows at all (fold,
    move) - the one thing on this line that is not itself an action.
    """
    if agent_keys is not None:
        keys = agent_keys + [("?", "help")]
    else:
        keys = {ACCOUNTS: ACCOUNT_KEYS, AGENTS: AGENT_KEYS, MESSAGES: MESSAGE_KEYS,
                STUCK: STUCK_KEYS}[panel]
    # A panel that is not drawn and a panel with nothing in it look the same to
    # a reader, so that notice is part of the head and is never what gets
    # dropped to make room.
    note = f"{YEL}{hidden} panel(s) need a taller window{R} {DIM}·{R} " if hidden else ""
    head = f"{DIM}{PANEL_NAME[panel]}:{R} " + note
    # Two lengths. Which arrows move between panels and which move inside one
    # is the one thing on this line a reader cannot guess by pressing keys, so
    # it shrinks before it goes, and it goes before an action does only when
    # even the short form will not fit.
    nav = f" {DIM}· ←→ panel ↑↓ row · q quit{R}"
    nav_short = f" {DIM}· ←→ ↑↓ q{R}"
    sep = f" {DIM}·{R} "
    parts = [f"{BOLD}{k}{R} {DIM}{label}{R}" for k, label in keys]
    full = head + sep.join(parts)
    for tail in (nav, nav_short, ""):
        if vlen(full + tail) <= width:
            return full + tail
    for n in range(len(parts) - 1, 0, -1):
        line = head + sep.join(parts[:n]) + f" {DIM}+{len(parts) - n} more{R}"
        if vlen(line + nav_short) <= width:
            return line + nav_short
        if vlen(line) <= width:
            return line
    return fit(head, width)


def build(width: int, sel: dict | None = None,
          panel: int | None = None,
          wait_budget: int | None = None
          ) -> tuple[list[str], list[Panel], list[tuple[str, str]] | None]:
    """Return (header lines, panels, footer agent keys) for one frame.

    `sel` is {panel: key}: which row carries the cursor in each panel, so the
    caller can keep a cursor across redraws without this knowing how it moves.

    The third value is `agent_footer_keys` for the row under the cursor when
    `panel` is AGENTS or STUCK, computed here while the database connection
    and that row are already open - `keybar` takes it rather than reopening
    both to ask the same question again. None on every other panel, where the
    footer's keys do not depend on a row at all.
    """
    sel = sel or {}
    con = db.connect()
    try:
        # Whether the daemon is running decides whether anything on this screen
        # will happen on its own. Which pid it is decides how to kill it, which
        # is a question for whoever is debugging it - `ocp ps` has it.
        state = (f"{GRN}running{R}" if daemon.running_elsewhere()
                 else f"{RED}NOT RUNNING{R}")
        # The legend earns its place only if it fits whole. Truncated, it cuts
        # the last marks off entirely and glosses the first one mid-word, which
        # is worse than not drawing it - and every mark it explains is also
        # explained by `ocp status`.
        bar_ = (f"{BOLD}ocp{R} {DIM}·{R} daemon {state} {DIM}·{R} "
                f"{time.strftime('%H:%M:%S')}")
        # The legend explains three marks. It was drawn whether or not any of
        # them was on the screen, which is most of the time.
        marks = set()
        for a in paths.list_accounts():
            st_ = status.read(a)
            marks |= {status.mark(st_.pct_5h_source), status.mark(st_.pct_7d_source)}
        # Not "5h": both usage columns carry these marks now.
        with_legend = f"{bar_}{DIM} · {status.SOURCE_LEGEND_SHORT}{R}"
        show_legend = any(m.strip() for m in marks) and vlen(with_legend) <= width
        header = [fit(with_legend if show_legend else bar_, width)]
        # Above the accounts, above everything: it is the one thing on this
        # screen that stops until a person acts.
        header += wait_lines(actions.founder_waits(con), width, wait_budget) + [""]

        # ---- accounts ------------------------------------------------
        acc = Panel("ACCOUNTS", width, focused=panel == ACCOUNTS)
        acc.selected = sel.get(ACCOUNTS)
        w = ACCT_W
        cols = acct_cols(width)
        acc.add(f"{DIM}{'':<{ACCT_INDENT}}" + "".join(f"{ACCT_HEAD[k]:<{w[k]}}" for k in cols)
                + R, heading=True)
        # The account with the most headroom: where work goes when an agent's
        # own account is full. Only accounts that can start a session count.
        # The reason each one sits where it does is on its own page (Enter).
        loads = loadmod.read_all()
        roster, unplaced = loadmod.roster(con)
        global _LAST_NEXT
        first, ranked = status.pick_account(
            [a for a in paths.list_accounts() if _logged_in(a)], load=loads,
            prefer=_LAST_NEXT)
        _LAST_NEXT = first
        for name in paths.list_accounts():
            st = status.read(name)
            # The tick beside the name says the pick could use it: not only
            # that it is under the wall but that it has a slot and would last.
            allowed, _ = status.account_verdict(name, st, loads.get(name), ranked,
                                                _logged_in(name))
            mark = f"{GRN}✔{R}" if allowed else f"{RED}✘{R}"
            # Where work goes next is said in a word after the tick, not drawn
            # in the gutter the selection cursor uses on every other panel.
            nxt = f" {GRN}{BOLD}next{R}" if name == first else ""
            # Every cell is padded on its visible width: the bars carry escape
            # sequences, so a plain :<n counts them and the columns drift out
            # from under their own headings. The figure shown is the one
            # decisions are made on, with a mark saying where it came from -
            # a quiet window drawn as "?" while the daemon acts on 0% is the
            # dashboard and the daemon disagreeing.
            cells = account_cells(name, st, counts=roster.get(name, (0, 0)),
                                  unplaced=unplaced,
                                  at_cap=bool(loads.get(name) and loads[name].at_cap))
            acc.add(
                " " * ACCT_INDENT
                + padfit(f"{BOLD}{name}{R}", w["name"])
                + "".join(pad(cells[k], w[k]) for k in cols if k != "name")
                + mark + nxt,
                key=name,
            )
        if not paths.list_accounts():
            acc.add(f"{DIM}  no accounts registered{R}")
        outlook = status.team_outlook()
        if outlook:
            acc.add(f"{DIM}  {outlook}{R}")

        # ---- agents --------------------------------------------------
        ag = Panel("AGENTS", width, focused=panel == AGENTS)
        ag.selected = sel.get(AGENTS)
        # No heading for the last column: it is empty unless something is about
        # to happen, and then it says what, in words.
        drawn_tree = _walk_tree()
        # Model and effort are configuration and do not change while anyone is
        # watching, which was the case for leaving them off. What changed is
        # that they will not all be claude: with a second vendor coming, what
        # an agent *is* stops being a constant of the screen and starts being
        # the difference between two rows doing the same work at different
        # prices. It sits beside the account for that reason - the two together
        # are what a row costs.
        state_rows = {n: db.get_agent_state(con, n) for n, _, _ in drawn_tree}
        cells = [_state_text(r) for r in state_rows.values()]
        state_w = _state_width(cells) if cells else 1 + len("state")
        kinds = _kind_fits(width, state_w)
        ag.add(f"{DIM}{'agent':<{NAME_W}}{'acct':<{ACCT_COL}}"
               + f"{'kind':<{KIND_W}}" * kinds
               + f"{'state':<{state_w}}{'ctx':>4}   {'in':<4}{R}", heading=True)
        shown = {k: oneline(v) for k, v in shown_names(drawn_tree).items()}
        for name, depth, hidden in drawn_tree:
            under = len(hidden)
            # Indented under whoever routes work to it. Who an agent reports to
            # decides where its report goes and who may assign to it, and it was
            # written down in team.yaml all along - a flat list made the reader
            # hold the routing table in their head to know what they were
            # looking at.
            twig = f"{DIM}{'  ' * (depth - 1)}└ {R}" if depth else ""
            # How many of its reports are not drawn, and the most urgent thing
            # among them. Fitted around, not into, the name: cut off, the only
            # sign a team is hidden disappears.
            urgency = hidden_urgency(con, hidden) if under else None
            try:
                acct, cfg = agents.placed(name, con)
            except Exception as e:  # noqa: BLE001
                ag.add(pad(fit(twig + shown[name], NAME_W), NAME_W)
                       + f"{RED}config error: {e}{R}", key=name)
                continue
            st = status.read_for_agent(name, acct)
            row = state_rows[name]
            s, until = _state_text(row)
            # Only when something is actually about to happen. Measured on a
            # live team: nothing firing, six rows reading "— closed" - which
            # the state column says already, with the same word - and two
            # reading "—". A column that is empty or duplicated is a column
            # that trains a reader not to look at it.
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            fire = (f"{YEL}⚠ {t.kind}{R}"
                    if t.fired and daemon.triggers_apply(row) else "")
            # The time it waits until, where it waits: park, a degraded retry
            # and an await fallback all set one, and it is when the agent
            # comes back.
            state_cell = (f"{BOLD + RED if s == WAITING_ON_YOU else STATE_COLOR.get(s, '')}"
                          f"{fit(s, state_w - 1 - (len(until) + 1 if until else 0))}{R}"
                          + (f" {DIM}{until}{R}" if until else ""))
            ctxs = f"{pct(st.ctx_pct)}%"
            if st.ctx_pct is not None and st.ctx_pct >= triggers.CONTEXT_PCT:
                ctxs = YEL + ctxs + R
            inbox = len(db.queued_for(con, name))
            # Padded on visible width, like the accounts panel: a state that
            # grew a "·closed" suffix used to push every column after it right.
            prefix = (
                # Fitted before it is padded: a long name, or a deep enough
                # twig, is wider than the column, and `pad` alone then pushes
                # every column after it right - the same drift the "·closed"
                # suffix caused.
                pad(fold_marker(twig + f"{BOLD}{shown[name]}{R}", under, urgency), NAME_W)
                + pad(oneline(acct), ACCT_COL)
                + (pad(f"{DIM}{fit(oneline(agents.kind(cfg)), KIND_W - 1)}{R}", KIND_W)
                   if kinds else "")
                + pad(state_cell, state_w)
                + pad(ctxs, CTX_COL)
                + pad(str(inbox) if inbox else "·", IN_COL)
            )
            # Same predicate `ocp ps` and `ocp triggers` ask for this - so this
            # screen cannot disagree with the daemon that actually acts on it -
            # but drawn to fit the column instead of their full sentence: there
            # is room here for the account and the clock, not "waiting for
            # ...'s 5h window (...)" in full.
            w = daemon.would_wake(con, row) and launch.wake_wait(name, row, con)
            if w:
                when = "?" if not w.reset_5h else time.strftime("%H:%M", time.localtime(w.reset_5h))
                # The acct column already names it; only worth repeating when
                # the wake would land elsewhere, or when it is only assumed -
                # the acct column has no way to say that on its own - and only
                # when there is room left for it beside the clock, which is
                # never sacrificed.
                mark = "?" if w.assumed else ""
                room = width - PANEL_BORDERS - vlen(prefix)
                who = (f"{w.account}{mark} " if (w.account != acct or w.assumed)
                       and room >= vlen(f"⏳ {w.account}{mark} {when}") else "")
                # No room to name the account, but still assumed: the mark
                # moves to the one thing left to put it on.
                walled_cell = f"{YEL}⏳ {who}{when}{mark if not who else ''}{R}"
            else:
                walled_cell = ""
            ag.add(
                prefix
                # A STALLED or DEGRADED agent needs a human whatever else is
                # true of it; a wall clears itself the moment the window
                # rolls over, so it is the last thing shown, not the thing
                # that hides the one mark nobody but a human clears.
                + (fire or _needs_you(row) or walled_cell),
                key=name,
            )
        if not paths.list_agents():
            ag.add(f"{DIM}  no agents registered{R}")

        # ---- messages ------------------------------------------------
        # What is waiting for the human, which is the one queue nothing else in
        # this system will ever clear: an agent's escalation sits here until it
        # is answered, and it used to be visible only by running `ocp inbox`
        # with the right name.
        waiting = actions.messages(con)
        gates = sum(1 for m in waiting if m["gate"])
        parked = sum(1 for m in waiting if m["waiting"] and not m["gate"])
        back = sum(1 for m in waiting if m["answer"])
        counts = [(gates, "to decide"), (parked, "asked for a reply"),
                  (back, "came back"),
                  (len(waiting) - gates - parked - back, "to read")]
        said = ", ".join(f"{n} {what}" for n, what in counts if n)
        title = f"FOR YOU — {said}" if said else "FOR YOU"
        ms = Panel(title, width, focused=panel == MESSAGES)
        ms.selected = sel.get(MESSAGES)
        today = time.strftime("%Y%m%d")
        for m in waiting:
            # The clock alone made a two-day-old row look like this hour's.
            when = time.strftime(
                "%H:%M" if time.strftime("%Y%m%d", time.localtime(m["ts"])) == today
                else "%m-%d", time.localtime(m["ts"]))
            # Same rule as the reading view: what a message says is its text.
            # An escape here is invisible to the width count, so it is never
            # truncated, and `esc [ 2J` clears the screen drawing it. The first
            # line is the first one with something on it, as it is everywhere
            # else - a body that starts with a blank line drew an empty row.
            first = drawable(actions.first_line(m["body"]))[0]
            sender = oneline(m["from"])
            # An agent parked until this is answered is the only thing on the
            # screen that no one else will ever clear; it is not drawn like a
            # report that happens to be addressed here.
            if m["gate"]:
                mark, name = f"{YEL}⚠{R}", f"{BOLD}{sender:<12}{R}"
            elif m["waiting"]:
                mark, name = f"{CYA}◷{R}", f"{BOLD}{sender:<12}{R}"
            elif m["answer"]:
                # An answer is the one row here that is not addressed to the
                # human at all: they asked for it. Drawn as what it is, so a
                # reply cannot be read as a new demand on their time.
                mark, name = f"{GRN}↩{R}", f"{BOLD}{sender:<12}{R}"
            else:
                mark, name = f"{DIM}·{R}", f"{DIM}{sender:<12}{R}"
            ms.add(f"{DIM}{when}{R} {mark} {CYA}#{m['id']:<5}{R}{name}"
                   f"{pad(oneline(m['kind'] or 'ask'), 11)}{first}", key=m["id"])
        if not waiting:
            ms.add(f"{DIM}  nothing waiting on you{R}")

        # ---- stuck -----------------------------------------------------
        # This was "in flight", and almost nothing ever is: a handoff takes
        # seconds, and what it actually drew was requests that had sat for a
        # day and a half because their target had no terminal to be told with.
        # Traffic drawn as traffic said the system was busy while it was stuck.
        blocked = actions.stuck(con)
        fl = Panel("STUCK" if blocked else "STUCK — nothing is waiting on anything", width,
                   focused=panel == STUCK)
        fl.selected = sel.get(STUCK)
        for b in blocked:
            fl.add(pad(f"{YEL}{oneline(b['what'])}{R}", 34)
                   + pad(f"{DIM}{age(b['age']) if b['age'] else ''}{R}", 7)
                   + f"{DIM}{oneline(b['why'])}{R}", key=b["agent"])

        footer_agent = sel.get(AGENTS) if panel == AGENTS else \
            sel.get(STUCK) if panel == STUCK else None
        footer_keys = (agent_footer_keys(con, str(footer_agent) if footer_agent is not None else None)
                       if panel in (AGENTS, STUCK) else None)

        return header, [acc, ag, ms, fl], footer_keys
    finally:
        con.close()


def page(title: str, sections: list[tuple[str, list[str]]],
         panel: int = AGENTS, key: object = None,
         picks: dict[str, list[int]] | None = None) -> dict:
    """A detail page laid out as the screen's own panels, not as a wall of text.

    It keeps its sections rather than the lines they make, because the width
    they are laid out at is the width the terminal has *now*: a page drawn at
    80 columns and then read at 60 is a page whose every box border is one line
    too long, and re-wrapping a drawn border tears it in half.
    """
    d = opened(title, [], panel, key)
    d["sections"] = sections
    # Sections whose rows can be selected: the start of the heading, and the id
    # each row stands for. The cursor is `at`, an index into that list.
    d["picks"] = picks or {}
    return d


def _agent_page(con, name: str, panel: int = AGENTS, title: str | None = None,
                old: dict | None = None) -> dict:
    """An agent's page, with its WAITING FOR IT rows selectable. `old` is the
    page it replaces, so a redraw after an action keeps the section, the row
    and the scroll the reader was at."""
    if old and old.get("stem") == actions.CHAT_TAB:
        actions.chat_seen(con, name)   # before it is counted: it is being read
    d = page(title or f"agent {name}", actions.agent_detail(con, name), panel, name,
             picks={"WAITING FOR IT": actions.waiting_ids(con, name)})
    d["agent"] = name
    if old:
        for k in ("tab", "at", "top", "end", "stem"):
            d[k] = old.get(k, 0)
        # A tab that appears or goes (ANSWERS IT HAS NOT BEEN TOLD) moves the
        # others; the reader stays on the section they were reading.
        stems = _tab_stems(d)
        if old.get("stem") in stems:
            d["tab"] = stems.index(old["stem"])
        # The row it was on may be the one that just left.
        d["at"] = max(0, min(d["at"], len(d["picks"]["WAITING FOR IT"]) - 1))
    return d


def _tab_stems(detail: dict) -> list[str]:
    """The names of a page's tabs, without the counts in their headings."""
    return [h.split(" (")[0] for h, _ in detail.get("sections") or [] if h]


def _go_to_tab(detail: dict, at: int) -> None:
    """Put the reader on tab `at`. The page follows the reader by the tab's
    name (`stem`), so moving the number alone is undone by the next refresh."""
    stems = _tab_stems(detail)
    at = max(0, min(at, len(stems) - 1))
    detail["tab"], detail["stem"] = at, stems[at] if stems else ""
    detail["top"], detail["end"] = 0, True


def _picks_of(detail: dict, heading: str) -> list[int]:
    for prefix, ids in (detail.get("picks") or {}).items():
        if heading.startswith(prefix):
            return ids
    return []


def _open_picks(detail: dict) -> list[int]:
    """The requests the open section lets the cursor land on, or none."""
    tabs = [h for h, _ in (detail.get("sections") or []) if h]
    if not tabs:
        return []
    at = max(0, min(detail.get("tab", 0), len(tabs) - 1))
    return _picks_of(detail, tabs[at])


def lines_of(detail: dict, width: int) -> list[str]:
    """What is open, as the lines it takes at this width.

    A page lays its sections out here; anything opened as plain text is wrapped
    here. Either way the caller gets lines that already fit, and nothing that
    has been laid out is laid out twice.
    """
    sections = detail.get("sections")
    if sections is None:
        return wrap(detail["lines"], width)
    # What is always on the page, and what is one of its tabs. A page opened on
    # an agent asks four different questions - what is it, what is waiting for
    # it, what does it know, what has it done - and stacking all four meant
    # scrolling past three to read the fourth. The untitled part is what the
    # page is *about*, so it stays; the rest is ←→.
    intro = [r for h, rows in sections if not h for r in rows]
    tabs = [(h, rows) for h, rows in sections if h]
    lines: list[str] = _rows_lines(intro, width) + [""] if intro else []
    if not tabs:
        return lines
    at = max(0, min(detail.get("tab", 0), len(tabs) - 1))
    detail["tab"] = at
    # The tabs are the box's own top edge. Drawn as a strip above a box that
    # then repeated the open tab's name, the page said "EARLIER WITH MANAGER"
    # twice in two lines.
    heading, rows = tabs[at]
    detail["stem"] = heading.split(" (")[0]
    box = Panel(_tabstrip([h for h, _ in tabs], at), width, plain_title=True)
    # Wrapped into the box, not fitted to it: what is quoted here is a message
    # someone wrote, and a box that ends every line in "…" is not a section
    # anyone can read. A panel on the main screen cuts a row because the row
    # underneath needs the line; a page scrolls.
    picks = _picks_of(detail, heading)
    if picks and len(picks) == len(rows):
        # One line a request, so the cursor is on a row and not on part of one.
        at = max(0, min(detail.get("at", 0), len(picks) - 1))
        detail["at"] = at
        box.focused, box.selected = True, picks[at]
        for i, (r, rid) in enumerate(zip(rows, picks)):
            if i:
                box.add("")
            head, *rest = (_entry_lines(r, max(1, width - 5)) if isinstance(r, actions.Entry)
                           else wrap([r], max(1, width - 5)))
            box.add(head, key=rid)         # the cursor is in the gutter of its first line
            for ln in rest:
                box.add(ln)
        return lines + box.draw()
    room = max(1, width - 5)
    for ln in (_note_lines(rows, room) if detail["stem"] == actions.KNOWS_TAB
               else _rows_lines(rows, room, dim_plain=detail["stem"] == actions.CHAT_TAB)):
        box.add(ln)
    return lines + box.draw()


def _clock(ts: float, seconds: bool = False) -> str:
    """A time of day, with the date only when it is not today."""
    t = time.localtime(ts)
    if time.strftime("%m-%d", t) == time.strftime("%m-%d"):
        return time.strftime("%H:%M:%S" if seconds else "%H:%M", t)
    return time.strftime("%m-%d %H:%M:%S" if seconds else "%m-%d %H:%M", t)


def _body(text: str, width: int) -> list[str]:
    """What a sender typed, wrapped to the column and indented under its
    header. It goes through `wrap`, which drops any escape in it; the caller
    styles only what it adds."""
    return [f"  {ln}" if ln else "" for ln in wrap(text.splitlines() or ["(empty)"],
                                                  max(1, width - 2))]


def _entry_lines(e, width: int) -> list[str]:
    """A request: its number and who it is with on one line, all of its text under."""
    head = f"{BOLD}#{e.id}{R} from {oneline(e.who)}  {DIM}{_clock(e.ts)}{R}"
    return [fit(head, width)] + _body(e.text if e.text.strip() else "", width)


def _fact_lines(f, width: int) -> list[str]:
    """Labelled values, as many to a line as fit; one never breaks from its label."""
    out: list[str] = []
    cur, used = "", 0
    for k, v in f.pairs:
        parts = wrap([f"{k} {v}"], width)
        styled = [f"{DIM}{k}{R}{p[len(k):]}" if p.startswith(k) and j == 0 else p
                  for j, p in enumerate(parts)]
        if len(parts) == 1 and cur and used + 3 + vlen(parts[0]) <= width:
            cur, used = f"{cur}   {styled[0]}", used + 3 + vlen(parts[0])
            continue
        if cur:
            out.append(cur)
        out += styled[:-1]
        cur, used = styled[-1], vlen(parts[-1])
    return out + ([cur] if cur else [])


def _event_lines(events: list, width: int) -> list[str]:
    """What was done, in columns while there is room for them and stacked when
    there is not. A payload is wrapped under its own column, never cut."""
    kinds = [oneline(e.kind) for e in events]
    tw = max(vlen(_clock(e.ts, True)) for e in events)
    kw = min(18, max(vlen(k) for k in kinds))
    cols = tw + 2 + kw + 2
    out: list[str] = []
    for e, kind in zip(events, kinds):
        when = f"{DIM}{pad(_clock(e.ts, True), tw)}{R}"
        if cols > width // 2 or vlen(kind) > kw:
            clock = _clock(e.ts, True)
            head = wrap([f"{clock}  {kind}"], width)
            out.append(f"{DIM}{clock}{R}{BOLD}{head[0][len(clock):]}{R}" if head[0].startswith(clock)
                       else head[0])
            out += head[1:]
            out += _body(e.payload, width) if e.payload.strip() else []
            continue
        rows = wrap(e.payload.splitlines() or [""], max(1, width - cols))
        out.append(f"{when}  {BOLD}{pad(kind, kw)}{R}  {rows[0]}".rstrip())
        out += [" " * cols + ln if ln else "" for ln in rows[1:]]
    return out


_ITEM = re.compile(r"^(\s*(?:[-*\u2022]|\d+[.)])\s+|\s+)")


def _note_lines(rows: list[str], width: int) -> list[str]:
    """Free text an agent kept for itself: `#` lines are headings, and a wrapped
    list item goes back under its own words rather than under its bullet."""
    out: list[str] = []
    for r in rows:
        if r.lstrip().startswith("#"):
            out += [f"{BOLD}{ln}{R}" if ln else "" for ln in wrap([r], width)]
            continue
        indent = len(m.group(1).expandtabs()) if (m := _ITEM.match(r)) else 0
        lead = r[:indent] if indent else ""
        rest = wrap([r[indent:]], max(1, width - indent)) or [""]
        out += [(lead if i == 0 else " " * indent) + ln if ln else ""
                for i, ln in enumerate(rest)]
    return out


def _header(bar: str, marker: str, who: str, when: str, tail: str, waiting: bool,
            width: int) -> list[str]:
    """A message's first line: the marker and who, then when and its state. What
    does not fit goes on a line of its own under it - the name is never cut, and
    neither is the state."""
    names = wrap([who], max(1, width - 4)) if who else [""]
    lines = [f"{bar} {marker} {names[0]}{R}"]
    lines += [f"{bar}   {n}" for n in names[1:]]
    used = vlen(lines[-1])
    for plain, styled in ((when, f"{DIM}{when}{R}"),
                          (f"· {tail}" if tail else "",
                           f"{YEL if waiting else DIM}\u00b7 {tail}{R}")):
        if not plain:
            continue
        if used + 2 + vlen(plain) <= width:
            lines[-1] += f"  {styled}"
            used += 2 + vlen(plain)
        else:
            lines.append(f"{bar}   {styled}")
            used = 4 + vlen(plain)
    return lines


class Filled(str):
    """A row of a panel drawn on a background: `fill` is the colour, and the
    panel paints it across the whole row."""
    fill: str

    def __new__(cls, text: str, fill: str):
        self = super().__new__(cls, text)
        self.fill = fill
        return self


# The founder's messages: bright white on bright black, both set, so the text
# does not depend on the terminal's own foreground; 16-colour codes only.
FILL_YOU = "\033[97;100m"

# Who said it, three ways that do not depend on colour: the bar down the side
# of the message (heavy for the human, thin for the agent this page is about,
# dotted for anyone else), the marker on its header, and the name.
_SIDES = {"you": (f"{CYA}{BOLD}\u258c{R}", f"{CYA}{BOLD}\u276f", ""),
          "agent": (f"{GRN}\u2502{R}", f"{GRN}{BOLD}\u25cf", ""),
          "other": (f"{DIM}\u2506{R}", f"{DIM}\u00b7", DIM)}


def _rows_lines(rows: list, width: int, dim_plain: bool = False) -> list[str]:
    """The rows of a tab as the screen shows them, each kind laid out its own way.

    A message or a request is a block: a header line and its text indented
    under it, with a blank line between blocks, so where one ends is a gap and
    not a count. Plain rows are wrapped.
    """
    out: list[str] = []
    prev: tuple | None = None          # the speaker of the message above
    block = False                      # the row above was a block
    if rows and all(isinstance(r, actions.Event) for r in rows):
        return _event_lines(rows, width)   # columns are the section's, not a row's
    for r in rows:
        if not isinstance(r, actions.Row):
            out += [f"{DIM}{ln}{R}" if dim_plain and ln else ln for ln in wrap([r], width)]
            block = False
            continue
        if isinstance(r, actions.Fact):
            out += _fact_lines(r, width)
            continue
        if isinstance(r, actions.Entry):
            if block:
                out.append("")
            out += _entry_lines(r, width)
            block = True
            continue
        if r.kind == "thread":
            if out:
                out.append("")
            out.append(f"{DIM}{fit(r + ' ', width)}{'─' * max(0, width - vlen(r) - 1)}{R}")
            prev, block = None, False
            continue
        if block:
            out.append("")
        bar, head, dim = _SIDES[r.side]
        who = "" if prev == (r.side, r.who) else r.who
        prev, block = (r.side, r.who), True
        # The bar is on every line of the message, blank ones included, so a
        # long or multi-line message is still one side's from top to bottom.
        lines = _header(bar, head, who, _clock(r.ts), r.tail, r.waiting, width) + [
            f"{bar}{dim}   {ln}{R}" if ln else bar
            for ln in wrap(r.text.splitlines() or [""], max(1, width - 4))]
        out += [Filled(ln, FILL_YOU) for ln in lines] if r.side == "you" else lines
    return out


def _tabstrip(names: list[str], at: int) -> str:
    """The sections of an open page, with the one being read lit."""
    parts = [f"{CYA}{BOLD}{oneline(n)}{R}" if i == at else f"{DIM}{oneline(n)}{R}"
             for i, n in enumerate(names)]
    return f" {DIM}·{R} ".join(parts)


def opened(title: str, lines: list[str], panel: int = AGENTS, key: object = None) -> dict:
    """One thing held open on the screen, and where it came from.

    It carries its own row rather than just its text: what is open is the thing
    a key then acts on, and a reader answering a message must be answering the
    one they are reading.
    """
    return {"title": title, "lines": lines, "panel": panel, "key": key, "top": 0}


def frame(width: int, height: int, sel: dict | None = None, panel: int = AGENTS,
          flash: str = "", detail: dict | None = None) -> str:
    if detail is not None:
        return _detail_frame(width, height, detail, flash)
    # The pinned block takes what is left after the bar, the blank line, the
    # two footer rows and the smallest box the mailbox can be drawn in.
    header, panels, footer_keys = build(width, sel, panel, wait_budget=height - 4 - PANEL_MIN)
    room = height - len(header) - 2                    # the two footer rows
    limits, hidden = _layout([len(p.rows) for p in panels], room)
    body: list[str] = []
    for p, lim in zip(panels, limits):
        if lim == 0:
            continue
        body += p.draw(limit=lim)
    foot = [fit(flash, width) if flash else "", keybar(panel, width, hidden, footer_keys)]
    lines = header + body
    lines = lines[:max(0, height - len(foot))]
    while len(lines) < height - len(foot):
        lines.append("")
    lines = (lines + foot)[:height]
    return "\n".join(line + CLEAR_EOL for line in lines)


# A panel that cannot have a border, one real row, its "… N more" marker and
# its other border says nothing at all - and an empty box reads as "nothing
# here" while the thing it would have shown is what the reader is looking for.
# Three was not enough: the one row a panel got was overwritten by its own
# marker, so the screen drew four boxes with no content between them.
PANEL_MIN = 4


# Which panels a short window keeps, most important first. Drawn in their own
# order, dropped in this one: what is waiting on a human is the last thing to
# go, and the accounts are the first - they are two rows that rarely change,
# and `ocp status` has them.
DROP_LAST = (MESSAGES, STUCK, AGENTS, ACCOUNTS)


def _layout(want: list[int], room: int) -> tuple[list[int | None], int]:
    """How many rows each panel may draw, and how many panels do not fit.

    Everything fits, or every panel that is drawn at all gets one row and then
    they grow together a row at a time - so a long list cannot starve a short
    one, and the screen never draws a box it has nothing to put in.
    """
    if sum(w + 2 for w in want) <= room:
        return [None] * len(want), 0
    keep = len(want)
    while keep and keep * PANEL_MIN > room:
        keep -= 1
    kept = set(DROP_LAST[:keep])
    # Two rows each to begin with: one to say something, one for the marker
    # that says how much is not being said.
    limits = [2 if i in kept else 0 for i in range(len(want))]
    left = room - keep * PANEL_MIN
    while left > 0 and any(limits[i] < want[i] for i in kept):
        for i in range(len(want)):
            if left <= 0:
                break
            if i in kept and limits[i] < want[i]:
                limits[i] += 1
                left -= 1
    return [None if limits[i] >= want[i] and limits[i] else limits[i]
            for i in range(len(want))], len(want) - keep


def _detail_frame(width: int, height: int, detail: dict, flash: str = "") -> str:
    """One thing, opened, with what is off the bottom said rather than cut.

    The panels shorten a list and count what they held back; a message is not a
    list, so this wraps and scrolls instead - and a message read half way is
    exactly the one someone is about to answer, which is why it can be answered
    from here.
    """
    if detail.get("refresh_error") and not flash:
        flash = f"{RED}page not refreshed - {detail['refresh_error']}{R}"
    body = lines_of(detail, width)
    room = max(1, height - 4)
    last = max(0, len(body) - room)
    top = max(0, min(detail.get("top", 0), last))
    if detail.get("stem") == actions.CHAT_TAB and detail.get("end"):
        top = last                     # a conversation read at its end stays there
    detail["top"] = top
    detail["end"] = top >= last
    shown = body[top:top + room]
    while len(shown) < room:
        shown.append("")
    where = (f"{DIM}  line {top + 1}-{min(len(body), top + room)} of {len(body)}{R}"
             if len(body) > room else "")
    keys = ("esc back · ←→ section · ↑↓ pick · x cancel" if _open_picks(detail)
            else "esc back · ←→ section · ↑↓ scroll")
    if detail.get("sections") is None or not [h for h, _ in detail["sections"] if h]:
        keys = "esc back · ↑↓ scroll · space page"
    if detail.get("stem") == actions.CHAT_TAB:
        keys = "esc back · ←→ section · ↑↓ scroll · ↵ write · e in $EDITOR"
    if detail.get("panel") == MESSAGES:
        keys += " · a answer · e answer in $EDITOR · x dismiss"
    elif detail.get("panel") in (AGENTS, STUCK):
        # A connection per render, unlike `build`'s one-per-frame: this page
        # is already reopening the agent's own rows on every redraw
        # (`_refreshed`), so a second connection here costs nothing this
        # screen was not already paying.
        con = db.connect()
        try:
            agent_keys = agent_footer_keys(con, detail.get("agent"))
        finally:
            con.close()
        keys += " · " + " · ".join(f"{k} {label}" for k, label in agent_keys
                                   if label != "open") + " · ? help"
    lines = ([fit(f"{BOLD}{oneline(detail['title'])}{R}{where}", width), ""]
             + [fit(ln, width) for ln in shown]
             + [fit(flash, width) if flash else "", fit(f"{DIM}{keys} · q quit{R}", width)])
    return "\n".join(line + CLEAR_EOL for line in lines[:height])


def move(keys: list, current, delta: int):
    """The next key in a panel's list, or None when that is off its end."""
    if not keys:
        return None
    if current not in keys:
        return keys[0]
    i = keys.index(current) + delta
    return keys[i] if 0 <= i < len(keys) else None


def settle(sel: dict, known: dict) -> None:
    """Keep every panel's cursor on a row that exists.

    A row leaves a list under the cursor - dismissed, answered, a report closing
    on open - and a cursor keyed on it is then on nothing, drawn as nowhere.
    It goes to the row that took the place, or to the one above when the last
    row went, and is dropped only when the list is empty. `known` is each
    panel's keys as of the last call, which is what says where the row was.
    """
    for panel in PANELS:
        keys = _keys_of(panel)
        cur = sel.get(panel)
        if cur is not None and cur not in keys:
            was = known.get(panel, [])
            at = was.index(cur) if cur in was else 0
            if keys:
                sel[panel] = keys[min(at, len(keys) - 1)]
            else:
                del sel[panel]
        known[panel] = keys


def step(panel: int, sel: dict, delta: int) -> int:
    """One row down or up, crossing into the next panel at the end of one.

    The panels are four lists stacked on one screen, and a reader arrowing down
    the screen means the screen: stopping at the bottom of the team list reads
    as "this is as far as you go", which is how a mailbox two panels below it
    goes unread. ←→ jumps whole panels; ↑↓ walks everything in order.

    A panel with nothing selectable in it is stepped over, not stopped in.
    """
    nxt = move(_keys_of(panel), sel.get(panel), delta)
    if nxt is not None:
        sel[panel] = nxt
        return panel
    for _ in range(len(PANELS) - 1):
        panel = (panel + delta) % len(PANELS)
        keys = _keys_of(panel)
        if keys:
            sel[panel] = keys[0] if delta > 0 else keys[-1]
            return panel
    return panel


# Keys read ahead of the one being handled. One read can return several - a
# held-down arrow, or two keys typed inside a frame - and dropping the rest
# loses keypresses the human made.
_pending: list[str] = []


def split_keys(data: str) -> list[str]:
    """One read of the terminal, split into the keys it contains.

    An arrow is three bytes, `esc [ A`, or `esc O A` where the terminal has
    switched to application cursor keys; PageUp and a modified arrow are longer
    (`esc [ 5 ~`, `esc [ 1 ; 5 A`). A bare escape is one byte and means the
    human backed out.
    """
    out, i = [], 0
    while i < len(data):
        m = _CSI.match(data, i) if data[i] == "\033" else None
        out.append(m.group() if m else data[i])
        i = m.end() if m else i + 1
    return out


def _cut(data: str) -> bool:
    """Whether `data` ends part way through an escape sequence - or on an
    escape that may be the start of one."""
    i = data.rfind("\033")
    return i >= 0 and (i == len(data) - 1 or bool(_CSI_CUT.fullmatch(data[i:])))


# How long a read that ends inside an escape sequence waits for the rest. An
# arrow can reach us as `esc` and then `[ B`, and that first byte is what a
# bare Esc looks like: the two are told apart only by whether more follows, so
# the wait is the price of Esc - short enough that it cannot be felt. 50 ms is
# right for a local terminal (a split arrow joined at gaps up to about 45 ms).
# Over a link whose jitter exceeds it an arrow arrives as Esc, as in every
# terminal program; raise it here if the bridge is ever driven over one. The
# whole join is bounded to a few waits, so a stream that never completes a
# sequence cannot hold the screen.
ESC_WAIT = 0.05


_paste_state = {"in": False, "carry": ""}


def _unpasted(data: str) -> str:
    """One read of the terminal without the text that was pasted into it.

    Outside a prompt a paste is not a command: read as keys, its first newline
    is Enter and its letters are shortcuts (`a`, then a line of text, was how a
    pasted screen became a message). The terminal marks a paste, so it is
    dropped whole - across reads too, since a long paste arrives in pieces.
    A prompt reads the marks itself (Composer) and keeps what is pasted.
    """
    st = _paste_state
    text, st["carry"], out = st["carry"] + data, "", ""
    start, end = Composer.START, Composer.END
    while text:
        mark = end if st["in"] else start
        i = text.find(mark)
        if i >= 0:
            out += "" if st["in"] else text[:i]
            text, st["in"] = text[i + len(mark):], not st["in"]
            continue
        # A mark cut off by the end of the read is carried to the next one. A
        # lone escape is not: outside a paste it is the human backing out.
        keep = next((k for k in range(min(len(mark) - 1, len(text)), 0, -1)
                     if mark.startswith(text[-k:]) and (k > 1 or st["in"])), 0)
        if not st["in"]:
            out += text[:len(text) - keep]
        st["carry"] = text[len(text) - keep:] if keep else ""
        break
    return out


# The terminal's bytes as text, kept across reads: a character cut by the end of
# one read (a Korean letter is three bytes) is held until the rest arrives, not
# dropped. A byte that is not UTF-8 at all is drawn as U+FFFD, so it shows. A
# lead byte whose continuation never comes stays held until the next byte, and
# then surfaces as one U+FFFD glued to it - possibly in the next prompt's text.
# It is visible, and it needs a byte that is never finished; that is left as is.
_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")


def _read(fd: int) -> str:
    """One read of the terminal as text. Every place that reads the keyboard
    reads through here, so a character and a key are each delivered whole
    whatever the bytes' boundaries: a read that ends inside an escape sequence
    waits for the rest (`ESC_WAIT`)."""
    data = _decoder.decode(os.read(fd, 4096))
    end = time.monotonic() + 4 * ESC_WAIT
    while _cut(data) and time.monotonic() < end and select.select([fd], [], [], ESC_WAIT)[0]:
        data += _decoder.decode(os.read(fd, 4096))
    return data


def _key(timeout: float) -> str | None:
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    if _pending:
        return _pending.pop(0)
    fd = sys.stdin.fileno()
    if not select.select([fd], [], [], timeout)[0]:
        return None
    # Read the descriptor, not the buffered stream. `sys.stdin.read(1)` pulls
    # the whole escape sequence into Python's own buffer, and then select()
    # reports the fd has nothing left - so every arrow key arrived here as a
    # bare escape with its `[B` still sitting in the buffer.
    try:
        data = _read(fd)
    except OSError:
        return None
    data = _unpasted(data)
    if not data:
        return None
    keys = split_keys(data)
    _pending.extend(keys[1:])
    return keys[0]


# An escape sequence: `esc [` and parameters up to a final byte, or `esc O x`.
_CSI = re.compile(r"\033(?:\[[0-?]*[ -/]*[@-~]|O.)")
# What an escape sequence looks like before its final byte has arrived.
_CSI_CUT = re.compile(r"\033(?:\[[0-?]*[ -/]*|O)")


def _word_left(pos: int, text: str) -> int:
    while pos and text[pos - 1] == " ":
        pos -= 1
    while pos and text[pos - 1] != " ":
        pos -= 1
    return pos


def _word_right(pos: int, text: str) -> int:
    n = len(text)
    while pos < n and text[pos] == " ":
        pos += 1
    while pos < n and text[pos] != " ":
        pos += 1
    return pos


# Where a key moves the cursor to, by the CSI sequence a terminal sends for it -
# both the `\033[` and the older `\033O` forms, since a terminal's application-
# cursor-keys mode picks one or the other for the plain arrows. A sequence this
# does not name still reaches Composer.feed (so Delete, function keys and the
# rest are still read and discarded, as before), it just does not move `pos`.
_MOVE = {
    "\033[D": lambda p, t: max(0, p - 1), "\033OD": lambda p, t: max(0, p - 1),
    "\033[C": lambda p, t: min(len(t), p + 1), "\033OC": lambda p, t: min(len(t), p + 1),
    "\033[H": lambda p, t: 0, "\033OH": lambda p, t: 0,
    "\033[1~": lambda p, t: 0, "\033[7~": lambda p, t: 0,
    "\033[F": lambda p, t: len(t), "\033OF": lambda p, t: len(t),
    "\033[4~": lambda p, t: len(t), "\033[8~": lambda p, t: len(t),
    "\033[1;5D": _word_left, "\033[1;3D": _word_left,
    "\033[1;5C": _word_right, "\033[1;3C": _word_right,
}
# Delete (not Backspace): removes the character at `pos` rather than moving it.
_DELETE_AT = {"\033[3~"}
# Alt/Option+key, sent as ESC then the key in the same read on this platform's
# terminals: b/f for word-left/right, matching what macOS Terminal and iTerm2
# send by default (auditor, #1033).
_META_MOVE = {"b": _word_left, "f": _word_right}


class Composer:
    """Text typed on the bottom line, as many lines as the sender pasted.

    Enter sends, so a newline inside the message cannot be told from Enter -
    except in a bracketed paste, where the terminal says where the pasted text
    begins and ends. Text between those markers is kept whole, newlines
    included; a paste never sends and never confirms, whatever it contains.
    """
    START, END = "\033[200~", "\033[201~"

    def __init__(self, multi: bool = False) -> None:
        # A prompt that wants one word (an account name, "type its name to
        # confirm") has no use for a pasted newline, and keeping one would put
        # a line break inside the answer.
        self.multi = multi
        self.text, self.pasting, self._carry = "", False, ""
        self.typed = ""                # what was keyed, as against pasted
        self.pos = 0                   # where in `text` the next key lands

    def feed(self, data: str) -> str | None:
        """'send', 'cancel', or None while the message is still being typed."""
        data, self._carry = self._carry + data, ""
        while data:
            if data.startswith(self.START):
                self.pasting, data = True, data[len(self.START):]
            elif data.startswith(self.END):
                self.pasting, data = False, data[len(self.END):]
            elif (len(data) < 6 and (self.START.startswith(data) or self.END.startswith(data))
                  and (len(data) > 1 or self.pasting)):
                # A marker cut in two by the read. A lone escape is one too
                # while pasting; outside a paste it is the way out.
                self._carry = data
                return None
            elif self.pasting:
                ch, data = data[0], data[1:]
                if ch == "\r" and data[:1] == "\n":
                    data = data[1:]
                ins = ("\n" if self.multi else " ") if ch in "\r\n" else ch
                self.text = self.text[:self.pos] + ins + self.text[self.pos:]
                self.pos += len(ins)
            elif data[0] in "\r\n":
                return "send"
            elif data[0] == "\033":
                m = _CSI.match(data)
                if m:
                    seq, data = data[:m.end()], data[m.end():]
                    self.pos = _MOVE.get(seq, lambda p, t: p)(self.pos, self.text)
                    if seq in _DELETE_AT:
                        self.text = self.text[:self.pos] + self.text[self.pos + 1:]
                elif data[1:2] in ("[", "O"):
                    if _CSI_CUT.fullmatch(data):
                        self._carry = data     # a sequence cut short by the read
                        return None
                    data = data[2:]        # not one after all (Alt-[): drop the prefix
                elif len(data) > 1:
                    # ESC with another character in the same read is Alt/Option
                    # held with it, not a lone Escape - only ESC with nothing
                    # after it (below) is the way out. An unmapped one is read
                    # and discarded, the way an unmapped CSI sequence already is.
                    ch, data = data[1], data[2:]
                    if ch in _META_MOVE:
                        self.pos = _META_MOVE[ch](self.pos, self.text)
                    elif ch in ("\x7f", "\b"):
                        end = self.pos
                        self.pos = _word_left(self.pos, self.text)
                        self.text = self.text[:self.pos] + self.text[end:]
                else:
                    return "cancel"
            elif data[0] in ("\x7f", "\b"):
                if self.pos:
                    self.text = self.text[:self.pos - 1] + self.text[self.pos:]
                    self.pos -= 1
                data = data[1:]
            elif data[0] == "\x03":
                return "cancel"
            else:
                ch, data = data[0], data[1:]
                if ch.isprintable():
                    self.text = self.text[:self.pos] + ch + self.text[self.pos:]
                    self.pos += 1
                    self.typed += ch
        return None

    def shown(self, prompt: str, width: int, cap: int) -> list[str]:
        """The prompt and everything typed, wrapped at the column edge, with the
        cursor at the edit position - the last character only while it has not
        been moved. At most `cap` rows: the window follows the cursor, so
        moving it out of view scrolls to it rather than losing it."""
        # A pasted escape is drawn as its picture, not sent to the terminal.
        # One character in, one out on every branch here - self.pos indexes
        # this escaped string directly below, and a substitution that changed
        # length would put the cursor on the wrong character. A pasted U+258F
        # (the cursor's own glyph, so a copied composer line pastes one) is
        # substituted here too, to a different picture, so the one inserted
        # below for the real cursor is the only one in the string - the
        # search for it stays exact instead of finding whichever comes first.
        text = "".join("\u2595" if c == "\u258f" else
                       c if c == "\n" or c.isprintable() else
                       chr(0x2400 + ord(c)) if c < " " else "\ufffd" for c in self.text)
        pos = max(0, min(self.pos, len(text)))
        marked = f"{text[:pos]}\u258f{text[pos:]}"
        rows = _wrap_typed(f"{prompt} {marked}", width)
        cur = next(i for i, r in enumerate(rows) if "\u258f" in r)
        rows = _window_rows(rows, cur, cap)
        left = len(prompt)         # the prompt is bold wherever it wrapped to
        out = []
        for row in rows:
            k, left = min(left, len(row)), max(0, left - len(row))
            out.append(f"{BOLD}{row[:k]}{R}{row[k:]}" if k else row)
        return out


def _window_rows(rows: list[str], cur: int, cap: int) -> list[str]:
    """At most `cap` rows, keeping row `cur` (the cursor's) visible. This is the
    old behaviour - the last `cap` rows - when `cur` is the last row, which it
    always was before the cursor could move; moving it back now scrolls the
    window to it instead of losing it."""
    if len(rows) <= cap:
        return rows
    room = max(1, cap - 1)     # one row spent on the note, when there is one
    lo = max(0, min(cur, len(rows) - room))
    hi = lo + room
    before, after = lo, len(rows) - hi
    if cap <= 1:      # no room for a note; (not before and not after) never fires here
        return rows[lo:hi]
    bits = ([f"{before} earlier"] if before else []) + ([f"{after} more"] if after else [])
    return [f"{DIM}… {' and '.join(bits)} rows{R}"] + rows[lo:hi]


def _wrap_typed(text: str, width: int) -> list[str]:
    """Break text into rows of at most `width` columns and lose none of it.

    `wrap` drops the spaces it breaks on, which is right for reading and wrong
    for editing: the cursor is after the last character typed, and a trailing
    space that vanished puts it on the wrong one. A row ends after the last
    space that fits, or at the column edge when a word does not.
    """
    width = max(1, width)
    out: list[str] = []
    for line in text.split("\n"):
        while True:
            room = cut = 0
            for i, ch in enumerate(line):
                if room + cwidth(ch) > width:
                    break
                room += cwidth(ch)
                cut = i + 1
            else:
                out.append(line)
                break
            cut = max(cut, 1)
            space = line.rfind(" ", 0, cut)
            if line[cut] != " " and line[cut - 1] != " " and space > 0:
                cut = space + 1
            out.append(line[:cut])
            line = line[cut:]
    return out


def _composer(multi: bool = False) -> Composer:
    """A prompt's reader, starting where the screen's own reader left off.

    A read can end in the middle of a paste that _key began skipping. There is
    one paste state, and it moves to the prompt: left behind, the screen would
    go on dropping keys and the prompt would take the rest of the paste as typing.
    """
    box = Composer(multi)
    st = _paste_state
    box.pasting, box._carry = st["in"], st["carry"]
    st["in"], st["carry"] = False, ""
    return box


_UP_DOWN = re.compile(r"\033(?:\[[AB]|O[AB])")


def _line(prompt: str, width: int, height: int, multi: bool = False,
          under=None, commands: list[tuple[str, str]] | None = None) -> str | None:
    """Read a message with the screen still up. None when the human backs out.

    Every prompt reads through here, so a paste is one paste in all of them:
    text arrives whole, and only a key the human presses sends it. `multi` is
    for prompts whose text goes to an agent; the rest take one line.

    What is typed is shown whole, on as many rows as it takes (at most half the
    screen). The rows come out of the bottom of the screen, so `under(h)` - the
    screen as it draws at height h - is painted above them; without it they
    cover what was there until the prompt closes.

    `commands`, when given, turns a leading '/' into autocomplete: the names
    that match what follows it are listed under the composer, ↑↓ (otherwise
    unbound here - Composer has no vertical move) changes which is
    highlighted, and Enter sends the highlighted one - as '/name' whole, not
    whatever prefix was actually typed - rather than the typed text. Text
    starting with '/' that matches nothing is sent as typed: a real message
    is still allowed to start with a slash.

    Enter resolves to a command only when exactly one matches, or the human
    pressed ↑↓ to say which - never by defaulting to whichever sorts first
    among several. A command name is a verb, one of them possibly a mutating
    one, and picking one for someone who typed an ambiguous prefix and only
    meant to send it is the wrong direction to guess in; sent as plain text,
    a wrong guess is merely a strange message, still visible and undoable."""
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    box = _composer(multi)
    data = "".join(_pending)           # keys already read ahead of this prompt
    _pending.clear()
    painted = 1                        # rows the prompt has, as the screen was drawn
    sel = 0                            # highlighted row in the command list, if one is shown
    matches: list[str] = []            # what is currently displayed, from the last render
    picked = False                     # the human moved the highlight, deliberately
    while True:
        # Every leading arrow, not just the first - one read can hold several
        # (key repeat, a paste, a script), and Composer silently swallows an
        # arrow it does not itself map, so a second one left in `data` would
        # otherwise move the highlight by one row instead of two.
        while matches and data:
            m = _UP_DOWN.match(data)
            if not m:
                break
            sel = max(0, min(len(matches) - 1,
                             sel + (1 if m.group() in ("\033[B", "\033OB") else -1)))
            picked = True
            data = data[m.end():]
        done = box.feed(data) if data else None
        if done == "cancel":
            return None
        # Recomputed from the text as it now stands - a burst that types a
        # prefix and presses Enter in the same read (a paste, a fast typist,
        # a script) reaches `done == "send"` in this same call, and `matches`
        # from before the feed would still be the empty list from before
        # anything was typed, sending the raw prefix as a literal message
        # instead of resolving it.
        new_matches = ([n for n, _ in commands if n.startswith(box.text[1:])]
                       if commands is not None and box.text.startswith("/") else [])
        # The highlight only survives typing that leaves the same list in the
        # same order - clamping alone would keep index 1 pointed at whatever
        # is now there, silently retargeting it to a different command.
        if new_matches != matches:
            sel, picked = 0, False
        matches = new_matches
        if done == "send":
            # Several matches and no explicit pick is not "run the one that
            # happens to sort first" - a command name is a verb, one of them
            # possibly a mutating one, and a typo left ambiguous is not a
            # human choosing it.
            if len(matches) == 1 or (matches and picked):
                return f"/{matches[sel]}"
            return box.text.strip("\n") if multi else box.text.strip()
        rows = box.shown(prompt, width, max(1, height // 2))
        if matches:
            rows = rows + [f"{DIM}  " + "  ".join(
                (f"{R}▸{DIM}" if i == sel else " ") + n for i, n in enumerate(matches)) + R]
        top = height - len(rows) + 1
        if under and len(rows) != painted:
            sys.stdout.write(HOME + under(top))
        painted = len(rows)
        sys.stdout.write("".join(f"\033[{top + i};1H" + CLEAR_EOL + row
                                 for i, row in enumerate(rows)))
        sys.stdout.flush()
        data = ""
        if not select.select([fd], [], [], 30.0)[0]:
            continue
        try:
            data = _read(fd)
        except OSError:
            return None


def _confirm(what: str, width: int, height: int) -> bool:
    """Ask yes or no over the bottom of the screen; only a typed y confirms.

    It takes no `under`, unlike `_line`, and that is safe for one reason: its
    row count is fixed by `textwrap.wrap(what)[:3]` and drawn once, before it
    reads anything, and it never draws what is typed - so a paste cannot make it
    grow over the page.
    """
    if not sys.stdin.isatty():
        return False
    # What will happen and who is told can be longer than one line, and a
    # question whose answer is cut off is one nobody can answer.
    rows = textwrap.wrap(f"{what}?", max(10, width - 8))[:3] or [f"{what}?"]
    out = "".join(f"\033[{height - len(rows) + 1 + i};1H" + CLEAR_EOL
                  + fit(f"{YEL}{ln}{R}" + (f" {DIM}y / n{R}" if i == len(rows) - 1 else ""),
                        width)
                  for i, ln in enumerate(rows))
    sys.stdout.write(out)
    sys.stdout.flush()
    # Read through a Composer so that what is pasted is not read as keys: a
    # paste with a "y" in it must not confirm a destructive action.
    fd = sys.stdin.fileno()
    box = _composer()
    data = "".join(_pending)
    _pending.clear()
    while True:
        if data and box.feed(data) is not None:
            return False               # Enter or escape is not a yes
        if box.typed:
            return box.typed[0] in ("y", "Y")
        data = ""
        if not select.select([fd], [], [], 30.0)[0]:
            continue
        try:
            data = _read(fd)
        except OSError:
            return False


def _suspend(argv: list[str], env: dict, fd, saved) -> tuple[int | None, str]:
    """Hand the whole terminal to another program, then take it back.

    Logging in is a human typing a code into claude's own screen: nothing about
    that can happen behind an alternate screen with the cursor hidden.
    """
    # Only a screen that is up has to be taken down. Without a terminal there
    # is no alternate screen to leave, and writing the escapes anyway puts them
    # in whatever is reading our output.
    on_screen = sys.stdout.isatty()
    if on_screen:
        sys.stdout.write(CUR_ON + ALT_OFF)
        sys.stdout.flush()
    if saved:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    code, out = None, ""
    try:
        r = subprocess.run(argv, env=env)
        code, out = r.returncode, f"{argv[0]} exited {r.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        out = f"could not run {argv[0]}: {e}"
    finally:
        if saved:
            tty.setcbreak(fd)
        if on_screen:
            sys.stdout.write(ALT_ON + CUR_OFF)
            sys.stdout.flush()
    return code, out


def run(interval: float = 2.0, once: bool = False) -> int:
    if once or not sys.stdout.isatty():
        # No cursor and no interaction: `sel` is deliberately left empty, so
        # the footer falls back to the unfiltered common set rather than
        # picking one agent among many to filter by. There is no single row
        # this snapshot is "about" the way an interactive cursor names one.
        size = shutil.get_terminal_size((100, 40))
        print(frame(min(size.columns, 200), size.lines))
        return 0

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if sys.stdin.isatty() else None
    panel, sel, flash, detail = AGENTS, {}, "", None
    _enter(panel, sel)
    known: dict = {}
    try:
        if saved:
            tty.setcbreak(fd)
        sys.stdout.write(ALT_ON + CUR_OFF)
        while True:
            settle(sel, known)
            size = shutil.get_terminal_size((100, 40))
            width, height = size.columns, size.lines
            shown, detail = _redraw(width, height, sel, panel, flash, detail)
            sys.stdout.write(HOME + shown)
            sys.stdout.flush()
            k = _key(interval)
            if k is None:
                continue
            if k in ("q", "Q", "\x03"):
                return 0
            if detail is not None:
                try:
                    detail, flash = _in_detail(detail, k, width, height, fd, saved)
                except Exception as e:  # noqa: BLE001
                    flash = f"{RED}{type(e).__name__}: {e}{R}"
                continue
            flash = ""
            moved = navigate(panel, sel, k)
            if moved is not None:
                panel = moved
                continue
            try:
                flash, detail = _act(panel, sel, k, width, height, fd, saved)
            except Exception as e:  # noqa: BLE001
                # One action failing is that action's failure, not the end of
                # the screen - the daemon wraps each agent for the same reason.
                flash = f"{RED}{type(e).__name__}: {e}{R}"
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write(CUR_ON + ALT_OFF)
        sys.stdout.flush()
        if saved:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _redraw(width: int, height: int, sel: dict, panel: int, flash: str,
            detail: dict | None) -> tuple[str, dict | None]:
    """One tick of the screen: the open page brought up to date, then drawn."""
    detail = _refreshed(detail)
    return frame(width, height, sel, panel, flash, detail), detail


def _refreshed(detail: dict | None) -> dict | None:
    """An open agent page as the database has it now.

    The screen redraws every interval, and a page laid out once would show a
    conversation as it was when it was opened - an answer landing while it is
    read would not appear until the reader backed out and came back.
    """
    if not detail or not detail.get("agent"):
        return detail
    con = db.connect()
    try:
        return _agent_page(con, detail["agent"], detail["panel"], detail["title"], detail)
    except Exception as e:  # noqa: BLE001
        # The last page stays up, and says it is not current.
        detail["refresh_error"] = f"{type(e).__name__}: {e}"
        return detail
    finally:
        con.close()


SCROLL_KEYS = ("j", "\033[B", "\033OB", "k", "\033[A", "\033OA",
               " ", "\033[6~", "b", "\033[5~", "g", "G")


def _in_detail(detail: dict, k: str, width: int, height: int,
               fd=None, saved=None) -> tuple[dict | None, str]:
    """One keypress while something is open. Returns (what is open now, line).

    Closing is not the only thing a reader wants to do with a message: the one
    they have just read is the one they are about to answer, and making them
    back out to the panel to do it is how an answer gets put off.
    """
    room = max(1, height - 4)
    if k in ("\033", "\x1b"):
        return None, ""
    if k in ("\033[C", "\033OC", "\t"):
        _go_to_tab(detail, detail.get("tab", 0) + 1)
        return detail, ""
    if k in ("\033[D", "\033OD", "\033[Z"):
        _go_to_tab(detail, detail.get("tab", 0) - 1)
        return detail, ""
    if detail.get("stem") == actions.CHAT_TAB and k in ("\r", "\n", "e", "m"):
        return _write_in_thread(detail, k, width, height, fd, saved)
    picks = _open_picks(detail)
    if picks and k in ("j", "\033[B", "\033OB", "k", "\033[A", "\033OA"):
        down = k in ("j", "\033[B", "\033OB")
        detail["at"] = max(0, min(len(picks) - 1, detail.get("at", 0) + (1 if down else -1)))
        # Scrolled only as far as keeps the whole request in view - or its
        # first line, when it is taller than the page.
        plain = [_ANSI.sub("", ln) for ln in lines_of(detail, width)]
        row = next((i for i, ln in enumerate(plain) if ln.startswith("│▸")), None)
        if row is not None:
            end = next((i for i in range(row + 1, len(plain))
                        if not plain[i][1:-1].strip() or plain[i].startswith("└")), len(plain)) - 1
            top = detail.get("top", 0)
            if row < top:
                top = row
            elif end >= top + room:
                top = min(row, end - room + 1)
            detail["top"] = max(0, top)
        return detail, ""
    if k == "x" and detail.get("panel") in (AGENTS, STUCK):
        if not picks:
            return detail, "x cancels a request: open WAITING FOR IT (←→) and pick one (↑↓)"
        return _cancel_key(detail, picks, width, height)
    if k in SCROLL_KEYS:
        # The frame pins a conversation to its end while `end` is set, so a
        # reader who scrolls has to clear it; the frame sets it again when
        # they come back to the bottom.
        detail["end"] = False
    if k in ("j", "\033[B", "\033OB"):
        detail["top"] += 1
        return detail, ""
    if k in ("k", "\033[A", "\033OA"):
        detail["top"] = max(0, detail["top"] - 1)
        return detail, ""
    if k in (" ", "\033[6~"):
        detail["top"] += room
        return detail, ""
    if k in ("b", "\033[5~"):
        detail["top"] = max(0, detail["top"] - room)
        return detail, ""
    if k == "g":
        detail["top"] = 0
        return detail, ""
    if k == "G":
        detail["top"] = max(0, len(lines_of(detail, width)) - room)
        return detail, ""
    if detail.get("panel") in (AGENTS, STUCK):
        # An agent's page is opened to decide something about that agent, and
        # making the reader back out to the list to press the key is how a
        # decision gets put off.
        if k == "?":
            return detail, agent_help_line()
        label = dict(AGENT_KEYS).get(k)
        if not label or label == "open":
            return detail, ""
        name = str(detail["key"])
        con = db.connect()
        try:
            return _dispatch_agent_verb(con, detail, name, label, width, height, fd, saved)
        finally:
            con.close()
    if detail.get("panel") != MESSAGES or k not in ("a", "e", "x"):
        return detail, ""

    rid = int(detail["key"])
    con = db.connect()
    try:
        return _answer_key(con, detail, rid, k, width, height, fd, saved)
    except Exception as e:  # noqa: BLE001
        # Answering can fail in ways reading cannot - an editor that will not
        # start, a request resolved from elsewhere between the read and the
        # reply. That is this key's failure, not the screen's.
        return detail, f"{RED}{type(e).__name__}: {e}{R}"
    finally:
        con.close()


def _dispatch_agent_verb(con, detail: dict, name: str, label: str, width: int, height: int,
                         fd, saved) -> tuple[dict, str]:
    """Run one AGENT_KEYS verb against `name` - the body both a keypress and a
    slash command funnel through, so a command cannot drift from what its
    keybinding does. `can` has already been asked by neither caller; it is
    asked here, once, so there is exactly one place that can say no."""
    allowed, why = actions.can(con, name, label)
    if not allowed:
        return detail, why
    if label in ("message", "editor"):
        body = _compose_message(name, label, width, height, fd, saved,
                                under=lambda h: _detail_frame(width, h, dict(detail)))
        return detail, (actions.message(con, name, body) if body is not None
                        else _nothing_sent("e" if label == "editor" else "m"))
    if label == "terminal" and _needs_a_terminal(con, name):
        repo = _line(f"{name} has nowhere to run - repo to start it in "
                     f"[{os.getcwd()}]:", width, height,
                     under=lambda h: _detail_frame(width, h, dict(detail)))
        if repo is None:
            return detail, "not started"
        _say(f"starting {name}", width, height)
        return detail, actions.terminal(con, name, repo.strip() or os.getcwd())
    if label == "move":
        return detail, _move_agent(con, name, width, height,
                                   under=lambda h: _detail_frame(width, h, dict(detail)))
    if label in CONFIRM and not _confirm(f"{label} {name}", width, height):
        return detail, "left alone"
    _say(f"{label} {name}", width, height)
    said = getattr(actions, label)(con, name)
    # What it says about itself has just changed.
    return _agent_page(con, name, detail["panel"], detail["title"], detail), said


def _run_info_command(con, detail: dict, name: str, cmd: str) -> tuple[dict, str]:
    """The read-only slash commands: nothing to decide, so no `can` gate - the
    terminal handle and worktree path the head used to show."""
    if cmd == "where":
        return detail, actions.agent_where(con, name)
    if cmd == "worktree":
        return detail, actions.agent_worktree(con, name)
    return detail, f"/{cmd}: not wired"        # unreachable while INFO_COMMANDS names all of it


def _run_command(con, detail: dict, name: str, cmd: str, cmds: dict[str, str],
                 width: int, height: int, fd, saved) -> tuple[dict, str]:
    """A slash command typed in the composer, already resolved to a canonical
    name by `_line`'s own autocomplete - never raw text a person half-typed.
    `cmds` is the caller's own `agent_commands` read, not asked again here."""
    if cmds.get(cmd) == "info":
        return _run_info_command(con, detail, name, cmd)
    return _dispatch_agent_verb(con, detail, name, cmd, width, height, fd, saved)


def _write_in_thread(detail: dict, k: str, width: int, height: int,
                     fd, saved) -> tuple[dict, str]:
    """The composer under a conversation: what is typed goes under its thread,
    unless it is a slash command - the same conversation, the same composer,
    a second kind of thing it can send."""
    name = str(detail["key"])
    con = db.connect()
    try:
        allowed, why = actions.can(con, name, "message")
        if not allowed:
            return detail, why
        cmds = agent_commands(con, name)
        if k == "e":
            body = _editor(f"# a message to {name}. Lines starting with # are dropped.\n",
                           fd, saved)
        else:
            body = _line(f"to {name}:", width, height, multi=True,
                         under=lambda h: _detail_frame(width, h, dict(detail)),
                         commands=cmds)
        if body is None:
            return detail, _nothing_sent(k)
        cmds = dict(cmds)
        # `_line`'s own contract: text starting with '/' that matched nothing
        # comes back exactly as typed, so it is not necessarily a resolved
        # command - only a body that names one of `cmds` is. A literal
        # message that happens to type a real command's name runs it, the
        # same tradeoff any chat command syntax makes.
        if k != "e" and body.startswith("/") and body[1:] in cmds:
            return _run_command(con, detail, name, body[1:], cmds, width, height, fd, saved)
        said = actions.chat_reply(con, name, body)
        detail["end"] = True
        return _agent_page(con, name, detail["panel"], detail["title"], detail), said
    finally:
        con.close()


def _cancel_key(detail: dict, picks: list[int], width: int, height: int
                ) -> tuple[dict, str]:
    """x on a request in WAITING FOR IT: say what it will do and to whom, ask,
    then take it out of the queue. One key for a request not yet delivered
    (withdrawn) and for one an agent is sitting on (closed as rejected)."""
    rid = picks[max(0, min(detail.get("at", 0), len(picks) - 1))]
    name = str(detail["key"])
    con = db.connect()
    try:
        ok, plan = actions.cancel_plan(con, rid)
        if not ok:
            return detail, plan
        if not _confirm(plan, width, height):
            return detail, "left alone"
        _say(f"cancelling #{rid}", width, height)
        said = actions.cancel(con, rid)
        return _agent_page(con, name, detail["panel"], detail["title"], detail), said
    finally:
        con.close()


def _answer_key(con, detail: dict, rid: int, k: str, width: int, height: int,
                fd, saved) -> tuple[dict | None, str]:
    if k == "x":
        return None, actions.dismiss(con, rid, me=actions.HUMAN)
    text = _compose(rid, k, width, height, fd, saved,
                    under=lambda h: _detail_frame(width, h, dict(detail)))
    if text is None:
        return detail, _nothing_sent(k)
    _say(f"answering #{rid}", width, height)
    return None, actions.answer(con, rid, text)


def _compose_message(agent: str, label: str, width: int, height: int,
                     fd, saved, under=None) -> str | None:
    """An order, typed on the line or written in $EDITOR. None means none.

    The line is enough to say "rebase onto main"; it is not enough to say what
    to do and why not to do the other thing, and an order that needs a
    paragraph was being written somewhere else and pasted - or cut down to fit.
    """
    if label == "editor":
        return _editor(f"# a message to {agent}. Lines starting with # are dropped.\n",
                       fd, saved)
    return _line(f"message to {agent}:", width, height, multi=True, under=under)


def _compose(rid: int, key: str, width: int, height: int, fd, saved,
             under=None) -> str | None:
    """An answer, typed on the line or written in $EDITOR. None means none."""
    if key == "e":
        return _editor(f"# answering #{rid}. Lines starting with # are dropped.\n",
                       fd, saved)
    return _line(f"answer #{rid}:", width, height, multi=True, under=under)


def _nothing_sent(key: str) -> str:
    if key == "e" and not (os.environ.get("EDITOR") or os.environ.get("VISUAL")):
        return "nothing sent - $EDITOR is not set"
    return "nothing sent"


def _editor(seed: str, fd=None, saved=None) -> str | None:
    """Write a long answer where long answers are written.

    A one-line prompt is enough to say yes; it is not enough to say why, and an
    answer to an escalation is usually the why. None when nothing was written,
    so backing out of the editor sends nothing.
    """
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        return None
    try:
        argv = shlex.split(editor)
    except ValueError:
        return None                    # $EDITOR with an unclosed quote
    fd_, path = tempfile.mkstemp(prefix="ocp-answer-", suffix=".md")
    os.close(fd_)
    Path(path).write_text(seed)
    try:
        # `saved` is the terminal as the human's shell had it. Handing an
        # editor the cbreak mode this screen runs in gives it a terminal in a
        # state it did not ask for and does not put back.
        code, _out = _suspend([*argv, path], dict(os.environ), fd, saved)
        if code != 0:
            # Non-zero is how an editor says "abandon this" - `:cq` in vim, and
            # what `git commit` honours. Sending the buffer anyway would send
            # half an answer.
            return None
        body = "\n".join(ln for ln in Path(path).read_text().splitlines()
                         if not ln.startswith("#")).strip()
        return body or None
    except OSError:
        return None                    # the editor removed the file under us
    finally:
        Path(path).unlink(missing_ok=True)


def _say(what: str, width: int, height: int) -> None:
    """Put one line on the bottom row now, without waiting for a redraw.

    An action can take half a minute - waking waits for a session to appear,
    parking clears a session - and a screen still showing the frame from before
    the keypress, or the confirmation that was just answered, reads as a hang.
    The next redraw repaints every row, so this cannot outlive its action.
    """
    if not sys.stdout.isatty():
        return                      # no screen to put it on
    sys.stdout.write(f"\033[{height};1H" + CLEAR_EOL + fit(f"{DIM}{what}…{R}", width))
    sys.stdout.flush()


# Keys that move the cursor rather than act on what it is on. One function
# because the loop and the tests have to press the same keys: a `step` that was
# right and a loop that still called the old `move` is how ↓ stopped at the
# bottom of a panel and left the cursor nowhere.
NEXT_PANEL = ("\t", "\033[C", "\033OC")
PREV_PANEL = ("\033[Z", "\033[D", "\033OD")
# ←→ without tab and shift-tab: on a supervisor the arrows fold its team,
# and tab is left as the key that always jumps.
ARROWS = ("\033[C", "\033OC", "\033[D", "\033OD")
NEXT_ROW = ("j", "\033[B", "\033OB")
PREV_ROW = ("k", "\033[A", "\033OA")


def navigate(panel: int, sel: dict, k: str) -> int | None:
    """Where this keypress leaves the cursor, or None if it was not a move.

    ←→ and tab jump a whole panel; ↑↓ walk the screen and cross into the next
    panel at the end of one. On a supervisor, ←→ close and open its team
    first; tab always jumps. `r` is a move of zero: it asks for a redraw, which
    happens anyway, and it must not fall through to the actions.
    """
    if panel == AGENTS and k in ARROWS and _fold(sel, k):
        return panel
    if k in NEXT_PANEL:
        return _enter((panel + 1) % len(PANELS), sel)
    if k in PREV_PANEL:
        return _enter((panel - 1) % len(PANELS), sel)
    if k in NEXT_ROW:
        return step(panel, sel, 1)
    if k in PREV_ROW:
        return step(panel, sel, -1)
    if k in ("r", "R"):
        return panel
    return None


def _enter(panel: int, sel: dict) -> int:
    """Move into a panel, with the cursor on something.

    A panel nobody had arrowed through yet had no selected row, so the first
    key pressed in it answered "selection moved - press again" - which reads as
    "you cannot do that here" rather than "press it once more". Arriving is
    what puts the cursor on the first row.
    """
    if sel.get(panel) is None:
        keys = _keys_of(panel)
        if keys:
            sel[panel] = keys[0]
    return panel


def _needs_a_terminal(con, agent: str) -> bool:
    """Is there nowhere for this agent to run? Then one has to be made first."""
    row = db.get_agent_state(con, agent)
    return not (row and row["terminal"])


def _keys_of(panel: int) -> list:
    """What the cursor can sit on in a panel, read fresh each time.

    The list changes under the cursor - an agent is registered, a message is
    answered - so it is asked for rather than remembered.
    """
    if panel == ACCOUNTS:
        return paths.list_accounts()
    if panel == AGENTS:
        # In the order they are drawn, not the order they are stored: the
        # cursor moves down the screen, and a tree is not alphabetical.
        return [name for name, _depth, _under in visible_tree()]
    con = db.connect()
    try:
        if panel == MESSAGES:
            return [m["id"] for m in actions.messages(con)]
        # The rows about an account have no agent to act on, and a name that
        # appears twice is one place to put the cursor, not two.
        seen: list = []
        for b in actions.stuck(con):
            if b["agent"] and b["agent"] not in seen:
                seen.append(b["agent"])
        return seen
    finally:
        con.close()


def _move_agent(con, name: str, width: int, height: int, under=None) -> str:
    """Ask where, confirm (it ends a session), then the daemon's own move."""
    dest = _line(f"move {name} to which account:", width, height, under=under)
    if not dest:
        return "not moved"
    dest = dest.strip()
    if not _confirm(f"move {name} to {dest} - records its handoff, ends its session, "
                    f"and makes {dest} its preferred account", width, height):
        return "left alone"
    # A confirmation waits on a person; what was true at the keypress may not be.
    allowed, why = actions.can(con, name, "move")
    if not allowed:
        return f"{why} - it changed while you looked"
    _say(f"moving {name} to {dest}", width, height)
    return actions.move(con, name, dest)


def _add_account(width: int, height: int, fd, saved, under=None) -> str:
    """Name it, then the same sequence as `ocp account add`."""
    name = _line("new account name:", width, height, under=under)
    if not name:
        return "not added"
    ok, msg = accounts.add_account(
        name, lambda argv, env: _suspend(argv, env, fd, saved)[0])
    return msg


def _set_cap(name: str, width: int, height: int, under=None) -> str:
    """The most agents that may be mid-turn on this account at once."""
    text = _line(f"most agents mid-turn on {name} at once [{loadmod.cap_of(name)}]:",
                 width, height, under=under)
    if not text or not text.strip():
        return "cap unchanged"
    try:
        loadmod.set_cap(name, int(text.strip()))
    except ValueError:
        return "a cap is a whole number, at least 1"
    return f"{name}: at most {text.strip()} agents mid-turn"


def _remove_account(name: str, width: int, height: int, fd, saved, under=None) -> str:
    """The same sequence as `ocp account remove`, confirmed by typing its name."""
    con = db.connect()
    try:
        ok, msg = accounts.remove_account(
            name, lambda: _line(f"remove '{name}'? type its name to confirm:",
                                width, height, under=under) == name,
            lambda argv, env: _suspend(argv, env, fd, saved)[0], con)
    finally:
        con.close()
    return msg


def _act(panel: int, sel: dict, key: str, width: int, height: int,
         fd, saved) -> tuple[str, list[str] | None]:
    """One keypress on the selected row, leaving the cursor on a row that exists."""
    before = {panel: _keys_of(panel)}
    try:
        return _act_on(panel, sel, key, width, height, fd, saved)
    finally:
        settle(sel, before)


def _act_on(panel: int, sel: dict, key: str, width: int, height: int,
            fd, saved) -> tuple[str, list[str] | None]:
    """One keypress on the selected row. Returns (line to show, detail or None)."""
    keys = _keys_of(panel)
    target = sel.get(panel)
    # Adding needs no row under the cursor: the first account is added to an
    # empty panel.
    if panel == ACCOUNTS and key == "a":
        return _add_account(width, height, fd, saved,
                            under=lambda h: frame(width, h, sel, panel)), None
    if not keys:
        return "nothing to act on", None
    if target not in keys:
        # The row under the cursor is gone - a message answered elsewhere, an
        # agent that went away - and nothing is drawn as selected. Acting on
        # the first row would act on something the human never pointed at: it
        # dismissed an unread message that way.
        sel[panel] = keys[0]
        return "selection moved - press again", None

    if panel == ACCOUNTS and key in ("\r", "\n"):
        con = db.connect()
        try:
            return "", page(f"account {target}",
                            actions.account_detail(con, str(target), _logged_in),
                            ACCOUNTS, target)
        finally:
            con.close()
    if panel == ACCOUNTS and key == "l":
        line, argv, env = actions.login_argv(str(target))
        if not argv:
            return line, None
        return _suspend(argv, env, fd, saved)[1], None
    if panel == ACCOUNTS and key == "x":
        return _remove_account(str(target), width, height, fd, saved,
                               under=lambda h: frame(width, h, sel, panel)), None
    if panel == ACCOUNTS and key == "c":
        return _set_cap(str(target), width, height,
                        under=lambda h: frame(width, h, sel, panel)), None

    con = db.connect()
    try:
        if panel in (AGENTS, STUCK):
            name = str(target)
            if key in ("\r", "\n"):
                return "", _agent_page(con, name, panel)
            if key == "?":
                # Every key AGENT_KEYS binds, not only the ones the footer
                # shows for this row: `?` is the one place fold and move (and
                # whatever the row's own state hides) are named at all.
                return agent_help_line(), None
            label = dict(AGENT_KEYS).get(key)
            if not label:
                return "", None
            allowed, why = actions.can(con, name, label)
            if not allowed:
                return why, None
            if label == "terminal" and _needs_a_terminal(con, name):
                # Going to an agent that has nowhere to run means making it
                # somewhere first, and the only thing that cannot be worked out
                # from here is which repo.
                repo = _line(f"{name} has nowhere to run - repo to start it in "
                             f"[{os.getcwd()}]:", width, height,
                             under=lambda h: frame(width, h, sel, panel))
                if repo is None:
                    return "not started", None
                _say(f"starting {name}", width, height)
                return actions.terminal(con, name, repo.strip() or os.getcwd()), None
            if label in ("message", "editor"):
                body = _compose_message(name, label, width, height, fd, saved,
                                        under=lambda h: frame(width, h, sel, panel))
                return (actions.message(con, name, body) if body is not None
                        else _nothing_sent("e" if label == "editor" else "m")), None
            if label == "move":
                return _move_agent(con, name, width, height,
                                   under=lambda h: frame(width, h, sel, panel)), None
            if label in CONFIRM:
                if not _confirm(f"{label} {name}", width, height):
                    return "left alone", None
                # A confirmation waits on a human for as long as the human
                # takes, and the daemon ticks every two seconds: work can
                # arrive and a turn can start inside that wait. What was true
                # when the key was pressed is not what this acts on.
                allowed, why = actions.can(con, name, label)
                if not allowed:
                    return f"{why} - it changed while you looked", None
            _say(f"{label} {name}", width, height)
            return getattr(actions, label)(con, name), None

        if panel == MESSAGES:
            rid = int(target)
            row = con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
            if not row:
                return f"#{rid} is gone", None
            if key in ("\r", "\n"):
                actions.opened_by_human(con, rid)
                detail = actions.message_detail(con, rid)
                if detail is None:
                    return f"#{rid} is gone", None
                title, sections = detail
                return "", page(title, sections, MESSAGES, rid)
            if key in ("a", "e"):
                text = _compose(rid, key, width, height, fd, saved,
                                under=lambda h: frame(width, h, sel, panel))
                if text is None:
                    return _nothing_sent(key), None
                _say(f"answering #{rid}", width, height)
                return actions.answer(con, rid, text), None
            if key == "x":
                return actions.dismiss(con, rid, me=actions.HUMAN), None
        return "", None
    finally:
        con.close()
