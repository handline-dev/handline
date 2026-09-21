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
a fixed clock through `pick_account`.
"""
from __future__ import annotations

import time

from . import db, status


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


def by_tick(rows) -> dict[int, dict[str, status.AccountStatus]]:
    """Every sampled tick's accounts, keyed by ts - a tick where only one
    account was sampled decides nothing, since neither rule chooses between
    fewer than two."""
    grouped: dict[int, dict[str, status.AccountStatus]] = {}
    for row in rows:
        grouped.setdefault(row["ts"], {})[row["account"]] = status_from_row(row)
    return {ts: accts for ts, accts in grouped.items() if len(accts) > 1}


def replay(con, account_filter=None, since: int | None = None) -> list[dict]:
    """Every recorded tick where the current rule and the old one would have
    picked differently, in order. `account_filter` narrows which accounts a
    tick's comparison considers, when only some are of interest.

    Returns one dict per divergence: ts, old, new, and each candidate
    account's pct_5h/pct_7d at that moment, for a human to read without
    reopening the table.
    """
    rows = db.usage_samples(con, since=since)
    ticks = by_tick(rows)
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
            new_picked, _ = status.pick_account(list(statuses.keys()), load={},
                                                read_status=statuses.get)
            old_picked = old_pick(statuses)
            if new_picked != old_picked:
                diffs.append({
                    "ts": ts, "old": old_picked, "new": new_picked,
                    "accounts": {n: (s.pct_5h, s.pct_7d, s.reset_7d) for n, s in statuses.items()},
                })
    finally:
        time.time = real_time
    return diffs
