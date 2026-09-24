"""The daemon (design section 8). The part that makes this worth running.

Four rules from the design shape this loop.

Triggers are conditions, not edges (8.1). Every tick re-derives the world from
persisted state, so a crossing that happened while the daemon was dead is still
acted on when it comes back.

Everything gets evaluated, not just what is running (8.4). If only dispatched
agents were considered, then once everything is parked there is nothing left to
evaluate, and the event that would have resumed them can never fire. The system
would sit still with empty accounts forever.

Reconciliation runs unconditionally at startup (8.4). One pass covers triggers
missed while down and lost timers at the same time. In-memory timers are an
optimization and never the source of truth.

Handoff progress is committed per step (8.9), so a restart resumes from the
step it reached. An agent with an in-flight handoff is excluded from trigger
evaluation, and a second trigger merges into the existing one as an added
reason rather than starting a competing handoff.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import (accounts, agents, ask as askmod, blocked, burnrate, collect, db, events,
               fold as foldmod, handoff as handoffmod, launch, load, lock, measure,
               observe as obs, orca, paths, recover as recovermod, service,
               spawn as spawnmod, status, textsafe, tokenlog, triggers, trust, usage_api)
from .yamlcompat import ConfigError, dump

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
# ask.py, events.py, actions.py, cli.py - wherever one is added) and fails
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


def park_wakeable(row) -> tuple[bool, str]:
    """Whether `w` on this PARKED, THROTTLED or DEGRADED row does anything,
    and why not when it does not (founder #1620/#1625/#1633: only a park a
    person started by hand is worth forcing early - a usage wall, a move's
    own short retry, an async reply and a degraded handoff's own retry all
    clear themselves, and forcing one wakes the agent into an account, an
    account switch, or a reply that still is not there).
    """
    detail = row["detail"] or ""
    if detail.startswith(askmod.AWAIT_REPLY_MARK):
        return False, f"{row['agent']} is waiting on a reply, not parked - it wakes when the reply lands"
    if detail.startswith(MOVING_MARK):
        return False, f"{row['agent']} is moving accounts - it retries on its own shortly"
    if detail.startswith(BY_HAND_MARK):
        return True, ""
    when = time.strftime("%H:%M", time.localtime(row["wake_at"])) if row["wake_at"] else "?"
    return False, f"{row['agent']} clears on its own - starts {when} when the window rolls over"

# How long after a limit StopFailure the agent counts as settled.
LIMIT_SETTLE_S = 30

# How long a DEGRADED agent waits before the daemon retries it.
DEGRADED_RETRY_S = 600

# A park computed against an assumed account has no real ceiling to sleep to
# - `reset_5h` belongs to an account this agent may not even be on - so it
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


# ---------------------------------------------------------------- the lock
# The name of the hold that makes this a single-instance program. One per
# OCP_HOME, so two installations on one machine still supervise their own.
DAEMON_LOCK = "daemon"


def running_elsewhere() -> int | None:
    """PID of the daemon supervising this OCP_HOME, or None when none is.

    Asked of the lock that refuses a second daemon (`lock.holder`), so a daemon
    is reported exactly when it is the one holding it. One that holds the lock
    without having written its pid - started before the lock recorded it - is
    named from the process table instead; only one that is in neither reads as
    None.
    """
    pid = lock.holder(DAEMON_LOCK)
    if pid is None:
        return None
    return pid or next(iter(processes()), None)


def _supervises_this_home(pid: int) -> bool:
    """Is that daemon ours, or another installation's?

    The lock is per OCP_HOME, so two homes on one machine each run their own
    daemon and neither is a duplicate. Compared as directories, not as
    spellings: /tmp and /private/tmp are one place on macOS, and an unresolved
    comparison answers "somebody else's" about our own duplicate.
    """
    env = obs.process_env(pid)
    if env is None:
        # Unreadable is the one worth naming: a supervisor we cannot place is
        # exactly the one silence let run twice.
        return True
    # Read, but not set: `ocp launch` (and launchd) set OCP_HOME, but a daemon
    # started by hand usually does not, and that process still runs against
    # the default home - not against ours just because we could not name one.
    home = None
    for token in env.split():
        if token.startswith("OCP_HOME="):
            home = token.split("=", 1)[1]
            break
    home_path = Path(home).expanduser() if home else paths.default_home()
    return os.path.realpath(home_path) == os.path.realpath(paths.home())


def processes() -> list[int]:
    """PIDs of every `ocp daemon` supervising this OCP_HOME, this one aside.

    The lock refuses the next duplicate; it cannot end one that was already
    running when it arrived, and the lock names only its holder. The process
    table is the only place both of them appear.
    """
    mine = os.getpid()
    found = []
    for pid, cmd in obs.process_table() or []:
        argv = cmd.split()
        # The program being run: our own path, `daemon` next, and both at the
        # front of the command line. Anywhere else in it is somebody talking
        # about us - a `grep -n ocp daemon`, an editor on this file, a session
        # whose system prompt quotes the command. `service.build_plist` sets
        # ProgramArguments to exactly [ocp_executable(), "daemon"], so
        # launchd's own line never needs more than this.
        if not any(argv[i].endswith("/ocp") and argv[i + 1] == "daemon"
                   for i in range(min(2, len(argv) - 1))):
            continue
        if pid != mine and _supervises_this_home(pid):
            found.append(pid)
    return found


# ---------------------------------------------------------------- reconcile
@dataclass
class Tick:
    now: int
    actions: list[str]


def _record_handoff(con, agent: str, hid: str, log) -> bool:
    """Take the handoff record and commit the step. False when it is degraded,
    which is only ever because a fact it holds could not be read.

    Does not touch the throttle streak on success: the record is only half of
    a handoff, and resetting here would wipe out a failure the clear that
    follows is about to add. The caller resets once the whole handoff - clear
    included - is known to have landed clean.
    """
    missing = handoffmod.record(con, agent)
    if not missing:
        db.advance_handoff(con, hid, "recorded")
        return True
    why = "could not read " + ", ".join(missing)
    db.advance_handoff(con, hid, "recorded", state="degraded", detail=why[:300])
    db.bump_counter(con, agent, "throttle_count")
    log(f"  {agent}: handoff degraded ({why})")
    return False


def resume_handoff(con, row, log) -> None:
    """Finish a handoff from the step it reached (8.9).

    Steps are ordered requested -> recorded -> extracted -> released, and each is
    committed, so a restart picks up where the kill landed instead of leaving
    the agent wedged in HANDOFF. A row written before the record was
    deterministic may say `folded` for the second step.
    """
    agent, step, hid = row["agent"], row["step"], row["id"]
    done_ok = True
    if step == "requested":
        done_ok = _record_handoff(con, agent, hid, log)
        # Reached at step "requested", so the kill landed before the record -
        # before the clear too. This path has no _reclaim_context of its own
        # and never runs one, so a good record is the whole of what it can
        # vouch for - same streak reset the record used to give itself before
        # a clear could still fail after it.
        if done_ok:
            db.reset_counter(con, agent, "throttle_count")
    if step in ("requested", "recorded", "folded"):
        db.advance_handoff(con, hid, "extracted")

    reading = agents.account_reading(agent, con)
    # Same rule as a fresh park's ceiling (see _perform_handoff): an unknown
    # account, or a forced move - which never hits a usage wall, whatever
    # account it names - is the move machinery still working, not a wall to
    # sleep out.
    kind = row["trigger_kind"] or ""
    short_retry = reading is None or reading.source == "assumed" or kind == "forced"
    if short_retry:
        wake = int(time.time()) + ASSUMED_ACCOUNT_RECHECK_S
    elif kind in USAGE_TRIGGERS or kind == BY_HAND_MARK:
        rst = status.read(reading.account)
        wake = triggers.wake_after(kind, rst.reset_5h, rst.reset_7d)
    else:
        # Not a usage wall and not a park a person asked for: whatever this
        # trigger fired on (context) is already handled by the clear
        # above, so there is nothing left to wait for - but the wake is still
        # what hands the agent its own record back (`wake_agent`/`wake_prompt`),
        # so this parks with an immediate wake rather than skipping it (#1957).
        wake = int(time.time())
    db.advance_handoff(con, hid, "released", state="done" if done_ok else "degraded")
    # Same rule as a fresh park's label (see _perform_handoff): a hand park
    # resumed after a restart is still one a person may force early.
    label = BY_HAND_MARK if kind == BY_HAND_MARK else (
        MOVING_MARK if short_retry else "resumed handoff")
    db.set_agent_state(con, agent, PARKED, wake_at=wake,
                       detail=f"{label}; wakes at "
                              f"{time.strftime('%H:%M', time.localtime(wake))}")
    # Same gap as a fresh park's - see _perform_handoff - through a different
    # path: a restart recovering an in-flight handoff parks the agent too.
    db.add_event(con, agent, "park", {
        "kind": kind, "wake_at": wake,
        "account": reading.account if reading else None,
        "account_source": reading.source if reading else None,
    })
    log(f"  {agent}: resumed handoff from '{step}' -> PARKED")


# ---------------------------------------------------------------- sending
# What the sweep saw this tick, so the paths that type into a terminal can ask
# without paying for their own process listing.
_seen: dict[str, obs.Observed] = {}
_seen_at = {"t": 0.0}

# How old that reading may be before a send looks again. A tick is 2s, so
# anything older came from another tick or from no tick at all - `ocp ask`
# delivers through here too - and one listing is cheap next to what a stray
# send does.
SEEN_FRESH_S = 10


def session_present(agent: str) -> bool:
    """Is there a session to type into, or only the shell it left behind?

    Orca runs claude as a login shell's child, so when the session ends the
    terminal stays and our handle keeps working - `send` succeeds and the text
    lands at a bash prompt. Handoffs and notices are not inert there: backticks
    run, `>` redirects, and the agent is not the one reading any of it.

    Fail-closed. Only a process we positively saw is a yes; "the process table
    could not be read" is a no, because the whole point is to stop sending on a
    maybe.
    """
    o = _seen.get(agent)
    if o is not None and time.time() - _seen_at["t"] <= SEEN_FRESH_S:
        return bool(o.session_seen and o.session_pid is not None)
    sessions = obs.live_sessions()
    return bool(sessions is not None and sessions.get(agent))


# How long a session told `/exit` has to leave the process table. SessionEnd
# fires at once and the process can outlive it by up to a sweep tick (gone by the
# next sweep, 31s later, in the one case measured), and the sweep reads a closed
# row over a live process as "running again" and clears the mark.
END_WAIT_S = 60
# How long after typing `/exit` to look for the dialog it may raise.
EXIT_DIALOG_WAIT_S = 6
END_POLL_S = 1


def _wait_session_gone(agent: str) -> bool:
    """Is this agent's session out of a fresh reading of the process table?

    Always a fresh reading - the sweep's snapshot is what read the old process
    as alive. An unreadable table is a no, the same fail-closed rule as
    `session_present`: not knowing that it ended is not evidence that it did.
    """
    deadline = time.time() + END_WAIT_S
    while True:
        sessions = obs.live_sessions()
        if sessions is not None and not sessions.get(agent):
            _seen.pop(agent, None)
            return True
        if time.time() >= deadline:
            return False
        time.sleep(END_POLL_S)


# How long a relaunched session has to show up in the process table. `ocp
# launch` execs claude, so it appears at once when it appears at all; this is
# slack for a slow start, not a wait for the TUI to be usable.
WAKE_SESSION_TIMEOUT_S = 15


def wait_for_session(agent: str, timeout_s: int, prompt: Path | None = None) -> bool:
    """Did the launch we typed actually start a session?

    `relaunch` reports on having sent the command, which is not the same thing:
    `ocp launch` refuses over the 5h limit and exits when the account is not
    logged in, leaving the shell exactly as it was. The reading is taken here
    rather than from the tick's cache, which predates the command.

    With `prompt`, the session counts only once that file is gone: the launch
    removes it just before it becomes the session. A launch typed by someone
    else starts a session without taking it, and counting that one as ours
    loses the prompt.
    """
    deadline = time.time() + timeout_s
    while True:
        sessions = obs.live_sessions()
        if (sessions is not None and sessions.get(agent)
                and not (prompt and prompt.exists())):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


def _past_dialog(con, agent: str, log, only_limit: bool = False) -> str:
    """Get past a dialog before typing an exit or a clear at this agent: '' when
    nothing stands in the way, else why. The caller holds the agent's terminal.
    While a reason stands, when this dialog first proved unpassable is kept, by
    its title: a different dialog starts its own clock, so a caller that can
    wait measures the wait of the dialog that is there."""
    why, title = blocked.past_dialog(con, agent, log, only_limit=only_limit)
    since_key, title_key = f"dialog_since:{agent}", f"dialog_title:{agent}"
    if not why:
        db.clear_cursor(con, since_key)
        db.clear_cursor(con, title_key)
        return ""
    # A dialog whose title did not parse cannot be told from another one, so it
    # is never the same as the last: its clock restarts, which errs toward
    # not stalling.
    mark = int(hashlib.sha1(title.encode()).hexdigest()[:8], 16) if title else 0
    if not mark or db.get_cursor(con, title_key) != mark or not db.get_cursor(con, since_key):
        db.set_cursor(con, title_key, mark)
        db.set_cursor(con, since_key, int(time.time()))
    return why


def _stalled_on_dialog(con, agent: str, why: str, log) -> bool:
    """A dialog ocp cannot get past, standing as long as a block takes to be
    raised with the founder, stops the agent: it is reported with the reason
    and left alone until a person clears it, not retried every tick."""
    since = db.get_cursor(con, f"dialog_since:{agent}")
    if not since or int(time.time()) - since < blocked.ESCALATE_AFTER_S:
        return False
    db.set_agent_state(con, agent, STALLED, detail=f"cannot get past a dialog: {why}")
    log(f"  {agent}: STALLED - cannot get past a dialog: {why}")
    return True


def _reclaim_context(con, agent: str, log) -> tuple[bool, str]:
    """Clear the agent's session so the handoff actually costs it its context.

    Called only from inside `perform_handoff`, which already holds this agent's
    terminal - taking it again here would fail rather than nest.
    """
    handle = db.get_terminal(con, agent)
    if not handle:
        return False, ("no terminal recorded; start the agent with `ocp spawn` or "
                       "`ocp launch` inside an Orca terminal so its context can be reclaimed")
    if not orca.available():
        return False, "orca is not on PATH"
    if not session_present(agent):
        return False, f"no session process; not typing {orca.clear_command(handle)} at a shell prompt"
    if _past_dialog(con, agent, log):
        return False, ("a dialog is open on its screen that ocp cannot get past; "
                       f"not typing {orca.clear_command(handle)} at it")
    running = orca.background_work(handle)
    if running:
        return False, (f"background work is running ({running}); "
                       f"not typing {orca.clear_command(handle)} at it")
    ok, why = orca.clear_context(handle, timeout_ms=orca.IDLE_PROBE_MS)
    log(f"  {agent}: {why}")
    return ok, why


def wall_reason(con, agent: str, row=None) -> str | None:
    """Why typing at this agent's open session would only spend quota, or None.

    A turn that ended on the usage limit, or a park that a usage trigger put
    it in, means the account cannot run one: what is typed starts a turn that
    fails at once, marks the request delivered, clears the very mark that
    says the agent is at the wall - and the next ask starts it over, each
    round counting as a degraded handoff. Checked where the keystrokes are
    sent, so no caller - `supervise`, `ocp ask`, `ocp resolve` - can skip it.
    """
    if events.at_limit(con, agent):
        return f"{agent} is at its usage limit"
    row = row if row is not None else db.get_agent_state(con, agent)
    if row and row["state"] == PARKED and not row["closed_at"]:
        last = db.last_handoff(con, agent)
        if last and last["trigger_kind"] in MOVABLE_TRIGGERS:
            return f"{agent} is parked until its usage window rolls over"
    return None


def _live_session(con, agent: str) -> str | None:
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    return st.session_id


def _session_changed(con, agent: str) -> bool:
    """Is the session on screen a different one from the one the head was typed into?

    A delivery belongs to the session that read it. A resume keeps its id, so
    it is not told twice; a fresh start has a new one and has read nothing. An
    id that cannot be read is not a difference - nothing is retyped on a guess.
    """
    live = _live_session(con, agent)
    return bool(live) and live != db.get_notified_session(con, agent)


# #1973, measured in daemon.out.log: a notify and a context handoff fired in
# the same tick, against the same agent - the handoff read idle from a probe
# taken before the notify landed, and typed /clear into the session the
# notify had just started, which sat unsent behind the turn instead of
# running. `lock.terminal` alone does not close this: it only keeps two
# writers from holding the terminal at once, and the notify had already
# released it by the time the handoff took it. The screen itself lags a
# moment behind a line that just landed - Orca's own idle probe can still
# read "idle" for a beat after Enter is sent - so a *destructive* type (one
# that throws work away if it lands mid-turn: a handoff's /clear or /exit)
# may not follow any type at the same agent, from any path or process,
# within this long - ordinary notifies are not held to it: the vendor this
# was measured on (#1973, Claude Code) keeps text typed mid-turn and runs it
# after the turn ends, rather than discarding it, which is what the incident
# above shows. A vendor that discards or interrupts on a mid-turn type would
# need notifies held to the window too; that has not been measured for any
# other vendor.
TYPE_SETTLE_S = 15


def _type_gate(con, agent: str, handle: str | None, *, require_idle: bool = True,
               require_settled: bool = False) -> str | None:
    """The re-read every path that types at an agent's terminal must do once
    its lock is actually held (see #1973 above) - never trust a reading taken
    before asking for the lock. None means clear to type; anything else is
    why not.

    `require_idle` is False at a site whose own act (`_end_session`) does its
    own fresh idle read inline, where it actually types - `orca.typeable_now`
    here would only be a second, redundant read of the same thing.

    `require_settled` is True wherever the type that follows is destructive
    (a handoff's `/clear` or `/exit`) or ends a session (a close, a parked
    move): the settle window above is what catches a stale idle reading that
    `typeable_now`, read fresh a moment too soon, still cannot.
    """
    if require_settled:
        last = db.get_cursor(con, f"typed_at:{agent}")
        if last and int(time.time()) - last < TYPE_SETTLE_S:
            return f"{agent} was typed at moments ago; letting the screen catch up"
    if require_idle and handle and orca.available() and not orca.typeable_now(handle):
        return f"{agent} is mid-turn or has a dialog open"
    return None


def _mark_typed(con, agent: str) -> None:
    """Called after every successful type at `agent`'s terminal - the other
    half of `_type_gate`'s settle window."""
    db.set_cursor(con, f"typed_at:{agent}", int(time.time()))


def deliver_queued(con, agent: str, log) -> bool:
    """Tell an agent about the head of its own queue (`db.head_of_queue`) -
    and only the head, per request #189. `ocp ask` writes a row and puts a
    file in the target's inbox; neither reaches the target's screen, which is
    why this exists at all. Told once per head: the cursor holds the id last
    announced, so a reminder only goes out once the head actually changes.
    """
    head = db.head_of_queue(con, agent)
    if not head:
        return False
    if db.get_cursor(con, f"notify:{agent}") == int(head["id"]) \
            and not _session_changed(con, agent):
        return False
    if wall_reason(con, agent):
        return False              # stays queued; told when it wakes or moves
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available() or not orca.alive(handle):
        return False
    if not session_present(agent):
        # Nothing here is read by an agent that is not running; at a shell
        # prompt this line is four commands in backticks and a redirection.
        # Leaving it queued loses nothing: the sweep records the agent closed
        # within a tick, and work arriving at a closed agent wakes it.
        return False
    # Typing at an agent mid-turn leaves the text sitting in its input box
    # unsent, which is how three agents once came up having been told nothing.
    if not orca.typeable_now(handle):
        return False
    depth = db.queue_depth(con, agent)
    with lock.terminal(agent) as mine:
        if not mine:
            return False          # something else is mid-send; next tick
        if _type_gate(con, agent, handle):
            return False          # stale reading above; next tick
        return _tell(con, agent, head, depth, handle, log)


# An agent is asked to resolve the request at the head of its queue once it has
# been idle this long with nothing of its own outstanding. Ten minutes: a turn
# that ended and left the request open is not thinking, and a real wait on
# something that is not an ask (a long job) can still say so when it is asked;
# shorter would nag an agent between two turns of a task.
NUDGE_AFTER_S = 600
# Once reminded, an agent still idle on the request this long after it last did
# anything is what the human is shown. The nudge is typed as a prompt, so the
# agent's answer to it counts as activity and this runs from there. Thirty
# minutes is the longest silence the daemon calls healthy (STUCK_RUNNING_S), so
# the screen and the RUNNING label agree on what stuck is.
FORGOTTEN_STUCK_S = STUCK_RUNNING_S
# A nudge that could not be typed (mid-turn, a modal) is not asked again for
# this long, because the idle probe that says no costs its whole timeout.
NUDGE_RETRY_S = 60


def track_background_work(con, agent: str, row) -> None:
    """Record whether an open session's own footer reports work still running
    behind its prompt, in whatever state it is waiting.

    A session with such work is not idle: the display shows it working, and
    nothing that acts on idleness (the resolve nudge, the idle close) acts on
    it. The session reports this, so it is read every tick, never assumed from
    a clock. When the footer stops reporting it, the idle clock starts there
    (`idle_since`), not at its last hook event; the end is not a model turn, so
    it stays out of `db.last_activity`, which the prompt cache is judged by."""
    if not row or row["state"] in (RUNNING, HANDOFF, STALLED) or row["closed_at"] \
            or not row["terminal"] or not orca.available():
        return
    running = orca.background_work(row["terminal"])
    key = f"bgwork:{agent}"
    if running is None:
        return                # unreadable is not "ended"
    if running and not db.get_cursor(con, key):
        db.set_cursor(con, key, 1)
    elif not running and db.get_cursor(con, key):
        db.clear_cursor(con, key)
        db.add_event(con, agent, "background_work_ended", {})


def idle_since(con, agent: str) -> int | None:
    """When this agent last did anything, or its background work last ended."""
    times = [t for t in (db.last_activity(con, agent),
                         db.last_event_ts(con, agent, "background_work_ended")) if t is not None]
    return max(times) if times else None


def has_background_work(con, agent: str) -> bool:
    return bool(db.get_cursor(con, f"bgwork:{agent}"))


def forgotten_resolve(con, agent: str, row, now: int) -> dict | None:
    """The head of this agent's queue, if it looks like a resolve nobody wrote.

    IDLE or QUEUED with a live terminal, the head delivered and told and
    still open, and nothing the agent asked that is still unanswered - which
    is what separates a request forgotten from one being waited on, since an
    agent waiting on an answer is also IDLE (or QUEUED) with its head open.
    Idle time counts from the later of its last activity and the delivery.

    QUEUED counts as IDLE here: it only means something is queued, and only a
    fresh prompt clears it, so a QUEUED agent whose head was told in its
    current session is the same forgotten resolve. `blocked.work_waiting`
    reads this function, so it also decides whether that agent is reported.
    """
    if not row or row["state"] not in (IDLE, QUEUED) or row["closed_at"] or not row["terminal"]:
        return None
    if has_background_work(con, agent):
        return None           # not idle: work it started is still running
    head = db.head_of_queue(con, agent)
    if not head or not head["delivered_at"] \
            or db.get_cursor(con, f"notify:{agent}") != int(head["id"]):
        return None
    if db.open_asks_of(con, agent) or db.unannounced_answers(con, agent):
        return None
    idle = now - max(int(head["delivered_at"]), idle_since(con, agent) or 0)
    return {"head": head, "idle_s": idle, "behind": db.queue_depth(con, agent) - 1}


def nudge_forgotten_resolve(con, agent: str, row, now: int, log) -> bool:
    """Type one reminder at an agent that has left its head request open.

    Never resolves for it: the verdict is the agent's to write. Once per
    request, gated like a delivery (not at the wall, nothing typed at a modal or
    a working agent), and nothing here touches an attempt counter, so it cannot
    stall an agent.
    """
    found = forgotten_resolve(con, agent, row, now)
    if not found or found["idle_s"] < NUDGE_AFTER_S:
        return False
    head = found["head"]
    if db.get_cursor(con, f"nudge:{agent}") == int(head["id"]) \
            or now - db.get_cursor(con, f"nudge_try:{agent}") < NUDGE_RETRY_S:
        return False
    db.set_cursor(con, f"nudge_try:{agent}", now)
    handle = db.get_terminal(con, agent)
    if wall_reason(con, agent) or not handle or not orca.available() \
            or not orca.alive(handle) or not session_present(agent) \
            or not orca.typeable_now(handle):
        return False
    behind = found["behind"]
    with lock.terminal(agent) as mine:
        if not mine:
            return False
        if _type_gate(con, agent, handle):
            return False
        what = (f"It asks nothing - `ocp dismiss {head['id']}` when read."
                if head["kind"] == "report" else
                f"If the work is done, `ocp resolve {head['id']} \"<verdict>\"`; if it is "
                f"not, say what you are waiting on.")
        ok, _ = orca.send(
            handle,
            f"#{head['id']} was delivered to you and is still open, and you have been "
            f"idle {status.ago(found['idle_s'])}"
            + (f" with {behind} more waiting behind it" if behind else "")
            + f". {what}")
        if ok:
            _mark_typed(con, agent)
    if ok:
        db.set_cursor(con, f"nudge:{agent}", int(head["id"]))
        db.add_event(con, agent, "nudged", {"request_id": head["id"],
                                            "idle_s": found["idle_s"], "behind": behind})
        log(f"  {agent}: idle {status.ago(found['idle_s'])} with #{head['id']} open and "
            f"nothing outstanding - nudged to resolve it")
    return ok


def notify_cancelled(con, row, verdict: str) -> tuple[bool, str]:
    """Type at an agent that a request it was handed has been closed under it.

    It may be mid-work on it, so it is told the way a delivery is - the same
    gates - and (typed, why not) says whether that happened. Not telling it is
    safe: the row is closed, so its own `ocp resolve` of it is refused.
    """
    agent = row["target"]
    handle = db.get_terminal(con, agent)
    if wall_reason(con, agent):
        return False, f"{agent} is at its usage limit"
    if not handle or not orca.available() or not orca.alive(handle):
        return False, f"{agent} has no terminal"
    if not session_present(agent):
        return False, f"{agent} has no running session"
    if not orca.typeable_now(handle):
        return False, f"{agent} is mid-work or at a prompt"
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"something else is typing at {agent}"
        why = _type_gate(con, agent, handle)
        if why:
            return False, why
        ok, _ = orca.send(
            handle,
            f"#{row['id']} from {row['requester']} was cancelled: {verdict}. Stop work "
            f"on it; it is closed and needs no resolve. `ocp read {row['id']}` shows it.")
        if ok:
            _mark_typed(con, agent)
    return ok, "" if ok else "the send failed"


def _answer_verb(head) -> str:
    """How the agent closes what it was just told: a report asks nothing, so
    it is dismissed, not answered."""
    if head["kind"] == "report":
        return f"it asks nothing - `ocp dismiss {head['id']}` when read"
    return f"answer with `ocp resolve {head['id']} \"<verdict>\"`"


def _tell(con, agent: str, head, depth: int, handle: str, log) -> bool:
    more = depth - 1
    body = head["body"] or "(empty)"
    # Capped at 400; `ocp read` has the rest.
    shown = body if len(body) <= 400 else body[:400] + "…"
    tail = (f"\n{more} more waiting - the next comes when you resolve or dismiss this one."
            if more else "")
    # Before the send, not after: this is what closes withdraw/replace's race
    # against delivery (request #189). A send that then fails leaves the row
    # marked anyway - its requester can no longer withdraw it, but the cursor
    # below is not advanced, so the next tick retries the same head.
    if not db.mark_delivered(con, int(head["id"])):
        # Retired (withdrawn/replaced) in the gap between head_of_queue and
        # here. Typing a dead request into the target's terminal would be
        # worse than skipping it; the next tick picks up whatever is now head.
        return False
    ok, _ = orca.send(
        handle,
        f"#{head['id']} from {head['requester']}, waiting for you:\n{shown}{tail}\n"
        f"`ocp read {head['id']}` shows it whole; {_answer_verb(head)}, "
        f"or `ocp inbox` any time.")
    if ok:
        _mark_typed(con, agent)
        db.set_cursor(con, f"notify:{agent}", int(head["id"]))
        db.set_notified_session(con, agent, _live_session(con, agent))
        db.add_event(con, agent, "notified", {"request_id": head["id"], "more_waiting": more})
        log(f"  {agent}: told about #{head['id']}" + (f" ({more} more waiting)" if more else ""))
    return ok


# How much of the original question an answer line quotes.
ASKED_SHOWN = 40


def answer_line(r) -> tuple[str, bool]:
    """One answered request as the line typed at its asker, and whether the
    answer has more than that line to read. A dismissal is said as a label.

    The start of the question rides along: a bare number is not something an
    agent can place once it has asked several things."""
    text = db.shown_verdict(r) or ""
    said = (text.splitlines() or [r["state"]])[0]
    asked = textsafe.oneline(r["body"] or "").strip()
    if len(asked) > ASKED_SHOWN:
        asked = asked[:ASKED_SHOWN].rstrip() + "…"
    head = f"#{r['id']} (you asked: {asked})" if asked else f"#{r['id']}"
    return f"{head} {r['target']}: {said[:160]}", len(said) > 160 or "\n" in text


def deliver_answers(con, agent: str, log) -> bool:
    """Tell an agent what came back from something it asked.

    Answering wrote the verdict on the row and stopped there, so the agent that
    asked - and that may have parked waiting - was never told. The founder
    answering an escalation on the screen, and the answer never reaching the
    agent it was for, is the same hole from the other end.

    Told once, and marked on the row rather than counted off with a cursor: two
    answers can come back in either order, and a high-water mark then loses the
    older one for good. A refusal counts as an answer - it is the one that means
    do something else - while a `denied` ask was refused by routing before
    anything was sent, and the agent read that reason on its own terminal.
    """
    answered = db.unannounced_answers(con, agent)
    if not answered:
        return False
    if wall_reason(con, agent):
        return False              # stays unannounced; told when it wakes or moves
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available() or not orca.alive(handle):
        return False
    if not session_present(agent):
        return False
    if not orca.typeable_now(handle):
        return False
    shown, rest = answered[-3:], answered[:-3]
    lines, cut = [], False
    for r in shown:
        line, more = answer_line(r)
        cut = cut or more
        lines.append(line)
    if rest:
        # Named, not counted: every id that is marked told has to have been
        # said, or this is the same silence with a number on it.
        lines.append("also answered: " + ", ".join(f"#{r['id']}" for r in rest))
        cut = True
    # Only when there is more to read. A one-line "ok go" followed by an
    # instruction to go and read it whole is three lines of ceremony around two
    # words, typed into an agent that was in the middle of something.
    tail = "\nRead one whole with `ocp read <id>`." if cut else ""
    with lock.terminal(agent) as mine:
        if not mine:
            return False
        if _type_gate(con, agent, handle):
            return False
        ok, _ = orca.send(
            handle, "Answers to what you asked:\n" + "\n".join(lines) + tail)
        if ok:
            _mark_typed(con, agent)
    if ok:
        db.mark_announced(con, [r["id"] for r in answered])
        db.add_event(con, agent, "answered", {"count": len(answered)})
        log(f"  {agent}: told about {len(answered)} answer(s)")
    return ok


def should_close(con, agent: str, state: str, row, now: int, st) -> bool:
    """Is this agent holding memory for nothing?

    Closing is about the machine, not the account, so the question is whether
    anyone is using this session and whether we can bring it back. Six things
    say no: it is working or being handed off, a human has to look at it,
    someone is waiting on it (unless it is PARKED), it has been
    busy recently, we cannot name the session we would resume, or a close of
    it failed within the last idle window. Anything else is a session whose
    only job right now is to occupy 250-500MB.

    The session id is the asymmetric trade: memory is recoverable the moment
    anything wants it, and a working context is not recoverable at all. Closing
    without a session id would buy back the small reversible thing by spending
    the large irreversible one.
    """
    if state in (RUNNING, HANDOFF, STALLED):
        return False
    if not row or row["closed_at"] or not row["terminal"]:
        return False
    # `deliver_queued` runs before this in the same tick, so a PARKED agent
    # that reaches here was not delivered to (walled by `wall_reason`); its queue is no reason to hold the session open.
    if has_background_work(con, agent):
        return False          # not idle: work is still running behind the prompt
    if db.queued_for(con, agent) and state != PARKED:
        return False          # work is waiting; closing would only delay it
    if not st.session_id:
        return False          # nothing to resume; losing the context costs more
    failed = db.last_event_ts(con, agent, "close_failed")
    if failed is not None and now - failed < IDLE_CLOSE_S:
        return False          # /exit did not end it; asking every tick types it every tick
    last = idle_since(con, agent)
    if last is None:
        return False          # nothing has ever been seen from it - leave it alone
    return (now - last) >= IDLE_CLOSE_S


def triggers_apply(row) -> bool:
    """Would the daemon act on this agent's triggers at all?

    A closed agent has no session to hand off, and closing is precisely what
    makes it look like one that needs handing off: its status line stops, so the
    snapshot goes stale. An agent with no terminal has nowhere to run at all -
    nothing to clear, nothing being spent - and a reading that is at the wall
    is the account's, so it sweeps up the registered-but-never-started agents
    with everyone else.

    Every screen asks this too - drawing a warning the daemon will never act on
    is the dashboard arguing with the daemon.
    """
    return bool(row and row["terminal"] and not row["closed_at"])


def would_wake(con, row) -> bool:
    """Is anything actually trying to wake this closed agent?

    Queued work, an answer it asked for, or a deadline already due while
    parked, throttled or degraded - the same three reasons `supervise` calls
    `wake_agent` on a closed row at all (section 8.4's step 2, and the two
    checks right before `deliver_queued` in the main pass). `launch.wake_wait`
    answers a narrower question - is the account full - and answers it for
    every closed agent on a walled account, including ones idle by choice
    with nothing asked of them. A screen gates on this first, so "waiting"
    only shows next to a wait that is actually happening.
    """
    if not row or not row["closed_at"]:
        return False
    agent = row["agent"]
    due = bool(row["wake_at"]) and row["wake_at"] <= int(time.time()) \
        and row["state"] in (PARKED, THROTTLED, DEGRADED)
    return bool(due or db.queued_for(con, agent) or
                db.unannounced_answers(con, agent))


def _cold_before_typing(con, agent: str, row, now: int, st, log) -> bool | None:
    """Close an open session whose cache has expired instead of typing into it.

    A line typed into it makes the vendor re-read the whole transcript
    uncached; closing it lets the wake, which starts a cold agent fresh on its
    handoff, deliver the same line. Only when there is something to type, so an
    idle session costs nothing until it is spoken to. None when the session is
    warm or there is nothing to say; otherwise whether it closed. A close that
    failed is not retried for IDLE_CLOSE_S, and that stretch types as usual: one
    uncached read beats a line that never arrives.
    A session running background work is left alone: closing would kill it.
    """
    if has_background_work(con, agent) or not cache_expired(con, agent, st.session_id, now):
        return None
    if now - db.get_cursor(con, f"cold_close_try:{agent}") < IDLE_CLOSE_S:
        return None           # a close just failed: type as usual, not held forever
    head = db.head_of_queue(con, agent)
    told = bool(head) and db.get_cursor(con, f"notify:{agent}") == int(head["id"]) \
        and not _session_changed(con, agent)
    found = forgotten_resolve(con, agent, row, now)
    nudge = bool(found) and found["idle_s"] >= NUDGE_AFTER_S \
        and db.get_cursor(con, f"nudge:{agent}") != int(found["head"]["id"])
    if not (db.unannounced_answers(con, agent) or (head and not told) or nudge):
        return None
    ok, _ = close_agent(con, agent, log, st)
    if not ok:
        db.set_cursor(con, f"cold_close_try:{agent}", now)
    if ok and nudge:
        # The wake re-tells the head, which is the reminder; closing again for
        # the same head would be one fresh turn an hour.
        db.set_cursor(con, f"nudge:{agent}", int(found["head"]["id"]))
    return ok


def close_agent(con, agent: str, log, st) -> tuple[bool, str]:
    """Give the machine back an idle agent's memory, keeping its terminal.

    This is not a handoff: no context is reclaimed, because there is nothing
    wrong with this agent's context - it is simply not being used. The session
    id is recorded so waking can resume that same session rather than starting
    a stranger in its place, and so is a handoff record: a wake that has to
    start elsewhere instead has nothing live to read it from. The id is the caller's
    reading, the same one `should_close` approved. Reading it again here would
    let the predicate check one value and the act store another, in the one
    place where the difference means closing with no way back.
    """
    with lock.terminal(agent) as mine:
        if not mine:
            return False, "someone else is typing at this agent; not closing"
        # A delivery or an answer just typed in this same tick can still read
        # idle on the very probe `_end_session` uses (#1973's settle window) -
        # require_idle=False since that probe is `_end_session`'s own to run,
        # inline, where `/exit` is actually typed.
        why = _type_gate(con, agent, db.get_terminal(con, agent),
                         require_idle=False, require_settled=True)
        if why:
            return False, why
        ok, said = _close_agent(con, agent, log, st)
        if ok:
            _mark_typed(con, agent)
        return ok, said


def _close_agent(con, agent: str, log, st) -> tuple[bool, str]:
    row = db.get_agent_state(con, agent)
    if row and row["state"] in (RUNNING, HANDOFF):
        # `should_close` refuses these, and every path that closes on a
        # schedule goes through it - but a human's key does not, and a turn can
        # start between the moment they press it and the moment they confirm.
        # Closing then throws away work nothing has recorded.
        #
        # A handoff is the exception and calls `_end_session` itself: it is the
        # one path that has already recorded the work, in its handoff record,
        # and then needs the session gone to start the next one somewhere else.
        return False, f"{agent} is {row['state']}; not closing a session mid-work"
    if not st.session_id:
        # Closing is only cheap because waking resumes the same session. With
        # no id to come back to, closing is just throwing the context away.
        return False, "no session to resume; not closing"
    # A record already waiting is the park's, and names the session from before
    # its /clear: this close must neither replace it nor, if it fails, delete it.
    # One it takes itself is kept only whole - a wake that moves elsewhere drops
    # the resume id, the only complete copy of the context.
    wrote = False
    if not db.get_handoff(con, agent):
        text, missing = handoffmod.capture(con, agent)
        if not missing:
            db.set_handoff(con, agent, text)
            wrote = True
    ok, why = _end_session(con, agent, log, st, resume=st.session_id)
    if not ok and wrote:
        db.set_handoff(con, agent, None)
    return ok, why


def _decline_exit_dialog(con, agent: str, log) -> bool:
    """The `/exit` just typed can raise a dialog offering to stop background
    work: decline it (Esc keeps the session), never choose it. True when one
    was declined; a dialog that would not be, or none, is left to the
    session-still-running report. Stops when the session is gone, or after
    EXIT_DIALOG_WAIT_S."""
    # END_WAIT_S bounds it too, which is what lets the suite run it in tenths of a second.
    deadline = time.time() + min(EXIT_DIALOG_WAIT_S, END_WAIT_S)
    handle = db.get_terminal(con, agent)
    while time.time() < deadline:
        sessions = obs.live_sessions()
        if sessions is not None and not sessions.get(agent):
            return False
        seen = orca.screen_blocker(handle) if handle else None
        if seen is not None and seen.kind == orca.MODAL:
            return not blocked.past_dialog(con, agent, log)[0]
        time.sleep(END_POLL_S)
    return False


def _end_session(con, agent: str, log, st, resume: str | None) -> tuple[bool, str]:
    """Type `/exit` at this agent, and record what may bring it back.

    `resume` is the session id to come back to, or None when nothing may: a
    session id exists only under its own account's config directory, so a close
    that precedes a move to another account must record none. Whether there is
    an id at all is the caller's question - a move does not want one - while
    whether there is a session to end is this one's, and it is asked of the
    process table rather than of a snapshot.
    """
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available():
        return False, "no terminal to close"
    if not session_present(agent):
        # `/exit` typed at the shell exits the shell, and Orca's terminal goes
        # with it - so the one path that turns a recoverable state into an
        # unrecoverable one is closing something that already closed itself.
        # There is nothing to reclaim either; the memory came back when the
        # session did whatever it did.
        return False, "no session process; nothing to close"
    # Work the session says it still runs is not ours to stop: an agent with
    # background work is not idle, whichever path is closing it.
    running = orca.background_work(handle)
    if running:
        return False, f"background work is running ({running}); not closing"
    # A dialog on screen is what `/exit` would answer instead of ending anything.
    # Past it first, so the close is one act and not a refusal the next tick
    # meets again; when it cannot be, the refusal says why.
    stuck = _past_dialog(con, agent, log)
    ok, why = orca.close_session(handle)
    if not ok and stuck:
        why = f"{why} ({stuck})"
    if not ok:
        # Refused outright - most often a dialog already on screen (ocp's own
        # earlier `/exit`, or Claude Code's own start-up modal) - is ocp's own
        # doing exactly as much as a `/exit` that was typed and never landed,
        # so it is on the list the same way: at once, not on blocked.sweep()'s
        # own next pass over this agent.
        blocked.note_close_failure(con, agent, log)
        return False, why
    # A dialog that offers to stop background work only appears now; declining
    # it is the answer, so this is a refusal and not a failed close.
    if _decline_exit_dialog(con, agent, log):
        why = "background work is running; the exit dialog was declined, session kept"
        db.add_event(con, agent, "close_failed", {"why": why, "pid": None})
        return False, why
    # Typed is not ended: the `/exit` can be eaten or slow, and a closed row
    # over a live process is cleared by the sweep, while a relaunch typed at
    # that terminal reaches the old session as a prompt. A session that does
    # not end is reported, neither retried (a second `/exit` goes to whatever
    # ate the first) nor killed (that skips its own shutdown); the sweep
    # records the close if it does end later.
    if not _wait_session_gone(agent):
        pid = (obs.live_sessions() or {}).get(agent)
        why = (f"/exit was typed but the session is still running"
               f"{f' (pid {pid})' if pid else ''} after {END_WAIT_S}s; not marked closed")
        db.add_event(con, agent, "close_failed", {"why": why, "pid": pid})
        blocked.note_close_failure(con, agent, log)
        return False, why
    db.set_closed(con, agent, resume)
    # RUNNING describes a session that exists. Leaving the label on a closed one
    # makes every screen say the opposite of what happened, and the daemon skips
    # RUNNING agents when it decides what to close next.
    row = db.get_agent_state(con, agent)
    if row and row["state"] == RUNNING:
        db.set_agent_state(con, agent, IDLE, wake_at=row["wake_at"],
                           detail="session closed while idle")
    db.add_event(con, agent, "close", {"session": st.session_id,
                                       "ctx_pct": st.ctx_pct})
    log(f"  {agent}: session closed (resume {st.session_id or 'unknown'})")
    return True, f"{agent}: session closed, terminal kept"


def cache_expired(con, agent: str, session: str | None, now: int) -> bool:
    """Should this closed agent start a fresh conversation rather than resume?

    Only when its prompt cache can no longer serve the resume: RESUME_WARM_S
    since it last did anything, or no clock at all, where a wrong "warm" costs
    the whole transcript and a wrong "cold" costs a handoff. Whether a handoff
    exists does not decide it; the wake takes one when it is missing.
    """
    if not session:
        return False
    last = db.last_activity(con, agent)
    return last is None or now - last >= RESUME_WARM_S


def wake_prompt(agent: str, resumed: bool, was_closed: bool = False,
                moved_to: str | None = None, con=None, cold: bool = False) -> str:
    """What an agent is told when it comes back.

    A resumed session already holds everything it knew, so it is told where it
    is rather than what it did. One that was parked is given its handoff here;
    one started fresh gets it in its system prompt, and is told which. The
    record is the request in hand, the session's transcript path and the
    worktree's state - the detail is in the transcript, and a session that has
    no record is told so.    """
    parts: list[str] = []
    record = None
    if moved_to:
        # A move is not a close. Its handoff was taken as its session ended, and
        # `launch.context_for` has already put it in this session's system
        # prompt.
        parts.append(f"Your account was at its 5h limit, so this session was started on "
                     f"{moved_to} instead of waiting for that window to roll over. Your "
                     f"handoff is in your system prompt above. Carry on from there.")
    elif resumed:
        parts.append("Your session was closed while you were idle and has been resumed. "
                     "Nothing was lost; carry on.")
    elif cold:
        # The handoff is in the system prompt, and carries the transcript's
        # path: nothing more is appended for it below.
        record = db.get_handoff(con, agent)
        parts.append(f"Your session was closed while you were idle for over "
                     f"{RESUME_WARM_S // 60} minutes - within {PROMPT_CACHE_S // 60} "
                     f"minutes of its prompt cache's life, the margin a resume needs to "
                     f"reach its first request before that cache is gone - so this is a "
                     f"fresh conversation: resuming risks re-reading the whole transcript "
                     f"at full price. Your handoff is in your system prompt above - the "
                     f"request you had in hand, your previous transcript's path and your "
                     f"worktree. Pick the task back up from it.")
    elif was_closed:
        # Taking the parked branch here would tell an agent that was never
        # parked that its context was cleared. What the close recorded is in
        # the system prompt of the session it started, if it recorded one.
        parts.append("Your session was closed while you were idle and could not be "
                     "resumed, so this is a fresh one. "
                     + ("Your handoff is in your system prompt above."
                        if con and db.get_handoff(con, agent) else
                        "There is no handoff. Read your journal and your inbox "
                        "before carrying on."))
    else:
        parts.append("You were parked and have been resumed. Your context was cleared; "
                     "below is your own handoff record. Carry on from here.")
        record = db.get_handoff(con, agent) if con else None
        if record:
            parts.append(record)
    if not record:
        st = status.read_for_agent(agent, agents.current_account(agent, con))
        if st.transcript_path:
            parts.append(
                f"Your previous session is {st.session_id}, recorded at "
                f"{st.transcript_path}\n"
                f"Grep it for what you need - do not read it whole, it is a JSONL "
                f"transcript with every tool result in it, and reading it back would "
                f"undo the point of parking you.")
    return "\n\n".join(parts)


def _start_for_work(con, agent: str, log) -> str:
    """Give an agent somewhere to run, because work is waiting for it.

    Counted against the same streak a failed wake is: a repo that will never
    check out, or an Orca that will not make a worktree, would otherwise be
    retried every tick forever. After MAX_FAILED_ATTEMPTS it stalls and says
    so, which is a human's problem by definition.
    """
    repo = agents.repo_of(agent)
    if not repo:
        asked = len(db.queued_for(con, agent))
        answers = len(db.unannounced_answers(con, agent))
        what = ", ".join(([f"{asked} request(s)"] if asked else [])
                         + ([f"{answers} answer(s)"] if answers else [])) or "work"
        return (f"{agent}: {what} waiting and it has no terminal - set `repo:` in "
                f"its agent.yaml, or start it by hand (t)")
    # With no room `ocp launch` would refuse in the new terminal, and the
    # refusal would count against the agent though it is the accounts that are full.
    if not launch.choose_account(agent, con=con):
        return f"{agent}: not started - {launch.no_room(agent)}"
    row = db.get_agent_state(con, agent)
    if row and int(row["wake_failures"]) >= MAX_FAILED_ATTEMPTS:
        return f"{agent}: not started again - {row['wake_failures']} attempts have failed"
    # Its own worktree if it still has one - a terminal can be closed by a
    # human or lost when Orca restarts without anything happening to the branch
    # or to what is uncommitted in it, and a second worktree would strand the
    # first.
    wt = spawnmod.worktree_path(row["worktree"] if row else None)
    ok, said = (spawnmod.start_in(agent, wt) if wt
                else spawnmod.spawn(agent, Path(repo).expanduser().resolve()))
    fresh = db.get_agent_state(con, agent)
    if fresh and fresh["terminal"]:
        db.reset_counter(con, agent, "wake_failures")
        db.add_event(con, agent, "started_for_work", {"repo": repo})
        log(f"  {agent}: started in {repo} - work was waiting for it")
        return f"{agent}: started for work waiting - {said.splitlines()[0]}"
    db.bump_counter(con, agent, "wake_failures")
    log(f"  {agent}: could not start for waiting work: {said.splitlines()[0]}")
    return f"{agent}: could not be started - {said.splitlines()[0]}"


def _write_wake_prompt(agent: str, text: str) -> Path:
    d = paths.home() / "wake"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{agent}.txt"
    p.write_text(text)
    return p


def wake_agent(con, agent: str, log) -> tuple[bool, str]:
    """Hand a cleared agent back its own handoff so it can carry on.

    This is the other half of the cycle: the agent kept its terminal and its
    account across the park, and the handoff is what it knows when it wakes.
    """
    with lock.terminal(agent) as mine:
        if not mine:
            # A human's key and a tick can both land here, and this one holds
            # the terminal for as long as a session takes to appear.
            return False, "someone else is typing at this agent; not waking twice"
        # Not `_type_gate`: closed means no live process to be mid-turn in,
        # and the sub-paths below that do meet a live one probe it themselves.
        ok, said = _wake_agent(con, agent, log)
        if ok:
            _mark_typed(con, agent)
        return ok, said


def _wake_agent(con, agent: str, log, moved_to: str | None = None) -> tuple[bool, str]:
    """The body of a wake, without the lock. Callers already holding it use this.

    `moved_to` names the account this wake is starting the agent on when it is
    not the one the last session was on, so the agent is told that rather than
    told it was closed.
    """
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available():
        return False, "no terminal to wake"

    row = db.get_agent_state(con, agent)
    closed = bool(row and row["closed_at"])
    if not closed and row and session_present(agent):
        # A drifted session is ended and takes the closed agent's wake path
        # below, with its own session id to resume; see `launch.fingerprint`.
        if launch.drifted(agent, row):
            acct = agents.current_account(agent, con)
            st = status.read_for_agent(agent, acct)
            if not st.session_id:
                log(f"  {agent}: launch recipe changed but there is no session id "
                    f"to resume - waking it in place")
            elif not _end_session(con, agent, log, st, resume=st.session_id)[0]:
                log(f"  {agent}: launch recipe changed but its session would not "
                    f"end - waking it in place")
            else:
                log(f"  {agent}: launch recipe changed since this session "
                    f"started - relaunching instead of waking it in place")
                row = db.get_agent_state(con, agent)
                closed = bool(row and row["closed_at"])
    if closed:
        # Before anything is typed: an agent.yaml that will not read is this
        # agent's ConfigError, which the callers stall by name.
        agents.read(agent)
        # `ocp launch` refuses at the 5h wall, and the daemon can read that
        # before it types anything. Trying anyway would spend the wake timeout
        # to be told what we already knew, and count a streak against an agent
        # whose only problem rolls over by itself.
        # The account this launch will land on. With a session to resume that
        # is the account it lives under, so an agent whose own account is at
        # the wall waits for its window rather than losing its conversation -
        # a fresh start elsewhere comes back with only the handoff. Only a wake
        # with nothing to resume is free to go where there is room, which is
        # the move's wake.
        session = row["resume_session"]
        wait = launch.wake_wait(agent, row, con)
        if wait:
            # The resume account is at the wall. If another account has room,
            # start fresh there: a resume id cannot follow an account change.
            # The handoff is the record the close took while the session was
            # live; a snapshot read now is stale by construction. One that is
            # missing a fact keeps the id, since the session is the only
            # complete copy of the context.
            rescued = False
            acct, pct = wait.account, wait.pct
            wait_kind = "usage_7d" if wait.window == "7d" else "usage"
            elsewhere = _move_plan(con, agent, wait_kind)[0] if session and not wait.assumed \
                and not _handoff_backing_off(con, agent) else None
            if elsewhere:
                hid = uuid.uuid4().hex[:12]
                db.start_handoff(con, hid, agent, session, "pinned",
                                 int(time.time()) + HANDOFF_DEADLINE_S)
                missing = [] if db.get_handoff(con, agent) else \
                    handoffmod.record(con, agent)
                if not missing:
                    db.advance_handoff(con, hid, "released", state="done")
                    db.reset_counter(con, agent, "throttle_count")
                    db.set_closed(con, agent, None)
                    db.add_event(con, agent, "moved_account",
                                 {"from": acct, "to": elsewhere, "trigger": "pinned"})
                    log(f"  {agent}: pinned to {acct} at {pct}% ({wait.window}) - starting "
                        f"fresh on {elsewhere}")
                    session, acct, moved_to, rescued = None, elsewhere, elsewhere, True
                    measure.begin(con, agent, elsewhere)
                else:
                    why = "could not read " + ", ".join(missing)
                    db.advance_handoff(con, hid, "released", state="degraded",
                                       detail=why[:300])
                    db.bump_counter(con, agent, "throttle_count")
                    log(f"  {agent}: handoff degraded ({why}); keeping the resume id")
            if not rescued:
                # Returned, not logged: this is reached on every retry for as
                # long as the window lasts, and each caller says it in its own
                # place - the tick once per reason, a command at once.
                return False, launch.describe_wake_wait(wait)
        # The shell is still there; put the agent back into it. Resuming the
        # same session is what makes closing cheap, so it is tried first and
        # a fresh start is the fallback, not the plan.
        base = launch.command_for(agent, account=moved_to)
        # Resuming the session is what makes closing cheap, so it is tried
        # first - but a resume can be the very thing that fails: an id whose
        # transcript was pruned, or a worktree moved out from under it, gives
        # a launch that refuses every time it is retried. A fresh start in the
        # same terminal loses the context and keeps the agent, which is the
        # better of the two ends this can have.
        # The wake prompt travels as the session's own first prompt, in a file
        # the launch reads and deletes: typed at the terminal instead, it lands
        # in a bare shell whenever the session dies first, and the shell runs
        # it line by line.
        resumed = False
        foreign = False
        # Past the cache's life a resume costs the whole transcript and a
        # fresh start costs the handoff; the record stays on the row for the
        # launch to inject.
        cold = moved_to is None and cache_expired(con, agent, session, int(time.time()))
        # A fresh start goes where there is room. With none, `ocp launch` would
        # refuse and the refusal would count against the agent's wake attempts,
        # though it is the accounts that are full.
        if moved_to is None and (cold or not session) \
                and not launch.choose_account(agent, con=con):
            return False, launch.no_room(agent)
        took = False
        if cold and not db.get_handoff(con, agent):
            # The close that would have taken it failed, and the snapshot no
            # longer names the session; the id on the row still does.
            gaps = handoffmod.record(con, agent, closed_session=session)
            took = True
            if gaps:
                why = "could not read " + ", ".join(gaps)
                # "handoff" must stay out of db.AGENT_ACTIVITY_KINDS: this is
                # ocp's own bookkeeping about a failed take, not the agent
                # doing anything, and counting it would read a dead cache warm.
                db.add_event(con, agent, "handoff", {"degraded": True, "session": session, "why": why})
                log(f"  {agent}: cold wake handoff degraded ({why})")
        ok = False
        taken = False
        for resume in ([True, False] if session and not cold else [False]):
            # Checked fresh before every send, not only after one fails: the
            # closed branch has no lock on the world outside ocp, and a
            # session can come up here between the first attempt's check and
            # the second's, or before either has run at all - auditor #1982
            # note 2. `taken` still holds the previous attempt's own verdict
            # here (False on the first iteration, since nothing has sent
            # anything yet): a session already live now is that attempt's own
            # come up late, not foreign, if that attempt's launch took the
            # prompt file - same rule the post-send check below uses. `why`
            # is left as that attempt's own failure reason in that case.
            if (obs.live_sessions() or {}).get(agent):
                foreign = not taken
                if foreign:
                    why = "a session it did not start is running"
                break
            pf = _write_wake_prompt(
                agent, wake_prompt(agent, resume, was_closed=closed,
                                   moved_to=moved_to, con=con, cold=cold))
            cmd = f"{base} --prompt-file {shlex.quote(str(pf))}" + (
                f" -- --resume {session}" if resume else "")
            ok, why = orca.relaunch(
                handle, cmd,
                started=lambda: wait_for_session(agent, WAKE_SESSION_TIMEOUT_S, pf))
            if ok:
                resumed = resume
                break
            taken = not pf.exists()
            pf.unlink(missing_ok=True)
            # A session owns the terminal now, and the next attempt's command
            # would be typed into it as a prompt. It is this launch's own, come
            # up late, only if the launch took the prompt.
            if (obs.live_sessions() or {}).get(agent):
                foreign = not taken
                if foreign:
                    why = "a session it did not start is running"
                break
        if not ok:
            if took:
                # A record no session was handed would be typed at whichever
                # live session the next wake finds, as if it had been parked.
                db.set_handoff(con, agent, None)
            # The closed mark and resume_session are the whole way back. A wake
            # that started nothing leaves both alone: the next attempt - work
            # arriving, or the next restart - has nothing else to use.
            db.add_event(con, agent, "wake_failed", {"resume_session": session,
                                                     "why": why})
            if foreign:
                # The agent is running, so this is no failed wake to count
                # toward STALLED; the sweep sees the session and clears the
                # closed mark.
                log(f"  {agent}: {why}; typed nothing more")
                return False, why
            db.bump_counter(con, agent, "wake_failures")
            log(f"  {agent}: {why}; still closed")
            fresh = db.get_agent_state(con, agent)
            if fresh and fresh["wake_failures"] >= MAX_FAILED_ATTEMPTS:
                # Work arriving retries the wake every tick, so a launch that
                # will never work is a loop that never ends and never says so.
                # Three launches that started nothing is not the wall - that
                # was turned away above, before it could count - and not a
                # resume that can be dropped, which the fallback above already
                # did. It waits for a human, or for the sweep to see a session
                # after all.
                db.set_agent_state(con, agent, STALLED,
                                   detail=f"{fresh['wake_failures']} wakes started "
                                          f"no session: {why}")
                log(f"  {agent}: STALLED after {fresh['wake_failures']} failed wakes")
            return False, why
        # A relaunch is where claude shows its folder-trust screen - on a
        # moved agent the new account has never trusted this folder - and the
        # wake prompt waits behind it. Until it is past, the agent is not woken.
        ok, why = _clear_trust_prompt(con, agent, handle, log)
        if not ok:
            return False, why
        db.reset_counter(con, agent, "wake_failures")
        db.clear_closed(con, agent)
        db.add_event(con, agent, "wake", {"resumed_session": session if resumed else None,
                                          **({"cache_expired": True} if cold else {})})
        db.set_handoff(con, agent, None)
        log(f"  {agent}: relaunched with its wake prompt"
            f"{' (session resumed)' if resumed else ' (fresh - its prompt cache had expired)' if cold else ''}")
        return True, "relaunched with the wake prompt"
    elif not db.get_handoff(con, agent):
        return False, "no handoff to resume from"
    elif not session_present(agent):
        # Not the closed branch: nothing was relaunched, so the row's claim
        # that a session is there is the only evidence - and it is the claim
        # that goes wrong. A handoff typed at a shell prompt is executed line
        # by line. The sweep records this agent closed on its next tick, and
        # the branch above then relaunches it before anything is typed.
        return False, "no session process; not typing the handoff at a shell prompt"

    ok, why = _clear_trust_prompt(con, agent, handle, log)
    if not ok:
        return False, why

    if not orca.typeable_now(handle):
        return False, "the session is busy or has a modal open; not typing the handoff"
    ok, why = orca.resume_with(handle, wake_prompt(agent, False, was_closed=False,
                                                   moved_to=moved_to, con=con))
    if ok:
        db.set_handoff(con, agent, None)
    log(f"  {agent}: {why}")
    return ok, why


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _clear_trust_prompt(con, agent: str, handle: str, log) -> tuple[bool, str]:
    """The wake paths' gate before the wake prompt: a no-op when nothing is
    showing, else standing consent (156 1b) or an escalation. It runs ahead
    of `typeable_now`, which refuses this screen as a dialog and would leave
    it unanswered. The deliver paths only check `typeable_now`, so they wait
    and the blocked sweep reports it.
    """
    if not orca.trust_prompt_showing(handle):
        return True, "clear"
    return _handle_trust_prompt(con, agent, handle, log)


def _handle_trust_prompt(con, agent: str, handle: str, log) -> tuple[bool, str]:
    """Standing consent: press "Yes" only for the folder the dialog itself
    names, and only when that folder is both a declared root and the one on
    record for this agent. Anything else - unreadable, undeclared, or a
    mismatch with the recorded worktree - escalates, as does a press whose
    keys did not land.
    """
    row = db.get_agent_state(con, agent)
    recorded = spawnmod.worktree_path(row["worktree"]) if row else None
    text = orca.screen_text(handle)
    shown = orca.trust_prompt_folder(text) if text else None
    if not shown or not recorded or not trust.is_declared(shown) \
            or not _same_path(shown, recorded):
        return _escalate_trust_prompt(con, agent, handle, log, shown, recorded)
    ok, why = orca.confirm_trust(handle)
    # The redraw is not instant; checking on the very next line risks reading
    # the screen claude was still drawing, and escalating a trust that
    # actually went through.
    orca.wait_for_trust_prompt(handle, showing=False)
    if not ok or orca.trust_prompt_showing(handle):
        return _escalate_trust_prompt(con, agent, handle, log, shown, recorded)
    db.add_event(con, agent, "trust_confirmed", {"path": shown})
    log(f"  {agent}: trusted {shown} (standing consent)")
    # The screen just changed under us; typing the wake prompt before claude
    # has finished drawing its ordinary TUI loses the opening characters.
    orca.wait_idle(handle)
    return True, "trusted"


TRUST_STALL = "folder-trust prompt"


def _escalate_trust_prompt(con, agent: str, handle: str, log,
                          shown: str | None = None, recorded: str | None = None
                          ) -> tuple[bool, str]:
    """claude is sitting at its folder-trust screen - nothing is typed, since
    typed text is not a choice there, it is a coin flip landing on the
    default "No, exit". STALLED and escalated rather than retried: the screen
    does not resolve itself.
    """
    # Already escalated: a later wake - the next tick, an ask, a key on the
    # bridge - meets the same screen and would raise the same request again.
    row = db.get_agent_state(con, agent)
    if row and row["state"] == STALLED and TRUST_STALL in (row["detail"] or ""):
        return False, row["detail"]
    acct = agents.current_account(agent, con)
    why = (f"{agent}'s terminal is showing claude's {TRUST_STALL} for "
           f"{shown or 'an unreadable folder'} (account {acct or 'none'}, recorded "
           f"worktree {recorded or 'unknown'}) - nothing was typed into it")
    askmod.escalate(con, agent, f"[trust] {why}", why)
    db.set_agent_state(con, agent, STALLED, wake_at=None, detail=why[:300])
    log(f"  {agent}: {why}")
    return False, why


def expire_handoffs(con, log) -> list[str]:
    """Fail any handoff past its deadline (8.3).

    This runs every tick, not only at startup. Checking it only in reconcile
    meant a handoff that wedged while the daemon stayed up was never timed out,
    and its agent sat in HANDOFF - excluded from trigger evaluation - forever.
    """
    actions: list[str] = []
    now = int(time.time())
    for row in db.in_flight_handoffs(con):
        if not row["deadline"] or row["deadline"] >= now:
            continue
        agent = row["agent"]
        db.advance_handoff(con, row["id"], row["step"], state="degraded",
                           detail=f"deadline passed at {row['deadline']}")
        db.bump_counter(con, agent, "throttle_count")
        db.set_agent_state(con, agent, DEGRADED, wake_at=now + DEGRADED_RETRY_S,
                           detail=f"handoff timed out at step '{row['step']}'")
        actions.append(f"{agent}: handoff timed out at step '{row['step']}' -> DEGRADED")
        log(f"  {agent}: handoff timed out at step '{row['step']}' -> DEGRADED")
    return actions


def _stall_agent(con, agent: str, log, actions: list[str],
                 e: Exception, where: str) -> None:
    """One agent's own failure, not the daemon's: count it against the agent
    (8.5) and stall it once the streak reaches MAX_FAILED_ATTEMPTS, rather
    than let it wedge every other agent behind it (reconcile's two callers)
    or end the tick early (tick()'s own per-agent loop, its third caller).
    What belongs in `e` is the caller's decision, not this function's - each
    call site's own except clause is what draws the line between "this
    agent's problem" and a fault the caller must keep propagating (reconcile:
    ConfigError/UnicodeDecodeError/OSError, the class a bad agent.yaml
    raises, the same distinction hr.listing already draws for a bad proposal
    file; tick(): bare Exception, the wider net a live turn can fail in more
    ways than a config read can)."""
    db.bump_counter(con, agent, "failed_attempts")
    fresh = db.get_agent_state(con, agent)
    actions.append(f"{agent}: {where}: {e}")
    log(f"  {agent}: {where}: {e}")
    if fresh and fresh["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
        db.set_agent_state(con, agent, STALLED,
                           detail=f"{fresh['failed_attempts']} failed attempts: {where}: {e}")
        log(f"  {agent}: STALLED after {fresh['failed_attempts']} failed attempts")


def reconcile(con, log) -> list[str]:
    """One unconditional pass at startup (8.4).

    Covers, together: crossings missed while the daemon was down, handoffs left
    in flight by a kill, and wake_at deadlines that elapsed with nobody
    listening.
    """
    actions: list[str] = []
    now = int(time.time())

    # 1. Handoffs left in flight by a kill. Expire the ones past their
    #    deadline, then actually finish the rest from the step they reached -
    #    a log line claiming a resume is not a resume (8.9).
    actions += expire_handoffs(con, log)
    for row in db.in_flight_handoffs(con):
        agent, step = row["agent"], row["step"]
        try:
            resume_handoff(con, row, log)
        except (ConfigError, UnicodeDecodeError, OSError) as e:
            # One agent's unreadable agent.yaml must not leave every other
            # in-flight handoff - and every due wake below it - unresolved.
            _stall_agent(con, agent, log, actions, e,
                                       f"could not resume handoff from step '{step}'")
            continue
        actions.append(f"{agent}: resumed in-flight handoff from step '{step}'")

    # 2. wake_at deadlines that have come due.
    for row in db.due_agents(con, now):
        agent = row["agent"]
        if row["state"] not in (PARKED, THROTTLED, DEGRADED):
            continue
        # A closed agent has no session left to notice its own wake_at - the
        # wake_at branch in supervise() is what would normally call this, and
        # it needs wake_at still set to run. So wake it here, before the
        # deadline is cleared: a wake that failed - the dead terminal a reboot
        # leaves behind is the ordinary way - has to stay due, or due_agents
        # never selects it again and the agent sleeps until a human looks.
        if row["closed_at"]:
            try:
                ok, why = wake_agent(con, agent, log)
            except (ConfigError, UnicodeDecodeError, OSError) as e:
                _stall_agent(con, agent, log, actions, e,
                                           "could not wake at reconciliation")
                continue
            if not ok:
                actions.append(f"{agent}: could not wake at reconciliation, "
                               f"still due - {why}")
                continue
            actions.append(f"{agent}: woken at reconciliation")
        new_state = _queued_or_idle(con, agent, f"woke from {row['state']} at reconciliation")
        actions.append(f"{agent}: {row['state']} -> {new_state} (wake_at was {row['wake_at']})")

    # 3. Agents we know about but have no state row for.
    known = set(paths.list_agents())
    have = {r["agent"] for r in db.all_agent_states(con)}
    for agent in sorted(known - have):
        db.set_agent_state(con, agent, IDLE, detail="registered, first seen")
        actions.append(f"{agent}: IDLE (first seen)")

    for a in actions:
        log(f"  reconcile: {a}")
    return actions


# Which suspect the next tick probes. Round-robin rather than always the first,
# or one agent that is merely busy would hold the slot and the genuinely stuck
# one behind it would never be looked at.
_probe_turn = {"n": 0}


def _resumable_session(con, agent: str) -> str | None:
    """The session id to remember when recording `agent` as closed, or None.

    Read the agent's own snapshot directly rather than through
    read_for_agent, which nulls the session id once that snapshot is stale.
    That rule is there to stop an agent being handed another agent's session
    off the shared account file, and it is right - but a session that has
    just died (or a terminal that just went with it) leaves a snapshot that
    is stale by construction, and its own id is the one thing here that is
    not in doubt. Losing it loses the only pointer to that agent's context,
    and memory comes back where context does not.
    """
    own = status.read(agents.current_account(agent, con) or "", paths.agent_status_file(agent))
    session = None if own.error else own.session_id
    # A session id lives under the account its session ran on. If the agent
    # has moved since - its new session not yet visible - the id in its
    # snapshot is the old account's, and a resume of it dies.
    ran_on = own.raw.get("account")
    if ran_on and ran_on != agents.current_account(agent, con):
        session = None
    return session


def sweep(con, log, actions: list[str]) -> None:
    """Ask the world what is true, and correct the record where it is not.

    Every other decision in this file reads a row we wrote ourselves, which is
    how a state that went wrong stayed wrong: an agent was set RUNNING, nothing
    ever set it back, and every screen faithfully reported a value nothing could
    contradict. Hooks are edges, and an edge that is missed is missed forever;
    this is the level-triggered half.

    It corrects rather than reports, because a supervisor that only files
    complaints leaves the human in the loop it was built to take them out of.
    What it may correct on its own is limited to what it observed rather than
    inferred: a terminal Orca does not list is gone, and a session with no
    process is not running. Where the only evidence is a probe, it wants two
    witnesses before it moves.
    """
    agent_names = paths.list_agents()
    terminals = {a: db.get_terminal(con, a) for a in agent_names}
    seen = obs.observe(agent_names, terminals)
    _seen.clear()
    _seen.update(seen)
    _seen_at["t"] = time.time()
    suspects: list[tuple[str, object]] = []

    for agent in agent_names:
        row = db.get_agent_state(con, agent)
        if not row:
            continue
        o = seen[agent]

        # The house is gone. Nothing may be delivered to it and nothing can be
        # closed in it, and both of those already key on having a terminal - so
        # dropping the handle is the whole correction.
        if o.terminal_alive is False and row["terminal"]:
            # The terminal is gone; the worktree is not. Dropping both loses
            # the only record of where this agent's work lives, and starting
            # it again would then make a second worktree beside the first.
            db.set_terminal(con, agent, None, row["worktree"])
            db.add_event(con, agent, "terminal_gone", {"handle": row["terminal"]})
            said = f"{agent}: terminal is gone; handle dropped"
            # Confirmed gone is an answer, not an unread one - the same close
            # the "session ended without us" branch below does when it has its
            # own confirmation, so a terminal closed at a prompt does not read
            # RUNNING, closed_at None, forever. Said in the same line, not a
            # second event: this is one fact (the terminal, and whatever was
            # running in it, are both gone), not two.
            if not row["closed_at"]:
                session = _resumable_session(con, agent)
                db.set_closed(con, agent, session)
                if row["state"] == RUNNING:
                    db.set_agent_state(con, agent, IDLE, detail="terminal gone")
                said += f"; recorded as closed (resume {session or 'unknown'})"
            actions.append(said)
            log(f"  {said}")
            continue

        if o.terminal_alive is not True or not o.session_seen:
            # Whether a session is running is only answerable about an agent
            # whose terminal we just confirmed. Without that, "no process" is
            # as likely to mean "we could not look" - and acting on it would
            # mark a live agent closed on the strength of an unread answer.
            continue

        alive = o.session_pid is not None
        closed = bool(row["closed_at"])

        if alive and closed:
            # It came back without us - a human relaunched it, or a resume
            # landed that we never recorded. Believing our own note here would
            # close it a second time on top of a live session.
            db.clear_closed(con, agent)
            actions.append(f"{agent}: a session is running; the closed mark was wrong")
            log(f"  {agent}: running again (pid {o.session_pid}); cleared the closed mark")
            if row["state"] == STALLED and row["wake_failures"] >= MAX_FAILED_ATTEMPTS:
                # This stall is ours: we stopped because our launches started
                # nothing, and a session is running now. 8.6 keeps a human in
                # the loop for a stall the agent cannot refute - this one the
                # world just refuted, on the same evidence that cleared the
                # closed mark, and leaving it would hide a live agent from
                # every delivery.
                db.reset_counter(con, agent, "wake_failures")
                db.set_agent_state(con, agent, IDLE,
                                   detail="a session is running; the wake stall is lifted")
                actions.append(f"{agent}: STALLED -> IDLE (it is running after all)")
        elif not alive and not closed and row["terminal"]:
            # It died, or a human ended it. Recording it as closed is what makes
            # work arriving wake it instead of being typed at a bash prompt.
            session = _resumable_session(con, agent)
            db.set_closed(con, agent, session)
            if row["state"] == RUNNING:
                db.set_agent_state(con, agent, IDLE, detail="session ended without us")
            db.add_event(con, agent, "session_gone", {"session": session})
            actions.append(f"{agent}: its session is gone; recorded as closed")
            log(f"  {agent}: session gone; recorded as closed "
                f"(resume {session or 'unknown'})")
        elif alive and row["state"] == RUNNING:
            suspects.append((agent, row))

    # The second witness is the only thing here that costs: a "not idle" answer
    # always takes the probe's full timeout, and an agent working a long turn is
    # a suspect on every tick for as long as the turn lasts. One per tick bounds
    # the tick at one probe however many are stuck at once - three were, on
    # 09-17 - and a stuck agent still comes back within seconds, because ticks
    # are two seconds apart and nothing else is competing for the slot.
    if suspects:
        agent, row = suspects[_probe_turn["n"] % len(suspects)]
        _probe_turn["n"] += 1
        _unstick_running(con, agent, row, log, actions)


def silent_minutes(con, agent: str, row) -> int | None:
    """How long this agent has read RUNNING with nothing heard, if too long.

    The first of the two witnesses, and the only one that costs nothing. `ocp
    doctor` asks it too, and must get the same answer to the same question -
    the threshold and the arithmetic used to live in both files.
    """
    if not row or row["state"] != RUNNING:
        return None
    last = db.last_activity(con, agent)
    if last is None:
        return None
    silent = int(time.time()) - last
    return silent // 60 if silent >= STUCK_RUNNING_S else None


def _unstick_running(con, agent: str, row, log, actions: list[str]) -> None:
    """RUNNING is the label that takes an agent out of service.

    Nothing is delivered to it and its session is never closed, so an agent that
    entered RUNNING and never left is ignored by the daemon while every screen
    reports it working. `Stop` returns it to IDLE now, but an agent that was
    already stuck when that landed cannot get out on its own: leaving RUNNING
    needs a Stop, a Stop needs a turn, and a turn needs work it will never be
    told about. Three agents sat that way, and the founder found each of them.

    This is the one correction here that rests on a probe rather than on a fact,
    and getting it wrong means closing an agent that is working - so it wants
    two witnesses that do not share a source. Our own event history says nothing
    has been heard for longer than a session is allowed to idle; Orca says the
    terminal is not mid-turn. Either alone is not enough: a long turn is silent
    in our events, and the probe is the one we have twice declined to trust.
    """
    mins = silent_minutes(con, agent, row)
    if mins is None:
        return
    handle = row["terminal"]
    if not handle or not orca.available() or not orca.idle_now(handle):
        return
    db.set_agent_state(con, agent, IDLE,
                       detail=f"RUNNING with nothing heard for {mins}m, and idle")
    db.add_event(con, agent, "unstuck", {"was": RUNNING, "silent_minutes": mins})
    actions.append(f"{agent}: RUNNING for {mins}m with nothing heard; back to IDLE")
    log(f"  {agent}: stuck in RUNNING for {mins}m and idle at its prompt - back to IDLE")


# ---------------------------------------------------------------- handoff
def force_move(con, agent: str, dest: str, by: str, log) -> tuple[bool, str]:
    """Move an agent's live session to `dest` now, through the handoff's own move.

    Record the handoff, end the session with no resume id, start fresh on
    `dest`, record the account: the path a usage wall takes with the
    destination fixed. Nothing is ended unless `dest` takes one more agent.
    `by` is recorded on the event.
    """
    if agent not in paths.list_agents():
        return False, f"no agent '{agent}'"
    if dest not in paths.list_accounts() or not accounts.logged_in(dest):
        return False, f"account '{dest}' does not exist or is not logged in"
    here = agents.current_account(agent, con)
    if here == dest:
        return False, f"{agent} is already on {dest}"
    row = db.get_agent_state(con, agent)
    if not row or not row["terminal"] or row["closed_at"]:
        return False, f"{agent} has no live session to move"
    if row["state"] in (HANDOFF, STALLED):
        return False, f"{agent} is {row['state']}"
    room, why = _forced_plan(con, agent, dest)
    if room is None:
        return False, f"{why} - not moving {agent} there"
    trigger = triggers.Trigger(True, "forced", f"forced by {by}: {here} -> {dest}")
    perform_handoff(con, agent, trigger, log, dest=dest)
    landed = db.get_agent_state(con, agent)
    if not landed or landed["account"] != dest:
        return False, (f"{agent} did not move (mid-turn, or the new session did not "
                       f"start on {dest}) - it stays on {here}")
    db.add_event(con, agent, "forced_move", {"from": here, "to": dest, "by": by})
    return True, f"{agent} moved {here} -> {dest} (forced by {by})"


def perform_handoff(con, agent: str, trigger: triggers.Trigger, log,
                    dest: str | None = None) -> bool:
    """Run the handoff pipeline, committing each step (8.9).

    Returns True when the agent ended parked cleanly, False when it degraded.
    """
    existing = db.in_flight_for(con, agent)
    if existing:
        db.advance_handoff(con, existing["id"], existing["step"],
                           detail=f"{existing['detail'] or ''}; also {trigger.kind}".strip("; "))
        log(f"  {agent}: already handing off at step '{existing['step']}', merged "
            f"'{trigger.kind}' as an added reason")
        return True

    # An agent that is mid-turn cannot be handed off: the record would miss the
    # work in flight and the clear would throw it away. Check before starting,
    # with a short probe so one busy agent does not stall the whole tick, and
    # simply come back next tick - the trigger is a condition, so it still
    # holds then.
    last_h = db.last_handoff(con, agent)
    last = last_h["updated"] if last_h else None
    # A person asked for a forced move, so the too-soon guard (for triggers that
    # repeat on their own) does not apply.
    if last and trigger.kind != "forced" \
            and int(time.time()) - int(last) < MIN_HANDOFF_INTERVAL_S:
        log(f"  {agent}: {trigger.kind} fired again {int(time.time()) - int(last)}s after the "
            f"last handoff - too soon, skipping")
        return True

    handle = db.get_terminal(con, agent)
    settled = False
    if handle and orca.available():
        # A turn that ended on the usage limit and has stayed quiet for a while
        # is over, whatever the terminal's activity probe still says; sooner
        # than that a person may be typing, so the probe still decides.
        stopped = events.at_limit(con, agent)
        settled = stopped and int(time.time()) - stopped["at"] >= LIMIT_SETTLE_S
        if not settled and not orca.typeable_now(handle):
            # The usage-limit dialog is not a turn: it does not end by waiting,
            # so it is got past here rather than deferred to, tick after tick.
            # Any other dialog may be a person's to answer, and this is only the
            # decision whether to begin - the session is not yet ending.
            # Only a modal dialog is got past: a blocker that is not one, such
            # as the backgrounded picker, still reads as a turn and defers.
            with lock.terminal(agent) as mine:
                in_the_way = _past_dialog(con, agent, log, only_limit=True) if mine else ""
            if in_the_way and _stalled_on_dialog(con, agent, in_the_way, log):
                return False
            if in_the_way or not orca.typeable_now(handle):
                log(f"  {agent}: {trigger.kind} fired but "
                    + (f"a dialog is in the way ({in_the_way})" if in_the_way else "the agent is mid-turn")
                    + " - deferring")
                return True
        if not session_present(agent):
            # A handoff for a session that is not there types its own text at
            # a shell prompt. The context it wants to reclaim went with the
            # session; the sweep records the agent closed, and a closed
            # agent's triggers do not fire at all.
            log(f"  {agent}: {trigger.kind} fired but no session is running - "
                f"not handing off")
            return True

    with lock.terminal(agent) as mine:
        if not mine:
            # Before the record, not after it. The clear comes at the end of
            # this pipeline, and losing the terminal there meant the handoff
            # was recorded done and the context it exists to reclaim was still
            # sitting in the agent. The trigger is a condition, so it fires
            # again next tick.
            log(f"  {agent}: {trigger.kind} fired but something else is typing at it "
                f"- not handing off")
            return True
        # The re-read `_type_gate` documents above, at the gap #1973 was in:
        # require_idle mirrors `settled` exactly as the dialog-handling block
        # above does, so a settled usage-limit stop still bypasses the probe.
        why = _type_gate(con, agent, handle, require_idle=not settled, require_settled=True)
        if why:
            log(f"  {agent}: {trigger.kind} fired but {why} - not handing off")
            return True
        ok, typed = _perform_handoff(con, agent, trigger, log, dest)
        if typed:
            _mark_typed(con, agent)
        return ok


# The triggers a move answers: the two walls, and a person's own order. Nothing
# below a wall is a reason to move a working agent, and an account nobody can
# read is not a wall.
MOVABLE_TRIGGERS = ("usage", "usage_7d", "forced")

# The only triggers with a real wall to sleep out: an account's own window has
# to roll over before either can clear. Everything else a handoff fires on -
# context - is already handled once the clear is done, so parking it
# against a usage window it never hit just sits it out for nothing (#1903:
# the auditor sat PARKED 09:25-11:30 on a context handoff with room to spare).
USAGE_TRIGGERS = ("usage", "usage_7d")


def _handoff_backing_off(con, agent: str) -> bool:
    """A handoff that keeps degrading is not retried every tick: a pinned move
    waits out the same backoff a degraded park does. Both share one streak and
    one last-attempt time."""
    row = db.get_agent_state(con, agent)
    streak = int(row["throttle_count"]) if row else 0
    last = con.execute("SELECT MAX(updated) AS t FROM handoff WHERE agent = ?",
                       (agent,)).fetchone()["t"]
    return bool(streak and last and int(time.time()) < int(last) + backoff_for(streak))


def _last_left(con, agent: str) -> str | None:
    """The account this agent's most recent move took it off."""
    row = con.execute("SELECT payload FROM events WHERE agent = ? AND kind = 'moved_account' "
                      "ORDER BY id DESC LIMIT 1", (agent,)).fetchone()
    try:
        return json.loads(row["payload"]).get("from") if row else None
    except ValueError:
        return None


def _move_plan(con, agent: str, kind: str) -> tuple[str | None, str]:
    """An agent at the wall: stay, or move and where to. (destination, why).

    Stay when nothing is waiting for it or its own account resets sooner than a
    cold start would pay back (`triggers.stay_or_move`). Otherwise the pick,
    over every other account that can take one more agent - and never back to
    the account it just left before that account resets, so one wall is one
    move. None with a reason when it stays, including when every account is
    capped or walled: the work then waits in the queue.
    """
    here = agents.current_account(agent, con)
    if here is None:
        return None, "no account is recorded for it"
    now = time.time()
    reset = status.read(here)
    reset = reset.reset_7d if kind == "usage_7d" else reset.reset_5h
    reset_in = max(0.0, reset - now) if reset else None
    work = bool(db.queued_for(con, agent) or db.unannounced_answers(con, agent))
    move, why = triggers.stay_or_move(
        work, reset_in, triggers.stay_within_s(measure.cold_start_pct(con)))
    if not move:
        return None, why
    exclude = {here: "the account it is at the wall on"}
    left = _last_left(con, agent)
    if left and left != here:
        left_reset = status.read(left).reset_5h
        if left_reset and left_reset > now:
            exclude[left] = "the account it just left, until it resets"
    # live_refresh=True: this decides whether a parked, walled agent actually
    # moves - not a screen - so a carried_old destination or a stale idle
    # floor is asked fresh before it decides the answer, rather than parking
    # an agent beside an account that could in fact take it (#1811 part 2,
    # #1816).
    dest, ranked = status.pick_account(
        [a for a in paths.list_accounts() if accounts.logged_in(a)],
        bootstrap=True, exclude=exclude, load=load.read_all(con), agent=agent,
        live_refresh=True)
    if dest is None:
        return None, "no account can take one more agent (" + "; ".join(
            f"{a}: {r}" for a, r in ranked if a not in exclude) + ")"
    return dest, dict(ranked)[dest]


def _forced_plan(con, agent: str, dest: str) -> tuple[str | None, str]:
    """A person's chosen destination, held to the same room test as any other:
    the pick over that one account, so a walled, capped or unreadable one is
    refused with the pick's own reason. (destination, why)."""
    picked, ranked = status.pick_account([dest], bootstrap=True, load=load.read_all(con),
                                         agent=agent, live_refresh=True)
    if picked is None:
        why = dict(ranked).get(dest) or "cannot take one more agent"
        return None, f"{dest} {why}"
    return picked, dict(ranked)[picked]


def _move_to_room(con, agent: str, trigger: triggers.Trigger, log,
                  dest: str | None = None) -> tuple[bool, bool]:
    """Put this agent's next session on an account that has room.

    (moved, retry_soon). `retry_soon` is only meaningful when `moved` is
    False: True once a destination account was actually found, so whatever
    stopped the move is about this terminal right now - a modal or a
    session that would not end - and not about the accounts, which is the
    caller's cue to look again shortly rather than park until this account's
    own window rolls over. False when no destination existed (a real park) or
    the trigger never moves accounts at all.

    `dest` fixes the destination (a forced move); it still has to be one the
    pick would send an agent to.

    What this changes is whether an agent waits out its own window while
    another account idles beside it. It needs no lease, because it does not
    take anything from a session anyone is using: it runs after the handoff is
    recorded, at the point where this session was ending either way.

    The new session cannot be the old one - a session id lives under one
    account's config directory - so the close records no resume id, and what
    carries over is what carries over across a park: the handoff in the system
    prompt, and the old transcript's path to grep.
    """
    if trigger.kind not in MOVABLE_TRIGGERS:
        return False, False
    here = agents.current_account(agent, con)
    there, picked_why = (_forced_plan(con, agent, dest) if dest
                         else _move_plan(con, agent, trigger.kind))
    if there is None:
        log(f"  {agent}: staying on {here} - {picked_why}")
        return False, False
    handle = db.get_terminal(con, agent)
    if not handle:
        return False, False
    if not orca.available():
        # Orca itself being unreachable is nobody's terminal in particular -
        # every agent's move would fail the same way, so this is not evidence
        # that a short recheck buys anything the account's own window would not.
        return False, False
    if not orca.alive(handle):
        # Two things this can mean - a terminal genuinely gone (not transient;
        # a short recheck buys nothing) or `live_terminals()` itself failing to
        # read (transient, same as `not handle`) - and `alive()`'s bool does
        # not say which. Treated as retry_soon anyway: the wrong guess here
        # costs an extra 5-minute recheck at worst, not a wrong long wait.
        return False, True

    # Closed through the same checks every other close goes through. `/exit`
    # typed at a shell exits the shell and Orca's terminal goes with it, and by
    # here the session it started with may be gone. No resume id: that session
    # belongs to the account being left.
    st = status.read_for_agent(agent, here)
    ok, why = _end_session(con, agent, log, st, resume=None)
    if not ok:
        log(f"  {agent}: staying on {here} - {why}")
        return False, True

    # `_perform_handoff` put this agent in HANDOFF before calling here, and
    # HANDOFF is one of events.PROTECTED_STATES - `_mark_activity` refuses to
    # move an agent out of a protected state, so the new session's own
    # UserPromptSubmit could never set RUNNING while it stood. HANDOFF was
    # protecting the session that just ended; that session is gone now, and
    # there is nothing left for it to guard. Off it comes before the wake, so
    # the hook can do its job.
    db.set_agent_state(con, agent, QUEUED, detail=f"moving {here} -> {there}")

    # The unlocked wake. This runs inside the handoff's own hold on the
    # terminal, and `lock.terminal` is flock on a fresh descriptor, so a second
    # acquire in this process does not nest - it fails, and a failed wake here
    # would read as "nowhere to go" and park the agent on the account it was
    # just moved off.
    measure.begin(con, agent, there)
    woke, why = _wake_agent(con, agent, log, moved_to=there)
    landed = db.get_agent_state(con, agent)
    # The wake prompt is the new session's own first prompt, so by the time
    # `_wake_agent` returns, UserPromptSubmit may already have fired and set
    # RUNNING - a hook that has seen the session is a better witness than
    # `woke`, which only says whether *we* saw it start in time. A `wait_for_
    # session` timeout can read as not-woke while the session is genuinely up.
    # (The write below is a compare-and-set, not a second read of this same
    # kind - a lot of I/O happens between here and there, and the hook can
    # land in that gap too, so a read taken now would be no safer than this
    # one.)
    if not woke and landed and landed["state"] != RUNNING and landed["closed_at"]:
        # Nothing started, and no hook says otherwise. The session is gone and
        # the row still says closed, so falling through parks it with a
        # deadline - which is what brings a closed agent back at all.
        # Returning True here would leave it closed with nothing due.
        # STALLED is one of the ways here: the third failed wake sets it, and
        # STALLED is protected the same way HANDOFF was - a hook that fires
        # late cannot turn it into RUNNING, so it never reaches the compare-
        # and-set below to be overruled. That is right: three launches that
        # started nothing is a human's call (8.6), not something a race
        # against one more relaunch attempt should quietly clear.
        measure.forget(con, agent)
        log(f"  {agent}: closed on {here} but could not start on {there}: {why[:120]}")
        return False, True

    # The session that started must be the one on `there`. A process that
    # outlived the /exit is still on `here`, and recording the move would name
    # an account no process is on. Unreadable is not a contradiction.
    sessions = obs.live_sessions()
    pid = sessions.get(agent) if sessions else None
    actual = obs.account_of_process(pid) if pid else None
    if actual is not None and actual != there:
        measure.forget(con, agent)
        log(f"  {agent}: asked to move {here} -> {there} but its process is on "
            f"{actual} - not recorded as moved")
        return False, True

    # A session is live on the new account. Whether the line after it - handing
    # it its own handoff - also landed is not the same question, and treating a
    # failed handback as a failed move would clear the context of the session
    # that just started and park it until the *old* account rolls over.
    db.set_account(con, agent, there)
    # A compare-and-set, not a read then a write: the hook can land in the I/O
    # above, and a plain write here would still have a window to clobber it in
    # - the read cannot be trusted a moment after it is taken. False means
    # RUNNING is what is there instead, and the row is left exactly as the
    # hook set it. Only RUNNING, not STALLED too: a STALLED row already took
    # the early return above (closed_at is never cleared on the way to it), so
    # this line is never reached while it stands.
    hook_running = not db.set_state_unless_running(
        con, agent, QUEUED, detail=f"moved {here} -> {there} ({trigger.kind})")
    if hook_running:
        fresh = db.get_agent_state(con, agent)
        # A closed mark left over from a wake that timed out before the hook
        # fired is stale now: the session is confirmed up, so it comes off,
        # the same as any other session found running (`sweep`). The wake's
        # own bookkeeping for a failed attempt is stale the same way - it
        # never reached clear_handoff or reset_counter because it believed
        # the wake had failed, and the hook says otherwise.
        if fresh and fresh["closed_at"]:
            db.clear_closed(con, agent)
        if not woke:
            db.reset_counter(con, agent, "wake_failures")
            db.set_handoff(con, agent, None)
    db.add_event(con, agent, "moved_account", {"from": here, "to": there,
                                               "trigger": trigger.kind})
    log(f"  {agent}: moved {here} -> {there} ({trigger.kind}) - {picked_why}"
        + ("" if woke or hook_running
           else f" - but it was not handed its handoff: {why[:80]}"))
    return True, False


def _perform_handoff(con, agent: str, trigger: triggers.Trigger, log,
                     dest: str | None = None) -> tuple[bool, bool]:
    """(ok, typed). `ok` is whether the record landed - the streak's own
    signal, unrelated to whether anything was typed. `typed` is the settle
    window's: a move types /exit whenever it happens at all, the fall-through
    path types /clear only when `_reclaim_context` says so, and the early
    "forced move did not happen" return types nothing. A caller that marks typed on `ok` instead marks it on a failed clear that
    never touched the terminal, and the next attempt is then held off by a
    settle window nothing here earned (#2064)."""
    hid = uuid.uuid4().hex[:12]
    acct = agents.current_account(agent, con)
    st = status.read(acct) if acct and agent in paths.list_agents() else None
    deadline = int(time.time()) + HANDOFF_DEADLINE_S
    db.start_handoff(con, hid, agent, st.session_id if st else None, trigger.kind,
                     deadline)
    db.set_agent_state(con, agent, HANDOFF, detail=trigger.reason)
    log(f"  {agent}: HANDOFF ({trigger.kind}) - {trigger.reason}")

    # Taken here, while the session is live: the move and the clear below both
    # end it. A degraded record is the failure edge out of HANDOFF that section
    # 8.2 insists on, and it is degraded only for a fact that could not be read.
    prior_handoff = db.get_handoff(con, agent)
    ok = _record_handoff(con, agent, hid, log)

    db.advance_handoff(con, hid, "extracted")

    # Where it goes next. An agent whose own account is out of room does not
    # have to sit out that window if another account has some. Here, after the
    # handoff is recorded, because this session is ending either way.
    moved, retry_soon = _move_to_room(con, agent, trigger, log, dest)
    if moved:
        db.advance_handoff(con, hid, "released", state="done" if ok else "degraded")
        db.bump_counter(con, agent, "continuations")
        if ok:
            # A move never clears context itself, so a good record is the
            # whole of this handoff - the streak ends here (see
            # _record_handoff for why it no longer resets itself).
            db.reset_counter(con, agent, "throttle_count")
        return ok, True
    landed = db.get_agent_state(con, agent)
    if trigger.kind == "forced" and landed and not landed["closed_at"]:
        # Nothing was ended, so there is nothing to park: the session is live
        # and a park would clear the context of an agent that never left.
        # The row goes back as it was: a record this attempt took would be
        # injected at the next launch as "you were interrupted".
        db.set_handoff(con, agent, prior_handoff)
        db.advance_handoff(con, hid, "released", state="degraded",
                           detail="forced move did not happen")
        db.set_agent_state(con, agent, IDLE, detail="forced move did not happen")
        return False, False
    # Return the context. Recording a handoff is only half of a handoff;
    # without this the context never drops, and a context trigger fires again
    # next tick and the agent hands off forever. /clear keeps the terminal,
    # worktree and account and empties only the conversation.
    cleared, why = _reclaim_context(con, agent, log)
    db.advance_handoff(con, hid, "extracted", detail=why)

    if ok and cleared:
        # A handoff that fully worked - record and clear both landed - ends
        # the streak. This is the only thing that clears it, so a run of
        # failures at either step accumulates until 8.6 escalates. Checked
        # here, after both steps, so a clear that fails right after a good
        # record cannot have its bump wiped out by the record's own success
        # (#1975).
        db.reset_counter(con, agent, "throttle_count")

    # Park until the usage window rolls over - or longer, if this agent keeps
    # degrading, so a broken one is not retried at full cadence forever. Never
    # against an assumed account's rollover: that ceiling belongs to a guess,
    # not to the account this agent may actually be spending, so it earns only
    # a short re-look instead.
    reading = agents.account_reading(agent, con)
    short_retry = reading is None or reading.source == "assumed" \
        or trigger.kind == "forced" or retry_soon
    if short_retry:
        # A forced move that ended the session and did not start it is not a
        # wall: the account it left had room, so there is no window to wait out.
        # `retry_soon` is the same shape: a destination had room, so whatever
        # stopped the move - a dialog blocking `/exit`, a session
        # that would not end - is about this terminal, not this account, and
        # may already be gone by the time this account's own window would
        # have rolled over. blocked.sweep() is already re-reading that screen
        # and pressing what it can (#996) every tick regardless of this park;
        # this only keeps the move itself from waiting hours to look again.
        wake = int(time.time()) + ASSUMED_ACCOUNT_RECHECK_S
    elif trigger.kind in USAGE_TRIGGERS or trigger.kind == BY_HAND_MARK:
        rst = status.read(reading.account)
        wake = triggers.wake_after(trigger.kind, rst.reset_5h, rst.reset_7d)
    else:
        # Not a usage wall and not a park a person asked for by hand: whatever
        # this trigger fired on (context) is already handled by the
        # clear above, so there is no window left to wait out - but the wake
        # is still what hands the agent its own record back
        # (`wake_agent`/`wake_prompt`), so this still parks, just with an
        # immediate wake instead of the account's reset (#1903, #1957).
        wake = int(time.time())
        if not cleared:
            # Unlike a usage wall or a hand park, nothing else here waits out
            # a real window - so a clear that keeps failing would retry at
            # full cadence forever with nothing counted against it. This
            # gives it the same streak, and the same eventual 8.6 escalation
            # to STALLED, that a failed record already gets (#1957 should-fix).
            db.bump_counter(con, agent, "throttle_count")
    # A hand park stays a hand park whatever ceiling its wake got - the mark,
    # not the timer, is what `w` reads to decide it may force this one early.
    label = MOVING_MARK if short_retry and trigger.kind != BY_HAND_MARK else trigger.kind
    fresh = db.get_agent_state(con, agent)
    streak = int(fresh["throttle_count"]) if fresh else 0
    if streak:
        # A broken handoff - or clear - is retried, never resumed at full
        # cadence: this is the one thing that still has to wait even when the
        # trigger itself (context) does not.
        backoff = backoff_for(streak)
        if int(time.time()) + backoff > wake:
            wake = int(time.time()) + backoff
            log(f"  {agent}: backing off {backoff}s after {streak} degraded handoff(s)")
    db.advance_handoff(con, hid, "released", state="done" if ok else "degraded")
    db.set_agent_state(con, agent, PARKED, wake_at=wake,
                       detail=f"{label}; wakes at {time.strftime('%H:%M', time.localtime(wake))}")
    # `close` and `wake` both leave a record of when and why; a park never did,
    # so `ocp events --agent` had nothing to show for it even though the
    # kind was already reserved for it (kept out of db.AGENT_ACTIVITY_KINDS,
    # same as the others). The account carries its own source, so a
    # park against an assumed one is diagnosable from the event alone rather
    # than needing the live row it was computed from, which has moved on by
    # the time anyone reads this.
    db.add_event(con, agent, "park", {"kind": trigger.kind, "wake_at": wake,
                                      "account": reading.account if reading else None,
                                      "account_source": reading.source if reading else None})
    db.bump_counter(con, agent, "continuations")  # 8.5: handoff is not a failure
    log(f"  {agent}: PARKED until {time.strftime('%Y-%m-%d %H:%M', time.localtime(wake))}")
    return ok, cleared


# ---------------------------------------------------------------- tick
# The last reason each agent could not be woken, so a reason that lasts for
# hours is reported when it starts rather than on every tick.
_SAID: dict[str, str] = {}
_SAID_CLOSE: dict[str, str] = {}


def _running_account(agent: str) -> str | None:
    """The account this agent's running process is spending, or None when
    there is no process in the sweep's snapshot or its environment cannot be
    read - "I don't know", which never contradicts anything."""
    o = _seen.get(agent)
    return obs.account_of_process(o.session_pid) if o and o.session_pid else None


def _confirmed_reading(con, agent: str, reading, log):
    """The reading, unless the running process is on another account.

    The column names where a move sent the agent; the process is where it is.
    A move whose old process outlived its /exit leaves the two apart, and
    every judgement made on the column's account - the unpark first - is then
    about an account the session is not spending: keeper was woken six times
    into a rate limit that way. The process is the fact, so it wins and the
    column is set right, which lets the agent's next park or move start from
    where it really is."""
    actual = _running_account(agent)
    if reading is None or actual is None or actual == reading.account:
        return reading
    log(f"  {agent}: recorded on {reading.account} but its process is on "
        f"{actual} - judging it there")
    db.add_event(con, agent, "account_mismatch",
                 {"recorded": reading.account, "running": actual})
    db.set_account(con, agent, actual)
    return agents.AccountReading(actual, "recorded")


def _queued_or_idle(con, agent: str, when: str) -> str:
    """Write QUEUED when there is a request still queued or an answer this
    agent asked for and has not been told about, IDLE otherwise - and return
    which. That is the project's one definition of work waiting, not this
    helper's: `would_wake`, `_move_plan` and `supervise` ask the same two
    questions, and the founder's screen lists what is queued and what is untold
    from the same two queries, so a row this calls IDLE is one the screen shows
    nothing queued and nothing untold for.
    Shared by every writer of a due wake_at (a stale one, from an unrelated
    throttle or park, can come due after the turn it was set during already
    went idle on its own - Stop only clears RUNNING), so QUEUED stays a claim
    about the queue everywhere it is made, not only where #645 was noticed."""
    if db.queued_for(con, agent) or db.unannounced_answers(con, agent):
        db.set_agent_state(con, agent, QUEUED, wake_at=None, detail=when)
        return QUEUED
    db.set_agent_state(con, agent, IDLE, wake_at=None, detail=f"{when}; no requests waiting")
    return IDLE


def supervise(con, agent: str, log, now: int, samples: dict,
              actions: list[str]) -> None:
    """One agent's turn in a tick.

    Its own function so that the whole of it - three calls out to Orca among
    them - sits inside the single try in `tick` that treats a failure as this
    agent's failure.
    """
    row = db.get_agent_state(con, agent)
    state = row["state"] if row else IDLE

    if state == STALLED:
        return  # 8.6: only a human clears this
    if state not in EVALUATED:
        return

    # An agent mid-handoff is excluded from trigger evaluation (8.9).
    if db.in_flight_for(con, agent):
        return

    # A turn still running when the park began can hit the usage wall on its
    # own afterward, well before wake_at comes due - the dialog that puts on
    # screen is not one a person is answering (`usage_limit_open`'s own
    # promise), so nothing is lost by pressing past it here rather than
    # leaving it up until the wake that would otherwise be the first thing to
    # look (#2087).
    if state in (PARKED, THROTTLED, DEGRADED) and row["terminal"] and not row["closed_at"] \
            and orca.available():
        with lock.terminal(agent) as mine:
            if mine:
                _past_dialog(con, agent, log, only_limit=True)

    # The live reading this tick judges the agent against, not the account a
    # resume would use: `account_reading` prefers a fresh report from the
    # agent's own running session over a column nothing has confirmed, so an
    # assumed guess never stands in for a running session's actual account
    # when there is better evidence of it. None when nothing says where it is.
    reading = _confirmed_reading(con, agent, agents.account_reading(agent, con), log)
    acct = reading.account if reading else None
    st = status.read_for_agent(agent, acct)
    ctx_limit = status.effective_context_limit(agents.read(agent), st.model_id)

    # An agent whose *closed* session was restored in this tick is judged on the
    # next one: `st` was read above, before the wake, and a closed agent's
    # snapshot is stale by construction. A parked agent that was never closed
    # kept its session and its status line, so its reading is current and there
    # is nothing to defer - deferring it anyway would only slow the degraded
    # retry loop down by a tick per attempt.
    woke_now = False

    # Wake anything whose deadline has come due, whatever its state.
    if row and row["wake_at"] and row["wake_at"] <= now and \
            state in (PARKED, THROTTLED, DEGRADED):
        # The streak is NOT cleared here. Waking is the normal end of every
        # park, so resetting on wake pinned throttle_count at 1 and made
        # the escalation in 8.6 unreachable. It is cleared by a handoff
        # that actually succeeds.
        # A parked agent can also be a closed one, and then this wake restores
        # the session - which is the case the gate below exists for.
        restored = bool(row and row["closed_at"])
        woke, why = wake_agent(con, agent, log)
        if restored and not woke:
            # Same rule reconcile's step 2 writes: for an agent whose session
            # was closed, the wake is the whole point of the deadline, and the
            # deadline is the only thing that makes it due again. Clearing it
            # on a wake that did not work is how a park slept through every
            # later tick until something else happened to want the agent.
            log(f"  {agent}: stays {state}, still due")
            return
        # The prompt has been typed by now, so the agent may already have
        # answered it: its own UserPromptSubmit writes RUNNING, and RUNNING is
        # the label that keeps the daemon out of a working agent's way. Only
        # write QUEUED if nothing has claimed it since.
        fresh = db.get_agent_state(con, agent)
        if fresh and fresh["state"] != state:
            state = fresh["state"]
            woke_now = woke_now or restored
            return
        # QUEUED is a claim about the queue, so the write has to look at it
        # (`_queued_or_idle`) - a stale wake_at can come due after the turn it
        # was set during already went idle on its own, since Stop only clears
        # RUNNING.
        new_state = _queued_or_idle(con, agent, "wake_at reached")
        why_word = "window rolled over" if new_state == QUEUED else "nothing queued"
        actions.append(f"{agent}: {state} -> {new_state} ({why_word})")
        log(f"  {agent}: {state} -> {new_state} ({why_word})")
        state = new_state
        row = db.get_agent_state(con, agent)
        woke_now = woke_now or restored

    # An agent with nowhere to run cannot be given work by anyone but a human,
    # which makes every order from another agent a request to press a key. That
    # is what sends an agent looking for a subagent instead: a thing it can
    # make itself, that dies with its session and appears on no screen. The one
    # fact the daemon needs to do it instead is which repository, and that is
    # `repo:` in agent.yaml.
    # An answer counts as work here, not only a request: a wake puts a session
    # back into a terminal the agent still has, so an agent that lost its
    # terminal while waiting on an answer has no way back at all.
    started_now = False
    if not (row and row["terminal"]) and (db.queued_for(con, agent)
                                          or db.unannounced_answers(con, agent)):
        actions.append(_start_for_work(con, agent, log))
        row = db.get_agent_state(con, agent)
        started_now = bool(row and row["terminal"])

    # A closed session cannot read its own inbox, so the arrival of work is
    # what has to bring it back. Without this an `ocp ask` queues into a
    # terminal with nobody in it and waits forever, which is worse than the
    # memory it was saving: reviewer sat closed with two requests pending.
    # Not one started a moment ago: that terminal is a fresh session booting,
    # the closed mark on it is stale until the sweep sees the session, and a
    # wake typed into a TUI that is still coming up loses its opening
    # characters and leaves the rest in the input box, unsent.
    if row and row["closed_at"] and not started_now:
        waiting = db.queued_for(con, agent)
        # An agent that asked something, parked on the answer and was then
        # closed is waiting on a reply, not on work - and resolving its request
        # writes QUEUED over the park and clears the wake_at that would have
        # brought it back. Without this, nothing in the system ever wakes it.
        answers = db.unannounced_answers(con, agent)
        if waiting or answers:
            ok, why = wake_agent(con, agent, log)
            what = (f"{len(waiting)} waiting request(s)" if waiting
                    else f"{len(answers)} answer(s) it asked for")
            actions.append(f"{agent}: woken for {what}"
                           if ok else f"{agent}: could not wake - {why}")
            if not ok and _SAID.get(agent) != why:
                # Said out loud, because this is the tick doing nothing about
                # something that is waiting - and said once, because the tick
                # comes round every few seconds and two of these reasons last
                # for hours. `_wake_agent` logs some of them itself; this is
                # the one place that reports the ones it returns silently.
                _SAID[agent] = why
                log(f"  {agent}: {what} and could not be woken - {why}")
            if ok:
                _SAID.pop(agent, None)
            if ok:
                row = db.get_agent_state(con, agent)
                woke_now = True

    # Work that has arrived is delivered before anything else is decided:
    # an agent that is about to be told it has work is not idle. An agent woken
    # in this tick is told on the next one: it is still reading the prompt that
    # brought it back, and typing at it now would leave the text in its input
    # box unsent.
    # An agent whose turn ended on the usage limit cannot run one: typing work
    # into it would start a turn that fails at once, mark the request
    # delivered, and clear the very mark that says it is at the wall.
    track_background_work(con, agent, row)
    row = db.get_agent_state(con, agent)
    at_wall = events.at_limit(con, agent)
    open_idle = state not in (RUNNING, HANDOFF) and not woke_now and not at_wall \
        and not (row and row["closed_at"])
    if open_idle:
        # An answer first: an agent that asked something and parked is waiting
        # on that, and telling it about new work while its own question is
        # still unanswered is the wrong order.
        closed = _cold_before_typing(con, agent, row, now, st, log)
        if closed:
            actions.append(f"{agent}: cache expired; session closed, the wake delivers")
            row = db.get_agent_state(con, agent)
        if closed is None:
            deliver_answers(con, agent, log)
            deliver_queued(con, agent, log)
            nudge_forgotten_resolve(con, agent, row, now, log)

    # Memory is the scarce resource on one laptop running a team, and an
    # idle session spends it while earning nothing: it holds its context,
    # costs no tokens, and answers no one. Closing it is not a handoff -
    # nothing is cleared - and waking it resumes the same session, so the
    # only thing that changes is who has the memory.
    if not at_wall and should_close(con, agent, state, row, now, st):
        ok, why = close_agent(con, agent, log, st)
        if ok:
            actions.append(f"{agent}: session closed while idle")
            row = db.get_agent_state(con, agent)
        elif _SAID_CLOSE.get(agent) != why:
            # Once per reason: a refusal that leaves no `close_failed` behind, a
            # dialog still open above all, comes round on every tick.
            _SAID_CLOSE[agent] = why
            log(f"  {agent}: idle but not closed - {why}")

    measure.settle(con, agent)

    # Keep a short burn-rate history per account: it is how fast the account is
    # filling, which the pick weighs (`pick_account`) and the screens show.
    # Only a figure this payload actually reported may enter the burn
    # history, stamped with when it was observed. A carried figure pairs an
    # unchanging percentage with a collected_at that moves every tick: the
    # buffer fills with flat samples and the measured rate falls to zero for an
    # account that was climbing.
    hist = samples.setdefault(acct, [])
    if st.is_live_reading:
        if not hist or hist[-1][0] != st.rl_at:
            hist.append((st.rl_at, st.pct_5h))
            del hist[:-triggers.BURN_WINDOW]
    burn = triggers.burn_rate_per_s(hist)
    # Stamped with the newest sample's time, so a pace nobody is keeping ages out.
    if hist:
        status.write_burn(acct, burn, hist[-1][0])

    # Un-parks a usage park - PARKED only, and only when the handoff that put
    # it there named a usage trigger, read off that handoff since nothing on
    # the row itself says why - the moment that same reading, evaluated the
    # way the park itself was, no longer fires. Never on an assumed or unknown
    # account: `reading` above is a guess exactly when there is neither a
    # recorded nor a fresh reported account to act on, and a guess is not
    # evidence enough to wake this agent into.
    if not woke_now and row and row["terminal"] and not row["closed_at"] \
            and state == PARKED and reading and reading.source != "assumed" \
            and orca.available() and orca.alive(row["terminal"]):
        last = db.last_handoff(con, agent)
        verdict = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_used,
                                    pct_7d=st.trusted_pct_7d, ctx_limit=ctx_limit)
        movable = last and last["trigger_kind"] in MOVABLE_TRIGGERS
        # Still at the wall here, but another account may have rolled over or
        # freed a slot since the park: if the work is worth a cold start, move
        # now rather than wait out this account's own window.
        if movable and verdict.fired and verdict.kind in MOVABLE_TRIGGERS \
                and _move_plan(con, agent, verdict.kind)[0]:
            with lock.terminal(agent) as mine:
                # require_idle=False: `_move_to_room` ends the session itself,
                # through `_end_session`, which does its own fresh idle read
                # inline where `/exit` is typed - require_settled=True is what
                # actually applies here, against a delivery just typed.
                moved = bool(mine) and not _type_gate(con, agent, None, require_idle=False,
                                                      require_settled=True) \
                    and _move_to_room(con, agent, verdict, log)[0]
                if moved:
                    _mark_typed(con, agent)
            if moved:
                actions.append(f"{agent}: moved off a parked account")
                return
        if movable and not verdict.fired:
            new_state = _queued_or_idle(con, agent, "usage recovered")
            actions.append(f"{agent}: {state} -> {new_state} (usage recovered)")
            log(f"  {agent}: {state} -> {new_state} (usage recovered)")
            row = db.get_agent_state(con, agent)
            state = new_state
            woke_now = True

    if state in (PARKED, THROTTLED, DEGRADED):
        return  # waiting on wake_at, already handled above

    # What sits at the other end of a closed agent is a shell prompt, and a
    # handoff document is full of backticks - handing one off would type
    # command substitutions at bash. `should_close` is careful about five
    # things; this needs the same care about one.
    #
    # An agent woken in this tick is judged on the next one, for the same reason
    # it is told about its inbox on the next one: `st` was read before the wake,
    # and a closed agent's snapshot is stale by construction, so evaluating it
    # here would hand off the session we just restored - and leave the work that
    # caused the wake still unread.
    if woke_now or not triggers_apply(row):
        return

    # trusted_* is None only when we genuinely do not know: no snapshot, or
    # one too old to believe. Unknown is not a wall, so it does not fire; an
    # account whose window is merely quiet reads as 0, not as unknown.
    t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_used,
                          pct_7d=st.trusted_pct_7d, ctx_limit=ctx_limit)
    # A turn that ended on the usage limit means the agent is at the wall
    # whatever the reading says (it can lag, or be the other window): move it
    # the same way a trigger would.
    stopped = at_wall
    if stopped and not (t.fired and t.kind in MOVABLE_TRIGGERS):
        text = stopped["limit"]
        t = triggers.Trigger(True, "usage_7d" if "week" in text.lower() else "usage",
                             f"turn ended on its usage limit: {text} "
                             f"(read from {stopped.get('limit_source', '?')})")
    if t.fired:
        ok = perform_handoff(con, agent, t, log)
        actions.append(f"{agent}: handoff ({t.kind}){'' if ok else ' -> degraded'}")

        fresh = db.get_agent_state(con, agent)
        if fresh and fresh["throttle_count"] >= MAX_THROTTLE_STREAK:
            # 8.6: escalate only after repeated automatic recovery failed.
            db.set_agent_state(con, agent, STALLED,
                               detail=f"{fresh['throttle_count']} consecutive degraded handoffs"
                               + blocked.dialog_note(con, agent))
            actions.append(f"{agent}: STALLED after {fresh['throttle_count']} degraded handoffs")
            log(f"  {agent}: STALLED - needs a human")
        elif fresh and fresh["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            db.set_agent_state(con, agent, STALLED,
                               detail=f"{fresh['failed_attempts']} failed attempts")
            actions.append(f"{agent}: STALLED after {fresh['failed_attempts']} failed attempts")


