"""What the bridge shows (design section 11): its panels' rows, header and keys as
data, and `snapshot`, the same as plain text. `ui/pages/home.py` draws them.

A join view over the same state the daemon decides on, so the two cannot
disagree: if a number looks wrong here, the daemon is acting on that same wrong
number. It reports; it does not decide. What a key does is a call into
`actions`, which calls the same function the daemon or the CLI calls - so the
screen can act without ever holding an opinion of its own.

Two rules are deliberate. Every figure carries a mark for where it came
from - inferred, carried, or carried past its bound - so an inference is never
drawn as a measurement, and what we genuinely do not know is drawn as "?" and
never as 0. The one unmarked case is a figure held at the window's high-water
mark: a real number reported by an earlier payload than the one on screen. And
an account's usage and an agent's context come from different files - rate
limits belong to the account, context to the session - so they are shown as
what they are rather than blended into one number.
"""
from __future__ import annotations

import re
import time
from typing import NamedTuple

from . import (accounts, actions, agents, ask as askmod, blocked as blockedmod, daemon, db,
               launch, load as loadmod, orca, paths, status, triggers, vendors)
from . import waiting as promptwait
from .textsafe import cwidth, drawable, oneline, wrap

R = "\033[0m"
DIM, BOLD = "\033[2m", "\033[1m"
RED, YEL, GRN, CYA = "\033[31m", "\033[33m", "\033[32m", "\033[36m"

_ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")

# The screen's whole vocabulary for what an agent's row shows - drawn here and
# by `ocp ps` (cli.py) through the one function below, screen_state(), so the
# two cannot disagree about a word. Only DIALOG and FATAL are red: the two
# states a person, not the daemon, clears.
SCREEN_WORDS = ("RUNNING", "IDLE", "PENDING", "MOVING", "DIALOG", "FATAL")
STATE_COLOR = {
    "RUNNING": GRN, "IDLE": DIM, "PENDING": CYA, "MOVING": YEL,
    "DIALOG": RED, "FATAL": RED,
}

# What an agent reads as while it sits at a prompt only the founder can answer.
# Its state underneath is still RUNNING; this is drawn over it. Not one of
# SCREEN_WORDS: it is the pinned block's own heading, never a row's state cell
# (that says DIALOG - see screen_state()).
WAITING_ON_YOU = "WAITING ON YOU"
WAITS_SHOWN = 5   # rows of the pinned block; the rest are counted, not drawn

# The panels a cursor can sit in, in tab order. STUCK is one of them because a
# row there names the agent that is stuck, and the keys that unstick it are the
# agent keys - pressing them where the problem is read beats finding the same
# name again in the list above.
ACCOUNTS, AGENTS, MESSAGES, STUCK = 0, 1, 2, 3
CTX_COL, IN_COL = 7, 4
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
    ("w", "wake"), ("c", "close"), ("p", "park"), ("f", "fold"),
    ("v", "move"),
]
ACCOUNT_KEYS = [("↵", "open"), ("l", "login"), ("a", "add"), ("c", "cap"), ("x", "remove")]
MESSAGE_KEYS = [("↵", "open"), ("a", "answer"), ("e", "answer in $EDITOR"),
                ("x", "dismiss")]
# A stuck row is about an agent, so it answers to the agent keys.
STUCK_KEYS = AGENT_KEYS
# `wake` is not here - see _needs_confirm(): it asks only when the row is
# FATAL, which is the one case unstall used to ask for on its own key
# (founder #1620/#1631, u removed).
CONFIRM = {"close", "park", "fold", "cancel"}


def _needs_confirm(con, name: str, label: str) -> bool:
    """Whether pressing `label` on `name` needs a yes first.

    Static for everything but `wake`: waking a park or a closed session is
    the ordinary case and asks nothing, same as before, but a FATAL row is
    now reached through the same key, and clearing a stall keeps the
    confirmation it always had under its old name, unstall.
    """
    if label == "wake":
        row = db.get_agent_state(con, name)
        return bool(row and row["state"] == daemon.STALLED)
    return label in CONFIRM


