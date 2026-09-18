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
from pathlib import Path

from . import (agents, db, fold as foldmod, launch, lock, observe as obs, orca,
               paths, service, spawn as spawnmod, status, triggers)

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

# An agent that has done nothing for this long is holding memory and nothing
# else: a resident session costs 250-500MB and no tokens at all. Closing it
# gives the machine that back, and resuming costs nothing extra - measured, a
# resumed session read its whole 68k prefix from cache exactly as a resident
# one did, because the cache lives on the server and expires on time, not on
# whether our process stayed alive.
IDLE_CLOSE_S = 1800

# How long an agent may read as RUNNING with nothing heard from it before the
# label is stuck rather than the turn being long. Same number as the idle-close
# threshold: past it the daemon would have reclaimed the session anyway.
STUCK_RUNNING_S = IDLE_CLOSE_S
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
# The name of the hold that makes this a single-instance program. One per
# OCP_HOME, so two installations on one machine still supervise their own.
DAEMON_LOCK = "daemon"


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


def clear_pidfile() -> None:
    """Remove the pidfile, but only while it still names this process.

    An exit that clears the file unconditionally clears whoever holds it, and
    a live daemon nothing records is one `ocp ps` reports as not running.
    """
    _reclaim(os.getpid())


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
        _reclaim(pid)
        return None
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        _reclaim(pid)
        return None
    return int(pid)


def _reclaim(stale_pid) -> None:
    """Drop a pidfile that names `stale_pid`, and only while it still does.

    Every reader calls `running_elsewhere`, and a daemon that starts between
    the read and the unlink writes its own name into the window. Deleting it
    unrecorded is the state this system spent a night in: a live supervisor
    that `ocp ps` reported as not running.
    """
    info = read_pidfile()
    if info and info.get("pid") == stale_pid:
        paths.daemon_pid().unlink(missing_ok=True)


def _supervises_this_home(pid: int) -> bool:
    """Is that daemon ours, or another installation's?

    The lock is per OCP_HOME, so two homes on one machine each run their own
    daemon and neither is a duplicate. Compared as directories, not as
    spellings: /tmp and /private/tmp are one place on macOS, and an unresolved
    comparison answers "somebody else's" about our own duplicate.
    """
    home = obs.env_of(pid, "OCP_HOME")
    if home is None:
        # Unreadable and unset are the same answer: a supervisor we cannot
        # place is the one worth naming, and silence is what let two run.
        return True
    return os.path.realpath(Path(home).expanduser()) == os.path.realpath(paths.home())


def processes() -> list[int]:
    """PIDs of every `ocp daemon` supervising this OCP_HOME, this one aside.

    The lock refuses the next duplicate; it cannot end one that was already
    running when it arrived, and that one is invisible to the pidfile, which
    names whoever wrote it last - so a daemon that exited and took the file
    with it leaves a live supervisor nothing records. The process table is the
    only place both of them appear.
    """
    mine = os.getpid()
    found = []
    for pid, cmd in obs.process_table() or []:
        argv = cmd.split()
        # The program being run: our own path, `daemon` next, and both at the
        # front of the command line. Anywhere else in it is somebody talking
        # about us - a `grep -n ocp daemon`, an editor on this file, a session
        # whose system prompt quotes the command.
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
        st = status.read(agents.current_account(agent, con))
    except Exception:  # noqa: BLE001 - a missing account must not wedge the resume
        pass
    wake = triggers.next_reset(st.reset_5h if st else None)
    db.advance_handoff(con, hid, "released", state="done" if done_ok else "degraded")
    db.set_agent_state(con, agent, PARKED, wake_at=wake,
                       detail=f"resumed handoff; wakes at "
                              f"{time.strftime('%H:%M', time.localtime(wake))}")
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


# How long a relaunched session has to show up in the process table. `ocp
# launch` execs claude, so it appears at once when it appears at all; this is
# slack for a slow start, not a wait for the TUI to be usable.
WAKE_SESSION_TIMEOUT_S = 15


