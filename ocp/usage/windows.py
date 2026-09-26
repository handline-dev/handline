"""The one place a usage window's stored shape is spelled.

A record holds `windows`: a list of `{"minutes", "used_percent", "resets_at",
"observed_at", "carried", "lost"}`, one per window the vendor reported, keyed by
length (design/03 §5). Nothing else in ocp asks what a window is called; it asks
for the shortest and the longest (`status.read`).

Records written before windows were a list keep the four names of claude's two
(`pct_5h`, `reset_5h`, `rl_at`, ...). They are never rewritten; `entries` reads
either shape, and this is the only code that knows the old names.
"""
from __future__ import annotations

import json
from typing import Mapping

# The two windows a record written before `windows` could hold, by length.
_LEGACY = ((300, "pct_5h", "reset_5h", "rl_at", "rl_carried", "rl_lost"),
           (10080, "pct_7d", "reset_7d", "rl7_at", "rl7_carried", "rl7_lost"))


def entry(minutes: int, pct: int | None, reset: int | None, at: int | None,
          carried: bool = False, lost: bool = False) -> dict:
    return {"minutes": minutes, "used_percent": pct, "resets_at": reset,
            "observed_at": at, "carried": carried, "lost": lost}


def _legacy(raw: Mapping) -> list[dict]:
    out = []
    for minutes, pk, rk, ak, ck, lk in _LEGACY:
        # A missing pct key is a window nobody wrote, which reads as unreadable;
        # an explicit null is a window the payload did not report.
        e = entry(minutes, raw.get(pk), raw.get(rk), raw.get(ak),
                  raw.get(ck) in (True, 1), raw.get(lk) in (True, 1))
        if pk not in raw or raw.get(pk + "_readable") in (0, False):
            e["readable"] = False
        out.append(e)
    return out


def entries(raw: Mapping) -> list[dict]:
    """The record's windows, shortest first, whichever shape it was written in.
    A record with no windows key and none of the old ones is an empty list."""
    got = raw.get("windows")
    if isinstance(got, str):        # a database row keeps the list as JSON text
        try:
            got = json.loads(got)
        except ValueError:
            got = None
    if isinstance(got, list):
        return sorted((e for e in got if isinstance(e, dict)
                       and isinstance(e.get("minutes"), int)),
                      key=lambda e: e["minutes"])
    if any(pk in raw for _, pk, *_ in _LEGACY):
        return _legacy(raw)
    return []


def label(minutes: int) -> str:
    """A window's length as a person says it: 300 -> 5h, 10080 -> 7d."""
    if minutes <= 0:
        return "?"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"