def _confirmed_can(con, name: str, label: str) -> tuple[bool, str]:
    """Re-runs `actions.can` after a confirm was answered yes: the daemon
    ticks every two seconds, and a confirmation waits on a human for as long
    as the human takes, so what was true when the key was pressed may not be
    true once they answer. Shared by every front end so a yes is checked the
    same way however it was asked; (False, why) means the label no longer
    applies."""
    allowed, why = actions.can(con, name, label)
    if not allowed:
        return False, f"{why} - it changed while you looked"
    return True, ""

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
# Read-only too, but too long for the flash line: each opens a page over the
# conversation, and esc comes back to it.
PAGE_COMMANDS = ("knows", "done")


def agent_commands(con, agent: str) -> list[tuple[str, str]]:
    """Every slash command `agent`'s conversation offers right now, and its
    kind: 'action' (an AGENT_KEYS verb, gated by the same `can` the keybinding
    itself checks), 'info' (always offered, nothing to decide), 'page' (a
    read-only page over the conversation) or 'cancel' (takes a queued request
    out of the queue).

    Derived from AGENT_KEYS and INFO_COMMANDS, not a third, hand-kept table -
    the same reason `agent_footer_keys` reads AGENT_KEYS instead of keeping
    its own list. `open` is excluded: typing a command to open the page
    already open is not a thing, the same way the footer never offers it."""
    out = [(label, "action") for _, label in AGENT_KEYS
           if label != "open" and actions.can(con, agent, label)[0]]
    out += [(name, "info") for name in INFO_COMMANDS]
    out += [(name, "page") for name in PAGE_COMMANDS]
    # Cancelling names what dies: the bare form is for the one request there is
    # to cancel, and each id is offered so autocomplete can say which.
    waiting = actions.waiting_ids(con, agent)
    out.append(("cancel", "cancel"))
    out += [(f"cancel {rid}", "cancel") for rid in waiting]
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


class AccountRow(NamedTuple):
    name: str
    cells: dict[str, str]    # `account_cells`, keyed like ACCT_W (no "name")
    mark: str                # the tick: could the pick use it
    next: str                # " next" on the one account work goes to, else ""


def accounts_data(con) -> tuple[list[AccountRow], list[str]]:
    """What the ACCOUNTS panel shows, before any of it is laid out: one row per
    account and the notes under them (none registered, the team's outlook)."""
    # The account with the most headroom: where work goes when an agent's
    # own account is full. Only accounts that can start a session count.
    # The reason each one sits where it does is on its own page (Enter).
    loads = loadmod.read_all()
    roster, unplaced = loadmod.roster(con)
    global _LAST_NEXT
    first, _ = status.pick_account(
        [a for a in paths.list_accounts() if _logged_in(a)], load=loads,
        prefer=_LAST_NEXT)
    _LAST_NEXT = first
    rows = []
    for name in paths.list_accounts():
        st = status.read(name)
        # The tick beside the name says the pick could use it: not only
        # that it is under the wall but that it has a slot and would last.
        # No ranking: the one in hand is claude's; the page says the words.
        allowed, _ = status.account_verdict(name, st, loads.get(name), [], _logged_in(name))
        # The figure shown is the one decisions are made on, with a mark saying
        # where it came from - a quiet window drawn as "?" while the daemon acts
        # on 0% is the dashboard and the daemon disagreeing.
        cells = account_cells(name, st, counts=roster.get(name, (0, 0)), unplaced=unplaced,
                              at_cap=bool(loads.get(name) and loads[name].at_cap),
                              ld=loads.get(name))
        rows.append(AccountRow(
            name, cells, f"{GRN}✔{R}" if allowed else f"{RED}✘{R}",
            # Where work goes next is said in a word after the tick, not drawn
            # in the gutter the selection cursor uses on every other panel.
            f" {GRN}{BOLD}next{R}" if name == first else ""))
    notes = [] if rows else ["no accounts registered"]
    outlook = status.team_outlook()
    if outlook:
        notes.append(outlook)
    return rows, notes


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
    fire: str = ""               # ANSI: a trigger about to fire
    wall: WallCell | None = None


def state_cell(s: str, until: str, width: int | None = None) -> str:
    """The state word in its colour and, dimmed, what it is until; cut to `width`
    columns when there is a column to fit."""
    word = fit(s, width - 1 - (len(until) + 1 if until else 0)) if width else s
    return (f"{(BOLD + STATE_COLOR[s]) if s in NEEDS_A_HUMAN else STATE_COLOR.get(s, '')}"
            f"{word}{R}" + (f" {DIM}{until}{R}" if until else ""))


