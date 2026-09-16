"""Handoff triggers (design section 8.1).

Triggers are conditions, evaluated fresh every tick, never edge detections.
An edge trigger ("did we cross the line during this tick?") misses every
crossing that happened while the daemon was down, and misses it permanently.
Stating them as conditions means a restart re-observes reality and acts on it.

Near the ceiling the current value is not good enough either. A heavy agent can
go 88% -> 97% between two 30s ticks, so above HOT_PCT we poll faster and judge
on the projected value, pct + burn * (poll + fold_eta), not on pct itself.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# Context: replaces the cache-expiry trigger that appendix A-2 removed.
CONTEXT_PCT = 60

# 90% is the hard stop. We act earlier by the reserve, which is the observed
# average cost of a fold.
HARD_STOP_PCT = 90
DEFAULT_RESERVE_PCT = 5

# Above this, tighten polling and switch to projected judgement.
HOT_PCT = 80
POLL_NORMAL_S = 30
POLL_HOT_S = 5
FOLD_ETA_S = 45


@dataclass
class Trigger:
    fired: bool
    kind: str = ""
    reason: str = ""
    projected: float | None = None


def poll_interval(pct_5h: int | None) -> int:
    """Unknown usage polls at the fast rate: not knowing is not a reason to
    relax, it is a reason to look sooner."""
    if pct_5h is None or pct_5h >= HOT_PCT:
        return POLL_HOT_S
    return POLL_NORMAL_S


def burn_rate_per_s(samples: list[tuple[int, int]]) -> float:
    """Percent per second, from (timestamp, pct) samples within one window.

    Returns 0.0 when it cannot be measured. A reset makes pct drop; that
    sample pair is skipped rather than producing a negative rate.
    """
    usable = [(t, p) for t, p in samples if p is not None]
    if len(usable) < 2:
        return 0.0
    usable.sort()
    rates = []
    for (t0, p0), (t1, p1) in zip(usable, usable[1:]):
        dt = t1 - t0
        if dt <= 0 or p1 < p0:
            continue
        rates.append((p1 - p0) / dt)
    if not rates:
        return 0.0
    return max(rates[-3:])


def evaluate(pct_5h: int | None, ctx_pct: int | None, reserve_pct: int = DEFAULT_RESERVE_PCT,
             burn: float = 0.0, poll_s: int | None = None,
             fold_eta_s: int = FOLD_ETA_S) -> Trigger:
    """Should this agent hand off now?"""
    # Unknown usage is fail-closed (section 4): hand off rather than run blind.
    if pct_5h is None:
        return Trigger(True, "usage_unknown",
                       "pct_5h is null - usage unknown, handing off (fail-closed)")

    limit = HARD_STOP_PCT - reserve_pct

    if pct_5h >= HARD_STOP_PCT:
        return Trigger(True, "usage", f"pct_5h {pct_5h}% >= hard stop {HARD_STOP_PCT}%")

    # Plain threshold first, so an ordinary crossing is reported as such and
    # "projected" is reserved for the case the projection actually adds.
    if pct_5h >= limit:
        return Trigger(True, "usage", f"pct_5h {pct_5h}% >= {limit}% "
                                      f"(hard stop {HARD_STOP_PCT}% minus reserve {reserve_pct}%)")

    if pct_5h >= HOT_PCT:
        poll = poll_interval(pct_5h) if poll_s is None else poll_s
        projected = pct_5h + burn * (poll + fold_eta_s)
        if projected >= limit:
            return Trigger(True, "usage_projected",
                           f"pct_5h {pct_5h}% projects to {projected:.1f}% within "
                           f"{poll + fold_eta_s}s (burn {burn * 60:.2f}%/min) >= {limit}%",
                           projected=projected)

    if ctx_pct is not None and ctx_pct >= CONTEXT_PCT:
        return Trigger(True, "context", f"context {ctx_pct}% >= {CONTEXT_PCT}%")

    return Trigger(False, "", f"pct_5h {pct_5h}% < {limit}%, context "
                              f"{'?' if ctx_pct is None else str(ctx_pct) + '%'} < {CONTEXT_PCT}%")


def next_reset(reset_5h: int | None, now: int | None = None) -> int:
    """When the 5h window rolls over. Used as wake_at for a parked agent."""
    now = now or int(time.time())
    if reset_5h and reset_5h > now:
        return int(reset_5h)
    return now + 300  # unknown: look again shortly rather than sleeping blind
