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
from ..infra import lock
from ..session import orca

from . import delivery, lifecycle


def can_type_now(handle: str) -> tuple[bool, str]:
    """Nothing on screen a typed line would answer instead - the one thing
    immediate typing waits for. Not `orca.typeable_now`: that also requires
    idle, which this deliberately does not."""
    if not orca.available():
        return False, "orca is not on PATH"
    blocker = orca.screen_blocker(handle)
    if blocker is not None:
        return False, blocker.reason()
    return True, ""


def type_now(con, agent: str, text: str) -> tuple[bool, str]:
    """Try to type `text` at `agent` right now; True only once it is actually
    sent. Any other outcome - no session, a screen_blocker, someone else
    holding the terminal - is left for `send_message`/`deliver_pending` to
    leave queued; nothing here writes or clears the pending row."""
    handle = db.get_terminal(con, agent)
    if not handle:
        return False, f"{agent} has no terminal"
    if not orca.available() or not orca.alive(handle):
        return False, f"{agent}'s terminal is gone"
    if not delivery.session_present(agent):
        return False, f"{agent} has no running session"
    ok, why = can_type_now(handle)
    if not ok:
        return False, why
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"{agent}: something else is using its terminal"
        # Re-read once the lock is actually held (#1973): a reading taken
        # before asking for it can already be stale.
        ok, why = can_type_now(handle)
        if not ok:
            return False, why
        ok, err = orca.send(handle, text)
        if not ok:
            return False, err or "the send failed"
        delivery._mark_typed(con, agent)
        return True, "typed"


def send_message(con, agent: str, text: str, log) -> tuple[bool, str]:
    """The socket's `message` verb. The pending row is written first (#2117:
    it is the one thing a reboot must not lose), then delivery is tried at
    once. `ok` is true once the daemon owns getting `text` to `agent` -
    typed already, or left in `pending_messages` for `deliver_pending` (the
    next tick) or a wake started here to pick up; only a connection the
    socket itself refuses means nothing was accepted at all."""
    row_id = db.add_pending(con, agent, text, int(time.time()))
    ok, why = type_now(con, agent, text)
    if ok:
        db.delete_pending(con, row_id)
        return True, f"{agent}: typed"
    if not delivery.session_present(agent):
        woke, wake_why = lifecycle.wake_agent(con, agent, log)
        if woke:
            ok, why = type_now(con, agent, text)
            if ok:
                db.delete_pending(con, row_id)
                return True, f"{agent}: woken and typed"
            return True, f"{agent}: queued - woken, will type once free ({why})"
        return True, f"{agent}: queued - waking it ({wake_why})"
    return True, f"{agent}: queued - {why}"


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
    ok, why = type_now(con, agent, row["text"])
    if ok:
        db.delete_pending(con, int(row["id"]))
        log(f"  {agent}: typed a pending message (#{row['id']})")
    return ok


def reload_pending(con, log) -> None:
    """Called once at daemon start (#2117): try every pending row at once,
    for whatever agent is already running and free to hear it, rather than
    waiting for that agent's own next tick to come due."""
    for agent in sorted({r["agent"] for r in db.list_pending(con)}):
        deliver_pending(con, agent, log)