def fold_suffix(under: int, urgency: tuple[str, int, str] | None) -> str:
    """` +3` and its most urgent report, as `fold_marker` draws them with room."""
    if not under:
        return ""
    count = f"{DIM} +{under}{R}"
    if urgency:
        glyph, n, colour = urgency
        return f"{count} {colour}{glyph}{n}{R}"
    return count


def agents_data(con) -> tuple[list[AgentRow], list[str]]:
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
        s, until = screen_state(con, row)
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
        t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_used, ctx_limit=ctx_limit)
        fire = f"{YEL}⚠ {t.kind}{R}" if t.fired and daemon.triggers_apply(row) else ""
        ctxs = f"{pct(st.ctx_pct)}%"
        if st.ctx_used is not None and st.ctx_used >= ctx_limit:
            ctxs = YEL + ctxs + R
        # Same predicate `ocp ps` and `ocp triggers` ask for this - so this
        # screen cannot disagree with the daemon that actually acts on it.
        w = daemon.would_wake(con, row) and launch.wake_wait(name, row, con)
        wall = (WallCell(w.account, launch.wait_clock(w), "?" if w.assumed else "",
                         w.account != acct or w.assumed) if w else None)
        rows.append(AgentRow(name, twig, shown[name], under, urgency, s, until, None,
                             oneline(acct or "-"), oneline(agents.kind(cfg)), ctxs,
                             len(db.queued_for(con, name)), fire, wall))
    return rows, [] if paths.list_agents() else ["no agents registered"]


class MessageRow(NamedTuple):
    key: object
    when: str
    flavour: str      # "gate", "waiting", "answer" or "read"
    id: int
    sender: str
    kind: str
    first: str        # the first line with something on it, drawable
    behind: str       # ANSI: how many more a fold stands for, or ""


def message_mark(flavour: str) -> tuple[str, str]:
    """The glyph and the sender's style for a message row: `{}` in the second
    is where the (padded) sender goes. An agent parked until this is answered
    is the only thing on the screen that no one else will ever clear; it is not
    drawn like a report that happens to be addressed here. An answer is the one
    row that is not addressed to the human at all: they asked for it, so it is
    drawn as what it is and cannot be read as a new demand on their time."""
    if flavour == "gate":
        return f"{YEL}⚠{R}", f"{BOLD}{{:<12}}{R}"
    if flavour == "waiting":
        return f"{CYA}◷{R}", f"{BOLD}{{:<12}}{R}"
    if flavour == "answer":
        return f"{GRN}↩{R}", f"{BOLD}{{:<12}}{R}"
    return f"{DIM}·{R}", f"{DIM}{{:<12}}{R}"


def messages_data(con) -> tuple[str, list[MessageRow], list[str]]:
    """What the FOR YOU panel shows, before any of it is laid out: its title
    (what is waiting, counted), one row per message and the notes under them.

    What is waiting for the human is the one queue nothing else in this system
    will ever clear: an agent's escalation sits here until it is answered."""
    waiting = actions.messages(con)
    # Counted over the unfolded rows, not `waiting`: a fold shows one
    # agent's latest, and a title built from the folded list would drop
    # every message its line does not happen to represent.
    raw = actions.mailbox_rows(con)
    gates = sum(1 for m in raw if m["gate"])
    parked = sum(1 for m in raw if m["waiting"] and not m["gate"])
    back = sum(1 for m in raw if m["answer"])
    to_read = sum(1 for m in raw if not m["gate"] and not m["waiting"]
                  and not m["answer"])
    counts = [(gates, "to decide"), (parked, "asked for a reply"),
              (back, "came back"), (to_read, "to read")]
    said = ", ".join(f"{n} {what}" for n, what in counts if n)
    today = time.strftime("%Y%m%d")
    rows = []
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
        flavour = ("gate" if m["gate"] else "waiting" if m["waiting"]
                   else "answer" if m["answer"] else "read")
        # A folded row stands for more than one: the count is the only
        # sign anything sits behind it.
        behind = f" {DIM}(+{m['unread'] - 1}){R}" if m["unread"] > 1 else ""
        rows.append(MessageRow(m["key"], when, flavour, m["id"], oneline(m["from"]),
                               oneline(m["kind"] or "ask"),
                               drawable(actions.first_line(m["body"]))[0], behind))
    return (f"FOR YOU — {said}" if said else "FOR YOU"), rows, \
        [] if waiting else ["nothing waiting on you"]


class StuckRow(NamedTuple):
    agent: str | None   # who the cursor acts on; None for a row about an account
    what: str
    age: str            # how long it has sat, or ""
    why: str