def wait_for_session(agent: str, timeout_s: int) -> bool:
    """Did the launch we typed actually start a session?

    `relaunch` reports on having sent the command, which is not the same thing:
    `ocp launch` refuses over the 5h limit and exits when the account is not
    logged in, leaving the shell exactly as it was. The reading is taken here
    rather than from the tick's cache, which predates the command.
    """
    deadline = time.time() + timeout_s
    while True:
        sessions = obs.live_sessions()
        if sessions is not None and sessions.get(agent):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


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
        return False, "no session process; not typing /clear at a shell prompt"
    ok, why = orca.clear_context(handle, timeout_ms=orca.IDLE_PROBE_MS)
    log(f"  {agent}: {why}")
    return ok, why


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
    if db.get_cursor(con, f"notify:{agent}") == int(head["id"]):
        return False
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
    if not orca.idle_now(handle):
        return False
    depth = db.queue_depth(con, agent)
    with lock.terminal(agent) as mine:
        if not mine:
            return False          # something else is mid-send; next tick
        return _tell(con, agent, head, depth, handle, log)


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
        f"`ocp read {head['id']}` shows it whole; answer with "
        f"`ocp resolve {head['id']} \"<verdict>\"`, or `ocp inbox` any time.")
    if ok:
        db.set_cursor(con, f"notify:{agent}", int(head["id"]))
        db.add_event(con, agent, "notified", {"request_id": head["id"], "more_waiting": more})
        log(f"  {agent}: told about #{head['id']}" + (f" ({more} more waiting)" if more else ""))
    return ok


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
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available() or not orca.alive(handle):
        return False
    if not session_present(agent):
        return False
    if not orca.idle_now(handle):
        return False
    shown, rest = answered[-3:], answered[:-3]
    lines, cut = [], False
    for r in shown:
        said = ((r["verdict"] or "").splitlines() or [r["state"]])[0]
        cut = cut or len(said) > 160 or "\n" in (r["verdict"] or "")
        lines.append(f"#{r['id']} from {r['target']}: {said[:160]}")
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
        ok, _ = orca.send(
            handle, "Answers to what you asked:\n" + "\n".join(lines) + tail)
    if ok:
        db.mark_announced(con, [r["id"] for r in answered])
        db.add_event(con, agent, "answered", {"count": len(answered)})
        log(f"  {agent}: told about {len(answered)} answer(s)")
    return ok


def should_close(con, agent: str, state: str, row, now: int, st) -> bool:
    """Is this agent holding memory for nothing?

    Closing is about the machine, not the account, so the question is whether
    anyone is using this session and whether we can bring it back. Five things
    say no: it is working or being handed off, a human has to look at it,
    someone is waiting on it, it has been busy recently, or we cannot name the
    session we would resume. Anything else is a session whose only job right now
    is to occupy 250-500MB.

    The last one is the asymmetric trade: memory is recoverable the moment
    anything wants it, and a working context is not recoverable at all. Closing
    without a session id would buy back the small reversible thing by spending
    the large irreversible one.
    """
    if state in (RUNNING, HANDOFF, STALLED):
        return False
    if not row or row["closed_at"] or not row["terminal"]:
        return False
    if db.queued_for(con, agent):
        return False          # work is waiting; closing would only delay it
    if not st.session_id:
        return False          # nothing to resume; losing the context costs more
    last = db.last_activity(con, agent)
    if last is None:
        return False          # nothing has ever been seen from it - leave it alone
    return (now - last) >= IDLE_CLOSE_S


def triggers_apply(row) -> bool:
    """Would the daemon act on this agent's triggers at all?

    A closed agent has no session to hand off, and closing is precisely what
    makes it look like one that needs handing off: its status line stops, so the
    snapshot goes stale and usage_unknown fires. An agent with no terminal has
    nowhere to run at all - nothing to fold, nothing to clear, nothing being
    spent - and usage_unknown fires across a whole account at once, so it sweeps
    up the registered-but-never-started agents with everyone else. backend and
    manager were parked that way on 09-17 and manager's fold was charged for it.

    Every screen asks this too - drawing a warning the daemon will never act on
    is the dashboard arguing with the daemon.
    """
    return bool(row and row["terminal"] and not row["closed_at"])


