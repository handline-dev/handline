"""Handoff triggers and the rules for where an agent runs (design section 8.1).

Triggers are conditions, evaluated fresh every tick, never edge detections.
An edge trigger ("did we cross the line during this tick?") misses every
crossing that happened while the daemon was down, and misses it permanently.
Stating them as conditions means a restart re-observes reality and acts on it.

A working agent is never handed off or moved on a percentage. It runs to the
wall - a turn that ends on the usage limit, or a reading at 100% - because
nothing is lost there (the transcript keeps the work and the next session picks
it up) and everything below it is paid for: a session started elsewhere spends
a measurable share of that account's window just getting back to where it was.
Whether to pay that is a comparison (`stay_or_move`), not a line.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# Replaces the cache-expiry trigger that appendix A-2 removed. Was a
# percentage of a per-model window (60%), which meant one thing when every
# model's window was 200k and a different, five times looser thing once
# opus/sonnet grew to 1M and the sentence did not change with it (#1665,
# #1668). An absolute figure means the same thing on every model, including
# one whose window this box has never priced. The founder sets the figure
# (#1898); an agent's own context_tokens overrides it, and
# CONTEXT_CLAMP_FRACTION holds a small-window model down to its share below.
CONTEXT_TOKENS = 150_000

# The clamp: a ceiling of last resort, not the normal decider. When the
# model is one collect.ctx_size() recognizes, the effective limit is never
# above this fraction of that model's own window, so a model with a small
# window still gets a working check even if its configured or default
# figure is sized for a 1M-token one. Reusing 60% (CONTEXT_PCT's old value)
# means this reproduces exactly what the rule already meant for each known
# model - sonnet/opus clamp to 600,000; haiku clamps to 120,000, below the
# flat default, so on haiku the clamp is the working limit. An unrecognized
# model has no window to take a fraction of, so it gets the flat default
# alone - the deliberate failure direction, since refusing to hand off
# forever (today's silent behaviour) is worse than a number picked for a
# different model.
CONTEXT_CLAMP_FRACTION = 0.60

# Both windows are spent to their own ceiling.
WALL_PCT = 100

# How often the daemon looks. Nothing here is racing a threshold any more: the
# wall announces itself.
POLL_S = 30

# T: an agent at the wall stays parked when its own account resets within this.
# A move costs a cold start - the destination's window pays for a new session
# to get back to where the old one was (measured on gamma: about 13% of a Pro
# 5h window for one first turn) - and that is worth waiting for only if the
# wait is shorter than the window time the start would spend: 13% of 5h is 39
# minutes. This is the starting value; `stay_within_s` replaces it with what the
# daemon has measured once it has enough samples (measure.py).
STAY_WITHIN_S = 39 * 60

# What one agent working steadily costs an account, in percent of its 5h window
# per second, until enough has been measured to say (`burnrate`): 0.75%/min. It
# is what 09-19's trace gave, where each account's rise was divided by the agents
# active on it - beta 50% -> 85% in 9 minutes with five, gamma 7% -> 51% in 13
# minutes with five - and it is where an agent of a kind nobody has measured yet
# starts.
TYPICAL_AGENT_BURN_PER_S = 0.75 / 60

# An account can take one more agent only if, at its current fill plus that
# agent's cost, it lasts this long before its wall. Below that a session spends
# more on getting started than it gets back: a cold start is about 13% of a
# window, 17 minutes of an agent at the starting value's 0.75%/min, and twice
# that, rounded to half an hour, is the least stretch worth beginning. The
# half hour stays put as agents are measured: a cheaper agent already lasts
# longer on the same account, and a floor derived from the cost would rise as
# agents got cheaper and refuse them for it. (T above is the same start in
# wall-clock time, and it does follow measure.py.)
MIN_STRETCH_S = 30 * 60

# Most agents mid-turn on one account at once (Pro). Set per account in config.
DEFAULT_ACTIVE_CAP = 2


@dataclass
class Trigger:
    fired: bool
    kind: str = ""
    reason: str = ""


# A window's percent arrives as a whole number, so the smallest change we can ever see is
# one point. Measured across a couple of seconds that single point reads as
# 30%/min, which is quantization noise, not a burn rate - and it was enough to
# hand off and clear a working agent. A rate is only meaningful once the window
# is long enough that one point of resolution is a small part of it.
MIN_BURN_SPAN_S = 120

# How many live samples an account's burn window keeps; the daemon and replay both read it.
BURN_WINDOW = 10


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


def evaluate(short_pct: int | None, ctx_used: int | None, long_pct: int | None = None,
             ctx_limit: int = CONTEXT_TOKENS) -> Trigger:
    """Should this agent hand off now?

    Usage answers only at the wall. An unreadable figure is not a wall: the
    wall announces itself, and moving an agent off an account nobody can read
    spends a cold start on a guess.

    `ctx_limit` is a token count, not a percentage of anything - the caller has
    already resolved the agent's own configured-or-default figure and, when the
    model is a known one, clamped it to CONTEXT_CLAMP_FRACTION of that model's
    window (see status.effective_context_limit). This function takes numbers, never a
    name, so it stays the one place both callers and every test can trust
    without re-deriving an agent's override or a model's window here too.
    """
    if short_pct is not None and short_pct >= WALL_PCT:
        return Trigger(True, "usage", f"shortest window {short_pct}% - at the wall")
    if long_pct is not None and long_pct >= WALL_PCT:
        return Trigger(True, "usage_7d", f"longest window {long_pct}% - at the wall")
    if ctx_used is not None and ctx_used >= ctx_limit:
        return Trigger(True, "context", f"context {ctx_used:,} >= {ctx_limit:,}")
    return Trigger(False, "", f"shortest window {'?' if short_pct is None else str(short_pct) + '%'} below "
                              f"the wall, context "
                              f"{'?' if ctx_used is None else format(ctx_used, ',')} "
                              f"< {ctx_limit:,}")


def stay_within_s(cold_start_pct: float | None = None, window_s: float = 0) -> float:
    """T. The measured cost of a cold start, as the seconds of the shortest
    window it spends (`window_s` long), when there is a measurement; the
    starting value otherwise."""
    if cold_start_pct is None or cold_start_pct <= 0 or window_s <= 0:
        return STAY_WITHIN_S
    return cold_start_pct / 100 * window_s


def stay_or_move(work_waiting: bool, reset_in_s: float | None,
                 stay_within: float = STAY_WITHIN_S) -> tuple[bool, str]:
    """An agent at the wall: is a move worth its cold start? (move, why).

    Never for an idle agent - a move is for work that is waiting - and not when
    its own window rolls over sooner than the start would pay back. An unknown
    reset is not "soon": it is treated as further than T.
    """
    if not work_waiting:
        return False, "no work is waiting - an idle agent does not move"
    if reset_in_s is not None and reset_in_s <= stay_within:
        return False, f"its account resets within {int(stay_within // 60)}m - cheaper to wait"
    return True, "work is waiting and its account resets too late to wait for"


def stretch_s(headroom_pct: float, fill_per_s: float,
              agent_per_s: float = TYPICAL_AGENT_BURN_PER_S) -> float:
    """How long an account lasts before its wall with one more agent on it, at
    that agent's cost."""
    rate = max(0.0, fill_per_s) + agent_per_s
    return headroom_pct / rate if rate > 0 else float("inf")


def next_reset(reset: int | None, now: int | None = None) -> int:
    """When a window rolls over. Used as wake_at for a parked agent."""
    now = now or int(time.time())
    if reset and reset > now:
        return int(reset)
    return now + 300  # unknown: look again shortly rather than sleeping blind


def wake_after(kind: str, short_reset: int | None, long_reset: int | None,
               now: int | None = None) -> int:
    """When a parked agent should look again: the window its trigger fired on
    has to roll over, and a trigger on the longest outlasts every reset of the
    shortest."""
    return next_reset(long_reset if kind == "usage_7d" else short_reset, now)