_last_rollover_day = {"day": ""}


def collect_snapshots(con, log) -> None:
    """Pull each live agent's context and session identity from its transcript.

    The statusLine pushed these once per turn; this reads them out of the file
    the session is writing anyway (`collect`, D11). Measured on the fleet: the
    same token count to the digit on every agent that had one, 1-2ms apiece.

    Only for an agent whose session is open. The snapshot's `collected_at` is a
    heartbeat - `status.stale` reads a fresh one as "a session is alive here" -
    and refreshing it for a closed agent would tell every reader the session is
    still up. A closed agent's file ages into stale exactly as it does now.
    """
    for row in db.all_agent_states(con):
        if row["closed_at"]:
            continue
        cwd = spawnmod.worktree_path(row["worktree"])
        if not cwd:
            continue
        try:
            collect.collect(row["agent"], cwd)
        except OSError as e:  # noqa: BLE001 - one unreadable transcript is not the tick's failure
            log(f"  collect: {row['agent']}: {e}")


def usage_due(con, acct: str, st: status.AccountStatus, now: int) -> str | None:
    """Why this account's rate limits need asking about, or None.

    There is no event for "your usage changed", so this is the nearest thing:
    the figures move when a turn is taken and at no other time, and a turn is
    something our hooks already record. An account nobody is working on is not
    asked at all - until its cadence comes round, because the spending we cannot
    see leaves no event to go by.
    """
    if not st.exists or st.rl_at is None:
        return "no reading yet"
    if now - st.rl_at >= USAGE_MAX_AGE_S:
        return f"last read {status.ago(now - st.rl_at)} ago"
    for window, reset in (("5h", st.reset_5h), ("7d", st.reset_7d)):
        if reset is not None and reset <= now:
            return f"its {window} window has ended"
    last = db.last_account_activity(con, acct)
    # Both are whole seconds, so a turn inside the same second as the reading
    # cannot be placed on either side of it. Asked, not assumed: this gates
    # handing out work, and the cooldown above bounds what the tie costs.
    if last is not None and last >= st.rl_at:
        return "a turn since the last reading"
    return None


