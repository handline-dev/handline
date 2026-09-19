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
import time
import tty
import unicodedata
from pathlib import Path

from . import accounts, actions, agents, daemon, db, launch, paths, status, triggers
from . import waiting as promptwait

ALT_ON, ALT_OFF = "\033[?1049h", "\033[?1049l"
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
]
ACCOUNT_KEYS = [("↵", "open"), ("l", "login"), ("a", "add"), ("x", "remove")]
MESSAGE_KEYS = [("↵", "read"), ("a", "answer"), ("e", "answer in $EDITOR"),
                ("x", "dismiss")]
# A stuck row is about an agent, so it answers to the agent keys.
STUCK_KEYS = AGENT_KEYS
CONFIRM = {"close", "park", "unstall", "fold"}


def cwidth(ch: str) -> int:
    """How many columns one character takes.

    A Korean or Japanese glyph is drawn two columns wide, and counting it as
    one put every box border a column short per glyph: a handoff written in
    Korean tore the panel it was drawn in. Combining marks take none.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


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


def bar(pct: int | None, width: int = 10) -> str:
    if pct is None:
        return DIM + "·" * width + R
    filled = max(0, min(width, round(pct / 100 * width)))
    color = RED if pct >= 85 else (YEL if pct >= 60 else GRN)
    return color + "█" * filled + DIM + "░" * (width - filled) + R


def pct(v: int | None) -> str:
    return "  ?" if v is None else f"{v:>3}"


def countdown(ts: int | None) -> str:
    if not ts:
        return "   -"
    left = ts - int(time.time())
    if left <= 0:
        return " due"
    if left >= 3600:
        return f"{left // 3600}h{(left % 3600) // 60:02d}"
    return f"{left // 60:>2}m{left % 60:02d}"


def age(secs: float | None) -> str:
    if secs is None:
        return "?"
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs / 60)}m"
    return f"{int(secs / 3600)}h"


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


def visible_tree() -> list[tuple[str, int, int]]:
    """The team as drawn: (name, depth, how many under it are not drawn).

    The cursor walks the same list, so it never lands on a row nobody can see.
    A report that needs a person is drawn whether its supervisor is open or
    not - hiding the one row this screen exists for is the one thing folding
    must not do.
    """
    tree = actions.agent_tree()
    keep = _wants_a_human()
    rows: list[list] = []
    closed: list[tuple[int, int]] = []     # (depth, row) of each closed supervisor
    for i, (name, depth) in enumerate(tree):
        while closed and depth <= closed[-1][0]:
            closed.pop()
        if closed and name not in keep:
            rows[closed[-1][1]][2] += 1
            continue
        under = 0
        for _n, d in tree[i + 1:]:
            if d <= depth:
                break
            under += 1
        rows.append([name, depth, 0])
        if under and name not in EXPANDED:
            closed.append((depth, len(rows) - 1))
    return [(n, d, u) for n, d, u in rows]


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
            out.append(f"{DIM}│{R}" + pad(fit(gutter + row, inner), inner) + f"{DIM}│{R}")
        if held_back and limit is not None and limit >= 2:
            more = f"{DIM}  … {held_back} more{R}"
            out.append(f"{DIM}│{R}" + pad(fit(more, inner), inner) + f"{DIM}│{R}")
        out.append(DIM + "└" + "─" * inner + "┘" + R)
        return out


# The states a person has to do something about. Everything else the daemon
# handles on its own, and saying so is saying nothing.
NEEDS_A_HUMAN = ("STALLED", "DEGRADED")


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
    return f"{RED}{row['state'].lower()}{R} {DIM}{(row['detail'] or '')[:60]}{R}"


def _plain(text: str) -> str:
    """What someone else wrote, made safe to draw: an escape is invisible to the
    width count and `esc [ 2J` clears the screen it is drawn on."""
    return "".join(c if c.isprintable() else " " for c in _ANSI.sub("", text))


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
        out.append(fit(f"{colour}  {_plain(w['who'])} · {_plain(w['what'])} · "
                       f"{age(now - w['since'])}{R}", width))
    if shown < len(waits) and room:
        out.append(f"{DIM}  … {len(waits) - shown} more{R}")
    return out


def keybar(panel: int, width: int, hidden: int = 0) -> str:
    """The keys this panel answers to, in as much room as there is.

    Half an action's name is worse than no name: `u u…` tells a reader nothing
    and looks like a bug. So the line sheds whole pieces - the navigation
    first, since it is the same on every panel, then keys from the end - and
    says how many it dropped.
    """
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
          wait_budget: int | None = None) -> tuple[list[str], list[Panel]]:
    """Return (header lines, panels) for one frame.

    `sel` is {panel: key}: which row carries the cursor in each panel, so the
    caller can keep a cursor across redraws without this knowing how it moves.
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
        acc.add(f"{DIM}{'':<2}{'account':<10}{'5h usage':<18}{'reset':<8}"
                f"{'7d':<12}{'7d reset':<9}why{R}", heading=True)
        # The account with the most headroom: where work goes when an agent's
        # own account is full. Only accounts that can start a session count.
        # Captured whole, not just the pick: this panel says why each one sits
        # where it does, not only which one won.
        first, ranked = status.pick_account(
            [a for a in paths.list_accounts() if _logged_in(a)])
        why_of = dict(ranked)
        for name in paths.list_accounts():
            st = status.read(name)
            allowed, plain_why = st.takes_work()
            # An account outside the ranking (not logged in) has no ranked
            # reason to fall back to - `assignable` already refused it and
            # says why.
            why = why_of.get(name, plain_why)
            mark = f"{GRN}✔{R}" if allowed else f"{RED}✘{R}"
            nxt = f"{GRN}{BOLD}>{R} " if name == first else "  "
            # Show the figure decisions are made on, with a mark saying where
            # it came from. Drawing a quiet window as "?" while the daemon acts
            # on 0% is the dashboard and the daemon disagreeing.
            five = st.trusted_pct_5h
            src = status.mark(st.pct_5h_source)
            # Every cell is padded on its visible width: the bars carry escape
            # sequences, so a plain :<n counts them and the columns drift out
            # from under their own headings.
            acc.add(
                nxt
                + pad(f"{BOLD}{name}{R}", 10)
                + pad(f"{bar(five)} {pct(five)}%{src}", 18)
                + pad(countdown(st.reset_5h), 8)
                # The weekly figure carries its provenance like the 5h one.
                # Drawn bare, a number from a stale snapshot - or from a week
                # that has since rolled over - reads as this week's measurement.
                + pad(f"{bar(st.trusted_pct_7d, 6)} {pct(st.trusted_pct_7d)}%"
                      f"{status.mark(st.pct_7d_source)}", 12)
                # The deadline the ordering spends against: when this
                # window's headroom is lost if nobody uses it.
                + pad(countdown(st.reset_7d), 9)
                + f"{mark} {DIM}{why}{R}",
                key=name,
            )
        if not paths.list_accounts():
            acc.add(f"{DIM}  no accounts registered{R}")

        # ---- agents --------------------------------------------------
        ag = Panel("AGENTS", width, focused=panel == AGENTS)
        ag.selected = sel.get(AGENTS)
        # No heading for the last column: it is empty unless something is about
        # to happen, and then it says what, in words.
        # No model and no effort: they are configuration, they do not change
        # while anyone is watching, and they are on the agent's own page. This
        # screen is about what is happening now.
        ag.add(f"{DIM}{'agent':<{NAME_W}}{'acct':<8}{'state':<16}"
               f"{'ctx':>4}   {'wake':<7}{'in':<4}{R}", heading=True)
        for name, depth, under in visible_tree():
            # Indented under whoever routes work to it. Who an agent reports to
            # decides where its report goes and who may assign to it, and it was
            # written down in team.yaml all along - a flat list made the reader
            # hold the routing table in their head to know what they were
            # looking at.
            twig = f"{DIM}{'  ' * (depth - 1)}└ {R}" if depth else ""
            # How many of its reports are not drawn. Fitted around, not into,
            # the name: cut off, the only sign a team is hidden disappears.
            folded = f"{DIM} +{under}{R}" if under else ""
            try:
                acct = agents.current_account(name, con)
                cfg = agents.read(name)
            except Exception as e:  # noqa: BLE001
                ag.add(pad(fit(twig + name, NAME_W), NAME_W)
                       + f"{RED}config error: {e}{R}", key=name)
                continue
            st = status.read_for_agent(name, acct)
            row = db.get_agent_state(con, name)
            s = row["state"] if row else "IDLE"
            # A closed session holds no memory and answers no one; showing it
            # as PARKED hides the one difference that matters on this machine.
            if row and row["closed_at"]:
                s += "·closed"
            elif promptwait.of(row):
                s = WAITING_ON_YOU
            # Only when something is actually about to happen. Measured on a
            # live team: nothing firing, six rows reading "— closed" - which
            # the state column says already, with the same word - and two
            # reading "—". A column that is empty or duplicated is a column
            # that trains a reader not to look at it.
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            fire = (f"{YEL}⚠ {t.kind}{R}"
                    if t.fired and daemon.triggers_apply(row) else "")
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
                pad(fit(twig + f"{BOLD}{name}{R}", NAME_W - vlen(folded)) + folded,
                    NAME_W)
                + pad(acct, 8)
                + pad(f"{BOLD + RED if s == WAITING_ON_YOU else STATE_COLOR.get(s, '')}"
                      f"{fit(s, 15)}{R}", 16)
                + pad(ctxs, 7)
                + pad(countdown(row["wake_at"] if row else None), 7)
                + pad(str(inbox) if inbox else "·", 4)
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
                room = width - 4 - vlen(prefix)  # borders + gutter
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
            first = _ANSI.sub("", actions.first_line(m["body"]))
            # An agent parked until this is answered is the only thing on the
            # screen that no one else will ever clear; it is not drawn like a
            # report that happens to be addressed here.
            if m["gate"]:
                mark, name = f"{YEL}⚠{R}", f"{BOLD}{m['from']:<12}{R}"
            elif m["waiting"]:
                mark, name = f"{CYA}◷{R}", f"{BOLD}{m['from']:<12}{R}"
            elif m["answer"]:
                # An answer is the one row here that is not addressed to the
                # human at all: they asked for it. Drawn as what it is, so a
                # reply cannot be read as a new demand on their time.
                mark, name = f"{GRN}↩{R}", f"{BOLD}{m['from']:<12}{R}"
            else:
                mark, name = f"{DIM}·{R}", f"{DIM}{m['from']:<12}{R}"
            ms.add(f"{DIM}{when}{R} {mark} {CYA}#{m['id']:<5}{R}{name}"
                   f"{pad(m['kind'] or 'ask', 11)}{first}", key=m["id"])
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
            fl.add(pad(f"{YEL}{b['what']}{R}", 34)
                   + pad(f"{DIM}{age(b['age']) if b['age'] else ''}{R}", 7)
                   + f"{DIM}{b['why']}{R}", key=b["agent"])

        return header, [acc, ag, ms, fl]
    finally:
        con.close()


