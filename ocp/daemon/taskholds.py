"""T721: a task waiting for its owner always says why, and a wait with no cause is told to the founder.

The daemon retries every path that can land a task each tick (`taskqueue.sweep` moves the next task
to a free owner, `delivery.deliver_queued` types it). What those paths said when they declined was
logged or held in memory, so `ocp task list` showed a queued task with nothing beside it. This reads
the same facts as a level each tick and keeps one line per waiting task in `meta`, for the readers in
other processes (`ocp task list`, `ocp team`, the bridge).

A reason is either a cause (behind another task, blocked, the owner mid-turn, closed, at its usage
limit, a dialog, no terminal) or "the owner can take it" - nothing is in the way and the daemon is
about to type it. A task that stays in that second state past ABLE_WARN_S has a cause nobody named,
and the founder is told once.
"""
from __future__ import annotations

import json
import time

from .. import db
from ..infra import paths, timefmt
from ..session import blocked, waiting
from . import delivery, locks
from .states import IDLE, QUEUED

PREFIX = "taskhold:"
# Owner able, task still waiting: this long is a missed delivery, not a tick's delay.
ABLE_WARN_S = 300
READY = "nothing is in the way - the daemon sends it on its next tick"


def _key(tid: int) -> str:
    return f"{PREFIX}{tid}"


def _read(con, tid: int) -> dict | None:
    raw = db.get_meta(con, _key(tid))
    try:
        rec = json.loads(raw) if raw else None
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def agent_reason(con, agent: str, now: int) -> tuple[str, bool]:
    """Why work is not reaching `agent`, and whether `agent` looks able to take it: the daemon's
    own gates in the order it meets them. ("", True) when nothing is in the way."""
    from .. import actions

    row = db.get_agent_state(con, agent)
    blk = db.block_get(con, agent)
    if blk:
        return blocked.reason(blk["kind"], blk["detail"]), False
    sessions, handles = actions._world()
    why, _ = actions._delivery_block(con, agent, sessions, handles, True)
    if why:
        return why, False
    if row and waiting.of(con, row):
        return f"{agent} is waiting on a permission prompt", False
    if delivery.clear_owed(con, agent):
        return f"{agent} owes a context clear first (background work is still running)", False
    held = delivery.held_reason(agent, now)
    if held:
        # The state says idle and the screen says otherwise: the T458 misread is exactly this.
        return held[0], held[1] and bool(row) and row["state"] in (IDLE, QUEUED)
    return "", True


def task_reason(con, row, now: int) -> tuple[str, bool] | None:
    """Why task `row`, queued or doing, has not been given to its owner, and whether the owner looks
    able to take it. None when it has been (typed, or nothing is waiting to type)."""
    from ..actions import tasks as taskmod

    tid, agent = row["id"], row["owner"]
    linked = db.task_requests(con, tid)
    req = next((r for r in linked if taskmod._asks(r) and r["target"] == agent), None)
    if agent == db.HUMAN or (not row["queued_by"] and req is None):
        return None
    blockers = db.task_blockers(con, tid)
    if blockers:
        return "blocked by " + ", ".join(f"T{b['id']} ({b['owner']})" for b in blockers), False
    if agent not in paths.list_agents():
        return f"{agent} is not registered - nothing will take it", False
    if not taskmod.holds_current(con, row) or (req is None and taskmod._current_task_still_asking(con, agent)):
        why = taskmod.child_wait(con, agent) or taskmod.block_wait(con, agent) or "still open"
        return f"behind T{db.current_task(con, agent)}, which {agent} holds ({why})", False
    if req is None:
        last = con.execute(f"SELECT kind, payload FROM events WHERE {db.EVENT_TASK} = ? "
                           "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        if last and last["kind"] == "task_deliver_refused":
            return f"its delivery was refused: {json.loads(last['payload']).get('why')}", False
        return READY, True
    if req["delivered_at"]:
        return None
    head = delivery.effective_head(con, agent)
    if head and int(head["id"]) != int(req["id"]):
        return f"behind #{head['id']}, which {agent} has not resolved", False
    why, able = agent_reason(con, agent, now)
    return (why, able) if why else (READY, True)


def sweep(con, log, now: int) -> None:
    """Record why each waiting task waits, drop what no longer waits, and tell the founder once of
    a task whose owner could take it and does not."""
    from ..team import ask as askmod

    seen: set[int] = set()
    for row in db.open_tasks(con):
        if row["owner"] == db.HUMAN or row["done_evidence"]:
            continue
        found = task_reason(con, row, now)
        if found is None:
            continue
        why, able = found
        tid = int(row["id"])
        seen.add(tid)
        rec = _read(con, tid) or {}
        changed = rec.get("why") != why or rec.get("able") != able
        if changed:
            was_able = rec.get("able")
            rec.update(why=why, able=able, since=now)
            if able and not was_able:
                rec["able_since"] = now
            if not able:
                rec.pop("able_since", None)
                rec.pop("warned", None)
        if able and not rec.get("warned") and now - rec.get("able_since", now) >= ABLE_WARN_S:
            askmod.report(con, row["owner"],
                          f"T{tid} has waited {(now - rec['able_since']) // 60} min for {row['owner']}, which "
                          f"looks able to take it ({why}). Delivery should have landed it - this is a "
                          f"missed delivery, not a queue.", "queued task not landing")
            rec["warned"] = now
            changed = True
            log(f"  T{tid}: waited {ABLE_WARN_S // 60} min for an owner that looks able - founder told")
        if changed:
            db.set_meta(con, _key(tid), json.dumps(rec))
    for name in db.meta_names(con, PREFIX):
        if int(name[len(PREFIX):] or 0) not in seen:
            db.clear_meta(con, name)


def wait_line(con, row, state: str) -> str | None:
    """What to show beside a waiting task: the daemon's reason and how long it has stood - or, when
    the daemon is not running, that nothing is retried. None for a task that is not waiting."""
    if state not in ("queued", "doing"):
        return None
    rec = _read(con, int(row["id"]))
    if rec is None and state != "queued":
        return None
    running = bool(locks.running_elsewhere())
    if not running:
        return ("waiting: the daemon is not running, so nothing lands it"
                + (f" (was: {rec['why']})" if rec else ""))
    if rec is None:
        return "waiting: the daemon has not looked at it yet - it does on its next tick"
    return f"waiting: {rec['why']} (for {timefmt.ago(max(int(time.time()) - int(rec['since']), 0))})"


def agent_waits(con, agent: str) -> list[tuple[int, str]]:
    """(task id, the daemon's reason) for each of `agent`'s tasks that is waiting, oldest first. With
    no daemon running the reason is that: what it recorded is no longer being kept true."""
    running = bool(locks.running_elsewhere())
    out = []
    for row in db.open_tasks(con):
        rec = _read(con, int(row["id"])) if row["owner"] == agent else None
        if rec:
            out.append((int(row["id"]), rec["why"] if running else "the daemon is not running"))
    return sorted(out)