def poll_usage(con, log) -> None:
    """Ask the accounts due for a reading, over their own OAuth credential.

    This is the half of the collector's payload the transcript cannot give
    (D11): a session learns its rate limits only from its own API responses, so
    a quiet account went silent, and until #1447 the answer was `probe.py` -
    boot a session on it and spend a real turn to force a number out. The
    endpoint answers for any account with nothing running at all, which is what
    makes asking cheap enough to do on a schedule, and what retired the probe.

    At most one *successful* reading per tick - five HTTPS GETs in one tick
    would put five timeouts in the path of a 30s loop - but a failing account
    must not consume that budget for every account behind it in
    `list_accounts` order (founder #1811, #1799 part 2): with four accounts
    failing every try (the endpoint itself 429s an account at its own wall, or
    a token stays dead), the old `return` on the first failure meant the loop
    never reached the rest - omega/sigma/theta answered fine and went unpolled
    for three hours because alpha/beta/delta/gamma came first in the list and
    always failed. A failing account keeps its own cursor moving instead
    (`continue`), and is backed off exponentially the way a stalled retry
    anywhere else in ocp is, so it costs less of the budget the longer it
    keeps failing rather than the same slice of it forever.
    """
    now = int(time.time())
    for acct in paths.list_accounts():
        try:
            if _poll_account(con, acct, now, log):
                return
        except Exception as e:  # noqa: BLE001 - one account's reading is not the tick's failure
            log(f"  usage: {acct}: reading raised {type(e).__name__}: {e}")
            _usage_backoff(con, acct, now, log)


