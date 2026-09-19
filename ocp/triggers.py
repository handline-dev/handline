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

# Replaces the cache-expiry trigger that appendix A-2 removed.
CONTEXT_PCT = 60

# 90% is the hard stop. We act earlier by the reserve, which is the observed
# average cost of a fold.
HARD_STOP_PCT = 90
DEFAULT_RESERVE_PCT = 5

# The weekly window is spent to its own ceiling (status.PCT_7D_BLOCK is the same
# 100; this module takes raw figures and never imports status).
PCT_7D_HARD_STOP = 100
DEFAULT_RESERVE_7D_PCT = 2

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


def poll_interval(pct_5h: int | None, burn: float = 0.0,
                  reserve_pct: int = DEFAULT_RESERVE_PCT) -> int:
    """How long we may sleep before looking again.

    Unknown usage polls fast: not knowing is a reason to look sooner, not to
    relax. Otherwise the question is whether a normal-length sleep could carry
    us past the line, so burn decides it, not the fixed HOT_PCT mark alone. At
    0.3%/s - the rate in this module's own 88->97 example - an agent at 76%
    would clear the reserve during one 30s sleep, and a fixed mark never sees
    it coming.
    """
    if pct_5h is None or pct_5h >= HOT_PCT:
        return POLL_HOT_S
    limit = HARD_STOP_PCT - reserve_pct
    if burn > 0 and pct_5h + burn * (POLL_NORMAL_S + FOLD_ETA_S) >= limit:
        return POLL_HOT_S
    return POLL_NORMAL_S


# pct_5h arrives as a whole number, so the smallest change we can ever see is
# one point. Measured across a couple of seconds that single point reads as
# 30%/min, which is quantization noise, not a burn rate - and it was enough to
# hand off and clear a working agent. A rate is only meaningful once the window
# is long enough that one point of resolution is a small part of it.
MIN_BURN_SPAN_S = 120


def burn_rate_per_s(samples: list[tuple[int, int]]) -> float:
    """Percent per second, measured across the whole sample window.

    Returns 0.0 when it cannot be measured honestly: too few samples, too short
    a span, or a drop (a reset), which would otherwise read as negative burn.
    Measuring end to end rather than between adjacent pairs is what keeps one
    quantized step from dominating the answer.
    """
    usable = sorted((t, p) for t, p in samples if p is not None)
    if len(usable) < 2:
        return 0.0
    (t0, p0), (t1, p1) = usable[0], usable[-1]
    span = t1 - t0
    if span < MIN_BURN_SPAN_S or p1 <= p0:
        return 0.0
    return (p1 - p0) / span


def evaluate(pct_5h: int | None, ctx_pct: int | None, reserve_pct: int = DEFAULT_RESERVE_PCT,
             burn: float = 0.0, poll_s: int | None = None,
             fold_eta_s: int = FOLD_ETA_S, pct_7d: int | None = None,
             reserve_7d_pct: int = DEFAULT_RESERVE_7D_PCT) -> Trigger:
    """Should this agent hand off now?"""
    # Unknown usage is fail-closed (section 4): hand off rather than run blind.
    # Callers pass the figure they may act on, so None here means no snapshot,
    # an unreadable one, one too old to believe, or a reading the collector
    # could not carry - never a window that is merely quiet.
    if pct_5h is None:
        return Trigger(True, "usage_unknown",
                       "no usable pct_5h - usage unknown, handing off (fail-closed)")

    limit = HARD_STOP_PCT - reserve_pct

    if pct_5h >= HARD_STOP_PCT:
        return Trigger(True, "usage", f"pct_5h {pct_5h}% >= hard stop {HARD_STOP_PCT}%")

    # Plain threshold first, so an ordinary crossing is reported as such and
    # "projected" is reserved for the case the projection actually adds.
    if pct_5h >= limit:
        return Trigger(True, "usage", f"pct_5h {pct_5h}% >= {limit}% "
                                      f"(hard stop {HARD_STOP_PCT}% minus reserve {reserve_pct}%)")

    # The weekly figure, a reserve short of its own wall. Unknown (None) is not
    # fail-closed here the way pct_5h is: a missing reading is not a wall.
    if pct_7d is not None:
        limit7 = PCT_7D_HARD_STOP - reserve_7d_pct
        if pct_7d >= PCT_7D_HARD_STOP:
            return Trigger(True, "usage_7d", f"pct_7d {pct_7d}% >= hard stop {PCT_7D_HARD_STOP}%")
        if pct_7d >= limit7:
            return Trigger(True, "usage_7d", f"pct_7d {pct_7d}% >= {limit7}% "
                                          f"(hard stop {PCT_7D_HARD_STOP}% minus reserve "
                                          f"{reserve_7d_pct}%)")

    # Project from wherever we are, not only above HOT_PCT. The horizon is the
    # sleep we are actually about to take plus the fold it would have to
    # finish, so a fast burner is caught before it crosses rather than after.
    if burn > 0:
        poll = poll_interval(pct_5h, burn, reserve_pct) if poll_s is None else poll_s
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


def wake_after(kind: str, reset_5h: int | None, reset_7d: int | None,
               now: int | None = None) -> int:
    """When a parked agent should look again: the window its trigger fired on
    has to roll over, and a weekly trigger outlasts every 5h reset."""
    return next_reset(reset_7d if kind == "usage_7d" else reset_5h, now)
