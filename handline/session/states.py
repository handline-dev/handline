"""The states an agent is in, and what wakes each."""
from __future__ import annotations

from ..infra import timefmt


# Section 8.2 states.
IDLE, QUEUED, RUNNING, DONE = "IDLE", "QUEUED", "RUNNING", "DONE"
HANDOFF, DEGRADED, PARKED = "HANDOFF", "DEGRADED", "PARKED"
THROTTLED, STALLED = "THROTTLED", "STALLED"

# Evaluated every tick (8.4). PARKED, THROTTLED and DEGRADED are in here
# deliberately: anything that is not STALLED must have a way back, or it
# becomes a silent terminal state and the account sits idle forever.
# RUNNING is set by the agent's own hooks, so a working agent is never treated
# as parked and never woken mid-task.
EVALUATED = {RUNNING, HANDOFF, PARKED, QUEUED, THROTTLED, IDLE, DEGRADED}

# Who or what wakes each state the daemon can write to `agent_state.state` -
# the permanent-stop guard section 8.4 warns about: a state with no way back
# sits idle forever once an agent lands in it. Checked by a test that scans
# every `set_agent_state(con, ..., <STATE>)` call in handline/*.py (this file,
# ask.py, events.py, handline/actions/, handline/cli/ - wherever one is added) and fails
# if the state it writes is not a key here. DONE is declared above but never
# written by anything, so it carries no entry and the guard does not need one.
WAKER = {
    RUNNING: "the agent's own Stop hook ends its turn - the daemon never touches a RUNNING row",
    IDLE: "would_wake, the moment a request or an answer is waiting for it",
    QUEUED: "would_wake dispatches it at once - it already has work",
    HANDOFF: "the handoff's own step commits, or expire_handoffs() times it out into DEGRADED",
    DEGRADED: "the daemon retries it itself after DEGRADED_RETRY_S",
    PARKED: "wake_at - the usage window, the async-reply fallback, or the short move recheck",
    THROTTLED: "wake_at - nothing sets it since the hourly ask cap went; a row left in it wakes at its wake_at",
    STALLED: "a person: w, behind a confirmation (unstall, folded into wake)",
}

# The marker a QUEUED or PARKED row's `detail` starts with when it is the
# move/placement machinery waiting, not a usage wall - screen_state draws
# MOVING, not PENDING.
MOVING_MARK = "moving"
# A PARKED row's own mark for the one reason `w` is worth pressing on it - a
# human parked it (`actions.park`, the trigger kind `park()` builds by hand).
BY_HAND_MARK = "by hand"


# An agent that has done nothing for this long is holding memory and nothing
# else: a resident session costs 250-500MB and no tokens at all. Closing it
# gives the machine that back, and resuming costs nothing extra - measured, a
# resumed session read its whole 68k prefix from cache exactly as a resident
# one did, because the cache lives on the server and expires on time, not on
# whether our process stayed alive.
# The round trip is small: across the recorded deliveries, a request that
# woke a closed session was told a median 13.5s after it arrived (p25 11s, p75
# 31s), against 4s for a live one, so a request every ten minutes pays about
# ten seconds each time. Raising this past PROMPT_CACHE_S would turn every
# resume into a cache miss (see RESUME_WARM_S), which is a cost change, not a
# tuning one.
IDLE_CLOSE_S = 600

# How long an agent may read as RUNNING with nothing heard from it before the
# label is stuck rather than the turn being long. A turn can be quiet for many
# minutes inside one tool call, so this is not tied to how soon idle sessions
# are closed.
STUCK_RUNNING_S = 1800


def park_wakeable(row) -> tuple[bool, str]:
    """Whether `w` on this PARKED, THROTTLED or DEGRADED row does anything,
    and why not when it does not (only a park a
    person started by hand is worth forcing early - a usage wall, a move's
    own short retry and a degraded handoff's own retry all
    clear themselves, and forcing one wakes the agent into an account or an
    account switch).
    """
    detail = row["detail"] or ""
    if detail.startswith(MOVING_MARK):
        return False, f"{row['agent']} is moving accounts - it retries on its own shortly"
    if detail.startswith(BY_HAND_MARK):
        return True, ""
    when = timefmt.stamp(row["wake_at"], timefmt.CLOCK) if row["wake_at"] else "?"
    return False, f"{row['agent']} clears on its own - starts {when} when the window rolls over"