def _usage_backoff(con, acct: str, now: int, log) -> None:
    streak = int(db.get_meta(con, f"usage-fail:{acct}") or 0) + 1
    db.set_meta(con, f"usage-fail:{acct}", str(streak))
    backoff = min(USAGE_POLL_S * 2 ** (streak - 1), USAGE_FAIL_BACKOFF_CAP_S)
    db.set_cursor(con, f"usage-poll:{acct}", now + backoff)
    log(f"  usage: {acct}: could not ask (next try in {backoff}s)")


def _ended_window(st, reading, now: int) -> tuple[str, int] | None:
    """The (window, reset) whose end is worth a real turn, or None.

    A window ended by the reading ocp holds; but the plain reading just
    fetched has the last word - a window it still reports exhausted has not
    reset, whatever the older figure said, and a turn on it is the spend the
    wall exists to stop.
    """
    for window, held, fresh_pct, fresh_reset, wall in (
            ("5h", st.reset_5h, reading.pct_5h, reading.reset_5h, status.PCT_5H_BLOCK),
            ("7d", st.reset_7d, reading.pct_7d, reading.reset_7d, status.PCT_7D_BLOCK)):
        if held is None or held > now:
            continue
        if fresh_pct is not None and fresh_pct >= wall and (fresh_reset is None or fresh_reset > now):
            continue
        return window, held
    return None


