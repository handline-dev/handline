"""Six notices about work that has run long, all read from the db every tick.

A request still open a set time after it was delivered tells its asker once
(`ocp ask --timebox`, config.yaml's `timebox_s`). A task's own timeout
(`ocp task new -t/--timeout`, T286) only counts while it is doing; past it,
its opener is told, and again every further timeout_s stretch, until the
task answers with one of `ocp task edit --timeout` (continue) or `ocp task
release` (back to queued) - or is simply done. An agent that shows RUNNING -
working, or with background work behind its prompt - while nothing is asked of
it tells its supervisor once, so the manager can ask what it is doing
(`unasked_running_s`). Background work with the agent silent for STALE_BG_S
tells its supervisor it may be stopped after a veto window
(`delivery.bg_may_stop`; the close itself is `lifecycle`'s). An agent IDLE
with its current task's own wait never recorded tells the founder once, the
Stop-hook gate's own backstop (T288) for whenever it is warn-only or missed.
An agent nudged to resolve a forgotten head as many times as
`delivery.NUDGE_SCHEDULE_S` allows, still open, tells its supervisor once
(T337) - nothing more is typed at the agent about it past that. The first
four act on nothing.

A notice to an agent is a line typed at its open session, so one that cannot be
typed now is tried again; a notice to the human has no session to type at and is
an event.
"""
from __future__ import annotations

from ..infra import paths
from ..infra import timefmt
from ..session import handoff as progressmod
from ..session import bgjobs, idle_close
from .. import db
from . import delivery
from .states import IDLE, RUNNING

TIMEBOX_S = 7200
UNASKED_RUNNING_S = 900
TITLE_SHOWN = 60
STOP_WAIT_BACKSTOP_S = 900
# A notice that could not be typed (mid-turn, a modal, no session) is not tried
# again for this long, per agent.
NOTICE_RETRY_S = 60
HUMAN = "human"


def timebox_s(row) -> int:
    return int(row["timebox_s"]) if row["timebox_s"] else \
        idle_close.config_seconds("timebox_s", TIMEBOX_S)


def _tell(con, who: str, text: str, now: int) -> bool:
    """Deliver a notice to `who`; False when it should be tried again."""
    if who == HUMAN:
        return True
    key = f"notice_try:{who}"
    if now - db.get_cursor(con, key) < NOTICE_RETRY_S:
        return False
    db.set_cursor(con, key, now)
    return delivery.type_notice(con, who, text)


def _state_of(con, agent: str) -> str:
    row = db.get_agent_state(con, agent)
    if not row:
        return "unknown"
    bg = " with background work" if delivery.has_background_work(con, agent) else ""
    return f"{row['state']}{bg}" + (f" ({row['detail']})" if row["detail"] else "")


def sweep_timeboxes(con, log, now: int) -> None:
    for row in db.timeboxed_open(con):
        rid, box = int(row["id"]), timebox_s(row)
        elapsed = now - int(row["landed_at"])
        if elapsed < box:
            continue
        note = " ".join((progressmod.progress_of(con, rid) or "").split())
        text = (f"#{rid} to {row['target']} is still open {timefmt.ago(elapsed)} after it was "
                f"delivered, past its {timefmt.ago(box)} timebox. {row['target']} is "
                f"{_state_of(con, row['target'])}. Last progress note: "
                f"{note[:300] or 'none'}. `ocp read {rid}` shows it."
                + ("" if row["kind"] == "say" else
                   f" It is still {row['target']}'s - nothing to do unless you want it back: "
                   f"a message to {row['target']} (`ocp say {row['target']} \"...\"`) is how."))
        if _tell(con, row["requester"], text, now):
            db.mark_timebox_noticed(con, rid)
            db.add_event(con, row["requester"], "timebox_exceeded",
                         {"request_id": rid, "target": row["target"], "elapsed_s": elapsed})
            log(f"  #{rid}: open {timefmt.ago(elapsed)} past its {timefmt.ago(box)} timebox - "
                f"{row['requester']} told")


