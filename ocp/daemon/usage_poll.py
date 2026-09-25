"""Asking each account for its usage, and the day's snapshots."""
from __future__ import annotations

import time

from ..team import accounts
from ..infra import timefmt
from ..usage import collect
from .. import db
from ..infra import paths
from ..usage import recover as recovermod
from ..team import spawn as spawnmod
from ..usage import status
from ..usage import usage_api

from .states import (LOGIN_CHECK_COOLDOWN_S, USAGE_FAIL_BACKOFF_CAP_S, USAGE_MAX_AGE_S,
                     USAGE_POLL_S, USAGE_REFRESH_COOLDOWN_S)


_last_rollover_day = {"day": ""}


def collect_snapshots(con, log) -> None:
    """Pull each live agent's context and session identity from its transcript.

    The statusLine pushed these once per turn; this reads them out of the file
    the session is writing anyway (`collect`, D11). Measured on the fleet: the
    same token count to the digit on every agent that had one, 1-2ms apiece.

    Only for an agent whose session is open. The snapshot's `collected_at` is a
    heartbeat - `status.stale` reads a fresh one as "a session is alive here" -
    and refreshing it for a closed agent would tell every reader the session is
    still up. A closed agent's file ages into stale exactly as it does now.
    """
    for row in db.all_agent_states(con):
        if row["closed_at"]:
            continue
        cwd = spawnmod.worktree_path(row["worktree"])
        if not cwd:
            continue
        try:
            collect.collect(row["agent"], cwd)
        except OSError as e:  # noqa: BLE001 - one unreadable transcript is not the tick's failure
            log(f"  collect: {row['agent']}: {e}")


def usage_due(con, acct: str, st: status.AccountStatus, now: int) -> str | None:
    """Why this account's rate limits need asking about, or None.

    There is no event for "your usage changed", so this is the nearest thing:
    the figures move when a turn is taken and at no other time, and a turn is
    something our hooks already record. An account nobody is working on is not
    asked at all - until its cadence comes round, because the spending we cannot
    see leaves no event to go by.
    """
    if not st.exists or st.rl_at is None:
        return "no reading yet"
    if now - st.rl_at >= USAGE_MAX_AGE_S:
        return f"last read {timefmt.ago(now - st.rl_at)} ago"
    for window, reset in (("5h", st.reset_5h), ("7d", st.reset_7d)):
        if reset is not None and reset <= now:
            return f"its {window} window has ended"
    last = db.last_account_activity(con, acct)
    # Both are whole seconds, so a turn inside the same second as the reading
    # cannot be placed on either side of it. Asked, not assumed: this gates
    # handing out work, and the cooldown above bounds what the tie costs.
    if last is not None and last >= st.rl_at:
        return "a turn since the last reading"
    return None


def poll_usage(con, log) -> None:
    """Ask the accounts due for a reading, over their own OAuth credential.

    This is the half of the collector's payload the transcript cannot give
    (D11): a session learns its rate limits only from its own API responses, so
    a quiet account went silent, and until #1447 the answer was `probe.py` -
    boot a session on it and spend a real turn to force a number out. The
    endpoint answers for any account with nothing running at all, which is what
    makes asking cheap enough to do on a schedule, and what retired the probe.

    At most one *successful* reading per tick - five HTTPS GETs in one tick
    would put five timeouts in the path of a 30s loop - but a failing account
    must not consume that budget for every account behind it in
    `list_accounts` order (founder #1811, #1799 part 2): with four accounts
    failing every try (the endpoint itself 429s an account at its own wall, or
    a token stays dead), the old `return` on the first failure meant the loop
    never reached the rest - omega/sigma/theta answered fine and went unpolled
    for three hours because alpha/beta/delta/gamma came first in the list and
    always failed. A failing account keeps its own cursor moving instead
    (`continue`), and is backed off exponentially the way a stalled retry
    anywhere else in ocp is, so it costs less of the budget the longer it
    keeps failing rather than the same slice of it forever.
    """
    now = int(time.time())
    for acct in paths.list_accounts():
        try:
            if _poll_account(con, acct, now, log):
                return
        except Exception as e:  # noqa: BLE001 - one account's reading is not the tick's failure
            log(f"  usage: {acct}: reading raised {type(e).__name__}: {e}")
            _usage_backoff(con, acct, now, log)


