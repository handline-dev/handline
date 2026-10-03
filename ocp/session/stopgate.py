"""The Stop hook gate (T288, founder #1748): refuses to let an agent end its
turn while its current task has no recorded wait
(`actions.tasks.stop_wait_reason`, T319/T292).

On unless the founder turns it off (T298, T480): every wait logs an event
(`stop_gate`), and blocks unless `enforced()` says the founder switched it off.
The switch lives in the protected store, so only the founder's own terminal
or the bridge can flip it - never an agent's session.
"""
from __future__ import annotations

from ..actions import tasks as taskmod
from ..infra import protected
from .. import db

_NS = "gates"
_NAME = "stop_wait"


def enforced() -> bool:
    """Whether a wait blocks the stop; only the founder's `off` makes it log."""
    return not protected.is_off(_NS, _NAME)


def set_enforced(on: bool) -> None:
    """Founder-only - `protected.put` itself refuses an agent session."""
    protected.put(_NS, _NAME, "on" if on else "off")


def _reason(tid: int, why: str) -> str:
    return (f"Stop refused: {why}, so ending your turn now would leave it with no owner working "
            f"and nothing it waits on. Finished your part: `ocp task handoff T{tid} <next> ...` (or "
            f"`ocp task done T{tid}` if you are the last step). Waiting on "
            f"something: `ocp task edit T{tid} --blocked-by T<m>`, or a child or "
            f"decision task.")


def _unanswered_founder_message(con, agent: str) -> int | None:
    """The id of the founder's latest delivered message to `agent` that nothing answered and
    that this gate has not already reminded of, else None. A task is excluded: it is answered
    by its handoff or done, which the wait check below already holds the agent to."""
    row = con.execute(
        "SELECT id FROM requests WHERE requester = ? AND target = ? AND kind != 'task' "
        "AND delivered_at IS NOT NULL ORDER BY id DESC LIMIT 1", (db.HUMAN, agent)).fetchone()
    if row is None:
        return None
    rid = int(row["id"])
    if db.get_meta(con, f"founder_reminded:{agent}") == str(rid):
        return None
    answered = con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? "
                           "AND parent_id = ? LIMIT 1", (agent, db.HUMAN, rid)).fetchone()
    return None if answered else rid


def decide(agent: str, raw: dict) -> tuple[bool, str | None]:
    """(block, reason) for this Stop event - manages its own connection, the
    same as `waiting.clear`.

    False for everything the founder's spec exempts: a repeat within the same
    stop_hook_active chain, the founder's own chat, the usage wall, a session
    the daemon has already closed, and a task already reported once this
    session (`session_id` + the current task, both in the marker). Otherwise
    a reason - a real block unless `enforced()` is off.
    """
    if not agent or agent == db.HUMAN or raw.get("stop_hook_active"):
        return False, None
    con = db.connect()
    try:
        from ..daemon import delivery
        if delivery.wall_reason(con, agent):
            return False, None
        row = db.get_agent_state(con, agent)
        if row and row["closed_at"]:
            return False, None
        owed = _unanswered_founder_message(con, agent)
        if owed is not None:
            db.set_meta(con, f"founder_reminded:{agent}", str(owed))
            return True, (f"The founder's #{owed} has no answer yet: `ocp say human --parent "
                          f"{owed} \"...\" --task T<n>` (or `--no-task \"<why>\"`) - if it is work, "
                          f"one line on what you will do or who has it.")
        why = taskmod.stop_wait_reason(con, agent)
        if why is None:
            return False, None
        tid = db.current_task(con, agent)
        marker = f"{raw.get('session_id') or ''}:{tid}"
        key = f"stop_gate:{agent}"
        if db.get_meta(con, key) == marker:
            return False, None
        db.set_meta(con, key, marker)
        on = enforced()
        db.add_event(con, agent, "stop_gate", {"task_id": tid, "why": why, "enforced": on})
        return on, _reason(tid, why)
    finally:
        con.close()
