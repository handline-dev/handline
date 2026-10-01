"""T440: a queued task nobody has been sent is delivered as soon as its owner is free.

Delivering the next task is done at the edges (`advance_current_task` after a done, a close, an
unblock, a queue); an edge that finds the owner busy, or that never fires, leaves the task waiting
for good. This reads the same picker as a level every tick, so a waiting task reaches its owner
whatever happened to the edge.
"""
from __future__ import annotations

from .. import db

# A delivery that landed nothing (every candidate refused) is tried again after this long. One that
# landed is not throttled: the owner's next task is told on the very next tick (T721).
RETRY_S = 60


def sweep(con, log, now: int) -> None:
    from .. import actions
    from ..actions import tasks as taskmod
    from ..team import ask as askmod

    taskmod.release_ended_blockers(con, log)
    owners = sorted({r["owner"] for r in db.open_tasks(con)
                     if r["queued_by"] and not r["done_evidence"] and r["owner"] != db.HUMAN})
    for agent in owners:
        if taskmod._current_task_still_asking(con, agent):
            continue
        if next(taskmod._waiting_tasks(con, agent), None) is None:
            continue
        key = f"taskqueue:{agent}"
        if now - (db.get_cursor(con, key) or 0) < RETRY_S:
            continue
        if actions.advance_current_task(con, agent):
            askmod.push(con, agent)
            log(f"  {agent}: a queued task was waiting and it is free - delivered")
        else:
            db.set_cursor(con, key, now)
