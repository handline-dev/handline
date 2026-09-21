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
the clock for everything else in the process, so call `replay` from the CLI,
never from inside a daemon tick.

What is reconstructed is not only the account's status but enough of its
load to let the week-runway gate run at all (#1400): burn, as the finite
difference between an account's own neighbouring samples inside one 5h
window (`reconstruct_burn`, the same reasoning `burnrate.observe` applies
live), and the week-per-5h cost ratio, from `burnrate.costs` run on this same
db - both already sit in `usage_samples` and its meta tables, so neither is a
fresh measurement or a read of today's state. Passing `load={}` here was
tried first and thrown out: `_no_week_room` needs `ld.costs` to ask anything,
so under `load={}` the week gate cannot fire and every row it would have
moved reads as agreement instead. What stays permanently unreconstructable
is concurrency: nothing recorded who else was mid-turn on an account at a
past tick, so every replayed `Load` carries `active=0` and `cap=math.inf` -
honest about not knowing, not a guessed count dressed as data. A tick the cap
or a rival agent's presence would have decided cannot be replayed correctly,
and no amount of history fixes that; this only reconstructs what the samples
actually kept.

A tick whose account has no reconstructed burn (its first sample, a carried
reading, a rolled window, or the first inside `since`) or no week ratio is one
the week gate could not properly ask: with no burn it judged the week at zero
fill, with no ratio not at all. Such a tick reads on screen the same as one
where the gate ran and allowed the pick, so every divergence lists those
accounts under "unasked", and `unasked=` collects every compared tick that had
any, divergent or not.
"""
from __future__ import annotations

import math
import time

from . import burnrate, db, load as loadmod, status


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


def _live_5h(row) -> bool:
    """Whether a row's 5h figure is worth differencing: `AccountStatus.
    is_live_reading`'s test, reapplied to a raw row since `status_from_row`
    builds the object but this runs before that, over every row at once."""
    return (row["pct_5h"] is not None and row["pct_5h_readable"]
            and not row["rl_carried"] and not row["rl_lost"] and row["rl_at"] is not None)


def reconstruct_burn(rows) -> dict[tuple[str, int], float]:
    """Percent of the 5h window per second, per account per recorded tick - a
    finite difference to that account's own previous live sample inside the
    same window, over rows already kept rather than a fresh measurement.
    Keyed by (account, ts) so `replay` can look a tick's burn up without
    rescanning. Absent for an account's first sample, a stale or carried
    reading on either end, or a reset between the two - a burn is a claim
    about a fill, and a fill it cannot see it does not guess at."""
    burns: dict[tuple[str, int], float] = {}
    last: dict[str, object] = {}
    for row in sorted(rows, key=lambda r: (r["account"], r["ts"])):
        acct = row["account"]
        prev = last.get(acct)
        last[acct] = row
        if prev is None or not (_live_5h(prev) and _live_5h(row)):
            continue
        if row["reset_5h"] != prev["reset_5h"] or row["rl_at"] <= prev["rl_at"]:
            continue
        rise = row["pct_5h"] - prev["pct_5h"]
        if rise < 0:
            continue
        burns[(acct, row["ts"])] = rise / (row["rl_at"] - prev["rl_at"])
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
    not properly judge at that tick (no burn or no week ratio). When `unasked`
    is a list, every compared tick with any such account is appended to it as
    (ts, accounts), whether or not the pick diverged.
    """
    rows = db.usage_samples(con, since=since)
    ticks = by_tick(rows)
    burns = reconstruct_burn(rows)
    shared_costs = burnrate.costs(con)
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
            # active=0, cap=inf: honest about the one thing no row ever recorded
            # (#1400) - who else was mid-turn on the account at this tick - rather
            # than a load={} that structurally cannot ask the week gate anything.
            tick_load = {n: loadmod.Load(account=n, active=0, cap=math.inf,
                                         burn=burns.get((n, ts)), costs=shared_costs)
                        for n in statuses}
            new_picked, _ = status.pick_account(list(statuses.keys()), load=tick_load,
                                                read_status=statuses.get)
            old_picked = old_pick(statuses)
            blind = sorted(n for n, ld in tick_load.items()
                           if ld.burn is None or shared_costs.week_per_5h(n) is None)
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
