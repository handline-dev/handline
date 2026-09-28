"""Closing and waking an agent's session, and the folder-trust prompt a wake can meet."""
from __future__ import annotations

import shlex
import time
import uuid
from pathlib import Path

from ..team import agents
from ..team import ask as askmod
from ..session import autoaccept, blocked
from .. import db
from ..session import handoff as handoffmod
from ..session import idle_close
from ..session import launch
from ..infra import lock
from ..usage import measure
from ..session import observe as obs
from ..session import orca
from ..infra import paths
from ..team import spawn as spawnmod
from ..usage import status
from ..team import trust
from .. import vendors

from . import delivery, handoff
from .states import (DEGRADED, HANDOFF, HANDOFF_DEADLINE_S, IDLE, IDLE_CLOSE_S,
                     MAX_FAILED_ATTEMPTS, PARKED, PROMPT_CACHE_S, RESUME_WARM_S, RUNNING, STALLED,
                     THROTTLED)


def should_close(con, agent: str, state: str, row, now: int, st) -> bool:
    """Is this agent holding memory for nothing?

    Closing is about the machine, not the account, so the question is whether
    anyone is using this session and whether we can bring it back. Seven things
    say no: it is working or being handed off, a human has to look at it,
    someone is waiting on it (unless it is PARKED, or the cache is already
    gone - a resume then costs the same re-read a wake's cold start would, so
    the queue is redelivered on that wake instead), it has been busy recently,
    we cannot name the session we would resume, a close of it failed within
    the last idle window, or this agent's own idle window
    (`idle_close.close_timeout_s`, T52) is NEVER. Anything else is a session
    whose only job right now is to occupy 250-500MB.

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
    # that reaches here was not delivered to (walled by `wall_reason`); its
    # queue is no reason to hold the session open.
    if delivery.bg_holds(con, agent, now):
        return False          # not idle: work is still running behind the prompt
    if not st.session_id:
        return False          # nothing to resume; losing the context costs more
    if db.queued_for(con, agent) and state != PARKED \
            and not cache_expired(con, agent, st.session_id, now):
        return False          # work is waiting and the cache is still warm; closing would only delay it
    timeout = idle_close.close_timeout_s(agent)
    if timeout == idle_close.NEVER:
        return False          # this agent's own override: never close its window
    failed = db.last_event_ts(con, agent, "close_failed")
    if failed is not None and now - failed < IDLE_CLOSE_S:
        return False          # /exit did not end it; asking every tick types it every tick
    last = delivery.idle_since(con, agent)
    if last is None:
        return False          # nothing has ever been seen from it - leave it alone
    return (now - last) >= timeout


def triggers_apply(row) -> bool:
    """Would the daemon act on this agent's triggers at all?

    A closed agent has no session to hand off, and closing is precisely what
    makes it look like one that needs handing off: its status line stops, so the
    snapshot goes stale. An agent with no terminal has nowhere to run at all -
    nothing to clear, nothing being spent - and a reading that is at the wall
    is the account's, so it sweeps up the registered-but-never-started agents
    with everyone else.

    A parked, throttled or degraded row is already past its wall: the tick
    waits on its wake_at and judges nothing until then (`supervise`).

    Every screen asks this too - drawing a warning the daemon will never act on
    is the dashboard arguing with the daemon.
    """
    return bool(row and row["terminal"] and not row["closed_at"]
                and row["state"] not in (PARKED, THROTTLED, DEGRADED))


def would_wake(con, row) -> bool:
    """Is anything actually trying to wake this closed agent?

    Undelivered work, an answer it asked for, or a deadline already due
    while parked, throttled or degraded - the same three reasons the main
    pass wakes a closed row with no deadline pending (the two checks right
    before `deliver_queued`). Not a request already delivered before the
    close (T154): that one is only sitting unresolved, and waking for it
    alone would close the agent right back up on the very next idle window,
    forever - reconciliation's own due-deadline wake (section 8.4's step 2)
    still reads any unresolved request as reason enough, since a deadline
    already means something else decided this agent is worth resuming.
    `launch.wake_wait` answers a narrower question - is the account full -
    and answers it for every closed agent on a walled account, including
    ones idle by choice with nothing asked of them. A screen gates on this
    first, so "waiting" only shows next to a wait that is actually happening.
    """
    if not row or not row["closed_at"]:
        return False
    agent = row["agent"]
    due = bool(row["wake_at"]) and row["wake_at"] <= int(time.time()) \
        and row["state"] in (PARKED, THROTTLED, DEGRADED)
    return bool(due or db.undelivered_for(con, agent) or
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
    A session running background work is left alone: closing would kill it -
    until the work is stale and its supervisor has had its say (`bg_may_stop`).
    """
    if delivery.bg_holds(con, agent, now) or not cache_expired(con, agent, st.session_id, now):
        return None
    if now - db.get_cursor(con, f"cold_close_try:{agent}") < IDLE_CLOSE_S:
        return None           # a close just failed: type as usual, not held forever
    head = db.head_of_queue(con, agent)
    told = bool(head) and db.get_cursor(con, f"notify:{agent}") == int(head["id"]) \
        and not delivery._session_changed(con, agent)
    found = delivery.forgotten_resolve(con, agent, row, now)
    nudge = bool(found) and found["idle_s"] >= delivery.NUDGE_AFTER_S \
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
        why = delivery._type_gate(con, agent, db.get_terminal(con, agent),
                         require_idle=False, require_settled=True)
        if why:
            return False, why
        ok, said = _close_agent(con, agent, log, st)
        if ok:
            delivery._mark_typed(con, agent)
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


