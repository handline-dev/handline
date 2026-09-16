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

from . import agents, db, fold as foldmod, paths, status, triggers

# Section 8.2 states.
IDLE, QUEUED, RUNNING, DONE = "IDLE", "QUEUED", "RUNNING", "DONE"
HANDOFF, DEGRADED, PARKED = "HANDOFF", "DEGRADED", "PARKED"
THROTTLED, STALLED = "THROTTLED", "STALLED"

# Evaluated every tick (8.4). PARKED and THROTTLED are in here deliberately.
EVALUATED = {RUNNING, HANDOFF, PARKED, QUEUED, THROTTLED, IDLE}

MAX_FAILED_ATTEMPTS = 3      # 8.5: real failures only
MAX_THROTTLE_STREAK = 5      # 8.6: then escalate to a human gate
HANDOFF_DEADLINE_S = 90      # 8.3


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
    # A pidfile from before the last boot cannot describe a live process.
    if boot and boot != boot_time():
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


def reconcile(con, log) -> list[str]:
    """One unconditional pass at startup (8.4).

    Covers, together: crossings missed while the daemon was down, handoffs left
    in flight by a kill, and wake_at deadlines that elapsed with nobody
    listening.
    """
    actions: list[str] = []
    now = int(time.time())

    # 1. Handoffs left in flight. Resume from the step each one reached.
    for row in db.in_flight_handoffs(con):
        agent, step = row["agent"], row["step"]
        if row["deadline"] and row["deadline"] < now:
            # 8.3: timed out. Cursor does not move; produce a degraded handoff
            # without a model; repeated occurrences throttle.
            db.advance_handoff(con, row["id"], step, state="degraded",
                               detail=f"deadline passed at {row['deadline']}")
            db.set_agent_state(con, agent, DEGRADED, detail="handoff timed out")
            db.bump_counter(con, agent, "throttle_count")
            actions.append(f"{agent}: in-flight handoff timed out at step '{step}' -> DEGRADED")
        else:
            actions.append(f"{agent}: resuming in-flight handoff from step '{step}'")

    # 2. wake_at deadlines that have come due.
    for row in db.due_agents(con, now):
        agent = row["agent"]
        if row["state"] in (PARKED, THROTTLED):
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
        # A second trigger merges as an added reason. It does not start a
        # competing handoff: min() over deadlines is not mutual exclusion.
        db.advance_handoff(con, existing["id"], existing["step"],
                           detail=f"{existing['detail'] or ''}; also {trigger.kind}".strip("; "))
        log(f"  {agent}: already handing off at step '{existing['step']}', merged "
            f"'{trigger.kind}' as an added reason")
        return True

    hid = uuid.uuid4().hex[:12]
    st = status.read(agents.account_of(agent)) if agent in paths.list_agents() else None
    cursor_from = db.get_cursor(con, f"fold:{agent}")
    deadline = int(time.time()) + HANDOFF_DEADLINE_S
    db.start_handoff(con, hid, agent, st.session_id if st else None, trigger.kind,
                     cursor_from, deadline)
    db.set_agent_state(con, agent, HANDOFF, detail=trigger.reason)
    log(f"  {agent}: HANDOFF ({trigger.kind}) - {trigger.reason}")

    ok, msg = foldmod.fold(agent, use_model=use_model)
    if ok:
        db.advance_handoff(con, hid, "folded", cursor_to=db.get_cursor(con, f"fold:{agent}"))
    else:
        # The failure edge out of HANDOFF that section 8.2 insists on. Without
        # it, every fold failure converges here and the agent holds its slot
        # forever in no state at all.
        db.advance_handoff(con, hid, "folded", state="degraded", detail=msg[:300])
        db.set_agent_state(con, agent, DEGRADED, detail=msg[:200])
        db.bump_counter(con, agent, "throttle_count")
        log(f"  {agent}: fold failed -> DEGRADED ({msg[:120]})")

    db.advance_handoff(con, hid, "extracted")

    # Park until the usage window rolls over.
    reset = st.reset_5h if st else None
    wake = triggers.next_reset(reset)
    db.advance_handoff(con, hid, "released", state="done" if ok else "degraded")
    db.set_agent_state(con, agent, PARKED, wake_at=wake,
                       detail=f"{trigger.kind}; wakes at {time.strftime('%H:%M', time.localtime(wake))}")
    db.bump_counter(con, agent, "continuations")  # 8.5: handoff is not a failure
    log(f"  {agent}: PARKED until {time.strftime('%Y-%m-%d %H:%M', time.localtime(wake))}")
    return ok


# ---------------------------------------------------------------- tick
def tick(con, log, use_model: bool = True, samples: dict | None = None) -> Tick:
    now = int(time.time())
    actions: list[str] = []
    samples = samples if samples is not None else {}

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
        except Exception as e:  # noqa: BLE001
            actions.append(f"{agent}: config error: {e}")
            continue
        st = status.read(acct)

        # Wake anything whose deadline has come due, whatever its state.
        if row and row["wake_at"] and row["wake_at"] <= now and state in (PARKED, THROTTLED):
            db.set_agent_state(con, agent, QUEUED, wake_at=None, detail="wake_at reached")
            db.reset_counter(con, agent, "throttle_count")
            actions.append(f"{agent}: {state} -> QUEUED (window rolled over)")
            log(f"  {agent}: {state} -> QUEUED (window rolled over)")
            state = QUEUED

        # Keep a short burn-rate history per account for the projection (8.1).
        hist = samples.setdefault(acct, [])
        if st.pct_5h is not None and st.collected_at:
            if not hist or hist[-1][0] != st.collected_at:
                hist.append((st.collected_at, st.pct_5h))
                del hist[:-10]
        burn = triggers.burn_rate_per_s(hist)

        if state in (PARKED, THROTTLED):
            continue  # waiting on wake_at, already handled above

        t = triggers.evaluate(st.pct_5h, st.ctx_pct, burn=burn)
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
            worst = min((status.read(a).pct_5h for a in paths.list_accounts()
                         if status.read(a).pct_5h is not None), default=None)
            time.sleep(triggers.poll_interval(worst))
        log(f"stopped after {ticks} tick(s)")
        return 0
    finally:
        con.close()
        paths.daemon_pid().unlink(missing_ok=True)
