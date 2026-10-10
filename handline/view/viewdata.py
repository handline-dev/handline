"""What the agent and account rows hold as data, shared by the CLI, the daemon's row checks and the tests."""
from __future__ import annotations

import re
import time
from typing import NamedTuple

from ..team import agents
from .. import actions, db
from ..session import launch, triggers
from ..usage import load as loadmod, status
from ..infra import paths
from ..infra import timefmt
from ..daemon import delivery as dmn_delivery, lifecycle as dmn_lifecycle, states as dmn_states
from ..daemon.screenstate import screen_state
from ..infra.textsafe import cwidth, oneline


R = "\033[0m"

DIM, BOLD = "\033[2m", "\033[1m"

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

# `wake` is not here - see _needs_confirm(): it asks only when the row is
# FATAL, which is the one case unstall used to ask for on its own key
# (founder #1620/#1631, u removed).
CONFIRM = {"close", "restart", "park", "fold", "cancel"}

def _needs_confirm(con, name: str, label: str) -> bool:
    """Whether pressing `label` on `name` needs a yes first.

    Static for everything but `wake`: waking a park or a closed session is
    the ordinary case and asks nothing, same as before, but a FATAL row is
    now reached through the same key, and clearing a stall keeps the
    confirmation it always had under its old name, unstall.
    """
    if label == "wake":
        row = db.get_agent_state(con, name)
        return bool(row and row["state"] == dmn_states.STALLED)
    return label in CONFIRM

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

class WallCell(NamedTuple):
    account: str      # the account whose window the wake waits on
    when: str         # the clock the wake lands at
    mark: str         # "?" when the account is only assumed
    elsewhere: bool   # worth naming: not the row's own account, or only assumed

class AgentRow(NamedTuple):
    name: str
    twig: str                    # the indent and "└" under a supervisor, or ""
    shown: str                   # the name, one line
    under: int                   # reports not drawn under it
    urgency: tuple[str, int, str] | None
    state: str
    until: str
    error: str | None            # the config error, when the row has no cells
    acct: str = ""
    kind: str = ""
    ctx: str = ""                # ANSI: yellow at its context limit
    inbox: int = 0
    tasks: int = 0                # owned and not done: waiting plus current (T317)
    ready: bool = False           # IDLE with a queued task no open blocker holds (T300)
    fire: str = ""               # ANSI: a trigger about to fire
    wall: WallCell | None = None

def state_cell(s: str, until: str, width: int | None = None) -> str:
    """The state word in its colour and, dimmed, what it is until; cut to `width`
    columns when there is a column to fit."""
    word = fit(s, width - 1 - (len(until) + 1 if until else 0)) if width else s
    return (f"{(BOLD + STATE_COLOR[s]) if s in NEEDS_A_HUMAN else STATE_COLOR.get(s, '')}"
            f"{word}{R}" + (f" {DIM}{until}{R}" if until else ""))

def state_cell_of_view(view: dict, width: int | None = None) -> str:
    """The state column from an agent's stored view (daemon/agentview.py), with nothing recomputed."""
    from ..daemon import agentview
    return state_cell(*agentview.screen_word(view), width)