def _answer_exit_dialog(con, agent: str, log, stop: bool = False) -> bool:
    """The `/exit` just typed can raise a dialog offering to stop background
    work: decline it (Esc keeps the session). Only with `stop` - the work is
    stale and its supervisor has had its say - is it answered the other way,
    and then only by `orca.stopping_key`'s narrow match; a dialog that does
    not match is declined as ever. True when one was declined; a dialog that
    would not be, or none, is left to the session-still-running report. Stops
    when the session is gone, or after EXIT_DIALOG_WAIT_S."""
    # END_WAIT_S bounds it too, which is what lets the suite run it in tenths of a second.
    deadline = time.time() + min(delivery.EXIT_DIALOG_WAIT_S, delivery.END_WAIT_S)
    handle = db.get_terminal(con, agent)
    while time.time() < deadline:
        sessions = obs.live_sessions()
        if sessions is not None and not sessions.get(agent):
            return False
        seen = orca.screen_blocker(handle) if handle else None
        if seen is not None and seen.kind == orca.MODAL:
            if stop and blocked.stop_work_and_exit(con, agent, log):
                return False
            return not blocked.past_dialog(con, agent, log)[0]
        time.sleep(delivery.END_POLL_S)
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
    if not delivery.session_present(agent):
        # `/exit` typed at the shell exits the shell, and Orca's terminal goes
        # with it - so the one path that turns a recoverable state into an
        # unrecoverable one is closing something that already closed itself.
        # There is nothing to reclaim either; the memory came back when the
        # session did whatever it did.
        return False, "no session process; nothing to close"
    # Work the session says it still runs is not ours to stop: an agent with
    # background work is not idle, whichever path is closing it - unless it is
    # stale and its supervisor was told (`bg_may_stop`).
    stale = delivery.bg_may_stop(con, agent, int(time.time()))
    running = orca.background_work(handle)
    if running and not stale:
        return False, f"background work is running ({running}); not closing"
    # A dialog on screen is what `/exit` would answer instead of ending anything.
    # Past it first, so the close is one act and not a refusal the next tick
    # meets again; when it cannot be, the refusal says why.
    stuck = delivery._past_dialog(con, agent, log)
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
    if _answer_exit_dialog(con, agent, log, stop=stale):
        why = "background work is running; the exit dialog was declined, session kept"
        db.add_event(con, agent, "close_failed", {"why": why, "pid": None})
        return False, why
    # Typed is not ended: the `/exit` can be eaten or slow, and a closed row
    # over a live process is cleared by the sweep, while a relaunch typed at
    # that terminal reaches the old session as a prompt. A session that does
    # not end is reported, neither retried (a second `/exit` goes to whatever
    # ate the first) nor killed (that skips its own shutdown); the sweep
    # records the close if it does end later.
    if not delivery._wait_session_gone(agent):
        pid = (obs.live_sessions() or {}).get(agent)
        why = (f"/exit was typed but the session is still running"
               f"{f' (pid {pid})' if pid else ''} after {delivery.END_WAIT_S}s; not marked closed")
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

    Unless `idle_close.ignore_cache_expiry` (T52) says this agent's resume
    may reuse the cache past that margin regardless - a human call that the
    whole transcript re-read costs less than a fresh conversation would, for
    whatever this agent is.
    """
    if not session or idle_close.ignore_cache_expiry(agent):
        return False
    last = db.last_activity(con, agent)
    return last is None or now - last >= RESUME_WARM_S


NOTHING_WAITING = "nothing is waiting for it; it is woken when something is sent to it"


def waiting_for(con, agent: str) -> bool:
    """Is anything waiting to be told to `agent`: a request at the head of its
    queue, an answer to something it asked, or a say (T293) - never a head,
    but still owed a delivery with nothing else queued. A closed agent is
    woken for that and for nothing else."""
    return bool(db.head_of_queue(con, agent) or db.unannounced_answers(con, agent)
                or db.send_now_pending(con, agent))


def wake_prompt(agent: str, resumed: bool, was_closed: bool = False,
                moved_to: str | None = None, con=None, cold: bool = False,
                waiting: str | None = None) -> str:
    """What an agent is told when it comes back.

    A resume is started for a message and is handed that message, nothing
    else (`waiting`, from `delivery.waiting_prompt`); there is no resume with
    nothing to say.

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
        return waiting or ""
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
        record = db.get_handoff(con, agent) if con else None
        parts.append("Your session was closed while you were idle and could not be "
                     "resumed, so this is a fresh one. "
                     + ("Your handoff is in your system prompt above."
                        if record else
                        "There is no handoff. Read your journal and your inbox "
                        "before carrying on."))
        if not record and (note := handoffmod.memory_line(agent)):
            parts.append(note)
    else:
        parts.append("You were parked and have been resumed. Your context was cleared; "
                     "below is your own handoff record. Carry on from here.")
        record = db.get_handoff(con, agent) if con else None
        if record:
            parts.append(record)
    if not record and con:
        # Nothing carried the request or the transcript, so this says both,
        # finding the path by the session's id when the status names none.
        session, path = handoffmod.previous_session(con, agent, agents.current_account(agent, con))
        if not moved_to:
            parts.append(handoffmod._request_line(con, agent))
        if path:
            parts.append(
                f"Your previous session is {session}, recorded at {path}\n"
                f"Grep it for what you need - do not read it whole, it is a JSONL "
                f"transcript with every tool result in it, and reading it back would "
                f"undo the point of parking you.")
        elif not moved_to:
            parts.append("Your previous session's transcript could not be found; "
                         "read your journal and your inbox to see where you were.")
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


