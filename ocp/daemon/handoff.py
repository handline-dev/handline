"""Moving an agent to another account: the plan, the room, and the handoff itself."""
from __future__ import annotations

import json
import time
import uuid

from .. import accounts
from .. import timefmt
from .. import agents
from .. import db
from .. import events
from .. import handoff as handoffmod
from .. import load
from .. import lock
from .. import measure
from .. import observe as obs
from .. import orca
from .. import paths
from .. import status
from .. import triggers

from . import delivery, lifecycle
from .states import (ASSUMED_ACCOUNT_RECHECK_S, BY_HAND_MARK, HANDOFF, HANDOFF_DEADLINE_S, IDLE,
                     LIMIT_SETTLE_S, MIN_HANDOFF_INTERVAL_S, MOVING_MARK, PARKED, QUEUED, RUNNING,
                     STALLED, backoff_for)


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
                              f"{timefmt.stamp(wake, timefmt.CLOCK)}")
    # Same gap as a fresh park's - see _perform_handoff - through a different
    # path: a restart recovering an in-flight handoff parks the agent too.
    db.add_event(con, agent, "park", {
        "kind": kind, "wake_at": wake,
        "account": reading.account if reading else None,
        "account_source": reading.source if reading else None,
    })
    log(f"  {agent}: resumed handoff from '{step}' -> PARKED")


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
                in_the_way = delivery._past_dialog(con, agent, log, only_limit=True) if mine else ""
            if in_the_way and delivery._stalled_on_dialog(con, agent, in_the_way, log):
                return False
            if in_the_way or not orca.typeable_now(handle):
                log(f"  {agent}: {trigger.kind} fired but "
                    + (f"a dialog is in the way ({in_the_way})" if in_the_way else "the agent is mid-turn")
                    + " - deferring")
                return True
        if not delivery.session_present(agent):
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
        why = delivery._type_gate(con, agent, handle, require_idle=not settled, require_settled=True)
        if why:
            log(f"  {agent}: {trigger.kind} fired but {why} - not handing off")
            return True
        ok, typed = _perform_handoff(con, agent, trigger, log, dest)
        if typed:
            delivery._mark_typed(con, agent)
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
    ok, why = lifecycle._end_session(con, agent, log, st, resume=None)
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
    woke, why = lifecycle._wake_agent(con, agent, log, moved_to=there)
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
    cleared, why = delivery._reclaim_context(con, agent, log)
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
                       detail=f"{label}; wakes at {timefmt.stamp(wake, timefmt.CLOCK)}")
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
    log(f"  {agent}: PARKED until {timefmt.stamp(wake, timefmt.DATE_TIME)}")
    return ok, cleared