def sweep_task_timeouts(con, log, now: int) -> None:
    """A task's timeout clock, read each tick: it runs only while the task is doing;
    past it the opener is told, and again every stretch until it is done, extended
    or released. A task with no timeout_s takes the default where it becomes doing."""
    from .. import actions
    for row in db.tasks_to_clock(con):
        tid = int(row["id"])
        if actions.task_state(con, row) != "doing":
            if row["timeout_at"] is not None:
                db.pause_task_timeout(con, tid)
            continue
        if row["timeout_at"] is None:
            db.advance_task_timeout(con, tid, now, actions.DEFAULT_TASK_TIMEOUT_S)
            continue
        if row["timeout_at"] > now:
            continue
        box = int(row["timeout_s"])
        title = " ".join((row["title"] or "").split())
        title = title if len(title) <= TITLE_SHOWN else title[:TITLE_SHOWN].rstrip() + "…"
        # A task begun on a founder's message has no queue to go back to: release refuses it.
        stop = "" if actions._begun(con, tid) else \
            f" Stop it: `ocp task release T{tid}` sends it back to queued."
        text = (f"T{tid} ({title}), held by {row['owner']}, has been doing past its "
                f"{timefmt.ago(box)} timeout. Keep it going: `ocp task edit T{tid} --timeout <duration>`."
                f"{stop}")
        if _tell(con, row["opened_by"], text, now):
            db.advance_task_timeout(con, tid, now, actions.DEFAULT_TASK_TIMEOUT_S)
            db.add_event(con, row["opened_by"], "task_timeout_exceeded",
                         {"task_id": tid, "timeout_s": box, "text": text})
            log(f"  T{tid}: doing, past its {timefmt.ago(box)} timeout - {row['opened_by']} told")


def _unasked_running(con, agent: str, row) -> bool:
    if not row or row["closed_at"] or not row["terminal"] or db.queued_for(con, agent):
        return False
    # Background work with nothing owed is not asked about: `sweep_forgotten_background` settles it.
    if row["state"] != RUNNING or delivery.has_background_work(con, agent):
        return False
    from .. import actions
    return actions.child_wait(con, agent) is None      # T443: waiting on a child of its own is a reason


def sweep_unasked_running(con, log, now: int) -> None:
    # `actions` imports the daemon, so it is not imported at module level.
    from .. import actions
    limit = idle_close.config_seconds("unasked_running_s", UNASKED_RUNNING_S)
    team = actions.routing()
    for agent in paths.list_agents():
        since_key, told_key = f"unasked_since:{agent}", f"unasked_told:{agent}"
        if not _unasked_running(con, agent, db.get_agent_state(con, agent)):
            db.clear_cursor(con, since_key)
            db.clear_cursor(con, told_key)
            continue
        since = db.get_cursor(con, since_key)
        if not since:
            db.set_cursor(con, since_key, now)
            continue
        if now - since < limit or db.get_cursor(con, told_key):
            continue
        boss = actions.supervisor_of(team, agent) or HUMAN
        text = (f"{agent} has shown RUNNING ({_state_of(con, agent)}) for "
                f"{timefmt.ago(now - since)} with no request open. Ask it what it is doing: "
                f"`ocp say {agent} \"...\"`.")
        if _tell(con, boss, text, now):
            db.set_cursor(con, told_key, 1)
            db.add_event(con, agent, "unasked_running", {"told": boss, "for_s": now - since})
            log(f"  {agent}: RUNNING {timefmt.ago(now - since)} with no request open - {boss} told")


def sweep_stale_background(con, log, now: int) -> None:
    """Tell the supervisor once that an agent's background work is stale, and
    that its session may be closed - which stops the work - after the veto
    window. The mark is cleared when the work is no longer stale, so a later
    stretch is told again."""
    from .. import actions
    team = actions.routing()
    for agent in paths.list_agents():
        key = f"stale_bg_told:{agent}"
        if not delivery.bg_stale(con, agent, now) or delivery.bg_owes_nothing(con, agent) \
                or idle_close.close_timeout_s(agent) == idle_close.NEVER:
            db.clear_cursor(con, key)
            continue
        if db.get_cursor(con, key):
            continue
        boss = actions.supervisor_of(team, agent) or HUMAN
        silent = delivery.bg_silent_s(con, agent, now)
        text = (f"{agent} has had background work for {timefmt.ago(now - (delivery.bg_since(con, agent) or now))} "
                f"and shown nothing for {timefmt.ago(silent)}. In {timefmt.ago(delivery.STALE_BG_VETO_S)} "
                f"its session may be closed, which stops that work, unless it shows activity or "
                f"`ocp idle-timeout never --agent {agent}` is set.")
        if _tell(con, boss, text, now):
            db.set_cursor(con, key, now)
            db.add_event(con, agent, "stale_background", {"told": boss, "silent_s": silent})
            log(f"  {agent}: background work stale ({timefmt.ago(silent)} silent) - {boss} told")