def would_wake(con, row) -> bool:
    """Is anything actually trying to wake this closed agent?

    Queued work, an answer it asked for, or a deadline already set - the same
    three reasons `supervise` calls `wake_agent` on a closed row at all
    (section 8.4's step 2, and the two checks right before `deliver_queued` in
    the main pass). `launch.wake_wait` answers a narrower question - is the
    account full - and answers it for every closed agent on a walled account,
    including ones idle by choice with nothing asked of them. A screen gates
    on this first, so "waiting" only shows next to a wait that is actually
    happening.
    """
    if not row or not row["closed_at"]:
        return False
    agent = row["agent"]
    return bool(row["wake_at"] or db.queued_for(con, agent) or
                db.unannounced_answers(con, agent))


def close_agent(con, agent: str, log, st) -> tuple[bool, str]:
    """Give the machine back an idle agent's memory, keeping its terminal.

    This is not a handoff: nothing is folded and no context is reclaimed,
    because there is nothing wrong with this agent's context - it is simply
    not being used. The session id is recorded so waking can resume that same
    session rather than starting a stranger in its place - and it is the caller's
    reading, the same one `should_close` approved. Reading it again here would
    let the predicate check one value and the act store another, in the one
    place where the difference means closing with no way back.
    """
    with lock.terminal(agent) as mine:
        if not mine:
            return False, "someone else is typing at this agent; not closing"
        return _close_agent(con, agent, log, st)


def _close_agent(con, agent: str, log, st) -> tuple[bool, str]:
    row = db.get_agent_state(con, agent)
    if row and row["state"] in (RUNNING, HANDOFF):
        # `should_close` refuses these, and every path that closes on a
        # schedule goes through it - but a human's key does not, and a turn can
        # start between the moment they press it and the moment they confirm.
        # Closing then throws away work nothing has recorded.
        #
        # A handoff is the exception and calls `_end_session` itself: it is the
        # one path that has already recorded the work, with the fold, and then
        # needs the session gone to start the next one somewhere else.
        return False, f"{agent} is {row['state']}; not closing a session mid-work"
    if not st.session_id:
        # Closing is only cheap because waking resumes the same session. With
        # no id to come back to, closing is just throwing the context away.
        return False, "no session to resume; not closing"
    return _end_session(con, agent, log, st, resume=st.session_id)


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
    ok, why = orca.close_session(handle)
    if not ok:
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


def wake_prompt(agent: str, resumed: bool, was_closed: bool = False,
                moved_to: str | None = None, con=None) -> str:
    """What an agent is told when it comes back.

    A resumed session already holds everything it knew, so it is told where it
    is rather than what it did. One that was parked gets its handoff. One whose
    close left no session to resume gets neither, and is told so rather than
    handed an older park's record as if it were current. All three get the path
    to their own record when it can be named: a digest is a summary, and the
    detail it dropped is still on disk.
    """
    parts: list[str] = []
    if moved_to:
        # A move is not a close. Its session ended a moment ago with a fold, so
        # the handoff is current and `launch.context_for` has already put it in
        # this session's system prompt - telling it "nothing was folded" would
        # send it to read a journal for what it is holding.
        parts.append(f"Your account was at its 5h limit, so this session was started on "
                     f"{moved_to} instead of waiting for that window to roll over. Your "
                     f"work was folded first and your handoff is in your system prompt "
                     f"above. Carry on from there.")
    elif resumed:
        parts.append("Your session was closed while you were idle and has been resumed. "
                     "Nothing was lost; carry on.")
    elif was_closed:
        # Closing writes no handoff, because closing is not a handoff. Taking
        # the parked branch here would tell an agent that was never parked that
        # its context was cleared, and hand it whatever handoff an earlier park
        # left behind - a stale record read as current, which is the failure
        # this whole branch exists to remove.
        parts.append("Your session was closed while you were idle and could not be "
                     "resumed, so this is a fresh one. There is no handoff: nothing "
                     "was folded when it closed. Read your journal and your inbox "
                     "before carrying on.")
    else:
        parts.append("You were parked and have been resumed. Your context was cleared; "
                     "below is your own handoff record. Carry on from here.")
        p = foldmod.handoff_path(agent)
        if p.exists():
            parts.append(p.read_text())
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
        return _wake_agent(con, agent, log)


