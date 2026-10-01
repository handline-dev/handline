"""One tick: what `supervise` decides for an agent, and the tick that runs it for all of them."""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..team import accounts
from ..team import agents
from ..session import autoaccept, blocked
from ..usage import burnrate
from .. import db
from ..session import events
from ..session import fold as foldmod
from ..usage import load
from ..infra import lock
from ..usage import measure
from ..session import observe as obs
from ..session import orca
from ..infra import paths
from ..team.ask import HUMAN
from ..usage import status
from ..usage import tokenlog
from ..session import triggers

from . import delivery, handoff, landing, lifecycle, messaging, reconciler, schedules, taskholds, taskqueue, timebox, usage_poll, waitfor
from .states import (DEGRADED, EVALUATED, HANDOFF, IDLE, MAX_FAILED_ATTEMPTS, MAX_THROTTLE_STREAK,
                     PARKED, QUEUED, RUNNING, STALLED, THROTTLED)


# ---------------------------------------------------------------- reconcile
@dataclass
class Tick:
    now: int
    actions: list[str]


# ---------------------------------------------------------------- tick
# The last reason each agent could not be woken, so a reason that lasts for
# hours is reported when it starts rather than on every tick.
_SAID: dict[str, str] = {}
_SAID_CLOSE: dict[str, str] = {}


def _running_account(agent: str) -> str | None:
    """The account this agent's running process is spending, or None when
    there is no process in the sweep's snapshot or its environment cannot be
    read - "I don't know", which never contradicts anything."""
    o = delivery._seen.get(agent)
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


