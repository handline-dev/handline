"""The states an agent is in, what wakes each, and the timing constants the daemon runs on."""
from __future__ import annotations


from ..team import ask as askmod
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
# every `set_agent_state(con, ..., <STATE>)` call in ocp/*.py (this file,
# ask.py, events.py, ocp/actions/, ocp/cli/ - wherever one is added) and fails
# if the state it writes is not a key here. DONE is declared above but never
# written by anything, so it carries no entry and the guard does not need one.
WAKER = {
    RUNNING: "the agent's own Stop hook ends its turn - the daemon never touches a RUNNING row",
    IDLE: "would_wake, the moment a request or an answer is waiting for it",
    QUEUED: "would_wake dispatches it at once - it already has work",
    HANDOFF: "the handoff's own step commits, or expire_handoffs() times it out into DEGRADED",
    DEGRADED: "the daemon retries it itself after DEGRADED_RETRY_S",
    PARKED: "wake_at - the usage window, the async-reply fallback, or the short move recheck",
    THROTTLED: "wake_at - the ask window rolls over (team.ASK_WINDOW_S)",
    STALLED: "a person: w, behind a confirmation (unstall, folded into wake)",
}

# The marker a QUEUED or PARKED row's `detail` starts with when it is the
# move/placement machinery waiting, not a usage wall - tui.screen_state draws
# MOVING, not PENDING.
MOVING_MARK = "moving"
# A PARKED row's own mark for the one reason `w` is worth pressing on it - a
# human parked it (`actions.park`, the trigger kind `park()` builds by hand).
BY_HAND_MARK = "by hand"


def awaiting_reply(row) -> bool:
    """Is this PARKED row the one an async request writes while its asker waits for the answer? The
    session is live: it is not a usage park, whatever handoff the agent last had."""
    return (row["detail"] or "").startswith(askmod.AWAIT_REPLY_MARK)


def park_wakeable(row) -> tuple[bool, str]:
    """Whether `w` on this PARKED, THROTTLED or DEGRADED row does anything,
    and why not when it does not (founder #1620/#1625/#1633: only a park a
    person started by hand is worth forcing early - a usage wall, a move's
    own short retry, an async reply and a degraded handoff's own retry all
    clear themselves, and forcing one wakes the agent into an account, an
    account switch, or a reply that still is not there).
    """
    detail = row["detail"] or ""
    if awaiting_reply(row):
        return False, f"{row['agent']} is waiting on a reply, not parked - it wakes when the reply lands"
    if detail.startswith(MOVING_MARK):
        return False, f"{row['agent']} is moving accounts - it retries on its own shortly"
    if detail.startswith(BY_HAND_MARK):
        return True, ""
    when = timefmt.stamp(row["wake_at"], timefmt.CLOCK) if row["wake_at"] else "?"
    return False, f"{row['agent']} clears on its own - starts {when} when the window rolls over"

# How long after a limit StopFailure the agent counts as settled.
LIMIT_SETTLE_S = 30

# How long a DEGRADED agent waits before the daemon retries it.
DEGRADED_RETRY_S = 600

# A park computed against an assumed account has no real ceiling to sleep to
# - the shortest window's reset belongs to an account this agent may not even be on - so it
# gets a short re-look instead of a multi-hour wait, on the chance that the
# next tick has a recorded or reported account to judge it by instead.
ASSUMED_ACCOUNT_RECHECK_S = 300

# An account with no reading and no credentials asks `logged_in` every tick
# for ever otherwise - on macOS a keychain subprocess apiece. A cooldown of its
# own, so that an account which does log in is not left waiting on it.
LOGIN_CHECK_COOLDOWN_S = 300

# The shortest gap between two asks about one account (`usage_api`, D11). One
# account per tick, so a slow endpoint costs the tick one timeout and not five.
USAGE_POLL_S = 120

# And the longest. Between the two, an account is asked only when something
# could have changed: a turn on it since the last reading (`db.last_account_
# activity`), a window that has ended, or no reading at all. That makes an idle
# fleet cost nothing instead of a request every two minutes - but only for the
# spending we can see. `ocp fold`'s `claude -p` and anything a human runs by
# hand leave no event, so this cadence is kept whatever the events say.
USAGE_MAX_AGE_S = 900

# `usage_api.read(refresh=True)` starts the Claude CLI to renew the account's
# token, which the poll above must not do every time. The token lasts about
# eight hours, so a renewal is only tried when the request actually failed, and
# then at most this often - otherwise a logged-out account pays a CLI start
# every two minutes for ever.
USAGE_REFRESH_COOLDOWN_S = 900

# poll_usage backs a failing account off by USAGE_POLL_S * 2**(streak-1),
# capped here, rather than retrying it every USAGE_POLL_S forever - both so a
# persistently-failing account does not keep costing a request every two
# minutes, and so it stops eating the one-success-per-tick budget every
# account behind it in list_accounts order depends on (#1811).
USAGE_FAIL_BACKOFF_CAP_S = 1800

MAX_FAILED_ATTEMPTS = 3      # 8.5: real failures only
MAX_THROTTLE_STREAK = 5      # 8.6: then escalate to a human gate

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

# A subscription's main-conversation requests keep their prompt cache for one
# hour, and every read restarts that clock; on API-key or cloud billing it is
# five minutes (Anthropic's prompt-caching table, #1065). Every account here is
# a subscription login - checked, not assumed (auditor's #1223):
#   claude -p "hi" --output-format json | jq .usage.cache_creation
#   -> ephemeral_1h_input_tokens: 15819, ephemeral_5m_input_tokens: 0
# One condition this does not cover: once an account is over its plan's usage
# limit and Claude Code is drawing on usage credits, the main conversation
# drops to the same five-minute TTL as API billing. ocp mostly avoids this by
# refusing at the wall before spending credits (the 5h and 7d-at-100% checks),
# so the window is an account that is over its limit but not yet walled - rare
# rather than absent, and RESUME_WARM_S below is wrong by a factor of twelve
# for exactly that window.
PROMPT_CACHE_S = 3600

# A closed session resumes only while its cache can still serve it. The clock is
# time since the agent's last request; the margin under the hour is the resume
# itself reaching its first request. Past it a resume re-reads the whole
# transcript uncached (~330k measured) while a fresh conversation reads only
# the handoff, which carries the request in hand, the transcript's path and the
# worktree's state - taken at the wake when the close did not take one; see
# `cache_expired`.
RESUME_WARM_S = PROMPT_CACHE_S - 300

# How long an agent may read as RUNNING with nothing heard from it before the
# label is stuck rather than the turn being long. A turn can be quiet for many
# minutes inside one tool call, so this is not tied to how soon idle sessions
# are closed.
STUCK_RUNNING_S = 1800
HANDOFF_DEADLINE_S = 90      # 8.3

# 8.6: a repeatedly-degrading agent backs off rather than retrying at the same
# cadence forever. Doubling from a minute, capped so it always comes back.
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600

# A handoff clears the agent's context, so two in quick succession is never right -
# the usage that triggered the first cannot have meaningfully changed. This is a circuit
# breaker, not a policy: if some other path ever un-parks an agent the way
# SessionStart once did, this stops it from burning tokens in a loop.
MIN_HANDOFF_INTERVAL_S = 120


def backoff_for(streak: int) -> int:
    """Seconds to wait before retrying, given how many attempts have failed."""
    if streak <= 0:
        return 0
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (streak - 1)))
