"""The weekly headroom of all accounts together, the last day and the next (founder #824).

Past points are what `usage_samples` recorded. The future is a guess: each account keeps using
its week at the pace it used it over the last day, and when its week resets the amount it had
used comes back, so it starts again from nothing used at that pace. What is measured and what
is guessed are kept apart (`past`, `future`); this is pure, given the sample rows."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import windows

STEP_S = 1800
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
    grid = range(now - hours * 3600, now + hours * 3600 + 1, STEP_S)
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