def stuck_data(con) -> tuple[str, list[StuckRow]]:
    """What the STUCK panel shows, before any of it is laid out: its title and
    one row per thing that is not moving.

    This was "in flight", and almost nothing ever is: a handoff takes seconds,
    and what it actually drew was requests that had sat for a day and a half
    because their target had no terminal to be told with. Traffic drawn as
    traffic said the system was busy while it was stuck."""
    blocked = actions.stuck(con)
    rows = [StuckRow(b["agent"], oneline(b["what"]), age(b["age"]) if b["age"] else "",
                     oneline(b["why"])) for b in blocked]
    return ("STUCK" if blocked else "STUCK — nothing is waiting on anything"), rows


class HeaderData(NamedTuple):
    running: bool         # whether the daemon is running: whether anything here happens on its own
    clock: str
    legend: bool          # whether a mark the legend explains is on the screen
    waits: list[dict]     # what is stopped until the founder acts, most urgent first


def header_data(con) -> HeaderData:
    """What the bar above the panels shows. The legend explains three marks and
    earns its place only when one of them is on the screen."""
    marks = set()
    for a in paths.list_accounts():
        st_ = status.read(a)
        marks |= {status.mark(st_.pct_5h_source), status.mark(st_.pct_7d_source)}
    return HeaderData(daemon.running_elsewhere(), time.strftime("%H:%M:%S"),
                      any(m.strip() for m in marks), actions.founder_waits(con))


def panel_keys(panel: int, agent_keys: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """The keys a panel answers to, as (key, label). `agent_keys` is
    `agent_footer_keys` for the row under the cursor on AGENTS or STUCK, with a
    `?` for the keys that filtering never shows at all (fold, move)."""
    if agent_keys is not None:
        return agent_keys + [("?", "help")]
    return {ACCOUNTS: ACCOUNT_KEYS, AGENTS: AGENT_KEYS, MESSAGES: MESSAGE_KEYS,
            STUCK: STUCK_KEYS}[panel]


def snapshot() -> str:
    """The bridge as plain text, once: what `ocp bridge --once` prints, and what
    a pipe or a script gets. Every panel's rows from the same data functions the
    screen draws from, their cells joined by two spaces and stripped of colour."""
    plain = lambda cell: _ANSI.sub("", cell)  # noqa: E731
    con = db.connect()
    try:
        head = header_data(con)
        accounts_, acct_notes = accounts_data(con)
        agents_, agent_notes = agents_data(con)
        title, messages_, message_notes = messages_data(con)
        stuck_title, stuck_ = stuck_data(con)
    finally:
        con.close()
    now = time.time()
    out = [f"ocp · daemon {'running' if head.running else 'NOT RUNNING'} · {head.clock}"
           + (f" · {status.SOURCE_LEGEND_SHORT}" if head.legend else "")]
    if head.waits:
        out.append(f"{WAITING_ON_YOU} ({len(head.waits)})")
        out += [f"  {oneline(w['who'])} · {oneline(w['what'])} · {age(now - w['since'])}"
                for w in head.waits]
    out += ["", "ACCOUNTS"]
    for r in accounts_:
        out.append("  " + "  ".join([oneline(r.name)] + [plain(r.cells[k]) for k in ACCT_W if k != "name"]
                                    + [plain(r.mark + r.next)]))
    out += [f"  {n}" for n in acct_notes]
    out += ["", "AGENTS"]
    for r in agents_:
        if r.error:
            out.append(f"  {plain(r.twig)}{r.shown}  config error: {oneline(r.error)}")
            continue
        wall = f"  ⏳ {r.wall.account}{r.wall.mark} {r.wall.when}" if r.wall else ""
        out.append(f"  {plain(r.twig)}{r.shown}{plain(fold_suffix(r.under, r.urgency))}  {r.acct}  "
                   f"{r.kind}  {plain(state_cell(r.state, r.until))}  {plain(r.ctx)}  "
                   f"{r.inbox or '·'}{'  ' + plain(r.fire) if r.fire else ''}{wall}")
    out += [f"  {n}" for n in agent_notes]
    out += ["", title]
    for r in messages_:
        out.append(f"  {r.when}  {plain(message_mark(r.flavour)[0])}  #{r.id}  {r.sender}  "
                   f"{r.kind}  {r.first}{plain(r.behind)}")
    out += [f"  {n}" for n in message_notes]
    out += ["", stuck_title]
    out += [f"  {r.what}  {r.age}  {r.why}".rstrip() for r in stuck_]
    return "\n".join(out)


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
        if con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? "
                       "AND state = 'queued'", (name, actions.HUMAN)).fetchone():
            found.setdefault(0, []).append(YEL)
            continue
        word, _ = screen_state(con, row)
        if word in NEEDS_A_HUMAN:
            found.setdefault(1, []).append(STATE_COLOR[word])
        elif daemon.wall_reason(con, name, row):
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
            out.append(f"{DIM}│{R}" + line + f"{DIM}│{R}")
        if held_back and limit is not None and limit >= 2:
            more = f"{DIM}  … {held_back} more{R}"
            out.append(f"{DIM}│{R}" + pad(fit(more, inner), inner) + f"{DIM}│{R}")
        out.append(DIM + "└" + "─" * inner + "┘" + R)
        return out


