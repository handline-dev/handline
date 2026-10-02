"""T351: a message typed into an agent's terminal at once - the socket's `message`
verb and `tell_now` for a request - and the pending row that survives a restart.

Nothing here waits for idle: Claude Code queues a line typed mid-turn and reads it
once the turn ends (founder #2104). Only a screen_blocker (a dialog, a permission
prompt, an unreadable screen), the wall, or a closed session holds a message, and a
closed session is woken first. Never Esc: a blocker is left standing. What is held
stays owed - on its row (a request) or in `pending_messages` (a bare line) - and the
tick tries again. Tasks are not messages: they keep `delivery.deliver_queued`'s
one-at-a-time rule.
"""
from __future__ import annotations

import time

from .. import db
from ..infra import lock, paths
from ..session import orca
from ..team import ask as askmod

from . import delivery, landing, lifecycle


def can_type_now(con, agent: str, handle: str) -> tuple[bool, str]:
    """Nothing on screen a typed line would answer instead, and not at the
    wall - the two things immediate typing waits for. Not `orca.typeable_now`:
    that also requires idle, which this deliberately does not.

    `wall_reason` is the same check every other typing path makes (its own
    docstring: "no caller ... can skip it") - typed anyway, a turn at the
    usage wall fails at once and clears the very mark that says so."""
    if not orca.available():
        return False, "orca is not on PATH"
    reason = delivery.typing_hold(con, agent)
    if reason:
        return False, reason
    blocker = orca.screen_blocker(handle)
    if blocker is not None:
        return False, blocker.reason()
    return True, ""


def finish_say(row) -> None:
    """A say is finished once it is typed: nothing is owed on it, so it is settled
    quietly instead of staying queued (and counting as pending) until someone
    resolves it. It is closed with no verdict: a verdict is drawn as the target's reply, and
    nobody replied (T441). Anything else typed for a request stays open until it is answered."""
    if row["kind"] == "say":
        askmod.resolve(int(row["id"]), None, notify=False)


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

    What is held stays on its row, undelivered, and the tick tries again
    (`tell_undelivered`); the row is marked delivered only once it is typed, so
    it can be withdrawn until then and counts as owed until then. Told out of
    turn, so the head rule does not say it a second time. A session that is not
    up is woken and reads it as its first prompt (`delivery.waiting_prompt` marks
    it delivered); this then finds it delivered. Tasks do not come here - they
    keep the one-at-a-time head rule (`ask.push`), except one marked send-now: a handoff is
    typed on arrival, its holder busy or not (T596). Returns what happened."""
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
    # A resume's first prompt may already have told it (`waiting_prompt`); a fresh start does not.
    row = db.get_request(con, rid)
    if row["state"] != "queued":
        return "answered meanwhile"
    if row["delivered_at"]:
        return "already told"
    handle = db.get_terminal(con, agent)
    if not handle or not delivery.session_present(agent):
        return "queued - no running session; told when it is up"
    if not orca.available() or not orca.alive(handle):
        return f"queued - {agent}'s terminal is gone"
    ok, why = can_type_now(con, agent, handle)
    if not ok:
        return f"queued - {why}"
    with lock.terminal(agent) as mine:
        if not mine:
            return f"queued - {agent}: something else is using its terminal"
        row = db.get_request(con, rid)
        if row["state"] != "queued" or row["delivered_at"]:
            return "already told"
        ok, why = can_type_now(con, agent, handle)
        if not ok:
            return f"queued - {why}"
        if not db.mark_delivered(con, rid):
            return "withdrawn first"
        if landing.landed_already(con, agent, rid):        # T766: the transcript first
            db.mark_typed(con, rid, delivery._live_session(con, agent))
            db.mark_landed(con, rid)
            return "already in its transcript"
        folded = delivery.folded_notices(con, agent)
        sent, err = delivery._send(handle, delivery.tell_text(con, row, 1, folded=folded))
        if not sent:
            db.clear_delivered(con, rid)
            return f"queued - {err or 'the send failed'}"
        delivery._mark_typed(con, agent)
        db.mark_notices_shown(con, [int(r["id"]) for r in folded], delivery._live_session(con, agent))
        db.mark_told_ahead(con, agent, rid, [int(r["id"]) for r in db.queued_for(con, agent)])   # becoming the head is not news
        db.mark_typed(con, rid, delivery._live_session(con, agent))
        landing.note_typed(rid)
        db.add_event(con, agent, "notified", {"request_id": rid, "more_waiting": 0})
    log(f"  {agent}: told about #{rid} at once")
    finish_say(row)
    return "typed"


def tell_undelivered(con, agent: str, log) -> None:
    """Each tick, the messages nobody has told `agent` yet - one that reached it
    while it had no session, or whose edge was still to be checked - the way
    they are told when they arrive. Tasks are not these, unless marked send-now (a handoff,
    T596): they keep their own turn."""
    state = db.get_agent_state(con, agent)
    if not (state and state["terminal"]) or state["closed_at"]:
        return          # starting or waking it is `supervise`'s own step, with its own gates
    for row in db.undelivered_for(con, agent):
        if row["kind"] != "task" or row["send_now"]:
            tell_now(con, int(row["id"]), log)


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


def migrate_old_messages(con, log) -> None:
    """Once, at daemon start (T398): an old say was told, or never will be, and is
    settled. An old ask needs nothing: one shown stays open until answered, one never
    shown is undelivered, so the tick types it like a new one."""
    for row in con.execute("SELECT id FROM requests WHERE state = 'queued' AND kind = 'say' "
                           "AND target != ?", (db.HUMAN,)).fetchall():
        askmod.resolve(int(row["id"]), None, notify=False)
        log(f"  #{row['id']}: an old say, settled")
    # T431: a request the daemon before T418 told has `delivered_at` and never a `typed_at`, so
    # the landing sweep (which reads typed rows) never saw it and it stayed "queued" for good.
    # Told then is typed then: the sweep now finds it in the transcript, or types it again.
    con.execute("UPDATE requests SET typed_at = delivered_at WHERE state = 'queued' AND target != ? "
                "AND delivered_at IS NOT NULL AND typed_at IS NULL AND landed_at IS NULL", (db.HUMAN,))


def reload_pending(con, log) -> None:
    """Called once at daemon start (#2117): try every pending row at once,
    for whatever agent is already running and free to hear it, rather than
    waiting for that agent's own next tick to come due."""
    for agent in sorted({r["agent"] for r in db.list_pending(con)}):
        deliver_pending(con, agent, log)
