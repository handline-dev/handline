"""Replay pick_account against recorded usage_samples (#1113).

Scenario 6 of #1001 - would the current rule and the earlier
earliest-deadline rule have chosen differently over real history - was not
buildable because nothing retained usage over time: burn.json and
status.json each hold only the latest sample. `db.usage_samples` is what
observe_burn now writes every tick, one row per account, with every
figure exactly as `AccountStatus` had it - `None` stays `None`, never a
guessed 0. This is the other side of that: reconstruct the AccountStatus a
tick actually saw, and ask both rules what they would have done with it.

A sample is judged "as of" the moment it was taken, not real now: whether a
5h reading was stale, whether a 7d window had rolled over, how much of a
week had elapsed - all of that has to be answered by the clock the sample
was under, or every old sample reads as though it were taken just now.
`time.time` is patched for the span of one tick's comparison and restored
immediately after, the same technique the test suite already uses to drive
a fixed clock through `pick_account`. That patch is process-global: it moves
the clock for everything else in the process, so call `replay` and `reconstruct_burn`
from the CLI, never from inside a daemon tick.

What is reconstructed is not only the account's status but enough of its
load to let the week-runway gate run at all (#1400): burn, from the same
rolling window of live samples the daemon keeps and the same
`triggers.burn_rate_per_s` (`reconstruct_burn`), and the week-per-5h ratio from
`burnrate.costs` run on this same db, with no plan lookup since a plan is
today's state on disk, not the tick's. Passing `load={}` was thrown out:
`_no_week_room` needs `ld.costs` to ask anything, so the week gate could not
fire and every row it would have moved read as agreement. Concurrency is what
no row kept, so every replayed `Load` carries `active=0` and `cap=math.inf`:
a tick the cap or a rival agent's presence would have decided cannot be
replayed correctly.

An account with no week ratio is one the week gate could not ask at all, which
reads on screen the same as a gate that ran and allowed the pick. Every
divergence lists such accounts under "unasked", and `unasked=` collects every
compared tick that had any, divergent or not. A tick with no measurable burn
is not unasked: the daemon gives it burn 0.0 and so does the replay.

One read of today's state remains: `pick_account` drops an account that is
being removed now from every past tick.
"""
from __future__ import annotations

import math
import time

from . import burnrate, load as loadmod, status
from .. import db
from ..session import triggers


def status_from_row(row) -> status.AccountStatus:
    """The `AccountStatus` one recorded row describes - the same object
    `pick_account` would have been given, reconstructed from what was kept
    rather than re-derived from a guess."""
    return status.AccountStatus(
        account=row["account"], exists=bool(row["exists_"]), error=row["error"],
        pct_5h=row["pct_5h"], pct_5h_readable=bool(row["pct_5h_readable"]),
        reset_5h=row["reset_5h"], rl_at=row["rl_at"], rl_carried=bool(row["rl_carried"]),
        rl_lost=bool(row["rl_lost"]), pct_7d=row["pct_7d"], reset_7d=row["reset_7d"],
        rl7_at=row["rl7_at"], rl7_carried=bool(row["rl7_carried"]), rl7_lost=bool(row["rl7_lost"]),
        collected_at=row["collected_at"])


def old_pick(statuses: dict[str, status.AccountStatus]) -> str | None:
    """The rule #1001 replaced: earliest weekly deadline first, then most 5h
    headroom, among accounts `takes_work` admits. Reconstructed here rather
    than kept in status.py, since main no longer carries it - this is the
    one place it still needs to exist, for exactly this comparison."""
    usable = []
    for name, st in statuses.items():
        allowed, _ = st.takes_work(0)
        if not allowed:
            continue
        deadline = float(st.reset_7d) if st.reset_7d and st.reset_7d > time.time() else float("inf")
        usable.append((name, deadline, -(st.headroom() or 0)))
    if not usable:
        return None
    usable.sort(key=lambda t: (t[1], t[2]))
    return usable[0][0]