def page(title: str, sections: list[tuple[str, list[str]]],
         panel: int = AGENTS, key: object = None) -> dict:
    """A detail page laid out as the screen's own panels, not as a wall of text.

    It keeps its sections rather than the lines they make, because the width
    they are laid out at is the width the terminal has *now*: a page drawn at
    80 columns and then read at 60 is a page whose every box border is one line
    too long, and re-wrapping a drawn border tears it in half.
    """
    d = opened(title, [], panel, key)
    d["sections"] = sections
    return d


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
    lines: list[str] = wrap(intro, width) + [""] if intro else []
    if not tabs:
        return lines
    at = max(0, min(detail.get("tab", 0), len(tabs) - 1))
    detail["tab"] = at
    # The tabs are the box's own top edge. Drawn as a strip above a box that
    # then repeated the open tab's name, the page said "EARLIER WITH MANAGER"
    # twice in two lines.
    heading, rows = tabs[at]
    box = Panel(_tabstrip([h for h, _ in tabs], at), width, plain_title=True)
    # Wrapped into the box, not fitted to it: what is quoted here is a message
    # someone wrote, and a box that ends every line in "…" is not a section
    # anyone can read. A panel on the main screen cuts a row because the row
    # underneath needs the line; a page scrolls.
    for r in wrap(rows, max(1, width - 5)):
        box.add(r)
    return lines + box.draw()