# The states a person has to do something about. Kept as an alias of
# SCREEN_WORDS' own two red words, for the one caller (hidden_urgency, on a
# folded row) that still needs to ask "does this need a person" about a state
# it does not otherwise draw.
NEEDS_A_HUMAN = ("DIALOG", "FATAL")

# DETAIL_CAP bounds the free text screen_state() shows beside a word - a
# dialog's own detail, or who an IDLE reply-wait is for - to what the state
# cell has room for; `fit()` truncates further if the column itself is
# narrower. Long text belongs in `ocp ps`'s own DETAIL column instead.
DETAIL_CAP = 40


# The state word for an opencode agent is read off its pane, not out of the
# db: claude reports RUNNING and IDLE through hooks, and opencode has no
# counterpart wired, so its row sits at whatever launch wrote - IDLE - for the
# life of the session. The witness is the pane's own screen, the same one
# wait_idle's screen half trusts: Orca's tui-idle word is no good here,
# measured on a busy opencode pane it answers idle while the turn streams
# (orca.py's docstring says the same). One screen read is ~110ms; the cache
# keeps a redraw's rows from each paying it, and the timestamp is taken after
# the read, so a slow answer is not born already expired.
OPENCODE_STATE_TTL_S = 2.0
_opencode_seen: dict[str, tuple[float, bool | None]] = {}


def _opencode_working(handle: str) -> bool | None:
    """What this pane's screen says: True working, False idle at its prompt.

    None when there is no evidence either way - a shell after /exit, a start
    screen - or the screen cannot be read (Orca down, handle gone), and the
    row keeps whatever the db state says. Positive evidence only: a closed
    agent's shell prompt must not read as work in progress.
    """
    now = time.monotonic()
    hit = _opencode_seen.get(handle)
    if hit and now - hit[0] <= OPENCODE_STATE_TTL_S:
        return hit[1]
    term = orca._screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        working = None
    else:
        working = orca._driver_for(handle).working_on_screen(tail)
    if len(_opencode_seen) > 128:
        for k in [k for k, (at, _) in _opencode_seen.items() if now - at > OPENCODE_STATE_TTL_S]:
            del _opencode_seen[k]
    _opencode_seen[handle] = (time.monotonic(), working)
    return working


