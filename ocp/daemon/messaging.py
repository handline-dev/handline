"""T351: one line typed into an agent's terminal at once - the socket's
`message` verb, and the pending row that survives a restart when it cannot be.

The old idle-gated delivery (`delivery.deliver_queued`, `type_notice`) stays
as it is: it waits for `orca.typeable_now` (idle *and* nothing on screen)
because it is telling an agent about a request that starts a fresh turn.
This is the new, simpler rule the founder settled on (#2104): Claude Code
queues a line typed mid-turn and reads it once the turn ends, so nothing
here waits for idle - only for a screen_blocker (`orca.screen_blocker`: a
dialog, a permission prompt, or a screen that could not be read) or a closed
session, which is woken first. Never Esc: a screen_blocker is left standing,
not pressed past.
"""
from __future__ import annotations

import time

from .. import db
from ..infra import lock, paths
from ..session import orca

from . import delivery, lifecycle


def can_type_now(con, agent: str, handle: str) -> tuple[bool, str]:
    """Nothing on screen a typed line would answer instead, and not at the
    wall - the two things immediate typing waits for. Not `orca.typeable_now`:
    that also requires idle, which this deliberately does not.

    `wall_reason` is the same check every other typing path makes (its own
    docstring: "no caller ... can skip it") - typed anyway, a turn at the
    usage wall fails at once and clears the very mark that says so."""
    if not orca.available():
        return False, "orca is not on PATH"
    reason = delivery.wall_reason(con, agent)
    if reason:
        return False, reason
    blocker = orca.screen_blocker(handle)
    if blocker is not None:
        return False, blocker.reason()
    return True, ""


def _deliver_row(con, agent: str, row_id: int, text: str) -> tuple[bool, str]:
    """Try to type `text` (pending row `row_id`) at `agent` right now; True
    only once it is actually sent, with the row deleted in the same lock hold
    that sent it - never as two separate steps, which is what let a message
    typed here and a concurrent `deliver_pending` elsewhere both find the row
    still present and type it twice."""
    handle = db.get_terminal(con, agent)
    if not handle:
        return False, f"{agent} has no terminal"
    if not orca.available() or not orca.alive(handle):
        return False, f"{agent}'s terminal is gone"
    if not delivery.session_present(agent):
        return False, f"{agent} has no running session"
    ok, why = can_type_now(con, agent, handle)
    if not ok:
        return False, why
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"{agent}: something else is using its terminal"
        # A concurrent caller for the same row (`send_message`'s own first
        # try and a tick's `deliver_pending` can both reach here for one
        # row) already sent and deleted it while this one waited for the
        # lock - nothing is sent twice for a row that is already gone.
        if db.get_pending(con, row_id) is None:
            return True, "already typed"
        # Re-read once the lock is actually held (#1973): a reading taken
        # before asking for it can already be stale.
        ok, why = can_type_now(con, agent, handle)
        if not ok:
            return False, why
        ok, err = delivery._send(handle, text)
        if not ok:
            return False, err or "the send failed"
        delivery._mark_typed(con, agent)
        db.delete_pending(con, row_id)
        return True, "typed"


def send_message(con, agent: str, text: str, log) -> tuple[bool, str]:
    """The socket's `message` verb. The pending row is written first (#2117:
    it is the one thing a reboot must not lose), then delivery is tried at
    once. `ok` is true once the daemon owns getting `text` to `agent` -
    typed already, or left in `pending_messages` for `deliver_pending` (the
    next tick) or a wake started here to pick up; only a connection the
    socket itself refuses means nothing was accepted at all."""
    row_id = db.add_pending(con, agent, text, int(time.time()))
    ok, why = _deliver_row(con, agent, row_id, text)
    if ok:
        return True, f"{agent}: typed"
    if not delivery.session_present(agent):
        woke, wake_why = lifecycle.wake_agent(con, agent, log)
        if woke:
            ok, why = _deliver_row(con, agent, row_id, text)
            if ok:
                return True, f"{agent}: woken and typed"
            return True, f"{agent}: queued - woken, will type once free ({why})"
        return True, f"{agent}: queued - not woken ({wake_why})"
    return True, f"{agent}: queued - {why}"


def tell_now(con, rid: int, log) -> str:
    """Tell an agent about request `rid` at once, the way the socket's `message`
    verb types a line: the notice goes out even mid-turn, and only a screen
    blocker, the wall or a closed session holds it (T351, founder #2104).

    Told out of turn, so the tick's head rule does not say it a second time.
    A session that is not up yet is woken and reads it as its first prompt
    (`delivery.waiting_prompt` marks it delivered first); this then finds it
    delivered and types nothing. Tasks do not come here - they keep the
    one-at-a-time head rule (`ask.push`). Returns what happened, for the log."""
    row = db.get_request(con, rid)
    if row is None or row["state"] != "queued":
        return "no longer queued"
    agent = row["target"]
    if agent == db.HUMAN or agent not in paths.list_agents():
        return "not an agent"
    if not delivery.enforce_edges(con, agent, log):
        return "held: team.yaml is not trusted"
    row = db.get_request(con, rid)
    if row["state"] != "queued":
        return "refused at delivery"
    state = db.get_agent_state(con, agent)
    if not (state and state["terminal"]):
        lifecycle._start_for_work(con, agent, log)
        state = db.get_agent_state(con, agent)
    if state and state["closed_at"]:
        lifecycle.wake_agent(con, agent, log)
    # Started or woken, its first prompt may already have told it (`waiting_prompt`).
    row = db.get_request(con, rid)
    if row["state"] != "queued":
        return "answered meanwhile"
    if row["delivered_at"]:
        return "already told"
    if not delivery.session_present(agent):
        return "queued - no running session; told when it is up"
    if not db.mark_delivered(con, rid):
        return "withdrawn first"
    text = delivery.tell_text(con, row, 1)
    pending = db.add_pending(con, agent, text, int(time.time()))
    db.mark_told_out_of_turn(con, rid)
    db.add_event(con, agent, "notified", {"request_id": rid, "more_waiting": 0})
    ok, why = _deliver_row(con, agent, pending, text)
    log(f"  {agent}: told about #{rid} at once" if ok else f"  {agent}: #{rid} waits - {why}")
    return "typed" if ok else f"queued - {why}"


def deliver_pending(con, agent: str, log) -> bool:
    """One tick's worth of what `send_message` could not type at once: the
    oldest pending row, if `agent`'s terminal will take it now. One per
    tick, the same rule `deliver_queued` holds to - a session that just
    cleared a dialog hears the oldest thing waiting first, not everything
    piled up behind it pasted as one block."""
    rows = db.list_pending(con, agent)
    if not rows:
        return False
    row = rows[0]
    ok, why = _deliver_row(con, agent, int(row["id"]), row["text"])
    if ok:
        log(f"  {agent}: typed a pending message (#{row['id']})")
    return ok


def reload_pending(con, log) -> None:
    """Called once at daemon start (#2117): try every pending row at once,
    for whatever agent is already running and free to hear it, rather than
    waiting for that agent's own next tick to come due."""
    for agent in sorted({r["agent"] for r in db.list_pending(con)}):
        deliver_pending(con, agent, log)
