"""Times and lengths of time, written one way everywhere they are shown.

`ago` is the one formatter for an age or a wait: every screen that prints one
calls it, so a reading three hours old is not "3h" on one and "13118s" on
another, and nothing writes 162h for what is six and a half days.
"""
from __future__ import annotations

import time

DATE_TIME = "%Y-%m-%d %H:%M"
SHORT = "%m-%d %H:%M"
CLOCK = "%H:%M"


def stamp(ts: float | None, fmt: str = DATE_TIME, empty: str | None = None) -> str:
    """`ts` in local time. A missing `ts` reads as `empty` when one is given."""
    if empty is not None and not ts:
        return empty
    return time.strftime(fmt, time.localtime(ts or 0))


def span(secs: float, precise: bool = False) -> str:
    """A length of time, every form ending in its unit: 6d18h from a day up,
    5h03m from an hour, then minutes (`precise` adds the seconds: 12m05s).
    Ages (`ago`) are coarser below a day - 3h, 40m - and call this from a day
    up, so no surface writes 162h."""
    secs = int(secs)
    if secs >= 86400:
        return f"{secs // 86400}d{(secs % 86400) // 3600}h"
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    if precise:
        return f"{secs // 60:>2}m{secs % 60:02d}s"
    return f"{max(1, secs // 60)}m"


def ago(secs: float | None) -> str:
    """A duration in the largest unit that still says something."""
    if secs is None:
        return "?"
    secs = int(secs)
    if secs < 120:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return span(secs)


def ago_short(ts: int, now: float | None = None) -> str:
    """How long since `ts`, in whole d, h or m: the proposals list's column."""
    secs = max(0, int((time.time() if now is None else now) - ts))
    return f"{secs // 86400}d" if secs >= 86400 else f"{secs // 3600}h" if secs >= 3600 \
        else f"{secs // 60}m"