def screen_state(con, row) -> tuple[str, str]:
    """The one word this agent's row shows - from SCREEN_WORDS - and what to
    say beside it: a clock time, or what it is doing or waiting for. Drawn
    here and read by `ocp ps` (cli.py) alike, through this one function, so
    the two screens cannot disagree about a row.

    No `·closed` suffix (founder #1639/#1640: information nobody needs on
    this screen - `closed_at` still decides whether the daemon can wake the
    agent, in `daemon.py`, untouched here).

    DIALOG overlays whatever the row's own state is, from either of two live
    person-facing sources - never a stored state, so there is nothing to
    restore when the dialog goes. MOVING never carries a clock: the short
    recheck it waits on (ASSUMED_ACCOUNT_RECHECK_S, 5 minutes) is not the
    account-move machinery's own wall, so a time on it would say more than is
    known.
    """
    if not row:
        return "IDLE", ""
    held = promptwait.of(con, row)
    if held:
        what = " ".join(x for x in (f"{held.get('tool') or 'tool'}:", held.get("command")) if x)
        return "DIALOG", oneline(what)[:DETAIL_CAP]
    block = db.block_get(con, row["agent"])
    if block and block["kind"] in (orca.MODAL, orca.BACKGROUNDED):
        return "DIALOG", oneline(blockedmod.headline(block["kind"], block["detail"]))[:DETAIL_CAP]

    state, detail = row["state"], row["detail"] or ""
    if state == daemon.STALLED:
        return "FATAL", oneline(detail)[:DETAIL_CAP]
    if state in (daemon.RUNNING, daemon.HANDOFF):
        # An opencode row's word is its pane's screen (see _opencode_working).
        # HANDOFF stays RUNNING - it is the daemon's protected mid-move, and
        # moving is working. A closed row's word is the db's, and so is any
        # row when the screen cannot be read.
        if state == daemon.RUNNING and row["terminal"] and not row["closed_at"] \
                and vendors.account_vendor(row["account"] or "") == "opencode" \
                and _opencode_working(row["terminal"]) is False:
            return "IDLE", ""
        return "RUNNING", ""
    if state == daemon.QUEUED:
        if detail.startswith(daemon.MOVING_MARK):
            return "MOVING", ""
        if daemon.has_background_work(con, row["agent"]):
            return "RUNNING", "background work"
        return "PENDING", ""
    if state in (daemon.PARKED, daemon.DEGRADED, daemon.THROTTLED):
        if detail.startswith(askmod.AWAIT_REPLY_MARK):
            return "IDLE", oneline(detail[len(askmod.AWAIT_REPLY_MARK):])[:DETAIL_CAP]
        if detail.startswith(daemon.MOVING_MARK):
            return "MOVING", ""
        if not row["wake_at"]:
            return "PENDING", ""
        # A wake_at already in the past is a tick the daemon has not run yet,
        # not a clock face worth printing - "due" says so the way the
        # countdown this replaced always did.
        until = "due" if row["wake_at"] <= int(time.time()) \
            else time.strftime("%H:%M", time.localtime(row["wake_at"]))
        return "PENDING", until
    # An opencode agent never writes RUNNING - its hooks do not exist - so an
    # agent that IS working sits here in the db and the plain IDLE below would
    # hide it. The same witness speaks first, but never for a closed agent:
    # its pane is a shell prompt, and its word is the db's.
    if state == daemon.IDLE and not row["closed_at"] and row["terminal"] \
            and vendors.account_vendor(row["account"] or "") == "opencode" \
            and _opencode_working(row["terminal"]) is True:
        return "RUNNING", ""
    # A row the daemon marked with work still running behind the prompt is not
    # idle, whatever the hooks last said.
    if state == daemon.IDLE and daemon.has_background_work(con, row["agent"]):
        return "RUNNING", "background work"
    # IDLE, and anything not written above (section 8.4's own DONE, never
    # actually written - see daemon.WAKER): shown as IDLE rather than a raw
    # internal name a person was never meant to read.
    return "IDLE", ""


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


def _agent_page(con, name: str, panel: int = AGENTS, title: str | None = None,
                old: dict | None = None, hint: bool = True) -> dict:
    """An agent's page: its head, and the conversation under it. `old` is the
    page it replaces, so a redraw keeps the scroll the reader was at. What is
    new in the conversation stays counted until the reader leaves the page.
    `hint`: see `actions.conversation_and_drawn` - False for a screen with its
    own input box, which already says where a message goes."""
    d = page(title or f"agent {name}", actions.agent_detail(con, name, hint), panel, name)
    d["agent"] = name
    d["stacked"], d["stem"], d["end"] = True, actions.CHAT_TAB, True
    if old:
        for k in ("top", "end"):
            d[k] = old.get(k, 0)
        for k in ("focus", "focus_pending"):
            if k in old:
                d[k] = old[k]
    return d


def _tab_stems(detail: dict) -> list[str]:
    """The names of a page's tabs, without the counts in their headings."""
    return [h.split(" (")[0] for h, _ in detail.get("sections") or [] if h]


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
    lines: list[str] = ((_note_lines(intro, width) if detail.get("notes")
                         else _rows_lines(intro, width)) + [""] if intro else [])
    if not tabs:
        return lines
    if detail.get("stacked"):
        stacked = _stacked_lines(detail, tabs, width)
        detail["conv_end"] += len(lines)       # counted from the top of the page
        if "focus_span" in detail:
            detail["focus_span"] = tuple(n + len(lines) for n in detail["focus_span"])
        return lines + stacked
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
    room = max(1, width - 5)
    for ln in _rows_lines(rows, room):
        box.add(ln)
    return lines + box.draw()