def _wake_agent(con, agent: str, log, moved_to: str | None = None) -> tuple[bool, str]:
    """The body of a wake, without the lock. Callers already holding it use this.

    `moved_to` names the account this wake is starting the agent on when it is
    not the one the last session was on, so the agent is told that rather than
    told it was closed with nothing folded.
    """
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available():
        return False, "no terminal to wake"

    row = db.get_agent_state(con, agent)
    closed = bool(row and row["closed_at"])
    resumed = False
    if closed:
        # `ocp launch` refuses at the 5h wall, and the daemon can read that
        # before it types anything. Trying anyway would spend the wake timeout
        # to be told what we already knew, and count a streak against an agent
        # whose only problem rolls over by itself.
        # The account this launch will land on. With a session to resume that
        # is the account it lives under, so an agent whose own account is at
        # the wall waits for its window rather than losing its conversation -
        # closing writes no handoff, so a fresh start elsewhere would come back
        # with nothing. Only a wake with nothing to resume is free to go where
        # there is room, which is the move's wake.
        session = row["resume_session"]
        why = launch.wake_wait_reason(agent, row, con)
        if why:
            log(f"  {agent}: {why}")
            return False, why
        # The shell is still there; put the agent back into it. Resuming the
        # same session is what makes closing cheap, so it is tried first and
        # a fresh start is the fallback, not the plan.
        base = launch.command_for(agent)
        # Resuming the session is what makes closing cheap, so it is tried
        # first - but a resume can be the very thing that fails: an id whose
        # transcript was pruned, or a worktree moved out from under it, gives
        # a launch that refuses every time it is retried. A fresh start in the
        # same terminal loses the context and keeps the agent, which is the
        # better of the two ends this can have.
        attempts = [base + f" -- --resume {session}", base] if session else [base]
        started = lambda: wait_for_session(agent, WAKE_SESSION_TIMEOUT_S)  # noqa: E731
        resumed = False
        for cmd in attempts:
            ok, why = orca.relaunch(handle, cmd, started=started)
            if ok:
                resumed = "--resume" in cmd
                break
        if not ok:
            # The closed mark and resume_session are the whole way back. A wake
            # that started nothing leaves both alone: the next attempt - work
            # arriving, or the next restart - has nothing else to use.
            db.add_event(con, agent, "wake_failed", {"resume_session": session,
                                                     "why": why})
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
        db.reset_counter(con, agent, "wake_failures")
        db.clear_closed(con, agent)
        db.add_event(con, agent, "wake", {"resumed_session": session if resumed else None})
    elif not foldmod.handoff_path(agent).exists():
        return False, "no handoff to resume from"
    elif not session_present(agent):
        # Not the closed branch: nothing was relaunched, so the row's claim
        # that a session is there is the only evidence - and it is the claim
        # that goes wrong. A handoff typed at a shell prompt is executed line
        # by line. The sweep records this agent closed on its next tick, and
        # the branch above then relaunches it before anything is typed.
        return False, "no session process; not typing the handoff at a shell prompt"

    ok, why = orca.resume_with(handle, wake_prompt(agent, resumed, was_closed=closed,
                                                   moved_to=moved_to, con=con))
    log(f"  {agent}: {why}{' (session resumed)' if resumed else ''}")
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
        if row["state"] not in (PARKED, THROTTLED, DEGRADED):
            continue
        # A closed agent has no session left to notice its own wake_at - the
        # wake_at branch in supervise() is what would normally call this, and
        # it needs wake_at still set to run. So wake it here, before the
        # deadline is cleared: a wake that failed - the dead terminal a reboot
        # leaves behind is the ordinary way - has to stay due, or due_agents
        # never selects it again and the agent sleeps until a human looks.
        if row["closed_at"]:
            ok, why = wake_agent(con, agent, log)
            if not ok:
                actions.append(f"{agent}: could not wake at reconciliation, "
                               f"still due - {why}")
                continue
            actions.append(f"{agent}: woken at reconciliation")
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


