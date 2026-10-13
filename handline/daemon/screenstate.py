"""The one word an agent's row shows and what is said beside it (`screen_state`): the daemon stores it in the
agent's view (`agentview.py`), and `hl ps` and the UI read it through here, so no two screens can disagree
about a row."""
from __future__ import annotations

import time

from .. import actions, db, vendors
from ..infra import timefmt
from ..infra.textsafe import oneline
from ..session import blocked as blockedmod, term
from . import delivery as dmn_delivery, lifecycle as dmn_lifecycle
from ..session import states as dmn_states

# DETAIL_CAP bounds the free text screen_state() shows beside a word - a
# dialog's own detail, or who an IDLE reply-wait is for - to what the state
# cell has room for; `fit()` truncates further if the column itself is
# narrower. Long text belongs in `hl ps`'s own DETAIL column instead.
DETAIL_CAP = 40


# The state word for an opencode agent is read off its pane, not out of the
# db: claude reports RUNNING and IDLE through hooks, and opencode has no
# counterpart wired, so its row sits at whatever launch wrote - IDLE - for the
# life of the session. The witness is the pane's own screen, the same one
# wait_idle's screen half trusts: Orca's tui-idle word is no good here,
# measured on a busy opencode pane it answers idle while the turn streams
# (term.py's docstring says the same). One screen read is ~110ms; the cache
# keeps a redraw's rows from each paying it, and the timestamp is taken after
# the read, so a slow answer is not born already expired.
OPENCODE_STATE_TTL_S = 2.0
_opencode_seen: dict[str, tuple[float, bool | None]] = {}


def _state_from_screen(acct: str) -> bool:
    """Does this account's vendor leave its agents' state to the screen, having
    no hooks to report it? False for a vendor nothing can be said about."""
    try:
        return vendors.for_account(acct).state_hooks() is None
    except vendors.UnknownVendor:
        return False


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
    frame = term._screen(handle)
    tail = frame.get("tail") if frame else None
    if not isinstance(tail, list) or frame.get("source") != "screen":
        working = None
    else:
        working = term._driver_for(handle).working_on_screen(tail)
    if len(_opencode_seen) > 128:
        for k in [k for k, (at, _) in _opencode_seen.items() if now - at > OPENCODE_STATE_TTL_S]:
            del _opencode_seen[k]
    _opencode_seen[handle] = (time.monotonic(), working)
    return working


def _background_detail(con, agent: str, now: int | None = None) -> str:
    """How long the session has reported background work, and "stale" once the
    agent has shown nothing beside it for `delivery.STALE_BG_S`."""
    now = int(time.time()) if now is None else now
    since = dmn_delivery.bg_since(con, agent)
    return (f"background {timefmt.ago(now - since)}" if since else "background work") \
        + (", stale" if dmn_delivery.bg_stale(con, agent, now) else "") \
        + (", told to finish or stop it" if dmn_delivery.bg_prompted_at(con, agent) else "")


def screen_state(con, row, now: int | None = None) -> tuple[str, str]:
    """`_screen_state`, with an idle agent whose task waits on a child of its own or is
    blocked saying so. `now` is the caller's clock, for a caller that compares two reads."""
    word, detail = _screen_state(con, row, now)
    if word == "IDLE" and not detail and row:
        detail = oneline(actions.child_wait(con, row["agent"]) or actions.block_wait(con, row["agent"]) or "")
    return word, detail


# The words that leave the daemon (contract x-state-words): lower case, by what the agent's row says, never by reading `detail`.
WIRE_WORDS = ("running", "idle", "queued", "parked", "waiting", "stalled", "blocked", "no_terminal")


def wire_state(con, row, word: str, detail: str) -> tuple[str, str]:
    """The screen word (`screen_state`'s) as the spec's lower-case state word and its state_detail. PENDING is two words, by what the
    row waits for: `parked` when a wake time stands in the way, `queued` when only the next tick does; MOVING is `parked` with the
    detail `moving`; an idle row with no terminal to show is `no_terminal`."""
    if word == "RUNNING":
        return "running", detail
    if word == "DIALOG":
        return "waiting", detail
    if word == "FATAL":
        return "stalled", detail
    if word == "MOVING":
        return "parked", "moving"
    if word == "PENDING":
        timed = (row is not None and row["state"] in (dmn_states.PARKED, dmn_states.DEGRADED, dmn_states.THROTTLED) and bool(row["wake_at"])
                 and not dmn_lifecycle.restart_retry_note(con, row["agent"], row))
        return ("parked" if timed else "queued"), detail
    if row is None or not row["terminal"]:
        return "no_terminal", detail
    return "idle", detail