def _live_at_its_tick(row) -> bool:
    """`AccountStatus.is_live_reading` for a recorded row, judged by the clock
    the row was taken under - the test the daemon applied before letting the
    figure into its burn window. Moves the process-global `time.time` while it
    runs, so never call it from inside a daemon tick."""
    real_time = time.time
    time.time = lambda: row["ts"]
    try:
        return status_from_row(row).is_live_reading
    finally:
        time.time = real_time


def reconstruct_burn(rows) -> dict[tuple[str, int], float]:
    """Percent of the 5h window per second, per account per recorded tick,
    keyed by (account, ts). Each account keeps the window the daemon keeps -
    its last `triggers.BURN_WINDOW` live samples - and reads it with `triggers.burn_rate_per_s`,
    so the replayed burn is the function the replayed tick ran, 0.0 where it
    could not measure. Under `since` the window starts empty, so an account's
    first ticks read 0.0 where the daemon had a rate."""
    burns: dict[tuple[str, int], float] = {}
    hist: dict[str, list[tuple[int, int]]] = {}
    for row in sorted(rows, key=lambda r: (r["account"], r["ts"])):
        h = hist.setdefault(row["account"], [])
        if _live_at_its_tick(row) and (not h or h[-1][0] != row["rl_at"]):
            h.append((row["rl_at"], row["pct_5h"]))
            del h[:-triggers.BURN_WINDOW]
        burns[(row["account"], row["ts"])] = triggers.burn_rate_per_s(h)
    return burns


def by_tick(rows) -> dict[int, dict[str, status.AccountStatus]]:
    """Every sampled tick's accounts, keyed by ts - a tick where only one
    account was sampled decides nothing, since neither rule chooses between
    fewer than two."""
    grouped: dict[int, dict[str, status.AccountStatus]] = {}
    for row in rows:
        grouped.setdefault(row["ts"], {})[row["account"]] = status_from_row(row)
    return {ts: accts for ts, accts in grouped.items() if len(accts) > 1}


def replay(con, account_filter=None, since: int | None = None,
           unasked: list | None = None) -> list[dict]:
    """Every recorded tick where the current rule and the old one would have
    picked differently, in order. `account_filter` narrows which accounts a
    tick's comparison considers, when only some are of interest.

    Returns one dict per divergence: ts, old, new, and each candidate
    account's pct_5h/pct_7d at that moment, for a human to read without
    reopening the table, and "unasked": the accounts whose week the gate could
    not judge at that tick (no week ratio). When `unasked`
    is a list, every compared tick with any such account is appended to it as
    (ts, accounts), whether or not the pick diverged.
    """
    rows = db.usage_samples(con, since=since)
    ticks = by_tick(rows)
    burns = reconstruct_burn(rows)
    shared_costs = burnrate.costs(con, plan_of=lambda _a: None)
    real_time = time.time
    diffs = []
    try:
        for ts in sorted(ticks):
            statuses = ticks[ts]
            if account_filter is not None:
                statuses = {n: s for n, s in statuses.items() if n in account_filter}
            if len(statuses) < 2:
                continue
            time.time = lambda ts=ts: ts  # noqa: E731 - judged as of the sample's own moment
            tick_load = {n: loadmod.Load(account=n, active=0, cap=math.inf,
                                         burn=burns[(n, ts)], costs=shared_costs)
                        for n in statuses}
            new_picked, _ = status.pick_account(list(statuses.keys()), load=tick_load,
                                                read_status=statuses.get)
            old_picked = old_pick(statuses)
            blind = sorted(n for n in tick_load if shared_costs.week_per_5h(n) is None)
            if blind and unasked is not None:
                unasked.append((ts, blind))
            if new_picked != old_picked:
                diffs.append({
                    "ts": ts, "old": old_picked, "new": new_picked, "unasked": blind,
                    "accounts": {n: (s.pct_5h, s.pct_7d, s.reset_7d) for n, s in statuses.items()},
                })
    finally:
        time.time = real_time
    return diffs