def _poll_account(con, acct: str, now: int, log) -> bool:
    """One account's turn in `poll_usage`; True when it took the tick's one
    successful reading."""
    if now < db.get_cursor(con, f"usage-poll:{acct}"):
        return False
    st = status.read(acct)
    if st.unmetered:
        return False
    why = usage_due(con, acct, st, now)
    if why is None:
        return False
    if not accounts.logged_in(acct):
        db.set_cursor(con, f"usage-poll:{acct}", now + LOGIN_CHECK_COOLDOWN_S)
        return False
    # No renewal on the common path: a session on the account keeps its own
    # token fresh, and two writers of one keychain entry race.
    reading = usage_api.read(acct, refresh=False)
    if reading is None and now >= db.get_cursor(con, f"usage-refresh:{acct}"):
        db.set_cursor(con, f"usage-refresh:{acct}", now + USAGE_REFRESH_COOLDOWN_S)
        reading = usage_api.read(acct, refresh=True)
    if reading is None:
        # #1885: a dead token reads as None forever without a real turn to
        # renew it. recover() records on success itself, so a success here
        # is handled exactly like an ordinary successful reading below.
        ok, msg = recovermod.recover(acct)
        if ok:
            db.set_meta(con, f"usage-fail:{acct}", None)
            db.set_cursor(con, f"usage-poll:{acct}", now + USAGE_POLL_S)
            log(f"  usage: {msg}")
            return True
        _usage_backoff(con, acct, now, log)
        return False
    ended = _ended_window(st, reading, now)
    if ended and db.get_meta(con, f"reset-ping:{acct}:{ended[0]}") != str(ended[1]):
        # A stale reading can still report success right up to the boundary
        # (#1885, task #76): nothing else forces the account to actually roll
        # over to its new window, so spend the same real turn recover() spends
        # on a dead token. A refusal here (off, already needs re-login, at the
        # 7d wall) is not a failure of this poll - the plain reading just
        # fetched still stands. Once per reset: the turn that succeeded is
        # remembered against the reset it answered, so a server that keeps
        # naming the old one does not get a turn on every poll.
        ok, msg = recovermod.recover(acct)
        if ok:
            db.set_meta(con, f"reset-ping:{acct}:{ended[0]}", str(ended[1]))
            db.set_meta(con, f"usage-fail:{acct}", None)
            db.set_cursor(con, f"usage-poll:{acct}", now + USAGE_POLL_S)
            log(f"  usage: {msg}")
            return True
    db.set_meta(con, f"usage-fail:{acct}", None)
    db.set_cursor(con, f"usage-poll:{acct}", now + USAGE_POLL_S)
    usage_api.record(reading)
    log(f"  usage: {acct}: {why} - 5h {reading.pct_5h}% 7d {reading.pct_7d}%")
    return True