def _screen_state(con, row, now: int | None = None) -> tuple[str, str]:
    """The one word this agent's row shows - from SCREEN_WORDS - and what to
    say beside it: a clock time, or what it is doing or waiting for. Drawn
    here and read by `hl ps` (handline/cli/) alike, through this one function, so
    the two screens cannot disagree about a row.

    No `·closed` suffix (the founder found it information nobody needs on
    this screen - `closed_at` still decides whether the daemon can wake the
    agent, in `handline/daemon/`, untouched here).

    DIALOG overlays whatever the row's own state is, from either of two live
    person-facing sources - never a stored state, so there is nothing to
    restore when the dialog goes. MOVING never carries a clock: the short
    recheck it waits on (ASSUMED_ACCOUNT_RECHECK_S, 5 minutes) is not the
    account-move machinery's own wall, so a time on it would say more than is
    known.
    """
    if not row:
        return "IDLE", ""
    prompt = actions.open_block_of(con, row["agent"])
    if prompt:
        return "DIALOG", oneline(actions.block_headline(prompt))[:DETAIL_CAP]
    block = db.block_get(con, row["agent"])
    if block and block["kind"] in (term.MODAL, term.BACKGROUNDED):
        return "DIALOG", oneline(blockedmod.headline(block["kind"], block["detail"]))[:DETAIL_CAP]

    state, detail = row["state"], row["detail"] or ""
    if state == dmn_states.STALLED:
        return "FATAL", oneline(detail)[:DETAIL_CAP]
    down = dmn_lifecycle.restart_retry_note(con, row["agent"], row)
    if down:
        return "PENDING", oneline(down)[:DETAIL_CAP]
    if state in (dmn_states.RUNNING, dmn_states.HANDOFF):
        # An opencode row's word is its pane's screen (see _opencode_working).
        # HANDOFF stays RUNNING - it is the daemon's protected mid-move, and
        # moving is working. A closed row's word is the db's, and so is any
        # row when the screen cannot be read.
        if state == dmn_states.RUNNING and row["terminal"] and not row["closed_at"] \
                and _state_from_screen(row["account"] or "") \
                and _opencode_working(row["terminal"]) is False:
            return "IDLE", ""
        return "RUNNING", ""
    if state == dmn_states.QUEUED:
        if detail.startswith(dmn_states.MOVING_MARK):
            return "MOVING", ""
        if dmn_delivery.has_background_work(con, row["agent"]):
            return "IDLE", _background_detail(con, row["agent"], now)      # nothing is being said: not working
        return "PENDING", ""
    if state in (dmn_states.PARKED, dmn_states.DEGRADED, dmn_states.THROTTLED):
        if detail.startswith(dmn_states.MOVING_MARK):
            return "MOVING", ""
        if not row["wake_at"]:
            return "PENDING", ""
        # Nothing waits for an agent parked on a wall with no work behind it,
        # so there is nothing pending: the wake is the daemon's own, and
        # queued_or_idle would say IDLE for the same row. A park a person
        # asked for by hand still shows the time they chose.
        if state == dmn_states.PARKED and not detail.startswith(dmn_states.BY_HAND_MARK) \
                and not db.unreached_count(con, row["agent"]):
            return "IDLE", ""
        # A wake_at already in the past is a tick the daemon has not run yet,
        # not a clock face worth printing - "due" says so the way the
        # countdown this replaced always did.
        until = "due" if row["wake_at"] <= (int(time.time()) if now is None else now) \
            else timefmt.stamp(row["wake_at"], timefmt.CLOCK)
        return "PENDING", until
    # An opencode agent never writes RUNNING - its hooks do not exist - so an
    # agent that IS working sits here in the db and the plain IDLE below would
    # hide it. The same witness speaks first, but never for a closed agent:
    # its pane is a shell prompt, and its word is the db's.
    if state == dmn_states.IDLE and not row["closed_at"] and row["terminal"] \
            and _state_from_screen(row["account"] or "") \
            and _opencode_working(row["terminal"]) is True:
        return "RUNNING", ""
    # A row the daemon marked with work still running behind the prompt says so beside
    # its word; it is not working (the founder asked for this).
    if state == dmn_states.IDLE and dmn_delivery.has_background_work(con, row["agent"]):
        return "IDLE", _background_detail(con, row["agent"], now)
    # IDLE, and anything not written above (section 8.4's own DONE, never
    # actually written - see daemon.WAKER): shown as IDLE rather than a raw
    # internal name a person was never meant to read.
    return "IDLE", ""