def queued_or_idle(con, agent: str, when: str) -> str:
    """Write QUEUED when something has not reached the agent yet (`db.unreached_count`:
    a task waiting behind its current one, a message not yet typed, an answer not yet
    told), IDLE otherwise - and return which. What is typed already is not waiting,
    answered or not, so an idle agent with nothing stuck reads IDLE. Shared by every
    writer of a due wake_at, so QUEUED stays a claim about the queue everywhere."""
    if db.unreached_count(con, agent):
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

    # T415: a clear held off by background work is settled before anything else is done for the agent.
    if handoff.settle_owed_clear(con, agent, log):
        return

    # T351: whatever a `message` could not type at once - a screen_blocker,
    # or a wake still settling - gets one more try every tick, RUNNING
    # included: unlike the rest of this function, a pending message is not
    # gated on being idle.
    landing.sweep(con, agent, log)
    messaging.tell_undelivered(con, agent, log)
    messaging.deliver_pending(con, agent, log)

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
                delivery._past_dialog(con, agent, log, only_limit=True)

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
        # the decision in 8.6 unreachable. It is cleared by a handoff
        # that actually succeeds.
        # A parked agent can also be a closed one, and then this wake restores
        # the session - which is the case the gate below exists for.
        restored = bool(row and row["closed_at"])
        if restored:
            # See lifecycle._wake_agent's own closed-agent gate (T146 pt.1):
            # nothing else ever enforces a closed agent's edges.
            delivery.enforce_edges(con, agent, log)
        if restored and not ((row["terminal"] or lifecycle.resumable_without_terminal(row))
                             and lifecycle.waiting_for(con, agent)):
            # The deadline is spent and nothing came for it: a closed session
            # is not resumed to be told nothing. It stays closed, and what
            # arrives next is what wakes it (the block below). Nor is one with
            # nowhere to run (a reboot leaves that): still-due would hold it
            # here for good, ahead of the start for work below that is what
            # gives it a terminal (T406). A closed one whose pane closed with
            # its session has a worktree to wake into.
            woke, restored = False, False
        else:
            woke, why = lifecycle.wake_agent(con, agent, log)
        if restored and not woke:
            delivery.note_hold(agent, f"could not be woken - {why}")
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
        # (`queued_or_idle`) - a stale wake_at can come due after the turn it
        # was set during already went idle on its own, since Stop only clears
        # RUNNING.
        new_state = queued_or_idle(con, agent, "wake_at reached")
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
    need = lifecycle.start_or_wake_needed(con, agent, row)
    started_now = False
    if need and need[0] == "start":
        said = lifecycle._start_for_work(con, agent, log)
        actions.append(said)
        row = db.get_agent_state(con, agent)
        started_now = bool(row and row["terminal"])
        delivery.note_hold(agent, None if started_now else f"could not be started - {said}")
        if started_now:
            _SAID.pop(agent, None)
        elif _SAID.get(agent) != said:
            # Once per reason, like a wake that cannot happen below: the tick
            # comes round every few seconds and a refusal can last for hours.
            _SAID[agent] = said
            log(f"  {agent}: {need[1]} and could not be started - {said}")

    # A closed session cannot read its own inbox, so the arrival of work is
    # what has to bring it back. Without this a `task new` or a say queues into a
    # terminal with nobody in it and waits forever, which is worse than the
    # memory it was saving: reviewer sat closed with two requests pending.
    # Not one started a moment ago: that terminal is a fresh session booting,
    # the closed mark on it is stale until the sweep sees the session, and a
    # wake typed into a TUI that is still coming up loses its opening
    # characters and leaves the rest in the input box, unsent.
    if need and need[0] == "wake":
        # `start_or_wake_needed` is the whole list, so the screens that say
        # "the next tick wakes it" and the daemon's start ask the same question
        # (T406). A request delivered before the close (T154) is not on it:
        # woken for alone it closes again on the next idle window, forever.
        what = need[1]
        ok, why = lifecycle.wake_agent(con, agent, log)
        delivery.note_hold(agent, None if ok else f"could not be woken - {why}")
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
    delivery.track_background_work(con, agent, row)
    row = db.get_agent_state(con, agent)
    at_wall = events.at_limit(con, agent)
    open_idle = state not in (RUNNING, HANDOFF) and not woke_now and not at_wall \
        and not (row and row["closed_at"])
    if open_idle:
        # An answer first: an agent that asked something and parked is waiting
        # on that, and telling it about new work while its own question is
        # still unanswered is the wrong order.
        closed = lifecycle._cold_before_typing(con, agent, row, now, st, log)
        if closed:
            actions.append(f"{agent}: cache expired; session closed, the wake delivers")
            row = db.get_agent_state(con, agent)
        if closed is None:
            delivery.deliver_answers(con, agent, log)
            # A task told past an idle head starts a turn: the nudge about that head waits.
            if not delivery.deliver_queued(con, agent, log):
                delivery.nudge_forgotten_resolve(con, agent, row, now, log)

    # Memory is the scarce resource on one laptop running a team, and an
    # idle session spends it while earning nothing: it holds its context,
    # costs no tokens, and answers no one. Closing it is not a handoff -
    # nothing is cleared - and waking it resumes the same session, so the
    # only thing that changes is who has the memory.
    if not at_wall and lifecycle.should_close(con, agent, state, row, now, st):
        ok, why = lifecycle.close_agent(con, agent, log, st)
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
        if not hist or hist[-1][0] != st.short.at:
            hist.append((st.short.at, st.short.pct))
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
            and state == PARKED \
            and reading and reading.source != "assumed" \
            and orca.available() and orca.alive(row["terminal"]):
        last = db.last_handoff(con, agent)
        verdict = triggers.evaluate(st.short_trusted, st.trusted_ctx_used,
                                    long_pct=st.long_trusted, ctx_limit=ctx_limit)
        movable = last and last["trigger_kind"] in handoff.MOVABLE_TRIGGERS
        # Still at the wall here, but another account may have rolled over or
        # freed a slot since the park: if the work is worth a cold start, move
        # now rather than wait out this account's own window.
        if movable and verdict.fired and verdict.kind in handoff.MOVABLE_TRIGGERS \
                and handoff._move_plan(con, agent, verdict.kind)[0]:
            with lock.terminal(agent) as mine:
                # require_idle=False: `_move_to_room` ends the session itself,
                # through `_end_session`, which does its own fresh idle read
                # inline where `/exit` is typed - require_settled=True is what
                # actually applies here, against a delivery just typed.
                moved = bool(mine) and not delivery._type_gate(con, agent, None, require_idle=False,
                                                      require_settled=True) \
                    and handoff._move_to_room(con, agent, verdict, log)[0]
                if moved:
                    delivery._mark_typed(con, agent)
            if moved:
                actions.append(f"{agent}: moved off a parked account")
                return
        if movable and not verdict.fired:
            new_state = queued_or_idle(con, agent, "usage recovered")
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
    if woke_now or not lifecycle.triggers_apply(row):
        return

    # trusted_* is None only when we genuinely do not know: no snapshot, or
    # one too old to believe. Unknown is not a wall, so it does not fire; an
    # account whose window is merely quiet reads as 0, not as unknown.
    t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used,
                          long_pct=st.long_trusted, ctx_limit=ctx_limit)
    # A turn that ended on the usage limit means the agent is at the wall
    # whatever the reading says (it can lag, or be the other window): move it
    # the same way a trigger would.
    stopped = at_wall
    if stopped and not (t.fired and t.kind in handoff.MOVABLE_TRIGGERS):
        text = stopped["limit"]
        t = triggers.Trigger(True, "usage_7d" if "week" in text.lower() else "usage",
                             f"turn ended on its usage limit: {text} "
                             f"(read from {stopped.get('limit_source', '?')})")
    if t.fired:
        ok = handoff.perform_handoff(con, agent, t, log)
        actions.append(f"{agent}: handoff ({t.kind}){'' if ok else ' -> degraded'}")

        fresh = db.get_agent_state(con, agent)
        if fresh and fresh["throttle_count"] >= MAX_THROTTLE_STREAK:
            # 8.6: raise to the founder only after repeated automatic recovery failed.
            db.set_agent_state(con, agent, STALLED,
                               detail=f"{fresh['throttle_count']} consecutive degraded handoffs"
                               + blocked.dialog_note(con, agent))
            actions.append(f"{agent}: STALLED after {fresh['throttle_count']} degraded handoffs")
            log(f"  {agent}: STALLED - needs a human")
        elif fresh and fresh["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            db.set_agent_state(con, agent, STALLED,
                               detail=f"{fresh['failed_attempts']} failed attempts")
            actions.append(f"{agent}: STALLED after {fresh['failed_attempts']} failed attempts")