# Which suspect the next tick probes. Round-robin rather than always the first,
# or one agent that is merely busy would hold the slot and the genuinely stuck
# one behind it would never be looked at.
_probe_turn = {"n": 0}


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
            actions.append(f"{agent}: terminal is gone; handle dropped")
            log(f"  {agent}: terminal is gone; handle dropped")
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
            #
            # Read the agent's own snapshot directly rather than through
            # read_for_agent, which nulls the session id once that snapshot is
            # stale. That rule is there to stop an agent being handed another
            # agent's session off the shared account file, and it is right - but
            # a session that has just died leaves a snapshot that is stale by
            # construction, and its own id is the one thing here that is not in
            # doubt. Losing it loses the only pointer to that agent's context,
            # and memory comes back where context does not.
            own = status.read(agents.current_account(agent, con),
                              paths.agent_status_file(agent))
            session = None if own.error else own.session_id
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
    if handle and orca.available():
        if not orca.idle_now(handle):
            log(f"  {agent}: {trigger.kind} fired but the agent is mid-turn - deferring")
            return True
        if not session_present(agent):
            # A handoff for a session that is not there costs a fold to write
            # and then types its own text at a shell prompt. The context it
            # wants to reclaim went with the session; the sweep records the
            # agent closed, and a closed agent's triggers do not fire at all.
            log(f"  {agent}: {trigger.kind} fired but no session is running - "
                f"not handing off")
            return True

    with lock.terminal(agent) as mine:
        if not mine:
            # Before the fold, not after it. The clear comes at the end of this
            # pipeline, and losing the terminal there meant the fold was paid
            # for, the handoff was recorded done, and the context it exists to
            # reclaim was still sitting in the agent. The trigger is a
            # condition, so it fires again next tick.
            log(f"  {agent}: {trigger.kind} fired but something else is typing at it "
                f"- not handing off")
            return True
        return _perform_handoff(con, agent, trigger, log, use_model)


# The triggers a move answers. `usage_unknown` is not one of them: it fires
# because we cannot read the account, and "we do not know" is not "it is full".
# Moving on it would send agents off an account nobody can see, which is the
# fail-closed rule pointed the wrong way.
MOVABLE_TRIGGERS = ("usage", "usage_projected")


def _move_to_room(con, agent: str, trigger: triggers.Trigger, log) -> bool:
    """Put this agent's next session on an account that has room. True if it moved.

    What this changes is whether an agent waits out its own window while
    another account idles beside it. It needs no lease, because it does not
    take anything from a session anyone is using: it runs after the fold, at
    the point where this session was ending either way.

    The new session cannot be the old one - a session id lives under one
    account's config directory - so the close records no resume id, and what
    carries over is what carries over across a park: the handoff in the system
    prompt, and the old transcript's path to grep.
    """
    if trigger.kind not in MOVABLE_TRIGGERS:
        return False
    here = agents.current_account(agent, con)
    # Judged on the line the trigger fired at, not on the hard stop. The fold
    # happens a reserve short of the wall so there is room to pay for it, and
    # an account the daemon has just decided is too full to work on is too full
    # to move to as well.
    there = launch.choose_account(agent, limit=triggers.HARD_STOP_PCT - triggers.DEFAULT_RESERVE_PCT)
    if there == here:
        log(f"  {agent}: staying on {here} - nowhere with more room")
        return False
    # bootstrap=True: `there` may be exactly the account choose_account just
    # bootstrapped - a plain assignable() would refuse the same account it
    # was chosen for, on the "no status.json" reason bootstrap exists to lift.
    allowed = status.pick_account([there], bootstrap=True)[0] == there
    why = "" if allowed else status.read(there).assignable()[1]
    if not allowed:
        log(f"  {agent}: staying on {here} - {there} takes no work either ({why})")
        return False
    handle = db.get_terminal(con, agent)
    if not handle or not orca.available() or not orca.alive(handle):
        return False

    # Closed through the same checks every other close goes through. `/exit`
    # typed at a shell exits the shell and Orca's terminal goes with it, and by
    # here the fold has taken long enough that the session it started with may
    # be gone. No resume id: that session belongs to the account being left.
    st = status.read_for_agent(agent, here)
    ok, why = _end_session(con, agent, log, st, resume=None)
    if not ok:
        log(f"  {agent}: staying on {here} - {why}")
        return False

    # The unlocked wake. This runs inside the handoff's own hold on the
    # terminal, and `lock.terminal` is flock on a fresh descriptor, so a second
    # acquire in this process does not nest - it fails, and a failed wake here
    # would read as "nowhere to go" and park the agent on the account it was
    # just moved off.
    woke, why = _wake_agent(con, agent, log, moved_to=there)
    landed = db.get_agent_state(con, agent)
    if not woke and landed and landed["closed_at"]:
        # Nothing started. The session is gone and the row still says closed,
        # so falling through parks it with a deadline - which is what brings a
        # closed agent back at all. Returning True here would leave it closed
        # with nothing due.
        log(f"  {agent}: closed on {here} but could not start on {there}: {why[:120]}")
        return False

    # A session is live on the new account. Whether the line after it - handing
    # it its own handoff - also landed is not the same question, and treating a
    # failed handback as a failed move would clear the context of the session
    # that just started and park it until the *old* account rolls over.
    db.set_account(con, agent, there)
    db.set_agent_state(con, agent, QUEUED, wake_at=None,
                       detail=f"moved {here} -> {there} ({trigger.kind})")
    db.add_event(con, agent, "moved_account", {"from": here, "to": there,
                                               "trigger": trigger.kind})
    log(f"  {agent}: moved {here} -> {there} ({trigger.kind})"
        + ("" if woke else f" - but it was not handed its handoff: {why[:80]}"))
    return True


