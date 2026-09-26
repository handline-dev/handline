"""The weekly headroom of all accounts together, the last day and the next (founder #824).

Past points are what `usage_samples` recorded. The future is a guess: each account keeps using
its week at the pace it used it over the last day, and when its week resets the amount it had
used comes back, so it starts again from nothing used at that pace. What is measured and what
is guessed are kept apart (`past`, `future`); this is pure, given the sample rows."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import windows

POINTS = 96                 # over the whole range, past and future
MAX_AGE_S = 2 * 3600        # a sample older than this no longer says what the account held
MIN_SPAN_S = 3600           # less than this is not a pace


@dataclass
class Outlook:
    past: list[tuple[int, float | None]] = field(default_factory=list)
    future: list[tuple[int, float | None]] = field(default_factory=list)
    accounts: int = 0


def _week(row) -> tuple[int, float, int | None] | None:
    """(ts, used percent, reset) of the longest window one sample holds."""
    got = [e for e in windows.entries(dict(row)) if e.get("used_percent") is not None
           and e.get("readable", True) and not e.get("lost") and e["minutes"] > 300]
    if not got:
        return None
    e = got[-1]
    return row["ts"], float(e["used_percent"]), e.get("resets_at")


def _at(series: list[tuple[int, float, int | None]], t: int):
    """The latest sample at or before `t`, unless it is too old."""
    best = None
    for s in series:
        if s[0] > t:
            break
        best = s
    return best if best and t - best[0] <= MAX_AGE_S else None


def _pace(series: list[tuple[int, float, int | None]]) -> float:
    """Percent of the week used per second over the samples of the current week."""
    last = series[-1]
    same = [s for s in series if s[2] == last[2]]
    span = last[0] - same[0][0]
    if span < MIN_SPAN_S:
        return 0.0
    return max(0.0, (last[1] - same[0][1]) / span)


def week_consumed(rows, lo: int, hi: int) -> dict[str, float]:
    """Points of each account's week used between `lo` and `hi`: the rises between one sample
    and the next, and after a reset what has been used since. Read, not guessed."""
    by: dict[str, list] = {}
    for r in rows:
        w = _week(r)
        if w and lo <= w[0] <= hi:
            by.setdefault(r["account"], []).append(w)
    out = {}
    for a, series in by.items():
        total = 0.0
        for prev, cur in zip(series, series[1:]):
            total += cur[1] - prev[1] if cur[1] >= prev[1] else cur[1]
        out[a] = total
    return out


def consumed_curve(rows, times: list[int]) -> dict[str, list[float]]:
    """For each account, the points of its week used from the first time to each of `times`
    (the rises so far, as in `week_consumed`), so a curve can be drawn beside the headroom."""
    by: dict[str, list] = {}
    for r in rows:
        w = _week(r)
        if w:
            by.setdefault(r["account"], []).append(w)
    out = {}
    for a, series in by.items():
        cum, run, prev, i = [], 0.0, None, 0
        for t in times:
            while i < len(series) and series[i][0] <= t:
                cur = series[i]
                if prev is not None and series[i][0] >= times[0]:
                    run += cur[1] - prev[1] if cur[1] >= prev[1] else cur[1]
                prev = cur
                i += 1
            cum.append(run)
        out[a] = cum
    return out


def weekly_outlook(rows, now: int, hours: int = 24) -> Outlook:
    """`rows` are `usage_samples` rows (any accounts, oldest first). The value is the
    headroom left of the week, in percent, averaged over the accounts that have a reading."""
    by: dict[str, list] = {}
    for r in rows:
        w = _week(r)
        if w:
            by.setdefault(r["account"], []).append(w)
    out = Outlook(accounts=len(by))
    if not by:
        return out
    step = max(900, hours * 3600 * 2 // POINTS)
    grid = range(now - hours * 3600, now + hours * 3600 + 1, step)
    paces = {a: _pace(s) for a, s in by.items()}
    for t in grid:
        left = []
        for a, series in by.items():
            if t <= now:
                s = _at(series, t)
                if s:
                    left.append(100 - s[1])
            else:
                last = series[-1]
                if now - last[0] > MAX_AGE_S:
                    continue
                used, reset = last[1], last[2]
                if reset and t >= reset:
                    used = paces[a] * (t - reset)
                else:
                    used = used + paces[a] * (t - now)
                left.append(100 - min(100.0, used))
        (out.past if t <= now else out.future).append(
            (t, sum(left) / len(left) if left else None))
    return out