def _tabstrip(names: list[str], at: int) -> str:
    """The sections of an open page, with the one being read lit."""
    parts = [f"{CYA}{BOLD}{n}{R}" if i == at else f"{DIM}{n}{R}"
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
    header, panels = build(width, sel, panel, wait_budget=height - 4 - PANEL_MIN)
    room = height - len(header) - 2                    # the two footer rows
    limits, hidden = _layout([len(p.rows) for p in panels], room)
    body: list[str] = []
    for p, lim in zip(panels, limits):
        if lim == 0:
            continue
        body += p.draw(limit=lim)
    foot = [fit(flash, width) if flash else "", keybar(panel, width, hidden)]
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


def wrap(lines: list[str], width: int) -> list[str]:
    """Break long lines to the terminal's width, at spaces where there are any.

    A message body arrives as whatever the sender typed, and an agent's report
    is often one paragraph with no newline in it at all. Fitting that to the
    width shows its first sentence and drops the rest - which is not reading
    the message.
    """
    width = max(1, width)
    out: list[str] = []
    for line in lines:
        # A body is whatever the sender typed. Escapes in it are invisible to
        # vlen, so they are never counted and never truncated: a pasted colour
        # runs past its line, and a pasted `esc [ 2J` clears the screen this is
        # drawing. What a message says is its text.
        line = _ANSI.sub("", line).rstrip()
        if not line:
            out.append("")
            continue
        while vlen(line) > width:
            # Cut on width, not on characters: with wide glyphs the two are not
            # the same number, and cutting on the second wraps a line that is
            # already too long back onto itself.
            room, cut = 0, 0
            for i, ch in enumerate(line):
                if room + cwidth(ch) > width:
                    break
                room += cwidth(ch)
                cut = i + 1
            space = line.rfind(" ", 0, cut + 1)
            if space > 0:
                cut = space
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return out


def _detail_frame(width: int, height: int, detail: dict, flash: str = "") -> str:
    """One thing, opened, with what is off the bottom said rather than cut.

    The panels shorten a list and count what they held back; a message is not a
    list, so this wraps and scrolls instead - and a message read half way is
    exactly the one someone is about to answer, which is why it can be answered
    from here.
    """
    body = lines_of(detail, width)
    room = max(1, height - 4)
    top = max(0, min(detail.get("top", 0), max(0, len(body) - room)))
    detail["top"] = top
    shown = body[top:top + room]
    while len(shown) < room:
        shown.append("")
    where = (f"{DIM}  line {top + 1}-{min(len(body), top + room)} of {len(body)}{R}"
             if len(body) > room else "")
    keys = "esc back · ←→ section · ↑↓ scroll"
    if detail.get("sections") is None or not [h for h, _ in detail["sections"] if h]:
        keys = "esc back · ↑↓ scroll · space page"
    if detail.get("panel") == MESSAGES:
        keys += " · a answer · e answer in $EDITOR · x dismiss"
    elif detail.get("panel") in (AGENTS, STUCK):
        keys += " · " + " · ".join(f"{k} {label}" for k, label in AGENT_KEYS
                                   if label != "open")
    lines = ([fit(f"{BOLD}{detail['title']}{R}{where}", width), ""]
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
    switched to application cursor keys; a bare escape is one byte and means
    the human backed out.
    """
    out, i = [], 0
    while i < len(data):
        if data[i] == "\033" and i + 2 < len(data) and data[i + 1] in "[O":
            out.append(data[i:i + 3])
            i += 3
        else:
            out.append(data[i])
            i += 1
    return out


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
        data = os.read(fd, 64).decode(errors="ignore")
    except OSError:
        return None
    if not data:
        return None
    keys = split_keys(data)
    _pending.extend(keys[1:])
    return keys[0]


def _line(prompt: str, width: int, height: int) -> str | None:
    """Read one line with the screen still up. None when the human backs out."""
    if not sys.stdin.isatty():
        return None
    buf = ""
    while True:
        sys.stdout.write(f"\033[{height};1H" + CLEAR_EOL
                         + fit(f"{BOLD}{prompt}{R} {buf}▏", width))
        sys.stdout.flush()
        ch = _key(30.0)
        if ch is None:
            continue
        if ch in ("\r", "\n"):
            return buf
        if ch == "\033":
            return None
        if ch in ("\x7f", "\b"):
            buf = buf[:-1]
            continue
        if ch == "\x03":
            return None
        if ch.isprintable():
            buf += ch


def _confirm(what: str, width: int, height: int) -> bool:
    if not sys.stdin.isatty():
        return False
    sys.stdout.write(f"\033[{height};1H" + CLEAR_EOL
                     + fit(f"{YEL}{what}?{R} {DIM}y / n{R}", width))
    sys.stdout.flush()
    while True:
        ch = _key(30.0)
        if ch is None:
            continue
        return ch in ("y", "Y")


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
            sys.stdout.write(HOME + frame(width, height, sel, panel, flash, detail))
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
        detail["tab"] = detail.get("tab", 0) + 1
        detail["top"] = 0
        return detail, ""
    if k in ("\033[D", "\033OD", "\033[Z"):
        detail["tab"] = max(0, detail.get("tab", 0) - 1)
        detail["top"] = 0
        return detail, ""
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
        label = dict(AGENT_KEYS).get(k)
        if not label or label == "open":
            return detail, ""
        name = str(detail["key"])
        con = db.connect()
        try:
            allowed, why = actions.can(con, name, label)
            if not allowed:
                return detail, why
            if label in ("message", "editor"):
                body = _compose_message(name, label, width, height, fd, saved)
                return detail, (actions.message(con, name, body) if body is not None
                                else _nothing_sent("e" if label == "editor" else "m"))
            if label == "terminal" and _needs_a_terminal(con, name):
                repo = _line(f"{name} has nowhere to run - repo to start it in "
                             f"[{os.getcwd()}]:", width, height)
                if repo is None:
                    return detail, "not started"
                _say(f"starting {name}", width, height)
                return detail, actions.terminal(con, name, repo.strip() or os.getcwd())
            if label in CONFIRM and not _confirm(f"{label} {name}", width, height):
                return detail, "left alone"
            _say(f"{label} {name}", width, height)
            said = getattr(actions, label)(con, name)
            # What it says about itself has just changed.
            fresh = page(detail["title"], actions.agent_detail(con, name),
                         detail["panel"], name)
            fresh["top"] = detail["top"]
            return fresh, said
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


def _answer_key(con, detail: dict, rid: int, k: str, width: int, height: int,
                fd, saved) -> tuple[dict | None, str]:
    if k == "x":
        return None, actions.dismiss(con, rid)
    text = _compose(rid, k, width, height, fd, saved)
    if text is None:
        return detail, _nothing_sent(k)
    _say(f"answering #{rid}", width, height)
    return None, actions.answer(con, rid, text)


def _compose_message(agent: str, label: str, width: int, height: int,
                     fd, saved) -> str | None:
    """An order, typed on the line or written in $EDITOR. None means none.

    The line is enough to say "rebase onto main"; it is not enough to say what
    to do and why not to do the other thing, and an order that needs a
    paragraph was being written somewhere else and pasted - or cut down to fit.
    """
    if label == "editor":
        return _editor(f"# a message to {agent}. Lines starting with # are dropped.\n",
                       fd, saved)
    return _line(f"message to {agent}:", width, height)


def _compose(rid: int, key: str, width: int, height: int, fd, saved) -> str | None:
    """An answer, typed on the line or written in $EDITOR. None means none."""
    if key == "e":
        return _editor(f"# answering #{rid}. Lines starting with # are dropped.\n",
                       fd, saved)
    return _line(f"answer #{rid}:", width, height)


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
    parking pays for a fold - and a screen still showing the frame from before
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


def _add_account(width: int, height: int, fd, saved) -> str:
    """Name it, then the same sequence as `ocp account add`."""
    name = _line("new account name:", width, height)
    if not name:
        return "not added"
    ok, msg = accounts.add_account(
        name, lambda argv, env: _suspend(argv, env, fd, saved)[0])
    return msg


def _remove_account(name: str, width: int, height: int, fd, saved) -> str:
    """The same sequence as `ocp account remove`, confirmed by typing its name."""
    con = db.connect()
    try:
        ok, msg = accounts.remove_account(
            name, lambda: _line(f"remove '{name}'? type its name to confirm:",
                                width, height) == name,
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
        return _add_account(width, height, fd, saved), None
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
                            actions.account_detail(con, str(target)),
                            ACCOUNTS, target)
        finally:
            con.close()
    if panel == ACCOUNTS and key == "l":
        line, argv, env = actions.login_argv(str(target))
        if not argv:
            return line, None
        return _suspend(argv, env, fd, saved)[1], None
    if panel == ACCOUNTS and key == "x":
        return _remove_account(str(target), width, height, fd, saved), None

    con = db.connect()
    try:
        if panel in (AGENTS, STUCK):
            name = str(target)
            if key in ("\r", "\n"):
                return "", page(f"agent {name}", actions.agent_detail(con, name),
                                panel, name)
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
                             f"[{os.getcwd()}]:", width, height)
                if repo is None:
                    return "not started", None
                _say(f"starting {name}", width, height)
                return actions.terminal(con, name, repo.strip() or os.getcwd()), None
            if label in ("message", "editor"):
                body = _compose_message(name, label, width, height, fd, saved)
                return (actions.message(con, name, body) if body is not None
                        else _nothing_sent("e" if label == "editor" else "m")), None
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
                text = _compose(rid, key, width, height, fd, saved)
                if text is None:
                    return _nothing_sent(key), None
                _say(f"answering #{rid}", width, height)
                return actions.answer(con, rid, text), None
            if key == "x":
                return actions.dismiss(con, rid), None
        return "", None
    finally:
        con.close()
