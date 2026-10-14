"""The Stop hook gate (T288, founder #1748): refuses to let an agent end its
turn while its current task has no recorded wait
(`stop_wait_reason`).

It also reminds, once per message, an agent that ends its turn without answering a question
put to it (`_unanswered_question`).

On unless the founder turns it off (T298, T480): every wait logs an event
(`stop_gate`), and blocks unless `enforced()` says the founder switched it off.
The switch lives in the protected store, so only the founder's own terminal
or the screen can flip it - never an agent's session.
"""
from __future__ import annotations

import re

from ..infra import protected
from ..team import proposals
from .. import db
from . import events
from .states import MOVABLE_TRIGGERS, PARKED

_NS = "gates"
_NAME = "stop_wait"


def enforced() -> bool:
    """Whether a wait blocks the stop; only the founder's `off` makes it log."""
    return not protected.is_off(_NS, _NAME)


def set_enforced(on: bool) -> None:
    """Founder-only - `protected.put` itself refuses an agent session."""
    protected.put(_NS, _NAME, "on" if on else "off")


def wall_reason(con, agent: str, row=None) -> str | None:
    """Why typing at this agent's open session would only spend quota, or None.

    A turn that ended on the usage limit, or a park that a usage trigger put
    it in, means the account cannot run one: what is typed starts a turn that
    fails at once, marks the request delivered, clears the very mark that
    says the agent is at the wall - and the next ask starts it over, each
    round counting as a degraded handoff. Checked where the keystrokes are
    sent, so no caller - `supervise`, `hl say`, an answer - can skip it.
    """
    if events.at_limit(con, agent):
        return f"{agent} is at its usage limit"
    row = row if row is not None else db.get_agent_state(con, agent)
    if row and row["state"] == PARKED and not row["closed_at"]:
        last = db.last_handoff(con, agent)
        if last and last["trigger_kind"] in MOVABLE_TRIGGERS:
            return f"{agent} is parked until its usage window rolls over"
    return None


def stop_wait_reason(con, agent: str) -> str | None:
    """Why `agent` should not stop right now: it holds a
    current task with no recorded wait. None if it holds none, or has one."""
    tid = db.current_task(con, agent)
    if tid is None:
        return None
    if db.open_child_tasks(con, tid):
        return None
    if db.task_blockers(con, tid):
        return None
    if db.open_decision_tasks_of(con, agent):
        return None
    if any(p.get("requested_by") == agent and p.get("state") == "proposed" for p in proposals.listing()):
        return None     # its roster proposal is waiting on the founder
    return f"T{tid} is current with no recorded wait"


def _reason(tid: int, why: str) -> str:
    return (f"Stop refused: {why}, so ending your turn now would leave it with no owner working "
            f"and nothing it waits on. Finished your part: `hl task handoff T{tid} <next> ...` (or "
            f"`hl task done T{tid}` if you are the last step). Waiting on "
            f"something: `hl task edit T{tid} --blocked-by T<m>`, or a child or "
            f"decision task.")


def _unanswered_question(con, agent: str) -> tuple[int, str] | None:
    """(id, sender) of the latest message told to `agent` if it asks something and nothing has
    answered it, unless this gate has reminded of it already. The founder's message asks; an
    agent's does when it has a question mark. A reply (it has a parent) answers and asks nothing,
    whoever sent it: most of the founder's rows are the machine's own answers, "handed to X" or
    "cleared". Nor does anything from the task board. A task is answered by its handoff or done,
    which the wait check holds the agent to.

    Answered: the agent has sent that sender anything since."""
    row = con.execute(
        "SELECT id, requester, body, parent_id FROM requests WHERE target = ? AND kind = 'say' "
        "AND requester != ? AND delivered_at IS NOT NULL ORDER BY id DESC LIMIT 1",
        (agent, db.NOTICE_REQUESTER)).fetchone()
    if row is None:
        return None
    rid, sender = int(row["id"]), row["requester"]
    if row["parent_id"] is not None or sender != db.HUMAN and not re.search(r"[?？]", row["body"] or ""):
        return None
    if db.get_meta(con, f"answer_reminded:{agent}") == str(rid):
        return None
    answered = con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? AND id > ? LIMIT 1",
                           (agent, sender, rid)).fetchone()
    return None if answered else (rid, sender)


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
        if wall_reason(con, agent):
            return False, None
        row = db.get_agent_state(con, agent)
        if row and row["closed_at"]:
            return False, None
        why = stop_wait_reason(con, agent)
        if why is None:
            owed = _unanswered_question(con, agent)
            if owed is None:
                return False, None
            rid, sender = owed
            db.set_meta(con, f"answer_reminded:{agent}", str(rid))
            db.add_event(con, agent, "stop_gate", {"answer_reminder": rid, "enforced": True})
            return True, (f"#{rid} may be waiting for an answer: if you have one, reply with "
                          f"`hl say {sender} --parent {rid} \"...\"` (what you write in the terminal "
                          f"reaches no one).")
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