def _write_wake_prompt(agent: str, text: str, suffix: str = "txt") -> Path:
    d = paths.home() / "wake"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{agent}.{suffix}"
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
            delivery._mark_typed(con, agent)
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
    if not closed and row and delivery.session_present(agent):
        # A drifted session is ended and takes the closed agent's wake path
        # below, with its own session id to resume; see `launch.fingerprint`.
        # Ending it is only for the resume that follows, and nothing resumes
        # without a message; with none waiting it is woken in place as before.
        if launch.drifted(agent, row) and waiting_for(con, agent):
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
        # A closed agent is brought back for a message. With none, there is
        # nothing to tell it and no reason to spend a session on it; the
        # message that arrives is what wakes it. A move is the exception: the
        # agent was mid-work and its handoff is what it is started with.
        if moved_to is None and not waiting_for(con, agent):
            return False, NOTHING_WAITING
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
            wait_kind = "usage_7d" if wait.is_long else "usage"
            elsewhere = handoff._move_plan(con, agent, wait_kind)[0] if session and not wait.assumed \
                and not handoff._handoff_backing_off(con, agent) else None
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

        def _running_elsewhere() -> tuple[bool, str] | None:
            """(foreign, why), or None when the table confirms nothing is
            running there. Unreadable is not "none" (T146 round 2, auditor
            #1992): typing over a session that might be there reaches it as
            a prompt, so it is refused the same way (`foreign` True) and for
            the same reason it is not blamed on the agent's own streak - the
            caller only takes `why` from here when `foreign` is True, same
            as it always has for the confirmed case below."""
            sessions = obs.live_sessions()
            if sessions is None:
                return True, "the process table could not be confirmed just now"
            if sessions.get(agent):
                return not taken, "a session it did not start is running"
            return None
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
            found = _running_elsewhere()
            if found:
                foreign, found_why = found
                if foreign:
                    why = found_why
                break
            # Only a resume reads it: a fresh start has read nothing of what the
            # queue held, and is told the ordinary way once it is up.
            waiting = delivery.waiting_prompt(con, agent, log) if resume else None
            if resume and not waiting:
                return False, "what was waiting for it is gone; nothing to resume it for"
            pf = _write_wake_prompt(
                agent, wake_prompt(agent, resume, was_closed=closed,
                                   moved_to=moved_to, con=con, cold=cold,
                                   waiting=waiting[0] if waiting else None))
            cmd = f"{base} --prompt-file {shlex.quote(str(pf))}" + (
                f" -- --resume {session}" if resume else "")
            ok, why = orca.relaunch(
                handle, cmd,
                started=lambda: delivery.wait_for_session(agent, delivery.WAKE_SESSION_TIMEOUT_S, pf))
            if ok:
                resumed = resume
                break
            taken = not pf.exists()
            pf.unlink(missing_ok=True)
            # A session owns the terminal now, and the next attempt's command
            # would be typed into it as a prompt. It is this launch's own, come
            # up late, only if the launch took the prompt.
            found = _running_elsewhere()
            if found:
                foreign, found_why = found
                if foreign:
                    why = found_why
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
            if resumed and waiting:
                # The session took the message as its first prompt and reads it
                # once past the screen. Recorded now, after the close, so the
                # `clear_closed` that follows when the sweep sees it running
                # does not void it and have it told twice (T126).
                waiting[1](session)
            return False, why
        db.reset_counter(con, agent, "wake_failures")
        db.clear_closed(con, agent)
        if resumed and waiting:
            # After `clear_closed`, which voids what a session was told.
            waiting[1](session)
        db.add_event(con, agent, "wake", {"resumed_session": session if resumed else None,
                                          **({"cache_expired": True} if cold else {})})
        db.set_handoff(con, agent, None)
        log(f"  {agent}: relaunched with its wake prompt"
            f"{' (session resumed)' if resumed else ' (fresh - its prompt cache had expired)' if cold else ''}")
        return True, "relaunched with the wake prompt"
    elif not db.get_handoff(con, agent):
        return False, "no handoff to resume from"
    elif not delivery.session_present(agent):
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
    # The record is a page of lines, and typed at a session that many arrive
    # inside <pasted_content> to be declined. It is written to a file the
    # session reads, and one line pointing at it is typed (T106). Not the
    # launch's prompt file: that one is taken and deleted by the launch.
    record = _write_wake_prompt(
        agent, wake_prompt(agent, False, was_closed=False, moved_to=moved_to, con=con),
        suffix="handoff.txt")
    ok, why = orca.resume_with(
        handle, f"You were parked and your context was cleared. Your handoff is in "
                f"{record} - read all of it, then carry on from there.")
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

    One `driver_scope()` for the whole check: `trust_prompt_showing` and
    `_handle_trust_prompt`'s own reads and `wait_for_trust_prompt`'s poll all
    ask this handle's driver, and a stalled clear would otherwise look it up
    fresh - a DB round trip - on every one of that poll's ticks.
    """
    with orca.driver_scope():
        if not orca.trust_prompt_showing(handle):
            return True, "clear"
        return _handle_trust_prompt(con, agent, handle, log)


def _handle_trust_prompt(con, agent: str, handle: str, log) -> tuple[bool, str]:
    """Standing consent: press "Yes" only for the folder the dialog itself
    names, only when that folder is both a declared root and the one on
    record for this agent, and only while `ocp auto-accept` is on. Anything
    else - unreadable, undeclared, a mismatch with the recorded worktree, or
    the setting off - escalates, as does a press whose keys did not land.
    """
    shown, recorded, consented = _trust_consent(con, agent, handle)
    if not consented:
        return _escalate_trust_prompt(con, agent, handle, log, shown, recorded)
    return _press_trust(con, agent, handle, log, shown, recorded)


def _trust_consent(con, agent: str, handle: str) -> tuple[str | None, str | None, bool]:
    """(folder on screen, folder on record, whether standing consent covers
    it): the auto-accept setting is on and the folder is a declared root and the
    agent's own recorded worktree."""
    row = db.get_agent_state(con, agent)
    recorded = spawnmod.worktree_path(row["worktree"]) if row else None
    shown = orca.trust_prompt_folder(handle)
    consented = bool(autoaccept.enabled() and shown and recorded and trust.is_declared(shown)
                     and _same_path(shown, recorded))
    return shown, recorded, consented