def _perform_handoff(con, agent: str, trigger: triggers.Trigger, log,
                     use_model: bool = True) -> bool:
    hid = uuid.uuid4().hex[:12]
    st = status.read(agents.current_account(agent, con)) if agent in paths.list_agents() else None
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

    # Where it goes next. An agent whose own account is out of room does not
    # have to sit out that window if another account has some. Here, after the
    # fold, because this session is ending either way.
    if _move_to_room(con, agent, trigger, log):
        db.advance_handoff(con, hid, "released", state="done" if ok else "degraded")
        db.bump_counter(con, agent, "continuations")
        return ok

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
# The last reason each agent could not be woken, so a reason that lasts for
# hours is reported when it starts rather than on every tick.
_SAID: dict[str, str] = {}


def supervise(con, agent: str, log, now: int, samples: dict,
              use_model: bool, actions: list[str]) -> None:
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

    acct = agents.current_account(agent, con)
    st = status.read_for_agent(agent, acct)

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
        db.set_agent_state(con, agent, QUEUED, wake_at=None, detail="wake_at reached")
        actions.append(f"{agent}: {state} -> QUEUED (window rolled over)")
        log(f"  {agent}: {state} -> QUEUED (window rolled over)")
        row = db.get_agent_state(con, agent)
        state = QUEUED
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
    if state not in (RUNNING, HANDOFF) and not woke_now \
            and not (row and row["closed_at"]):
        # An answer first: an agent that asked something and parked is waiting
        # on that, and telling it about new work while its own question is
        # still unanswered is the wrong order.
        deliver_answers(con, agent, log)
        deliver_queued(con, agent, log)

    # Memory is the scarce resource on one laptop running a team, and an
    # idle session spends it while earning nothing: it holds its context,
    # costs no tokens, and answers no one. Closing it is not a handoff -
    # nothing is folded, nothing is cleared - and waking it resumes the
    # same session, so the only thing that changes is who has the memory.
    if should_close(con, agent, state, row, now, st):
        ok, _ = close_agent(con, agent, log, st)
        if ok:
            actions.append(f"{agent}: session closed while idle")
            row = db.get_agent_state(con, agent)

    # Keep a short burn-rate history per account for the projection (8.1).
    # Only a figure this payload actually reported may enter the burn
    # history, stamped with when it was observed. A carried figure pairs an
    # unchanging percentage with a collected_at that moves every tick: the
    # buffer fills with flat samples, the measured rate falls to zero and
    # usage_projected stops firing for an account that was climbing.
    hist = samples.setdefault(acct, [])
    if st.pct_5h_source == "live" and st.pct_5h is not None and st.rl_at:
        if not hist or hist[-1][0] != st.rl_at:
            hist.append((st.rl_at, st.pct_5h))
            del hist[:-10]
    burn = triggers.burn_rate_per_s(hist)

    if state in (PARKED, THROTTLED, DEGRADED):
        return  # waiting on wake_at, already handled above

    # What sits at the other end of a closed agent is a shell prompt, and a
    # handoff document is full of backticks - handing one off would pay for a
    # fold and then type command substitutions at bash. `should_close` is
    # careful about five things; this needs the same care about one.
    #
    # An agent woken in this tick is judged on the next one, for the same reason
    # it is told about its inbox on the next one: `st` was read before the wake,
    # and a closed agent's snapshot is stale by construction, so evaluating it
    # here would hand off the session we just restored - and leave the work that
    # caused the wake still unread.
    if woke_now or not triggers_apply(row):
        return

    # trusted_* is None only when we genuinely do not know: no snapshot, or
    # one too old to believe. A frozen status.json keeps reporting its last
    # value forever, and believing it is the same failure as reading a null
    # as 0 - the trigger would simply never fire again after the collector
    # died. An account whose window is merely quiet reads as 0, not as
    # unknown, so an idle agent is left alone instead of handed off.
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