# ---------------------------------------------------------------- dev mode
# Task #64 (founder #2120, #2121): the daemon restarting itself, in place,
# once it notices its own code changed - so a merge no longer needs a human
# to kill and relaunch it by hand. Off by default; `ocp dev-mode on` turns it
# on (config.yaml, next to `active_cap`).
DEV_MODE_KEY = "dev_mode"


def dev_mode_enabled() -> bool:
    return bool(load._config().get(DEV_MODE_KEY))


def set_dev_mode(on: bool) -> None:
    # load._config()'s own read: same file `active_cap` writes to
    # (load.set_cap), same failure if it fails to parse - not new risk here,
    # just a second writer of it.
    cfg = load._config()
    cfg[DEV_MODE_KEY] = bool(on)
    paths.config_file().write_text(dump(cfg))


def _watched_files(root: Path) -> list[Path]:
    """ocp/*.py, bin/ocp, requirements.txt - task #64's own list, read from
    the checkout the daemon is running from rather than named once and left
    to rot as files are added."""
    files = sorted((root / "ocp").glob("*.py"))
    files.append(root / "bin" / "ocp")
    files.append(root / "requirements.txt")
    return files


def _code_fingerprint(root: Path) -> tuple:
    """mtime+size of every watched file - one stat() apiece, not a read,
    since this runs every tick dev mode is on. A file gone between listing
    and stat()ing it - a merge in the main checkout unlinks and rewrites
    files, and that is exactly when this runs - is left out of the
    fingerprint rather than raised: still gone or written back by the next
    tick, either way that tick's fingerprint differs from this one's."""
    found = []
    for f in _watched_files(root):
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        found.append((str(f.relative_to(root)), st.st_mtime_ns, st.st_size))
    return tuple(sorted(found))