def auto_trust(con, agent: str, log) -> bool:
    """The blocked sweep's use of standing consent, for a trust screen a wake
    never met: press "Yes" when consent covers it, and say nothing otherwise -
    the sweep reports what it cannot clear. True when the screen went."""
    handle = db.get_terminal(con, agent)
    if not handle:
        return False
    with orca.driver_scope():
        if not orca.trust_prompt_showing(handle):
            return False
        shown, recorded, consented = _trust_consent(con, agent, handle)
        return consented and _press_trust(con, agent, handle, log, shown, recorded)[0]


def _press_trust(con, agent: str, handle: str, log, shown: str, recorded: str) -> tuple[bool, str]:
    ok, why = orca.confirm_trust(handle)
    # The redraw is not instant; checking on the very next line risks reading
    # the screen the vendor was still drawing, and escalating a trust that
    # actually went through.
    orca.wait_for_trust_prompt(handle, showing=False)
    if not ok or orca.trust_prompt_showing(handle):
        return _escalate_trust_prompt(con, agent, handle, log, shown, recorded)
    db.add_event(con, agent, "trust_confirmed", {"path": shown})
    log(f"  {agent}: trusted {shown} (standing consent)")
    # The screen just changed under us; typing the wake prompt before the
    # vendor has finished drawing its ordinary TUI loses the opening characters.
    orca.wait_idle(handle)
    return True, "trusted"