_last_rollover_day = {"day": ""}


def tick(con, log, use_model: bool = True, samples: dict | None = None) -> Tick:
    now = int(time.time())
    actions: list[str] = []
    samples = samples if samples is not None else {}

    # Before anything is decided on what we believe, check what is true. Two
    # calls out, whatever the size of the team, and they cost 0.2s together.
    sweep(con, log, actions)

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
            supervise(con, agent, log, now, samples, use_model, actions)
        except Exception as e:  # noqa: BLE001
            # A real failure, not a resource shortage: bad config, unreadable
            # status, an Orca call that raised. This is what failed_attempts
            # counts (8.5), and it is capped, unlike continuations. Catching it
            # per agent is the point - supervising one agent is not allowed to
            # end supervision of every agent after it in the list.
            db.bump_counter(con, agent, "failed_attempts")
            fresh = db.get_agent_state(con, agent)
            actions.append(f"{agent}: error: {e}")
            log(f"  {agent}: error: {e}")
            if fresh and fresh["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
                db.set_agent_state(con, agent, STALLED,
                                   detail=f"{fresh['failed_attempts']} failed attempts: {e}")
                log(f"  {agent}: STALLED after {fresh['failed_attempts']} failed attempts")

    return Tick(now, actions)


# ---------------------------------------------------------------- loop
def run(once: bool = False, use_model: bool = True, verbose: bool = True,
        max_ticks: int | None = None) -> int:
    def log(msg: str) -> None:
        if verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    # The lock decides who supervises, not the pidfile: a daemon that never
    # wrote the file - started by hand, or orphaned when launchd reloaded the
    # job under it - is still a second supervisor, and the file cannot see one.
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
                # A supervisor that runs for weeks must not fill the disk.
                # Cheap when under threshold: two stat() calls and nothing
                # else. Caught separately so a rotation failure (e.g. a full
                # disk) degrades logging rather than taking the whole daemon
                # down with it.
                try:
                    service.rotate_logs()
                except OSError as e:
                    log(f"log rotation failed: {e}")
                if once or (max_ticks and ticks >= max_ticks):
                    break
                # The hottest account sets the pace, not the quietest: min()
                # here let an account at 88% be polled every 30s because
                # another sat at 10%. An account whose usage is unknown also
                # polls fast.
                pcts = [status.read(a).trusted_pct_5h for a in paths.list_accounts()]
                hottest = None if (not pcts or any(p is None for p in pcts)) else max(pcts)
                burn = max((triggers.burn_rate_per_s(h) for h in samples.values()), default=0.0)
                time.sleep(triggers.poll_interval(hottest, burn))
            log(f"stopped after {ticks} tick(s)")
            return 0
        finally:
            con.close()
            clear_pidfile()