def _dev_import_check(log) -> bool:
    """Whether the code on disk right now would still start: `bin/ocp
    --help`, the same entrypoint a restart execs, so this exercises the real
    venv selection and the real import of every module - not a read of the
    diff. Nothing it does is acted on; argparse builds the parser and exits.
    A version that fails this is not restarted into; the old process (already
    imported, already running) keeps ticking and this says why not."""
    exe = service.ocp_executable()
    try:
        res = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"dev-mode: import check could not run - staying on the running version ({e})")
        return False
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()
        why = tail[-1] if tail else f"exit {res.returncode}, no output"
        log(f"dev-mode: new code fails to import, staying on the running version - {why}")
        return False
    return True


def _dev_mode_should_restart(state: dict, log) -> bool:
    """Called once a tick. `state` is `run`'s own dict, carried across calls:
    `fp` is the fingerprint this process started (or was last found clean)
    with, `warned` is one that already failed the import check, so a version
    still broken is not re-checked and re-logged every tick until it is."""
    if not dev_mode_enabled():
        state["fp"] = None
        state["warned"] = None
        return False
    root = paths.repo_root()
    fp = _code_fingerprint(root)
    if state["fp"] is None:
        state["fp"] = fp  # dev mode just turned on, or the daemon just started - this is the baseline
        return False
    if fp == state["fp"] or fp == state["warned"]:
        return False
    if _dev_import_check(log):
        log("dev-mode: code changed - finishing this tick, then restarting")
        return True
    state["warned"] = fp
    return False