TRUST_STALL = "folder-trust prompt"
# What opens the escalation a trust stall raises, which `sweep` finds it by when the stall
# is lifted. Not "[trust]" alone: a standing-consent proposal (`team/trust.py`) opens with
# that too. Stalls raised before this marker began with "[trust] <agent>'s terminal ...".
TRUST_ESCALATION = "[trust stall]"


def trust_escalation_prefixes(agent: str) -> tuple[str, str]:
    return TRUST_ESCALATION, f"[trust] {agent}'s terminal is showing"


def _escalate_trust_prompt(con, agent: str, handle: str, log,
                          shown: str | None = None, recorded: str | None = None
                          ) -> tuple[bool, str]:
    """The agent's vendor is sitting at its folder-trust screen - nothing is
    typed, since typed text is not a choice there, it is a coin flip landing
    on whatever the vendor's own default is (claude's: "No, exit"). STALLED
    and escalated rather than retried: the screen does not resolve itself.
    """
    # Already escalated: a later wake - the next tick, an ask, a key on the
    # bridge - meets the same screen and would raise the same request again.
    row = db.get_agent_state(con, agent)
    if row and row["state"] == STALLED and TRUST_STALL in (row["detail"] or ""):
        return False, row["detail"]
    acct = agents.current_account(agent, con)
    vendor = vendors.for_agent(agent).name
    why = (f"{agent}'s terminal is showing {vendor}'s {TRUST_STALL} for "
           f"{shown or 'an unreadable folder'} (account {acct or 'none'}, recorded "
           f"worktree {recorded or 'unknown'}) - nothing was typed into it")
    askmod.escalate(con, agent, f"{TRUST_ESCALATION} {why}", why)
    db.set_agent_state(con, agent, STALLED, wake_at=None, detail=why[:300])
    log(f"  {agent}: {why}")
    return False, why