def sweep_forgotten_background(con, log, now: int) -> None:
    """Background work nobody waits on settles itself, with nobody asked. When the agent's
    idle window is over (`idle_close.bg_notice_s`, its idle close unless set) it is told
    once to finish or stop the work; `idle_close.bg_grace_s` later, with nothing shown, the
    idle close ends the session and its work (`bg_may_stop`; `lifecycle.close_agent` records
    why). An agent set to never close is left alone. Both marks clear with the stretch: when
    the work ends, or a request, an ask or a task makes it something owed."""
    for agent in paths.list_agents():
        key, noted = f"bg_prompt:{agent}", f"bg_stop_noted:{agent}"
        if not delivery.has_background_work(con, agent) or not delivery.bg_owes_nothing(con, agent) \
                or idle_close.close_timeout_s(agent) == idle_close.NEVER:
            db.clear_cursor(con, key)
            db.clear_cursor(con, noted)
            continue
        silent = delivery.bg_silent_s(con, agent, now)
        if not delivery.bg_prompted_at(con, agent):
            if silent is None or silent < idle_close.bg_notice_s(agent):
                continue
            text = (f"You have shown nothing for {timefmt.ago(silent)}, with no request open, and background "
                    f"work is still running. Finish it or stop it now, and say in one line what it was. "
                    f"If nothing more is shown in {timefmt.ago(idle_close.bg_grace_s(agent))}, it is stopped "
                    f"and this session closed.")
            if _tell(con, agent, text, now):
                db.set_cursor(con, key, now)
                db.add_event(con, agent, "background_prompted", {"silent_s": silent})
                log(f"  {agent}: background work with nothing owed, silent {timefmt.ago(silent)} - told to finish or stop it")


def sweep_background_check(con, log, now: int) -> None:
    """T450: every `idle_close.bg_check_s` while an agent has background work, at a turn's
    end, it is told what is running and asked to stop what it no longer needs - a shell
    nobody stops holds off the context rollover. Not the idle close: busy or idle, owing
    or not, a "never close" agent too. It waits out a T424 notice already in flight."""
    for agent in paths.list_agents():
        key = f"bg_check:{agent}"
        row = db.get_agent_state(con, agent)
        if not row or not delivery.has_background_work(con, agent):
            db.clear_cursor(con, key)
            continue
        if row["state"] != IDLE or delivery.bg_prompted_at(con, agent):
            continue
        every = idle_close.bg_check_s(agent)
        since = delivery.bg_since(con, agent) or now
        if now - max(db.get_cursor(con, key), since) < every:
            continue
        count = db.get_cursor(con, f"bgcount:{agent}") or None
        jobs = bgjobs.running(con, agent, count)
        unnamed = max(0, (count or len(jobs)) - len(jobs))
        listing = "; ".join(f"{i}. {bgjobs.line(j, now, timefmt.ago)}" for i, j in enumerate(jobs, 1))
        if unnamed:
            listing += ("; " if listing else "") + f"{unnamed} that ocp cannot name"
        text = (f"Background work has been running for {timefmt.ago(now - since)}: {listing or 'something ocp cannot name'}. "
                f"Stop what you no longer need, and for each one you keep say in a line why. "
                f"You are asked again in {timefmt.ago(every)} while any runs.")
        # The event goes in before the typing: its id is what separates the turn that ended
        # before the check from the one that answers it (`delivery.bg_last_shown`).
        checked = db.add_event(con, agent, "background_checked", {"jobs": len(jobs), "unnamed": unnamed})
        told = False
        try:
            told = _tell(con, agent, text, now)
        finally:
            if not told:                    # nothing typed, or the typing raised: no check was asked
                con.execute("DELETE FROM events WHERE id = ?", (checked,))
        if told:
            db.set_cursor(con, key, now)
            log(f"  {agent}: background work for {timefmt.ago(now - since)} - asked which to stop")


