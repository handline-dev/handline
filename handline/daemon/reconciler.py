"""The startup pass and the per-tick sweep: expiring handoffs, stalling, unsticking a RUNNING label."""
from __future__ import annotations

import time

from ..team import agents
from ..team import ask as askmod
from .. import db
from ..session import observe as obs
from ..session import term
from ..infra import paths
from ..usage import status
from ..infra.yamlcompat import ConfigError

from . import delivery, evaluate, handoff, lifecycle, login_expired
from .states import (DEGRADED, DEGRADED_RETRY_S, IDLE, MAX_FAILED_ATTEMPTS, PARKED, RUNNING,
                     STALLED, STUCK_RUNNING_S, THROTTLED)


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
            handoff.resume_handoff(con, row, log)
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
            # See lifecycle._wake_agent's own closed-agent gate (T146 pt.1):
            # nothing else ever enforces a closed agent's edges.
            delivery.enforce_edges(con, agent, log)
        if row["closed_at"] and row["terminal"] and lifecycle.waiting_for(con, agent):
            try:
                ok, why = lifecycle.wake_agent(con, agent, log)
            except (ConfigError, UnicodeDecodeError, OSError) as e:
                _stall_agent(con, agent, log, actions, e,
                                           "could not wake at reconciliation")
                continue
            if not ok:
                actions.append(f"{agent}: could not wake at reconciliation, "
                               f"still due - {why}")
                continue
            actions.append(f"{agent}: woken at reconciliation")
        new_state = evaluate.queued_or_idle(con, agent, f"woke from {row['state']} at reconciliation")
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


def _lift_trust_stall(con, agent: str, row, log, actions: list[str]) -> None:
    """A stall raised for a folder-trust screen is lifted once the screen has
    been read and is not there. The stall was the daemon's own, made on that
    screen, and the world has refuted it - a standing-consent press by the
    blocked sweep, a person at the keyboard, or the vendor itself answered it,
    and a running session is not something a person needs to clear. Its
    decision closes with it. An unreadable screen changes nothing.
    """
    with term.driver_scope():
        if not term.trust_prompt_gone(row["terminal"]):
            return
    _close_stall(con, agent, log, actions, lifecycle.trust_stall_prefixes(agent), "folder-trust prompt")


def _lift_dialog_stall(con, agent: str, row, log, actions: list[str]) -> None:
    """The same for a start-up dialog no rule answered (`lifecycle._raise_dialog_stall`):
    lifted once the screen is read and shows no dialog. Unreadable changes nothing."""
    with term.driver_scope():
        seen = term.screen_blocker(row["terminal"])
    if seen is None or seen.kind == term.BACKGROUNDED:
        _close_stall(con, agent, log, actions, (lifecycle.DIALOG_DECISION,), lifecycle.DIALOG_STALL)


def _close_stall(con, agent: str, log, actions: list[str], prefixes: tuple[str, ...], what: str) -> None:
    # Decision first, row second: a lift whose close failed would strand the
    # decision, and a kept stall is retried on the next tick.
    for rid in (r for prefix in prefixes for r in db.open_decision_ids(con, agent, prefix)):
        try:
            askmod.resolve(rid, f"cleared - its {what} is answered", state="done", notify=False)
        except Exception as e:
            log(f"  {agent}: could not close decision {rid} ({e}); the stall stays")
    if any(db.open_decision_ids(con, agent, prefix) for prefix in prefixes):
        return
    db.set_agent_state(con, agent, IDLE, detail=f"the {what} is answered; the stall is lifted")
    actions.append(f"{agent}: STALLED -> IDLE (its {what} is answered)")
    log(f"  {agent}: its {what} is answered; the stall is lifted")


# Once per terminal: the sweep comes round every tick, and a screen can stay drawn for a long time.
_SAID_DRAWN: dict[str, str] = {}


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
    delivery._seen.clear()
    delivery._seen.update(seen)
    delivery._seen_at["t"] = time.time()
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

        if alive and row["state"] == STALLED and lifecycle.TRUST_STALL in (row["detail"] or ""):
            _lift_trust_stall(con, agent, row, log, actions)
            row = db.get_agent_state(con, agent)   # what follows judges the row as it now is
        if alive and row["state"] == STALLED and lifecycle.DIALOG_STALL in (row["detail"] or ""):
            _lift_dialog_stall(con, agent, row, log, actions)
            row = db.get_agent_state(con, agent)

        if alive and row["state"] == STALLED:
            login_expired.lift(con, agent, row, log, actions)
            row = db.get_agent_state(con, agent)

        if alive and closed:
            # It came back without us - a human relaunched it, or a resume
            # landed that we never recorded. Believing our own note here would
            # close it a second time on top of a live session.
            db.clear_closed(con, agent)
            row = db.get_agent_state(con, agent)
            actions.append(f"{agent}: a session is running; the closed mark was wrong")
            log(f"  {agent}: running again (pid {o.session_pid}); cleared the closed mark")
            # Judged before the streak is reset: the reset is what refutes it.
            wake_stall = (row["state"] == STALLED and row["wake_failures"] >= MAX_FAILED_ATTEMPTS
                          and not any(m in (row["detail"] or "")
                                   for m in (lifecycle.TRUST_STALL, lifecycle.DIALOG_STALL)))
            if row["wake_failures"]:
                # A session is running, which refutes a streak of launches that
                # started nothing - whatever state the row is in by now.
                db.reset_counter(con, agent, "wake_failures")
                row = db.get_agent_state(con, agent)
            if wake_stall:
                # This stall is ours: we stopped because our launches started
                # nothing, and a session is running now. 8.6 keeps a human in
                # the loop for a stall the agent cannot refute - this one the
                # world just refuted, on the same evidence that cleared the
                # closed mark, and leaving it would hide a live agent from
                # every delivery. Not a trust stall: a session sitting at the
                # prompt is running too, and only the screen refutes that one.
                db.set_agent_state(con, agent, IDLE,
                                   detail="a session is running; the wake stall is lifted")
                actions.append(f"{agent}: STALLED -> IDLE (it is running after all)")
        elif not alive and not closed and row["terminal"] and delivery.session_still_drawn(row["terminal"], wait=False):
            # The table lists no session but the terminal's screen draws one: the table missed a live
            # session (it has done so), and recording it closed would have a wake type a launch line
            # at it. Left as it is; the next reading either lists it or the screen clears.
            if _SAID_DRAWN.get(agent) != row["terminal"]:
                _SAID_DRAWN[agent] = row["terminal"]
                log(f"  {agent}: the process table lists no session but one is drawn on its terminal; "
                    f"not recorded as closed")
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
    if not handle or not term.available() or not term.idle_now(handle):
        return
    # idle_now alone reads a permission prompt or a dialog as idle: nothing is
    # streaming while it waits on a person, the same silence this whole check
    # is looking for. screen_blocker catches what idle_now cannot - an agent
    # sitting at an unanswered prompt is not stuck for no reason, it is
    # legitimately waiting on the one thing that already has its own path to
    # the founder (T223/T187); flipping it to IDLE here only lets ordinary
    # delivery type new work over a screen nobody has answered yet.
    if term.screen_blocker(handle) is not None:
        return
    db.set_agent_state(con, agent, IDLE,
                       detail=f"RUNNING with nothing heard for {mins}m, and idle")
    db.add_event(con, agent, "unstuck", {"was": RUNNING, "silent_minutes": mins})
    actions.append(f"{agent}: RUNNING for {mins}m with nothing heard; back to IDLE")
    log(f"  {agent}: stuck in RUNNING for {mins}m and idle at its prompt - back to IDLE")
