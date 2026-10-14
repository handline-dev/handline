"""The numbers behind handoff triggers and where an agent runs (design section 8.1): the wall, the context
limit, and how fast an account burns. The daemon evaluates the triggers; the screens and `hl` read these.

A working agent is never handed off or moved on a percentage. It runs to the wall - a turn that ends on the
usage limit, or a reading at 100% - because nothing is lost there and everything below it is paid for: a
session started elsewhere spends a measurable share of that account's window just getting back to where it was.
"""
from __future__ import annotations

import time

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
# agents got cheaper and refuse them for it.
MIN_STRETCH_S = 30 * 60

# Most agents mid-turn on one account at once (Pro). Set per account in config.
DEFAULT_ACTIVE_CAP = 2


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