def sweep_stop_wait_backstop(con, log, now: int) -> None:
    """T288's own backstop: the clock (`since_key`) starts the moment the
    task-level wait goes missing and only clears once it is recorded or the
    task moves on - even to another with no recorded wait in between (T323); a
    RUNNING turn in between does not buy a fresh 15 minutes. It only tells while
    IDLE now, once, past the limit."""
    from .. import actions
    limit = idle_close.config_seconds("stop_wait_backstop_s", STOP_WAIT_BACKSTOP_S)
    for agent in paths.list_agents():
        since_key, told_key = f"stop_wait_since:{agent}", f"stop_wait_told:{agent}"
        task_key = f"stop_wait_task:{agent}"
        row = db.get_agent_state(con, agent)
        why = actions.stop_wait_reason(con, agent) if row and not row["closed_at"] else None
        if why is None:
            db.clear_cursor(con, since_key)
            db.clear_cursor(con, told_key)
            db.clear_cursor(con, task_key)
            continue
        tid = db.current_task(con, agent)
        if db.get_cursor(con, task_key) != tid:      # the clock is the task's, not the agent's
            db.set_cursor(con, task_key, tid)
            db.clear_cursor(con, since_key)
            db.clear_cursor(con, told_key)
        since = db.get_cursor(con, since_key)
        if not since:
            db.set_cursor(con, since_key, now)
            continue
        if row["state"] != IDLE or now - since < limit or db.get_cursor(con, told_key):
            continue
        db.set_cursor(con, told_key, 1)
        db.add_event(con, agent, "stop_wait_stuck", {"why": why, "for_s": now - since})
        log(f"  {agent}: {why}, idle {timefmt.ago(now - since)} with nothing recorded - "
            f"founder told")


def sweep_forgotten_resolve_capped(con, log, now: int) -> None:
    """T337 (founder #2002/#2003): `delivery.nudge_forgotten_resolve` stops
    once `NUDGE_SCHEDULE_S` is used up rather than typing at the agent
    forever - an agent that turns out to be genuinely stuck, not forgetful,
    still needs someone to look, so its supervisor is told once instead.
    Cleared as soon as it no longer applies (resolved, now blocked on record
    and so `deliver_queued`'s problem instead, or a different head), so a
    later head that is also forgotten and also runs out the schedule is told
    again."""
    from .. import actions
    team = actions.routing()
    for agent in paths.list_agents():
        told_key = f"forgotten_capped_told:{agent}"
        found = delivery.forgotten_resolve(con, agent, db.get_agent_state(con, agent), now)
        head_id = int(found["head"]["id"]) if found else None
        if not found or delivery.head_task_blocked(con, agent, found["head"]) \
                or not delivery.nudge_exhausted(con, agent, head_id):
            db.clear_cursor(con, told_key)
            continue
        if db.get_cursor(con, told_key) == head_id:
            continue
        boss = actions.supervisor_of(team, agent) or HUMAN
        behind = found["behind"]
        task = delivery._task_of(con, found["head"]) if found["head"]["kind"] == "task" else None
        on = f"T{task['id']} (#{head_id})" if task is not None else f"#{head_id}"
        after = f"after {len(delivery.NUDGE_SCHEDULE_S)} reminders"
        more = f" with {behind} more waiting behind it" if behind else ""
        if task is not None:
            # A task begun on a founder's message has no queue to go back to: release refuses it.
            back = "" if actions._begun(con, task["id"]) else \
                f"; take it back with `ocp task release T{task['id']}`"
            look = f"Look at it: `ocp read {head_id}`{back}, or ask {agent} by message (`ocp say {agent} \"...\"`)."
        else:
            look = f"Look at it: `ocp read {head_id}`, or message {agent} (`ocp say {agent} \"...\"`)."
        text = f"{agent} has been idle {timefmt.ago(found['idle_s'])} on {on} {after}{more}. {look}"
        if _tell(con, boss, text, now):
            db.set_cursor(con, told_key, head_id)
            db.add_event(con, agent, "forgotten_resolve_capped",
                         {"request_id": head_id, "idle_s": found["idle_s"], "told": boss})
            log(f"  {agent}: idle {timefmt.ago(found['idle_s'])} on #{head_id}, nudge cap "
                f"reached - {boss} told")


def sweep(con, log, now: int) -> None:
    sweep_timeboxes(con, log, now)
    sweep_task_timeouts(con, log, now)
    sweep_unasked_running(con, log, now)
    sweep_stale_background(con, log, now)
    sweep_forgotten_background(con, log, now)
    sweep_background_check(con, log, now)
    sweep_stop_wait_backstop(con, log, now)
    sweep_forgotten_resolve_capped(con, log, now)