def _usage_backoff(con, acct: str, now: int, log) -> None:
    streak = int(db.get_meta(con, f"usage-fail:{acct}") or 0) + 1
    db.set_meta(con, f"usage-fail:{acct}", str(streak))
    backoff = min(USAGE_POLL_S * 2 ** (streak - 1), USAGE_FAIL_BACKOFF_CAP_S)
    db.set_cursor(con, f"usage-poll:{acct}", now + backoff)
    log(f"  usage: {acct}: could not ask (next try in {backoff}s)")


def _ended_window(st, reading, now: int) -> tuple[str, int] | None:
    """The (window, reset) whose end is worth a real turn, or None.

    A window ended by the reading ocp holds; but the plain reading just
    fetched has the last word - a window it still reports exhausted has not
    reset, whatever the older figure said, and a turn on it is the spend the
    wall exists to stop.
    """
    for window, held, fresh_pct, fresh_reset, wall in (
            ("5h", st.reset_5h, reading.pct_5h, reading.reset_5h, status.PCT_5H_BLOCK),
            ("7d", st.reset_7d, reading.pct_7d, reading.reset_7d, status.PCT_7D_BLOCK)):
        if held is None or held > now:
            continue
        if fresh_pct is not None and fresh_pct >= wall and (fresh_reset is None or fresh_reset > now):
            continue
        return window, held
    return None


def _poll_account(con, acct: str, now: int, log) -> bool:
    """One account's turn in `poll_usage`; True when it took the tick's one
    successful reading."""
    if now < db.get_cursor(con, f"usage-poll:{acct}"):
        return False
    st = status.read(acct)
    if st.unmetered:
        return False
    why = usage_due(con, acct, st, now)
    if why is None:
        return False
    if not accounts.logged_in(acct):
        db.set_cursor(con, f"usage-poll:{acct}", now + LOGIN_CHECK_COOLDOWN_S)
        return False
    # No renewal on the common path: a session on the account keeps its own
    # token fresh, and two writers of one keychain entry race.
    reading = usage_api.read(acct, refresh=False)
    if reading is None and now >= db.get_cursor(con, f"usage-refresh:{acct}"):
        db.set_cursor(con, f"usage-refresh:{acct}", now + USAGE_REFRESH_COOLDOWN_S)
        reading = usage_api.read(acct, refresh=True)
    if reading is None:
        # #1885: a dead token reads as None forever without a real turn to
        # renew it. recover() records on success itself, so a success here
        # is handled exactly like an ordinary successful reading below.
        ok, msg = recovermod.recover(acct)
        if ok:
            db.set_meta(con, f"usage-fail:{acct}", None)
            db.set_cursor(con, f"usage-poll:{acct}", now + USAGE_POLL_S)
            log(f"  usage: {msg}")
            return True
        _usage_backoff(con, acct, now, log)
        return False
    ended = _ended_window(st, reading, now)
    if ended and db.get_meta(con, f"reset-ping:{acct}:{ended[0]}") != str(ended[1]):
        # A stale reading can still report success right up to the boundary
        # (#1885, task #76): nothing else forces the account to actually roll
        # over to its new window, so spend the same real turn recover() spends
        # on a dead token. A refusal here (off, already needs re-login, at the
        # 7d wall) is not a failure of this poll - the plain reading just
        # fetched still stands. Once per reset: the turn that succeeded is
        # remembered against the reset it answered, so a server that keeps
        # naming the old one does not get a turn on every poll.
        ok, msg = recovermod.recover(acct)
        if ok:
            db.set_meta(con, f"reset-ping:{acct}:{ended[0]}", str(ended[1]))
            db.set_meta(con, f"usage-fail:{acct}", None)
            db.set_cursor(con, f"usage-poll:{acct}", now + USAGE_POLL_S)
            log(f"  usage: {msg}")
            return True
    db.set_meta(con, f"usage-fail:{acct}", None)
    db.set_cursor(con, f"usage-poll:{acct}", now + USAGE_POLL_S)
    usage_api.record(reading)
    log(f"  usage: {acct}: {why} - 5h {reading.pct_5h}% 7d {reading.pct_7d}%")
    return True