def agents_data(con, now: int | None = None) -> tuple[list[AgentRow], list[str]]:
    """What the AGENTS panel shows, before any of it is laid out: one row per
    drawn agent, in tree order, and the notes under them."""
    drawn_tree = _walk_tree()
    # Model and effort are configuration and do not change while anyone is
    # watching, which was the case for leaving them off. What changed is
    # that they will not all be claude: with a second vendor coming, what
    # an agent *is* stops being a constant of the screen and starts being
    # the difference between two rows doing the same work at different
    # prices. It sits beside the account for that reason - the two together
    # are what a row costs.
    state_rows = {n: db.get_agent_state(con, n) for n, _, _ in drawn_tree}
    shown = {k: oneline(v) for k, v in shown_names(drawn_tree).items()}
    task_counts = actions.open_task_counts(con)      # one read of the open tasks, not one per agent
    rows = []
    for name, depth, hidden in drawn_tree:
        under = len(hidden)
        # Indented under whoever routes work to it. Who an agent reports to
        # decides where its report goes and who may assign to it, and it was
        # written down in team.yaml all along - a flat list made the reader
        # hold the routing table in their head to know what they were
        # looking at.
        twig = f"{DIM}{'  ' * (depth - 1)}└ {R}" if depth else ""
        # How many of its reports are not drawn, and the most urgent thing
        # among them.
        urgency = hidden_urgency(con, hidden) if under else None
        row = state_rows[name]
        s, until = screen_state(con, row, now)
        try:
            cfg = agents.read(name)
        except Exception as e:  # noqa: BLE001
            rows.append(AgentRow(name, twig, shown[name], under, urgency, s, until, str(e)))
            continue
        acct = agents.current_account(name, con)
        st = status.read_for_agent(name, acct)
        # Only when something is actually about to happen. Measured on a
        # live team: nothing firing, six rows reading "— closed" - which
        # the state column says already, with the same word - and two
        # reading "—". A column that is empty or duplicated is a column
        # that trains a reader not to look at it.
        ctx_limit = status.effective_context_limit(cfg, st.model_id)
        t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used, ctx_limit=ctx_limit)
        fire = f"{YEL}⚠ {t.kind}{R}" if t.fired and dmn_lifecycle.triggers_apply(row) else ""
        ctxs = f"{pct(st.ctx_pct)}%"
        if st.ctx_used is not None and st.ctx_used >= ctx_limit:
            ctxs = YEL + ctxs + R
        # Same predicate `hl ps` and `hl triggers` ask for this - so this
        # screen cannot disagree with the daemon that actually acts on it.
        w = dmn_lifecycle.would_wake(con, row) and launch.wake_wait(name, row, con)
        wall = (WallCell(w.account, launch.wait_clock(w), "?" if w.assumed else "",
                         w.account != acct or w.assumed) if w else None)
        cur = db.current_task(con, name)
        task_n = task_counts.get(name, 0)
        ready = (cur is not None and s == "IDLE"
                 and actions.task_state(con, db.get_task(con, cur)) == "queued"
                 and not db.task_blockers(con, cur))
        rows.append(AgentRow(name, twig, shown[name], under, urgency, s, until, None,
                             oneline(acct or "-"), oneline(agents.kind(cfg)), ctxs,
                             db.unreached_count(con, name), task_n, ready, fire, wall))
    return rows, [] if paths.list_agents() else ["no agents registered"]

def pct(v: int | None) -> str:
    return "  ?" if v is None else f"{v:>3}"

def countdown(ts: int | None) -> str:
    if not ts:
        return "   -"
    left = ts - int(time.time())
    if left <= 0:
        return " due"
    return timefmt.span(left, precise=True)

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
        if con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? "
                       "AND state = 'queued'", (name, actions.HUMAN)).fetchone():
            found.setdefault(0, []).append(YEL)
            continue
        word, _ = screen_state(con, row)
        if word in NEEDS_A_HUMAN:
            found.setdefault(1, []).append(STATE_COLOR[word])
        elif dmn_delivery.wall_reason(con, name, row):
            found.setdefault(1, []).append(YEL)
        elif loadmod.is_running(row):
            found.setdefault(2, []).append(STATE_COLOR["RUNNING"])
    if not found:
        return None
    worst = min(found)
    colours = found[worst]
    return ("▶" if worst == 2 else "!", len(colours), RED if RED in colours else colours[0])

# Supervisors whose reports are drawn. Closed by default: the screen is for
# what needs you, and a team of twelve drawn in full is a list to scroll past.
EXPANDED: set[str] = set()

def _wants_a_human() -> set[str]:
    """Agents in a state only a person clears. They are never hidden."""
    con = db.connect()
    try:
        return {r["agent"] for r in db.all_agent_states(con)
                if screen_state(con, r)[0] in NEEDS_A_HUMAN}
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

# Whether an account has credentials, asked at most this often. The check runs
# `security`, which is a subprocess with a ten-second timeout, and the screen
# redraws every two seconds and on every keypress - a slow keychain froze the
# dashboard. Logging in is a thing a person does by hand, so a minute-old
# answer is current.
LOGIN_TTL_S = 60

_LOGIN: dict[str, tuple[float, bool]] = {}

# The states a person has to do something about. Kept as an alias of
# SCREEN_WORDS' own two red words, for the one caller (hidden_urgency, on a
# folded row) that still needs to ask "does this need a person" about a state
# it does not otherwise draw.
NEEDS_A_HUMAN = ("DIALOG", "FATAL")