def _dev_mode_restart() -> None:
    """Exec the same entrypoint with the same arguments, in this process.

    Not a kill from outside (#2121): by the time this runs, the tick it
    interrupted has already finished, `run` has already released the daemon
    lock and closed the database connection the way a clean exit always
    does - the only difference is what runs next. `os.execv` also means
    launchd never sees an exit to restart from, so KeepAlive is irrelevant
    either way: this is the one restart path, whether launchd is supervising
    or the daemon is running by hand.
    """
    exe = service.ocp_executable()
    os.execv(exe, [exe] + sys.argv[1:])


def tick(con, log, samples: dict | None = None) -> Tick:
    now = int(time.time())
    actions: list[str] = []
    samples = samples if samples is not None else {}

    # Before anything is decided on what we believe, check what is true. Two
    # calls out, whatever the size of the team, and they cost 0.2s together.
    sweep(con, log, actions)

    # And what is true includes the two readings every decision below is made
    # on. Both are pulls now (D11), so they happen here rather than arriving
    # whenever a session happens to render a status line.
    collect_snapshots(con, log)
    try:
        poll_usage(con, log)
    except Exception as e:  # noqa: BLE001 - a failure outside any one account is not the tick's failure either
        log(f"  usage: poll failed: {type(e).__name__}: {e}")

    # Link a file added to shared/.claude into accounts registered before it.
    try:
        for acct, linked in accounts.relink_all(log).items():
            log(f"  relinked {acct}: {', '.join(linked)}")
    except Exception as e:  # noqa: BLE001 - a bad account here is not the tick's failure
        log(f"  relink: {e}")

    # A handoff past its deadline must fail here, not only at startup.
    actions += expire_handoffs(con, log)

    # Midnight rollover (section 5). Nothing else calls it, and without it
    # yesterday's "in progress" stays in progress forever - and every fold
    # after midnight has to keep repeating yesterday's completed items to pass
    # the monotonicity check, so the digest grows without bound.
    day = foldmod.today()
    if _last_rollover_day["day"] and _last_rollover_day["day"] != day:
        for agent in paths.list_agents():
            ok, msg = foldmod.rollover(agent)
            if ok and "already exists" not in msg:
                actions.append(msg)
                log(f"  rollover: {msg}")
    _last_rollover_day["day"] = day

    for agent in paths.list_agents():
        try:
            supervise(con, agent, log, now, samples, actions)
        except Exception as e:  # noqa: BLE001
            # A real failure, not a resource shortage: bad config, unreadable
            # status, an Orca call that raised. Bare Exception, deliberately
            # wider than reconcile()'s two callers of _stall_agent - a live
            # turn can fail in more ways than a config read can, and this is
            # still the outermost per-agent loop, so nothing after it treats
            # this as a case to narrow. Catching it per agent is the point -
            # supervising one agent is not allowed to end supervision of
            # every agent after it in the list.
            _stall_agent(con, agent, log, actions, e, "error")

    blocked.sweep(con, log, now)
    observe_burn(con, log)
    return Tick(now, actions)


def observe_burn(con, log) -> None:
    """Feed each account's reading and who is working on it to the estimate of
    what one more agent costs (`burnrate`), and log it (`db.record_usage_sample`,
    #1113) for a replay nobody has thought to ask for yet - the same read,
    spent twice, so neither costs an account anything the other did not
    already. After the agents, so it sees the states this tick left."""
    working = load.active_by_account(con)
    firsts = load.first_turn_agents(con)
    for acct in paths.list_accounts():
        st = status.read(acct)
        try:
            burnrate.observe(con, acct, working.get(acct, []), st, firsts)
        except Exception as e:  # noqa: BLE001 - a bad reading on one account is not the tick's failure
            log(f"  burn: {acct}: {e}")
        try:
            db.record_usage_sample(con, acct, st)
        except Exception as e:  # noqa: BLE001 - same: one account's row is not the tick's failure
            log(f"  sample: {acct}: {e}")
    try:
        db.prune_usage_samples(con)
    except Exception as e:  # noqa: BLE001
        log(f"  sample: prune failed: {e}")
    for acct in paths.list_accounts():
        try:
            tokenlog.ingest(con, acct)
        except Exception as e:  # noqa: BLE001 - a record that misses a slice is not the tick's failure
            log(f"  tokens: {acct}: {e}")
    try:
        tokenlog.prune(con)
    except Exception as e:  # noqa: BLE001
        log(f"  tokens: prune failed: {e}")
    try:
        burnrate.refresh_week_ratios(con)
    except Exception as e:  # noqa: BLE001 - no ratio only means the week is judged at its wall
        log(f"  sample: week ratio failed: {e}")


# ---------------------------------------------------------------- loop
def run(once: bool = False, verbose: bool = True, max_ticks: int | None = None) -> int:
    """`max_ticks` counts this process's own ticks. A dev-mode restart
    (task #64) execs a fresh process, so a run that restarts mid-way starts
    its count over at 0 and runs past `max_ticks` overall."""
    def log(msg: str) -> None:
        # Every line here can carry text an agent's own screen wrote - a
        # dialog's title among it - and this is stdout, tailed raw by whoever
        # watches the daemon log; the same hole auditor found in a request
        # body, at the sink it reaches most often.
        if verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {textsafe.oneline(msg)}", flush=True)

    # Set inside the lock below, read after it releases - restarting (task
    # #64) happens only once the lock and the db connection are given up the
    # same way a clean exit always gives them up.
    restarting = False

    # The lock decides who supervises, and records who: a daemon started by
    # hand, or orphaned when launchd reloaded the job under it, is still a
    # second supervisor.
    with lock.singleton(DAEMON_LOCK) as only:
        if not only:
            other = running_elsewhere()
            named = f" (pid {other})" if other else ""
            # Through the same logger as every other startup line, and sharing
            # its "daemon starting" wording, so a launchd job that respawns
            # into this refusal every ThrottleInterval is still visible to
            # crash_loop_detected - a silent `print` here is what a
            # launchd-driven crash loop looks like from the outside: healthy on
            # every check, dead the whole time.
            log(f"daemon starting - already running{named}, exiting")
            return 1
        stopping = {"now": False}
        # Separate from `stopping`: a restart also sets that flag to break
        # the loop, and `signalled` is what tells the two apart afterward -
        # a real SIGTERM/SIGINT must win over a restart whose 30s import
        # check (bin/ocp --help, subprocess.run's own timeout) was still
        # running when the signal arrived.
        signalled = {"now": False}

        def stop(signum, _frame):
            stopping["now"] = True
            signalled["now"] = True
            log(f"signal {signum} - stopping after this tick")

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, stop)
            except ValueError:
                pass  # not on the main thread

        con = db.connect()
        samples: dict = {}
        dev_state: dict = {"fp": None, "warned": None}
        try:
            log("daemon starting - reconciling")
            reconcile(con, log)

            ticks = 0
            while not stopping["now"]:
                t = tick(con, log, samples=samples)
                ticks += 1
                # A supervisor that runs for weeks must not fill the disk.
                # Cheap when under threshold: two stat() calls and nothing
                # else. Caught separately so a rotation failure (e.g. a full
                # disk) degrades logging rather than taking the whole daemon
                # down with it.
                try:
                    service.rotate_logs()
                except OSError as e:
                    log(f"log rotation failed: {e}")
                # Same reasoning as rotate_logs above: a tree mid-checkout
                # (a merge in the main checkout unlinks and rewrites files
                # while this is exactly what runs) must degrade dev mode,
                # not the daemon.
                try:
                    should_restart = _dev_mode_should_restart(dev_state, log)
                except Exception as e:  # noqa: BLE001
                    log(f"dev-mode check failed: {e}")
                    should_restart = False
                if should_restart:
                    if signalled["now"]:
                        log("dev-mode: restart skipped - a stop signal arrived during the import check")
                    else:
                        stopping["now"] = True
                        restarting = True
                if once or (max_ticks and ticks >= max_ticks):
                    break
                if not stopping["now"]:
                    time.sleep(triggers.POLL_S)
            log(f"stopped after {ticks} tick(s)")
        finally:
            con.close()

    if restarting:
        _dev_mode_restart()  # execs; does not return
    return 0