def start_waiting(con, log) -> list[str]:
    """T408: at daemon start, bring up at once every agent that has work waiting
    and no session - the tick would, but only after its usage polls and each
    agent in turn, and a reboot leaves every agent in that state. Runs the sweep
    first, so a terminal the reboot killed is dropped and the agent started in
    its worktree rather than woken into a handle that is gone."""
    actions: list[str] = []
    reconciler.sweep(con, log, actions)
    for agent in paths.list_agents():
        try:
            row = db.get_agent_state(con, agent)
            need = lifecycle.start_or_wake_needed(con, agent, row)
            if need and need[0] == "start":
                actions.append(lifecycle._start_for_work(con, agent, log))
            elif need:
                ok, why = lifecycle.wake_agent(con, agent, log)
                actions.append(f"{agent}: woken at start" if ok else f"{agent}: could not wake - {why}")
        except Exception as e:  # noqa: BLE001 - one agent's failure is not the start's
            log(f"  {agent}: start for waiting work failed: {type(e).__name__}: {e}")
    return actions


def tick(con, log, samples: dict | None = None) -> Tick:
    now = int(time.time())
    actions: list[str] = []
    samples = samples if samples is not None else {}

    # Before anything is decided on what we believe, check what is true. Two
    # calls out, whatever the size of the team, and they cost 0.2s together.
    reconciler.sweep(con, log, actions)

    # And what is true includes the two readings every decision below is made
    # on. Both are pulls now (D11), so they happen here rather than arriving
    # whenever a session happens to render a status line.
    usage_poll.collect_snapshots(con, log)
    try:
        usage_poll.poll_usage(con, log)
    except Exception as e:  # noqa: BLE001 - a failure outside any one account is not the tick's failure either
        log(f"  usage: poll failed: {type(e).__name__}: {e}")

    # Link a file added to shared/.claude into accounts registered before it.
    try:
        for acct, linked in accounts.relink_all(log).items():
            log(f"  relinked {acct}: {', '.join(linked)}")
    except Exception as e:  # noqa: BLE001 - a bad account here is not the tick's failure
        log(f"  relink: {e}")

    # A handoff past its deadline must fail here, not only at startup.
    actions += reconciler.expire_handoffs(con, log)

    # Midnight rollover (section 5). Nothing else calls it, and without it
    # yesterday's "in progress" stays in progress forever - and every fold
    # after midnight has to keep repeating yesterday's completed items to pass
    # the monotonicity check, so the digest grows without bound.
    day = foldmod.today()
    if usage_poll._last_rollover_day["day"] and usage_poll._last_rollover_day["day"] != day:
        for agent in paths.list_agents():
            ok, msg = foldmod.rollover(agent)
            if ok and "already exists" not in msg:
                actions.append(msg)
                log(f"  rollover: {msg}")
    usage_poll._last_rollover_day["day"] = day

    # The human is no agent, so no per-agent delivery checks the edges of what
    # agents wrote to them; without this an agent session's ask to the human
    # keeps edge_ok NULL for good.
    try:
        delivery.enforce_edges(con, HUMAN, log)
    except Exception as e:  # noqa: BLE001 - a bad table here is not the tick's failure
        log(f"  {HUMAN}: edge check failed: {type(e).__name__}: {e}")

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
            reconciler._stall_agent(con, agent, log, actions, e, "error")

    blocked.sweep(con, log, now)
    try:
        autoaccept.sweep(con, log, now)
    except Exception as e:  # noqa: BLE001 - a prompt left for a person is not the tick's failure
        log(f"  auto-accept: {type(e).__name__}: {e}")
    try:
        timebox.sweep(con, log, now)
    except Exception as e:  # noqa: BLE001 - a notice that failed is not the tick's failure
        log(f"  timebox: {type(e).__name__}: {e}")
    try:
        taskqueue.sweep(con, log, now)
    except Exception as e:  # noqa: BLE001 - a task left waiting is tried again, not the tick's failure
        log(f"  task queue: {type(e).__name__}: {e}")
    try:
        taskholds.sweep(con, log, now)
    except Exception as e:  # noqa: BLE001 - a reason not recorded is recorded on the next tick
        log(f"  task holds: {type(e).__name__}: {e}")
    try:
        schedules.sweep(con, log, now)
    except Exception as e:  # noqa: BLE001 - a message not sent is tried again, not the tick's failure
        log(f"  schedules: {type(e).__name__}: {e}")
    try:
        waitfor.sweep(con, log, now)
    except Exception as e:  # noqa: BLE001 - a report that failed is not the tick's failure
        log(f"  wait-for: {type(e).__name__}: {e}")
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
        burnrate.refresh_long_ratios(con)
    except Exception as e:  # noqa: BLE001 - no ratio only means the week is judged at its wall
        log(f"  sample: week ratio failed: {e}")