def _painted(line: str, width: int) -> str:
    """A row on its background, edge to edge. Every reset inside it ends the
    background, so it is switched on again after each; the last reset is the
    line's own and nothing carries to the next line."""
    fill = getattr(line, "fill", "")
    if not fill:
        return line
    return fill + pad(fit(line, width), width).replace(R, R + fill) + R


def _section_rule(title: str, width: int) -> str:
    """A section's heading: its name on a rule the width of the screen."""
    head = fit(f"── {oneline(title)} ", width)
    return f"{DIM}{head}{'─' * max(0, width - vlen(head))}{R}"


def _stacked_lines(detail: dict, tabs: list[tuple[str, list]], width: int) -> list[str]:
    """Every section of an agent's page, one under the other, each under a rule
    with its name - no boxes, so the conversation reads as the Claude Code
    screen does. The first is the conversation; the ones below it are what is
    not part of it, and each row names whose it is. `conv_end` is the line the
    conversation ends on, which is where a page kept at its end stays."""
    out: list[str] = []
    detail.pop("focus_span", None)
    for i, (heading, rows) in enumerate(tabs):
        out.append(_section_rule(heading, width))
        found: dict = {}
        base = len(out)
        out += _rows_lines(rows, width, dim_plain=i == 0,
                           focus=detail.get("focus") if i == 0 else None, found=found)
        if found:
            detail["focus_span"] = (base + found["span"][0], base + found["span"][1])
        if i == 0:
            detail["conv_end"] = len(out)
        out.append("")
    return out[:-1] if out else out


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


def _message_lines(r, width: int, focus: bool = False) -> list[str]:
    """One message as the Claude Code screen draws its own: a marker and the
    text, the rest of the text indented under the marker, and a dim line under
    it saying when and in what state. Who said it is the marker - the human's
    on a background, the agent this page is about with a dot - and only a
    message between two other agents names its speakers."""
    marker, text_dim = _MARKERS[r.side]
    if focus:
        marker = f"{YEL}{BOLD}\u25b6{R}"
    body = wrap(r.text.splitlines() or [""], max(1, width - 2))
    lines: list[str] = []
    if r.side == "other":
        lines.append(f"{DIM}\u00b7 {oneline(r.who)}{R}")
        lines += [f"  {text_dim}{ln}{R}" if ln else "" for ln in body]
    else:
        first, *rest = body
        lines.append(f"{marker} {first}")
        lines += [f"  {ln}" if ln else "" for ln in rest]
        if r.side == "you":
            lines = [Filled(ln, FILL_YOU) for ln in lines]
    # The state is never cut - it can carry the id /cancel names - so what does
    # not fit beside the time goes on lines of its own.
    when = f"  {DIM}{_clock(r.ts)}{R}"
    shown = " \u00b7 ".join(x for x in (r.tail, "the one you opened" if focus else "") if x)
    tail = wrap([f"\u00b7 {shown}"], max(1, width - 2)) if shown else []
    paint = YEL if r.waiting else DIM
    if tail and vlen(when) + 2 + vlen(tail[0]) <= width and len(tail) == 1:
        return lines + [f"{when}  {paint}{tail[0]}{R}"]
    return lines + [fit(when, width)] + [f"  {paint}{ln}{R}" for ln in tail]


class Filled(str):
    """A row of the conversation drawn on a background: `fill` is the colour,
    and `_painted` paints it across the whole row."""
    fill: str

    def __new__(cls, text: str, fill: str):
        self = super().__new__(cls, text)
        self.fill = fill
        return self


# The founder's messages: bright white on bright black, both set, so the text
# does not depend on the terminal's own foreground; 16-colour codes only.
FILL_YOU = "\033[97;100m"

# Who said it, three ways that do not depend on colour: the marker, the
# background the human's lines are on, and - for anyone else - the dim.
_MARKERS = {"you": (f"{CYA}{BOLD}\u276f{R}", ""),
            "agent": (f"{GRN}{BOLD}\u23fa{R}", ""),
            "other": ("", DIM)}


def _rows_lines(rows: list, width: int, dim_plain: bool = False,
                focus: tuple | None = None, found: dict | None = None) -> list[str]:
    """The rows of a tab as the screen shows them, each kind laid out its own way.

    A message or a request is a block - a marker or header line and its text
    under it - with a blank line between blocks, so where one ends is a gap and
    not a count. Plain rows are wrapped. A row on a background is painted here,
    to the width it is laid out at, whoever draws the rows.
    """
    return [_painted(ln, width) for ln in _rows_unpainted(rows, width, dim_plain, focus, found)]


