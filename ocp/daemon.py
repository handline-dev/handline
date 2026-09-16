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
missed while down, stale pidfiles, and lost timers at the same time. In-memory
timers are an optimization and never the source of truth.

Handoff progress is committed per step (8.9), so a restart resumes from the
step it reached. An agent with an in-flight handoff is excluded from trigger
evaluation, and a second trigger merges into the existing one as an added
reason rather than starting a competing handoff.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass

from . import agents, db, fold as foldmod, orca, paths, status, triggers

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

# How long a DEGRADED agent waits before the daemon retries it.
DEGRADED_RETRY_S = 600

MAX_FAILED_ATTEMPTS = 3      # 8.5: real failures only
MAX_THROTTLE_STREAK = 5      # 8.6: then escalate to a human gate
HANDOFF_DEADLINE_S = 90      # 8.3

# 8.6: a repeatedly-degrading agent backs off rather than retrying at the same
# cadence forever. Doubling from a minute, capped so it always comes back.
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600

# A handoff costs a fold, so two in quick succession is never right - the usage
# that triggered the first cannot have meaningfully changed. This is a circuit
# breaker, not a policy: if some other path ever un-parks an agent the way
# SessionStart once did, this stops it from burning tokens in a loop.
MIN_HANDOFF_INTERVAL_S = 120


def backoff_for(streak: int) -> int:
    """Seconds to wait before retrying, given how many attempts have failed."""
    if streak <= 0:
        return 0
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (streak - 1)))


