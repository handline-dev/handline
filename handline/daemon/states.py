"""The timing constants the daemon runs on."""
from __future__ import annotations


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
# spending we can see. `hl fold`'s `claude -p` and anything a human runs by
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
MAX_THROTTLE_STREAK = 5      # 8.6: then a decision task for the founder

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
# Once an account is over its plan's usage limit and Claude Code is drawing on
# usage credits, the main conversation drops to the same five-minute TTL as API
# billing. Where a session's transcript is readable that is followed exactly
# (`session.cachettl` reads the life off the last response); these two numbers
# are what is used where it is not.
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