def _rows_unpainted(rows: list, width: int, dim_plain: bool, focus: tuple | None = None,
                    found: dict | None = None) -> list[str]:
    out: list[str] = []
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
            block = False
            continue
        if block:
            out.append("")
        here = focus is not None and (r.rid, r.part) == focus
        start = len(out)
        out += _message_lines(r, width, here)
        if here and found is not None:
            found["span"] = (start, len(out))
        block = True
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


def move(keys: list, current, delta: int):
    """The next key in a panel's list, or None when that is off its end."""
    if not keys:
        return None
    if current not in keys:
        return keys[0]
    i = keys.index(current) + delta
    return keys[i] if 0 <= i < len(keys) else None


def step(panel: int, sel: dict, delta: int, panels: tuple = PANELS) -> int:
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
    for _ in range(len(panels) - 1):
        panel = panels[(panels.index(panel) + delta) % len(panels)]
        keys = _keys_of(panel)
        if keys:
            sel[panel] = keys[0] if delta > 0 else keys[-1]
            return panel
    return panel


# An escape sequence: `esc [` and parameters up to a final byte, or `esc O x`.
_CSI = re.compile(r"\033(?:\[[0-?]*[ -/]*[@-~]|O.)")
# What an escape sequence looks like before its final byte has arrived.
_CSI_CUT = re.compile(r"\033(?:\[[0-?]*[ -/]*|O)")


def _word_left(pos: int, text: str) -> int:
    while pos and text[pos - 1] == " ":
        pos -= 1
    if pos and text[pos - 1] == "\n":
        return pos - 1                 # a line's start is a boundary: stop at the one above
    while pos and text[pos - 1] not in " \n":
        pos -= 1
    return pos


def _word_right(pos: int, text: str) -> int:
    n = len(text)
    while pos < n and text[pos] == " ":
        pos += 1
    if pos < n and text[pos] == "\n":
        return pos + 1
    while pos < n and text[pos] not in " \n":
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
        left = len(prompt)         # the prompt is bold wherever it wrapped to
        styled = []
        for row in rows:
            k, left = min(left, len(row)), max(0, left - len(row))
            styled.append(f"{BOLD}{row[:k]}{R}{row[k:]}" if k else row)
        # Windowed after styling: the note row it adds is not typed text, so the
        # prompt's length must not be counted off it.
        return _window_rows(styled, cur, cap)


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


def _run_info_command(con, detail: dict, name: str, cmd: str) -> tuple[dict, str]:
    """The read-only slash commands: nothing to decide, so no `can` gate - the
    terminal handle and worktree path the head used to show."""
    if cmd == "where":
        return detail, actions.agent_where(con, name)
    if cmd == "worktree":
        return detail, actions.agent_worktree(con, name)
    return detail, f"/{cmd}: not wired"        # unreachable while INFO_COMMANDS names all of it


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


def navigate(panel: int, sel: dict, k: str, panels: tuple = PANELS) -> int | None:
    """Where this keypress leaves the cursor, or None if it was not a move.

    ←→ and tab jump a whole panel; ↑↓ walk the screen and cross into the next
    panel at the end of one. On a supervisor, ←→ close and open its team
    first; tab always jumps. `r` is a move of zero: it asks for a redraw, which
    happens anyway, and it must not fall through to the actions.
    """
    if panel == AGENTS and k in ARROWS and _fold(sel, k):
        return panel
    if k in NEXT_PANEL:
        return _enter(panels[(panels.index(panel) + 1) % len(panels)], sel)
    if k in PREV_PANEL:
        return _enter(panels[(panels.index(panel) - 1) % len(panels)], sel)
    if k in NEXT_ROW:
        return step(panel, sel, 1, panels)
    if k in PREV_ROW:
        return step(panel, sel, -1, panels)
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
            return [m["key"] for m in actions.messages(con)]
        # The rows about an account have no agent to act on, and a name that
        # appears twice is one place to put the cursor, not two.
        seen: list = []
        for b in actions.stuck(con):
            if b["agent"] and b["agent"] not in seen:
                seen.append(b["agent"])
        return seen
    finally:
        con.close()

