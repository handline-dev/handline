"""Telling an agent things: seeing its session, typing to it, delivering queued requests and answers, nudging a forgotten resolve."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from ..team import agents, ask as askmod, team as teammod
from ..infra.yamlcompat import ConfigError
from ..infra import paths, protected, sealed, timefmt
from ..session import blocked, idle_close
from .. import db
from ..session import events
from ..infra import lock
from ..session import observe as obs
from ..session import orca
from ..usage import status
from ..infra import textsafe

from . import handoff
from .states import (HANDOFF, IDLE, PARKED, QUEUED, RUNNING, STALLED, STUCK_RUNNING_S, awaiting_reply)


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
    if row and row["state"] == PARKED and not row["closed_at"] and not awaiting_reply(row):
        last = db.last_handoff(con, agent)
        if last and last["trigger_kind"] in handoff.MOVABLE_TRIGGERS:
            return f"{agent} is parked until its usage window rolls over"
    return None


def _live_session(con, agent: str) -> str | None:
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    return st.session_id


def _session_changed(con, agent: str) -> bool:
    """Is the session on screen a different one from the one the head was typed into?

    A delivery belongs to the session that read it. A resume keeps its id, so
    it is not told twice; a fresh start has a new one and has read nothing. A
    session that was closed and comes back is told anyway: `clear_closed`
    voids what it was told. An id that cannot be read is not a difference -
    nothing is retyped on a guess.
    """
    # What voids it on a resume that keeps the id is `db.clear_closed`
    # (`forget_told`), and every caller of that leans on this: a wake, the
    # reconciler finding a session running, a moved account's wake. A new
    # caller that is not a restart would re-tell what its session already read.
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


def enforce_edges(con, agent: str, log) -> bool:
    """Check every request still waiting for `agent` against the founder's routing
    table, here where the seal on team.yaml can be read (an agent's own session
    cannot read it, so its `ask` checks a file it cannot verify). A request whose
    edge that table does not allow is denied before anyone is told of it. False
    when the table cannot be trusted - changed, unsealed or unreadable: nothing
    is delivered until the founder has looked (T202). A request from a name that
    is not a registered agent (the system's own) is not an agent's edge."""
    hold = f"edgehold:{agent}"
    try:
        t = teammod.Team.load()
    except FileNotFoundError:
        for r in db.queued_for(con, agent):
            db.clear_edge(con, int(r["id"]))
        return True
    except (ConfigError, OSError) as e:
        if not db.get_cursor(con, hold):
            db.set_cursor(con, hold, 1)
            log(f"  {agent}: nothing delivered - team.yaml is not trusted ({e})")
        return False
    if protected.in_agent_session() and \
            sealed.state(sealed.TEAM, paths.team_file().read_text()) != "ok":
        # An agent's own push cannot read the seal, so it cannot clear anything.
        return False
    if db.get_cursor(con, hold):
        db.set_cursor(con, hold, 0)
    for r in db.queued_for(con, agent):
        if r["edge_ok"]:
            continue
        if r["requester"] not in t.agents:
            db.clear_edge(con, int(r["id"]))
            continue
        d = t.route(r["requester"], r["target"], chain_depth=int(r["chain_depth"] or 0),
                    edge_exempt=(r["relation"], r["kind"]) == (teammod.HANDBACK, "task"))
        kind = r["kind"] if r["fyi"] or r["kind"] in askmod.OVERRIDE_KINDS else d.kind
        if not d.allowed:
            why = d.reason
        elif (r["relation"], r["response"], r["kind"]) != (d.relation, d.response, kind):
            why = (f"the routing table says {d.relation}/{d.response}/{kind}, the request "
                   f"says {r['relation']}/{r['response']}/{r['kind']}")
        else:
            db.clear_edge(con, int(r["id"]))
            continue
        db.retire_queued(con, int(r["id"]), "denied", f"refused at delivery: {why}")
        log(f"  {agent}: #{r['id']} from {r['requester']} refused - {why}")
    return True


def head_task_blocked(con, agent: str, head) -> bool:
    """Is `head`'s own task on record as waited on (T337): blocked by another
    still-open task (`ocp task edit T<n> --blocked-by T<m>`), or held up on an
    ask `agent` itself sent that is linked to the same task. Built on what
    already exists, not a new kind of wait nothing else reads - a head with
    no task at all is never this, since there is nothing to check it
    against."""
    for t in db.tasks_for_request(con, int(head["id"])):
        tid = int(t["id"])
        if db.task_blockers(con, tid):
            return True
        if any(r["requester"] == agent and int(r["id"]) != int(head["id"])
               and r["state"] == "queued" for r in db.task_requests(con, tid)):
            return True
    return False


def effective_head(con, agent: str):
    """`db.head_of_queue`, unless it is blocked on record (T337) - then the
    next request behind it instead, so the one-at-a-time rule falls on
    whatever the agent can actually act on, not on what it is stuck waiting
    on.

    Read this, not `db.head_of_queue` directly, by anything asking what the
    agent is working on or was told - delivery, `forgotten_resolve`,
    `blocked.work_waiting`, a cold session's own close, a closed agent's wake,
    the resume prompt, the handoff and the progress note, and `ocp inbox`'s
    unread view and the stuck screen's own reason for a specific request
    (T337 round 4, auditor #2028's sweep). `db.head_of_queue` itself is for a
    different question - what is structurally first, whatever is or is not
    reachable right now - and every one of its own readers (`ocp inbox`
    reading its own queue, the board, a plain queue-not-empty check) keeps
    asking it directly: the true head is never touched by any of this, so it
    is exactly what they keep finding, the moment the block clears.

    Only one level: a request that jumps a blocked head and is itself
    blocked is not chased further. T337 was written for one open head with
    ordinary work queued behind it, not a chain of them."""
    head = db.head_of_queue(con, agent)
    if head and head_task_blocked(con, agent, head):
        return db.head_of_queue(con, agent, skip=int(head["id"]))
    return head


def effective_head_or_marked(con, agent: str):
    """`effective_head`, or - with none - the oldest `send_now_pending` row
    (`db.head_of_queue_or_marked`'s own fallback, T293): a say is never a
    head, but with nothing else queued it is still the one thing waiting -
    what a resumed session opens on, and `ocp inbox`'s own unread view."""
    return effective_head(con, agent) or next(iter(db.send_now_pending(con, agent)), None)


def effective_task_head(con, agent: str):
    """`db.task_head`, unless it is blocked on record (`head_task_blocked`, T337):
    then the next task behind it."""
    head = db.task_head(con, agent)
    if head and head_task_blocked(con, agent, head):
        return db.task_head(con, agent, skip=int(head["id"]))
    return head


def deliver_queued(con, agent: str, log) -> bool:
    """Tell an agent about its next task - only tasks, one at a time and only when
    it is free: a message is typed at once (`messaging.tell_now`) and never comes
    here (T398). Told once per task: the cursor holds the id last announced.
    """
    if not enforce_edges(con, agent, log):
        return False
    head = effective_task_head(con, agent)
    if not head:
        return False
    head_due = db.get_cursor(con, f"notify:{agent}") != int(head["id"]) \
        or _session_changed(con, agent)
    if head_due and head["told_out_of_turn"] and not _session_changed(con, agent):
        # Told already, out of turn; becoming the head is not news.
        db.set_cursor(con, f"notify:{agent}", int(head["id"]))
        head_due = False
    if not head_due:
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
    depth = sum(1 for r in db.queued_for(con, agent) if r["kind"] == "task")
    with lock.terminal(agent) as mine:
        if not mine:
            return False          # something else is mid-send; next tick
        if _type_gate(con, agent, handle):
            return False          # stale reading above; next tick
        # One task per pass: it starts the agent's turn, so a second would be typed
        # mid-turn. The rest go out on later ticks, each behind its own typeable_now.
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
# How many times a forgotten head is nudged, and how long it must have sat
# idle for each: ten minutes, thirty, then two hours - not evenly spaced,
# because being asked once already and still not answering is worth waiting
# longer on before asking again. Past the last one nothing more is typed at
# the agent (T337 founder #2002/#2003: repeating this forever at an agent
# that turns out to be genuinely stuck, not forgetful, has a real cost of its
# own); `timebox.sweep_forgotten_resolve_capped` tells its supervisor once
# instead.
NUDGE_SCHEDULE_S = (NUDGE_AFTER_S, FORGOTTEN_STUCK_S, 2 * 3600)


def track_background_work(con, agent: str, row) -> None:
    """Record whether an open session's own footer reports work still running
    behind its prompt, in whatever state it is waiting.

    A session with such work is not idle: the display shows it working, and
    nothing that acts on idleness (the resolve nudge, the idle close) acts on
    it. The session reports this, so it is read every tick, never assumed from
    a clock - RUNNING included (founder #1975/#2125): `screen_state` shows the
    same background detail while RUNNING, and a cursor frozen from whenever
    background work was first seen, however long ago, would make that detail
    wrong for as long as the turn runs. When the footer stops reporting it,
    the idle clock starts there (`idle_since`), not at its last hook event;
    the end is not a model turn, so it stays out of `db.last_activity`, which
    the prompt cache is judged by."""
    if not row or row["state"] in (HANDOFF, STALLED) or row["closed_at"] \
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


# Background work the agent has shown nothing beside this long is stale: forgotten
# rather than being waited on. Under the hour a prompt cache lives (RESUME_WARM_S),
# so it is caught while a resume would still have been warm (founder #435).
STALE_BG_S = 3000
# Once the supervisor has been told, this long passes before the work may be
# stopped: room to say no, by giving the agent something to do or with
# `ocp idle-timeout never --agent`.
STALE_BG_VETO_S = 600


def bg_since(con, agent: str) -> int | None:
    """When the daemon first saw this agent's session report background work
    in this stretch, or None when it does not."""
    return db.cursor_updated(con, f"bgwork:{agent}") if has_background_work(con, agent) else None


def bg_silent_s(con, agent: str, now: int) -> int | None:
    """How long this agent has shown nothing while its session reports
    background work, or None without any. Work that notifies the model or is
    waited on shows up as activity; hours without it is a shell nobody reads."""
    since = bg_since(con, agent)
    if since is None:
        return None
    return max(0, now - (db.last_activity(con, agent) or since))


def bg_stale(con, agent: str, now: int) -> bool:
    silent = bg_silent_s(con, agent, now)
    return silent is not None and silent >= idle_close.config_seconds("stale_background_s", STALE_BG_S)


def bg_may_stop(con, agent: str, now: int) -> bool:
    """Stale, and told to its supervisor at least STALE_BG_VETO_S ago - and
    not an agent whose close timeout is NEVER, the veto the notice promises."""
    if idle_close.close_timeout_s(agent) == idle_close.NEVER:
        return False
    told = db.get_cursor(con, f"stale_bg_told:{agent}")
    return bool(told) and now - told >= STALE_BG_VETO_S and bg_stale(con, agent, now)


def bg_holds(con, agent: str, now: int) -> bool:
    """Does background work keep this agent from being closed or typed past?"""
    return has_background_work(con, agent) and not bg_may_stop(con, agent, now)


def _waiting_on_ask(con, agent: str, head) -> bool:
    """Does `agent` have an open ask it may be waiting on instead of `head`?
    Not the ones that are why a task ahead of `head` is blocked (`head_task_blocked`,
    T337): `head` stands in for that task, so the agent is meant to be working it
    while they are open (T343)."""
    asks = set(db.open_ask_ids_of(con, agent))
    true_head = db.task_head(con, agent)
    if asks and true_head and int(true_head["id"]) != int(head["id"]) \
            and head_task_blocked(con, agent, true_head):
        for t in db.tasks_for_request(con, int(true_head["id"])):
            asks -= {int(r["id"]) for r in db.task_requests(con, int(t["id"]))}
    return bool(asks)


def forgotten_resolve(con, agent: str, row, now: int) -> dict | None:
    """What the agent is holding, if it looks like a resolve nobody wrote: its next
    task (`effective_task_head`) when that was told and is still open, otherwise the
    oldest open ask that was typed at it (T398) - a message is judged on its own
    delivery, not on a cursor the tick keeps for tasks.

    IDLE or QUEUED with a live terminal, delivered and told and still open, and
    nothing the agent asked that is still unanswered - which is what separates a
    request forgotten from one being waited on, since an agent waiting on an answer is
    also IDLE (or QUEUED) with its request open. Idle time counts from the later of its
    last activity and the delivery.

    QUEUED counts as IDLE here: it only means something is queued, and only a
    fresh prompt clears it, so a QUEUED agent whose task was told in its current
    session is the same forgotten resolve. `blocked.work_waiting` reads this function,
    so it also decides whether that agent is reported.

    A task blocked on record (T337) is never this on its own account:
    `effective_task_head` already looked past it.
    """
    if not row or row["state"] not in (IDLE, QUEUED) or row["closed_at"] or not row["terminal"]:
        return None
    if has_background_work(con, agent) and not bg_stale(con, agent, now):
        return None           # not idle: work it started is still running
    head = effective_task_head(con, agent)
    if not head or not head["delivered_at"] \
            or db.get_cursor(con, f"notify:{agent}") != int(head["id"]):
        cursor = db.get_cursor(con, f"notify:{agent}")
        head = next((r for r in db.queued_for(con, agent)
                     if r["kind"] not in ("task", "say") and r["delivered_at"]
                     and (r["told_out_of_turn"] or cursor == int(r["id"]))), None)
        if head is None:
            return None
    if _waiting_on_ask(con, agent, head) or db.unannounced_answers(con, agent):
        return None
    idle = now - max(int(head["delivered_at"]), idle_since(con, agent) or 0)
    return {"head": head, "idle_s": idle, "behind": db.queue_depth(con, agent) - 1}


# Claude Code hands typed text of four or more lines to the model as a paste,
# inside <pasted_content>, whose instructions it follows only where the user's
# own message asks. Every delivery of four or more lines in the agents'
# transcripts arrived wrapped (9 of 9), none of three or fewer (66 of 66), and
# orcapipe.ui declined a wrapped task as untrusted (T105).
MAX_TYPED_LINES = 3


def _send(handle: str, text: str) -> tuple[bool, str]:
    """`orca.send` for text an agent is meant to act on: never more than
    `MAX_TYPED_LINES` lines. Past that the middle lines are joined into one; a
    sender still builds its own three lines so nothing important is squeezed."""
    lines = text.split("\n")
    if len(lines) > MAX_TYPED_LINES:
        keep = MAX_TYPED_LINES - 2
        lines = lines[:keep] + [" ".join(lines[keep:-1])] + lines[-1:]
    return orca.send(handle, "\n".join(lines))


def type_notice(con, agent: str, text: str) -> bool:
    """Type one line at an agent's open session, if it may be typed at now.

    The gates every unprompted line goes through: not at the wall, a live
    session on a live terminal, nothing on screen that a line would answer, the
    terminal lock held and the screen read again once it is. False for any of
    them, or a send that failed; the caller decides when to try again. Nothing
    here touches an attempt counter, so a notice cannot stall an agent.
    """
    handle = db.get_terminal(con, agent)
    if wall_reason(con, agent) or not handle or not orca.available() \
            or not orca.alive(handle) or not session_present(agent) \
            or not orca.typeable_now(handle):
        return False
    with lock.terminal(agent) as mine:
        if not mine or _type_gate(con, agent, handle):
            return False
        ok, _ = _send(handle, text)
        if ok:
            _mark_typed(con, agent)
    return ok


def nudge_count(con, agent: str, head_id: int) -> int:
    """How many times the current head has already been nudged - 0 for a
    head that is not the one the `nudge:{agent}` cursor names, since that
    count belongs to whatever head earned it, not this one."""
    return db.get_cursor(con, f"nudge_count:{agent}") \
        if db.get_cursor(con, f"nudge:{agent}") == int(head_id) else 0


def nudge_exhausted(con, agent: str, head_id: int) -> bool:
    """True once `NUDGE_SCHEDULE_S` is used up for this head - nothing more
    will be typed at the agent about it; `timebox.sweep_forgotten_resolve_capped`
    is what tells its supervisor from here."""
    return nudge_count(con, agent, head_id) >= len(NUDGE_SCHEDULE_S)


def nudge_forgotten_resolve(con, agent: str, row, now: int, log) -> bool:
    """Type a reminder at an agent that has left its head request open, again
    at each rung of `NUDGE_SCHEDULE_S` for as long as that stays true, up to
    its last one.

    A single reminder used to be the last thing the daemon ever did about it:
    past that, only `actions.stuck` (the founder's own screen) would notice,
    and nothing typed at the agent again - so a forgotten head, and whatever
    piled up queued behind it, stayed silent until a human read the screen and
    acted (T337, founder #2002/#2003). The same silence that earns the first
    nudge earns the next rung too, up to the cap `NUDGE_SCHEDULE_S` sets.

    `forgotten_resolve` already reads the queue through `effective_task_head`,
    falling back to the oldest typed open ask, so `found["head"]` is never a
    task blocked on record - only one that is not, or a message, is ever
    this. The `head_task_blocked` check below is for a task behind it turning
    out to be blocked too (`effective_task_head` looks past one level, not a
    chain): nothing is typed about it either, the same as the true head - not
    forgotten, just not reachable from here.

    Never resolves for it: the verdict is the agent's to write. Gated like a
    delivery (not at the wall, nothing typed at a modal or a working agent),
    and nothing here touches an attempt counter, so it cannot stall an agent.
    """
    found = forgotten_resolve(con, agent, row, now)
    if not found:
        return False
    head = found["head"]
    if head_task_blocked(con, agent, head):
        return False
    count = nudge_count(con, agent, int(head["id"]))
    if count >= len(NUDGE_SCHEDULE_S) or found["idle_s"] < NUDGE_SCHEDULE_S[count]:
        return False
    if now - db.get_cursor(con, f"nudge_try:{agent}") < NUDGE_RETRY_S:
        return False
    db.set_cursor(con, f"nudge_try:{agent}", now)
    behind = found["behind"]
    idle = timefmt.ago(found["idle_s"])
    after = f" with {behind} more waiting behind it" if behind else ""
    task = _task_of(con, head) if head["kind"] == "task" else None
    if task is not None:
        tid = task["id"]
        text = (f"T{tid} (#{head['id']}) is still yours and you have been idle {idle}{after}. "
                f"Done: hand it on (`ocp task handoff T{tid} ...`) or `ocp task done T{tid}`. "
                f"Waiting on something: record it - `ocp task edit T{tid} --blocked-by T<m>`, "
                f"or a child or decision task.")
    else:
        what = (f"It asks nothing - `ocp dismiss {head['id']}` when read."
                if head["kind"] == "report" else
                f"If the work is done, `ocp resolve {head['id']} \"<verdict>\"`; if it is "
                f"not, say what you are waiting on.")
        text = (f"#{head['id']} was delivered to you and is still open, and you have been "
                f"idle {idle}{after}. {what}")
    ok = type_notice(con, agent, text)
    if ok:
        db.set_cursor(con, f"nudge:{agent}", int(head["id"]))
        db.set_cursor(con, f"nudge_count:{agent}", count + 1)
        db.add_event(con, agent, "nudged", {"request_id": head["id"],
                                            "idle_s": found["idle_s"], "behind": behind})
        log(f"  {agent}: idle {timefmt.ago(found['idle_s'])} with #{head['id']} open and "
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
        held = ""
        task = _task_of(con, row) if row["kind"] == "task" else None
        if task is not None:
            from .. import actions
            held = (f", and T{task['id']} is {actions.task_state(con, task)} - "
                    f"`ocp task list` shows what you hold")
        ok, _ = _send(
            handle,
            f"#{row['id']} from {row['requester']} was cancelled: {verdict}. Stop work "
            f"on it; it is closed and needs no resolve{held}. `ocp read {row['id']}` shows it.")
        if ok:
            _mark_typed(con, agent)
    return ok, "" if ok else "the send failed"


# The body `actions.tasks.task_handoff` sends: "T<n> handed to you by X: <title>\n\n<note>".
_HANDOFF_BODY = re.compile(r"\AT(\d+) handed to you by (\S+?): (.*?)\n\n(.*)\Z", re.S)
TITLE_SHOWN = 60
NOTE_SHOWN = 400


def handoff_parts(body: str | None) -> tuple[int, str, str, str] | None:
    """(task id, who handed it, title, note) of a task handoff's body, or None for any other."""
    m = _HANDOFF_BODY.match(body or "")
    return (int(m[1]), m[2], m[3].strip(), m[4].strip()) if m else None


def _task_of(con, req):
    """The task a kind-task request is about: the one it is linked to, else the one its body names."""
    linked = db.tasks_for_request(con, int(req["id"]))
    if linked:
        return linked[-1]
    parts = handoff_parts(req["body"])
    return db.get_task(con, parts[0]) if parts else None


def answer_verb(con, head) -> str:
    """How the agent closes what it was just told: a report is dismissed, a say
    needs nothing, a task handed on or finished (resolving it would leave the
    task owned by whoever resolved it), and everything else resolved."""
    if head["kind"] == "report":
        return f"it asks nothing - `ocp dismiss {head['id']}` when read"
    if head["kind"] == "say":
        return "it needs no answer"
    if head["kind"] == "task":
        task = _task_of(con, head)
        if task is not None:
            from ..actions import tasks as taskmod
            return (f"you own T{task['id']} now: hand it on with "
                    f"{taskmod.handoff_command(con, task, head['target'])}, or "
                    f"`ocp task done T{task['id']}` if you are its last step")
    return f"answer with `ocp resolve {head['id']} \"<verdict>\"`"


def more_waiting(kind: str, more: int) -> str | None:
    """What an agent is told about the queue behind the request it reads, or None. A message
    needs no answer, so it gates nothing and says nothing; a task's queue moves on when the
    task is handed on or finished."""
    if not more or kind == "say":
        return None
    if kind == "task":
        return f"{more} more waiting - the next comes when you hand this on or finish it."
    return f"{more} more waiting - the next comes when you resolve or dismiss this one."


def tell_text(con, head, depth: int, ahead_of=None) -> str:
    """The message that tells an agent about `head`, as it is typed.

    `ahead_of` is the head a "send now" request is jumping."""
    more = depth - 1
    # One line; `ocp read` has the rest. The message is three lines at most
    # (`MAX_TYPED_LINES`), so what follows the body rides on its line. A task handoff
    # shows its note first and cuts only the title: every handoff of a long-titled
    # task starts with the same title, so a cap over both hid the note (#2377).
    parts = handoff_parts(head["body"]) if head["kind"] == "task" else None
    if parts:
        tid, _, title, note = parts
        note = textsafe.oneline(note) or "(no note)"
        title = textsafe.oneline(title)
        shown = (f"T{tid} handed to you. Note: {note if len(note) <= NOTE_SHOWN else note[:NOTE_SHOWN] + '…'} "
                 f"Title: {title if len(title) <= TITLE_SHOWN else title[:TITLE_SHOWN].rstrip() + '…'}")
    else:
        body = textsafe.oneline(head["body"] or "").strip() or "(empty)"
        shown = body if len(body) <= 400 else body[:400] + "…"
    waiting = more_waiting(head["kind"], more)
    tail = f" [{waiting}]" if waiting else ""
    if ahead_of is not None:
        tail = (f" [A follow-up to #{ahead_of['id']}, which is still open.]"
                if head["parent_id"] == ahead_of["id"] else
                f" [Sent now, ahead of #{ahead_of['id']}, which is still open.]")
    reply = in_reply_to(con, head)
    return (f"#{head['id']} from {head['requester']}{f' ({reply})' if reply else ''}, waiting for you:\n{shown}{tail}\n"
            f"`ocp read {head['id']}` shows it whole; {answer_verb(con, head)}"
            + ("." if head["kind"] == "say" else ", or `ocp inbox` any time."))


def _record_told(con, agent: str, head, depth: int, log, session: str | None,
                 ahead_of=None) -> None:
    """What a delivered tell leaves behind, once it is known to have landed."""
    more = depth - 1
    if ahead_of is None:
        db.set_cursor(con, f"notify:{agent}", int(head["id"]))
        db.set_notified_session(con, agent, session)
    else:
        db.mark_told_out_of_turn(con, int(head["id"]))
    db.add_event(con, agent, "notified", {"request_id": head["id"], "more_waiting": more})
    log(f"  {agent}: told about #{head['id']}"
        + (" (send now)" if ahead_of is not None else "")
        + (f" ({more} more waiting)" if more else ""))


def _tell(con, agent: str, head, depth: int, handle: str, log, ahead_of=None) -> bool:
    """`ahead_of` is the head a "send now" request is jumping: it is told
    without touching the head's own cursor, so that head is not told twice."""
    # Before the send, not after: this is what closes withdraw/replace's race
    # against delivery (request #189). A send that then fails leaves the row
    # marked anyway - its requester can no longer withdraw it, but the cursor
    # below is not advanced, so the next tick retries the same head.
    if not db.mark_delivered(con, int(head["id"])):
        # Retired (withdrawn/replaced) in the gap between head_of_queue and
        # here. Typing a dead request into the target's terminal would be
        # worse than skipping it; the next tick picks up whatever is now head.
        return False
    ok, _ = _send(handle, tell_text(con, head, depth, ahead_of))
    if ok:
        _mark_typed(con, agent)
        _record_told(con, agent, head, depth, log,
                     _live_session(con, agent) if ahead_of is None else None, ahead_of)
    return ok


def waiting_prompt(con, agent: str, log):
    """The message waiting for a parked agent, as the first prompt of the
    session that resumes it, and what to record once that session is up -
    or None when nothing waits.

    It is the tell `deliver_queued` and `deliver_answers` would type on the
    next tick, so the resume does not say "carry on" and then say the message.
    Marked delivered here, before the launch, for the reason `_tell` is."""
    if not enforce_edges(con, agent, log):
        return None
    head = effective_head_or_marked(con, agent)
    if head:
        if not db.mark_delivered(con, int(head["id"])):
            return None
        depth = db.queue_depth(con, agent)

        def told(session):
            from . import messaging      # imports this module
            _record_told(con, agent, head, depth, log, session)
            if head["kind"] == "say":
                messaging.finish_say(head)
        return tell_text(con, head, depth), told
    answered = db.unannounced_answers(con, agent)
    if answered:
        return answers_text(answered), lambda session: _record_answers(con, agent, answered, log)
    # T351: a pending message (messaging.py) is the same kind of "owed" as a
    # head or an answer - `lifecycle.waiting_for` already counts it, and a
    # resume it woke for has to have this in step or "nothing to resume it
    # for" fires on every one of them. Typed as-is, not wrapped like a
    # request's tell: `wake_prompt`'s `resumed` branch returns it unchanged.
    pending = db.list_pending(con, agent)
    if pending:
        row = pending[0]
        return row["text"], lambda session: db.delete_pending(con, int(row["id"]))
    return None


# How much of the original question an answer line quotes.
ASKED_SHOWN = 40


def in_reply_to(con, row) -> str:
    """`in reply to #M: <first line of M>` for a request with a parent, so a
    bare "#N from human" says what it answers; "" for one without, or whose
    parent is gone."""
    if row["parent_id"] is None:
        return ""
    parent = db.get_request(con, int(row["parent_id"]))
    if parent is None:
        return ""
    lines = (parent["body"] or "").splitlines()
    first = textsafe.oneline(lines[0]).strip() if lines else ""
    if len(first) > ASKED_SHOWN:
        first = first[:ASKED_SHOWN].rstrip() + "…"
    return f"in reply to #{parent['id']}{': ' + first if first else ''}"


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
    parts = handoff_parts(r["body"]) if r["kind"] == "task" else None
    if parts:
        head = f"#{r['id']} (your handoff of T{parts[0]} to {r['target']})"
    else:
        head = f"#{r['id']} (you asked: {asked})" if asked else f"#{r['id']}"
    return f"{head} {r['target']}: {said[:160]}", len(said) > 160 or "\n" in text


def answers_text(answered) -> str:
    """What is typed at an agent about its answered requests."""
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
    return "Answers to what you asked:\n" + " | ".join(lines) + tail


def _record_answers(con, agent: str, answered, log) -> None:
    db.mark_announced(con, [r["id"] for r in answered])
    db.add_event(con, agent, "answered", {"count": len(answered)})
    log(f"  {agent}: told about {len(answered)} answer(s)")


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
    text = answers_text(answered)
    with lock.terminal(agent) as mine:
        if not mine:
            return False
        if _type_gate(con, agent, handle):
            return False
        ok, _ = _send(handle, text)
        if ok:
            _mark_typed(con, agent)
    if ok:
        _record_answers(con, agent, answered, log)
    return ok