# ---------------------------------------------------------------- pidfile
def boot_time() -> int:
    """Boot time, so a recycled PID cannot be mistaken for a live daemon."""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=5).stdout
        for part in out.replace("{", "").replace("}", "").split(","):
            if "sec" in part:
                return int(part.split("=")[1].strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    try:  # Linux fallback
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def write_pidfile() -> None:
    paths.daemon_pid().write_text(json.dumps({"pid": os.getpid(), "boot": boot_time()}))


def read_pidfile() -> dict | None:
    p = paths.daemon_pid()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def running_elsewhere() -> int | None:
    """PID of a live daemon, or None. Stale pidfiles are reclaimed (8.4)."""
    info = read_pidfile()
    if not info:
        return None
    pid, boot = info.get("pid"), info.get("boot", 0)
    if not pid:
        return None
    # A pidfile from before the last boot cannot describe a live process. If we
    # cannot read the boot time (either side being 0), we do not know that, and
    # guessing "stale" would let a second daemon start alongside a live one.
    current_boot = boot_time()
    if boot and current_boot and boot != current_boot:
        paths.daemon_pid().unlink(missing_ok=True)
        return None
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        paths.daemon_pid().unlink(missing_ok=True)
        return None
    return int(pid)


# ---------------------------------------------------------------- reconcile
@dataclass
class Tick:
    now: int
    actions: list[str]


def resume_handoff(con, row, log, use_model: bool = True) -> None:
    """Finish a handoff from the step it reached (8.9).

    Steps are ordered requested -> folded -> extracted -> released, and each is
    committed, so a restart picks up where the kill landed instead of redoing
    the fold or leaving the agent wedged in HANDOFF.
    """
    agent, step, hid = row["agent"], row["step"], row["id"]
    done_ok = True
    if step == "requested":
        ok, msg = foldmod.fold(agent, use_model=use_model)
        done_ok = ok
        if ok:
            db.advance_handoff(con, hid, "folded",
                               cursor_to=db.get_cursor(con, f"fold:{agent}"))
            db.reset_counter(con, agent, "throttle_count")
        else:
            db.advance_handoff(con, hid, "folded", state="degraded", detail=msg[:300])
            db.bump_counter(con, agent, "throttle_count")
    if step in ("requested", "folded"):
        db.advance_handoff(con, hid, "extracted")

    st = None
    try:
        st = status.read(agents.account_of(agent))
    except Exception:  # noqa: BLE001 - a missing account must not wedge the resume
        pass
    wake = triggers.next_reset(st.reset_5h if st else None)
    db.advance_handoff(con, hid, "released", state="done" if done_ok else "degraded")
    db.set_agent_state(con, agent, PARKED, wake_at=wake,
                       detail=f"resumed handoff; wakes at "
                              f"{time.strftime('%H:%M', time.localtime(wake))}")
    log(f"  {agent}: resumed handoff from '{step}' -> PARKED")


def _reclaim_context(con, agent: str, log) -> tuple[bool, str]:
    """Clear the agent's session so the handoff actually costs it its context."""
    handle = db.get_terminal(con, agent)
    if not handle:
        return False, ("no terminal recorded; start the agent with `ocp spawn` or "
                       "`ocp launch` inside an Orca terminal so its context can be reclaimed")
    if not orca.available():
        return False, "orca is not on PATH"
    ok, why = orca.clear_context(handle, timeout_ms=orca.IDLE_PROBE_MS)
    log(f"  {agent}: {why}")
    return ok, why


def wake_agent(con, agent: str, log) -> tuple[bool, str]:
    """Hand a cleared agent back its own handoff so it can carry on.

    This is the other half of the cycle: the agent kept its terminal and its
    account across the park, and the handoff is what it knows when it wakes.
    """
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available():
        return False, "no terminal to wake"
    p = foldmod.handoff_path(agent)
    if not p.exists():
        return False, "no handoff to resume from"
    text = ("You were parked and have been resumed. Your context was cleared; this is "
            "your own handoff record. Carry on from here.\n\n" + p.read_text())
    ok, why = orca.resume_with(handle, text)
    log(f"  {agent}: {why}")
    return ok, why


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
        # The clock says it is late, but the digest is what says whether the
        # fold actually landed. A fold that finished just as the deadline
        # passed must not be recorded as a timeout.
        before = (row["detail"] or "")
        if before.startswith("before=") and \
                before[len("before="):] != str(foldmod.fold_fingerprint(agent)):
            db.advance_handoff(con, row["id"], row["step"], state="done",
                               detail="deadline passed, but the digest advanced")
            log(f"  {agent}: fold landed just after its deadline - not a timeout")
            continue
        # The cursor is deliberately not advanced, so the delta is retried.
        db.advance_handoff(con, row["id"], row["step"], state="degraded",
                           detail=f"deadline passed at {row['deadline']}")
        db.bump_counter(con, agent, "throttle_count")
        db.set_agent_state(con, agent, DEGRADED, wake_at=now + DEGRADED_RETRY_S,
                           detail=f"handoff timed out at step '{row['step']}'")
        actions.append(f"{agent}: handoff timed out at step '{row['step']}' -> DEGRADED")
        log(f"  {agent}: handoff timed out at step '{row['step']}' -> DEGRADED")
    return actions


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
        resume_handoff(con, row, log)
        actions.append(f"{agent}: resumed in-flight handoff from step '{step}'")

    # 2. wake_at deadlines that have come due.
    for row in db.due_agents(con, now):
        agent = row["agent"]
        if row["state"] in (PARKED, THROTTLED, DEGRADED):
            db.set_agent_state(con, agent, QUEUED, wake_at=None,
                               detail=f"woke from {row['state']} at reconciliation")
            actions.append(f"{agent}: {row['state']} -> QUEUED (wake_at was {row['wake_at']})")

    # 3. Agents we know about but have no state row for.
    known = set(paths.list_agents())
    have = {r["agent"] for r in db.all_agent_states(con)}
    for agent in sorted(known - have):
        db.set_agent_state(con, agent, IDLE, detail="registered, first seen")
        actions.append(f"{agent}: IDLE (first seen)")

    for a in actions:
        log(f"  reconcile: {a}")
    return actions


# ---------------------------------------------------------------- handoff
def perform_handoff(con, agent: str, trigger: triggers.Trigger, log,
                    use_model: bool = True) -> bool:
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

    # An agent that is mid-turn cannot be handed off: the fold would miss the
    # work in flight and the clear would throw it away. Check before starting,
    # with a short probe so one busy agent does not stall the whole tick, and
    # simply come back next tick - the trigger is a condition, so it still
    # holds then.
    last = con.execute(
        "SELECT MAX(updated) AS t FROM handoff WHERE agent = ?", (agent,)
    ).fetchone()["t"]
    if last and int(time.time()) - int(last) < MIN_HANDOFF_INTERVAL_S:
        log(f"  {agent}: {trigger.kind} fired again {int(time.time()) - int(last)}s after the "
            f"last handoff - too soon, skipping (a fold is not free)")
        return True

    handle = db.get_terminal(con, agent)
    if handle and orca.available() and not orca.idle_now(handle):
        log(f"  {agent}: {trigger.kind} fired but the agent is mid-turn - deferring")
        return True

    hid = uuid.uuid4().hex[:12]
    st = status.read(agents.account_of(agent)) if agent in paths.list_agents() else None
    cursor_from = db.get_cursor(con, f"fold:{agent}")
    deadline = int(time.time()) + HANDOFF_DEADLINE_S
    db.start_handoff(con, hid, agent, st.session_id if st else None, trigger.kind,
                     cursor_from, deadline)
    # Remember what the digest looked like going in, so "did the fold finish?"
    # is answered by the digest itself rather than by the clock alone (8.3).
    db.advance_handoff(con, hid, "requested",
                       detail=f"before={foldmod.fold_fingerprint(agent)}")
    db.set_agent_state(con, agent, HANDOFF, detail=trigger.reason)
    log(f"  {agent}: HANDOFF ({trigger.kind}) - {trigger.reason}")

    ok, msg = foldmod.fold(agent, use_model=use_model)
    if ok:
        db.advance_handoff(con, hid, "folded", cursor_to=db.get_cursor(con, f"fold:{agent}"))
        # A handoff that worked ends the streak. This is the only thing that
        # clears it, so a run of failures accumulates until 8.6 escalates.
        db.reset_counter(con, agent, "throttle_count")
    else:
        # The failure edge out of HANDOFF that section 8.2 insists on. Without
        # it, every fold failure converges here and the agent holds its slot
        # forever in no state at all.
        db.advance_handoff(con, hid, "folded", state="degraded", detail=msg[:300])
        db.bump_counter(con, agent, "throttle_count")
        log(f"  {agent}: fold failed -> DEGRADED ({msg[:120]})")

    db.advance_handoff(con, hid, "extracted")

    # Return the context. Folding and writing a handoff is only half of a
    # handoff; without this the context never drops, a context trigger fires
    # again next tick, and the agent hands off forever paying for a fold each
    # time. /clear keeps the terminal, worktree and account and empties only
    # the conversation.
    cleared, why = _reclaim_context(con, agent, log)
    db.advance_handoff(con, hid, "extracted", detail=why)

    # Park until the usage window rolls over - or longer, if this agent keeps
    # degrading, so a broken one is not retried at full cadence forever.
    reset = st.reset_5h if st else None
    wake = triggers.next_reset(reset)
    fresh = db.get_agent_state(con, agent)
    streak = int(fresh["throttle_count"]) if fresh else 0
    if streak:
        backoff = backoff_for(streak)
        if int(time.time()) + backoff > wake:
            wake = int(time.time()) + backoff
            log(f"  {agent}: backing off {backoff}s after {streak} degraded handoff(s)")
    db.advance_handoff(con, hid, "released", state="done" if ok else "degraded")
    db.set_agent_state(con, agent, PARKED, wake_at=wake,
                       detail=f"{trigger.kind}; wakes at {time.strftime('%H:%M', time.localtime(wake))}")
    db.bump_counter(con, agent, "continuations")  # 8.5: handoff is not a failure
    log(f"  {agent}: PARKED until {time.strftime('%Y-%m-%d %H:%M', time.localtime(wake))}")
    return ok


# ---------------------------------------------------------------- tick
_last_rollover_day = {"day": ""}


def tick(con, log, use_model: bool = True, samples: dict | None = None) -> Tick:
    now = int(time.time())
    actions: list[str] = []
    samples = samples if samples is not None else {}

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
        row = db.get_agent_state(con, agent)
        state = row["state"] if row else IDLE

        if state == STALLED:
            continue  # 8.6: only a human clears this
        if state not in EVALUATED:
            continue

        # An agent mid-handoff is excluded from trigger evaluation (8.9).
        if db.in_flight_for(con, agent):
            continue

        try:
            acct = agents.account_of(agent)
            st = status.read_for_agent(agent, acct)
        except Exception as e:  # noqa: BLE001
            # A real failure, not a resource shortage: bad config, unreadable
            # status. This is what failed_attempts counts (8.5), and it is
            # capped, unlike continuations.
            db.bump_counter(con, agent, "failed_attempts")
            fresh = db.get_agent_state(con, agent)
            actions.append(f"{agent}: error: {e}")
            log(f"  {agent}: error: {e}")
            if fresh and fresh["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
                db.set_agent_state(con, agent, STALLED,
                                   detail=f"{fresh['failed_attempts']} failed attempts: {e}")
                log(f"  {agent}: STALLED after {fresh['failed_attempts']} failed attempts")
            continue

        # Wake anything whose deadline has come due, whatever its state.
        if row and row["wake_at"] and row["wake_at"] <= now and \
                state in (PARKED, THROTTLED, DEGRADED):
            # The streak is NOT cleared here. Waking is the normal end of every
            # park, so resetting on wake pinned throttle_count at 1 and made
            # the escalation in 8.6 unreachable. It is cleared by a handoff
            # that actually succeeds.
            db.set_agent_state(con, agent, QUEUED, wake_at=None, detail="wake_at reached")
            actions.append(f"{agent}: {state} -> QUEUED (window rolled over)")
            log(f"  {agent}: {state} -> QUEUED (window rolled over)")
            wake_agent(con, agent, log)
            state = QUEUED

        # Keep a short burn-rate history per account for the projection (8.1).
        hist = samples.setdefault(acct, [])
        if st.pct_5h is not None and st.collected_at:
            if not hist or hist[-1][0] != st.collected_at:
                hist.append((st.collected_at, st.pct_5h))
                del hist[:-10]
        burn = triggers.burn_rate_per_s(hist)

        if state in (PARKED, THROTTLED, DEGRADED):
            continue  # waiting on wake_at, already handled above

        # trusted_* is None when the snapshot is stale. A frozen status.json
        # keeps reporting its last value forever, and believing it is the same
        # failure as reading a null as 0 - the trigger would simply never fire
        # again after the collector died.
        t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct, burn=burn)
        if t.fired:
            ok = perform_handoff(con, agent, t, log, use_model=use_model)
            actions.append(f"{agent}: handoff ({t.kind}){'' if ok else ' -> degraded'}")

            fresh = db.get_agent_state(con, agent)
            if fresh and fresh["throttle_count"] >= MAX_THROTTLE_STREAK:
                # 8.6: escalate only after repeated automatic recovery failed.
                db.set_agent_state(con, agent, STALLED,
                                   detail=f"{fresh['throttle_count']} consecutive degraded handoffs")
                actions.append(f"{agent}: STALLED after {fresh['throttle_count']} degraded handoffs")
                log(f"  {agent}: STALLED - needs a human")
            elif fresh and fresh["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
                db.set_agent_state(con, agent, STALLED,
                                   detail=f"{fresh['failed_attempts']} failed attempts")
                actions.append(f"{agent}: STALLED after {fresh['failed_attempts']} failed attempts")

    return Tick(now, actions)


# ---------------------------------------------------------------- loop
def run(once: bool = False, use_model: bool = True, verbose: bool = True,
        max_ticks: int | None = None) -> int:
    def log(msg: str) -> None:
        if verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    other = running_elsewhere()
    if other and other != os.getpid():
        print(f"a daemon is already running (pid {other})")
        return 1
    write_pidfile()

    stopping = {"now": False}

    def stop(signum, _frame):
        stopping["now"] = True
        log(f"signal {signum} - stopping after this tick")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop)
        except ValueError:
            pass  # not on the main thread

    con = db.connect()
    samples: dict = {}
    try:
        log("daemon starting - reconciling")
        reconcile(con, log)

        ticks = 0
        while not stopping["now"]:
            t = tick(con, log, use_model=use_model, samples=samples)
            ticks += 1
            if once or (max_ticks and ticks >= max_ticks):
                break
            # The hottest account sets the pace, not the quietest: min() here
            # let an account at 88% be polled every 30s because another sat at
            # 10%. An account whose usage is unknown also polls fast.
            pcts = [status.read(a).trusted_pct_5h for a in paths.list_accounts()]
            hottest = None if (not pcts or any(p is None for p in pcts)) else max(pcts)
            burn = max((triggers.burn_rate_per_s(h) for h in samples.values()), default=0.0)
            time.sleep(triggers.poll_interval(hottest, burn))
        log(f"stopped after {ticks} tick(s)")
        return 0
    finally:
        con.close()
        paths.daemon_pid().unlink(missing_ok=True)
