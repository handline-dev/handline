"""Requests between agents: queueing, delivery, retirement, dismissal."""
from __future__ import annotations

import time

from ..infra import protected
from . import cursors

# The founder's name as a requester or target; team.ask re-exports it.
HUMAN = "human"


def unannounced_answers(con, agent: str) -> list:
    """What this agent asked, that came back, that it has not been told about.

    Marked per row rather than tracked with a cursor: a cursor works for
    arrival, which happens in id order by construction, and not for answering,
    which does not. Two answers out of order and the older one is behind the
    high-water mark forever - which is the failure this whole path exists to
    prevent, one layer down.
    """
    return con.execute(
        "SELECT id, ts, resolved_at, target, kind, state, verdict, body, parent_id, "
        "chain_id, dismissed_by, dismissed_reason FROM requests WHERE requester = ? "
        "AND state IN ('landed', 'withdrawn') AND announced_at IS NULL ORDER BY id",
        (agent,),
    ).fetchall()


def mark_read(con, ids: list[int]) -> None:
    """The founder has these in front of him (T781): the one place `read_at` is written, once."""
    now = int(time.time())
    con.executemany("UPDATE requests SET read_at = COALESCE(read_at, ?) WHERE id = ? AND target = ?",
                    [(now, int(i), HUMAN) for i in ids])


def mark_announced(con, ids: list[int]) -> None:
    now = int(time.time())
    con.executemany("UPDATE requests SET announced_at = ? WHERE id = ?",
                    [(now, int(i)) for i in ids])


# ---------------------------------------------------------------- requests
def add_request(con, requester: str, target: str, body: str, files: str | None,
                relation: str | None, response: str | None, kind: str | None,
                chain_depth: int, parent_id: int | None, state: str = "queued",
                verdict: str | None = None, chain_id: str | None = None,
                fyi: bool = False, timebox_s: int | None = None) -> int:
    """Insert a request. `queue_order`, not `id`, decides FIFO position - see
    `queued_for`; it starts as the id and only `promote_to_front` moves it.
    """
    # An agent's session cannot verify the routing table it read, so what it
    # writes waits for the daemon's check (`delivery.enforce_edges`) before
    # anyone can be handed it.
    edge_ok = None if protected.in_agent_session() else 1
    now = int(time.time())
    # T781: a say to the founder is born landed - nothing is typed at him, so nothing is owed - and
    # unread (`read_at` NULL) until he reads it. 'done' is the landed state until the states collapse.
    landed = target == HUMAN and kind == "say" and state == "queued"
    state = LANDED if landed or state == "done" else state
    cur = con.execute(
        "INSERT INTO requests(ts, requester, target, body, files, relation, response, "
        "kind, chain_depth, parent_id, state, verdict, chain_id, fyi, timebox_s, edge_ok, "
        "landed_at, resolved_at, announced_at, state_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (now, requester, target, body, files, relation, response, kind,
         chain_depth, parent_id, state, verdict, chain_id, int(fyi), timebox_s, edge_ok,
         now if landed else None, now if landed else None, now if landed else None, now),
    )
    rid = int(cur.lastrowid)
    if chain_id is None:
        # A request with no parent starts its own chain, named after itself.
        con.execute("UPDATE requests SET chain_id = ? WHERE id = ?", (str(rid), rid))
    con.execute("UPDATE requests SET queue_order = ? WHERE id = ?", (rid, rid))
    return rid


# T797: a request has one state and one time (`state_at`). queued: not sent; landed: the line is in
# its target's transcript, the normal end; withdrawn / replaced / denied: ended before sending.
# The closers below still take the old words as the intent of the close - "done" (answered, so
# landed) and "rejected" (taken back: landed if it had been delivered, else withdrawn) - and store
# the new ones.
LANDED = "landed"


def stored_state(con, rid: int, intent: str) -> str:
    """The state a close with `intent` leaves on request `rid`."""
    if intent in ("done", LANDED):
        return LANDED
    if intent == "rejected":
        r = con.execute("SELECT delivered_at, landed_at FROM requests WHERE id = ?", (rid,)).fetchone()
        return LANDED if r is not None and (r["delivered_at"] or r["landed_at"]) else "withdrawn"
    return intent


# States a request can leave 'queued' for without ever having reached its
# target: denied by routing, withdrawn by its own requester, or superseded by
# a replacement. None of the three is evidence of a real exchange, so a cycle
# or ping-pong check counting them would refuse a conversation that, from the
# target's side, never happened.
NEVER_REACHED_TARGET = ("denied", "withdrawn", "replaced")


def chain_members(con, chain_id: str) -> list:
    # An answer is a message in the chain, never an ask in it: the rate limit, the
    # cycle guard and the ping-pong check all count asks.
    clauses = [f"state NOT IN ({','.join('?' * len(NEVER_REACHED_TARGET))})", "settles IS NULL"]
    params: list = [chain_id, *NEVER_REACHED_TARGET]
    return con.execute(
        f"SELECT * FROM requests WHERE chain_id = ? AND {' AND '.join(clauses)} ORDER BY id",
        params,
    ).fetchall()


# T751: a delivery (kind 'task') whose task has ended, or has gone to someone else, is a leftover:
# nothing can be done with it, so nothing that hands requests out, counts them or lists them stuck
# reads it (`ocp doctor` lists them). The same fragment, on `requests`.
LEFTOVER_DELIVERY = ("(requests.kind = 'task' AND EXISTS (SELECT 1 FROM task_requests l0 WHERE l0.request_id = requests.id) "
                     "AND NOT EXISTS (SELECT 1 FROM task_requests l JOIN tasks t ON t.id = l.task_id "
                     "WHERE l.request_id = requests.id AND t.done_evidence IS NULL AND t.discarded_by IS NULL "
                     "AND t.owner = requests.target))")


def leftover_deliveries(con):
    """The queued deliveries `LEFTOVER_DELIVERY` describes, for `ocp doctor`."""
    return con.execute(f"SELECT * FROM requests WHERE state = 'queued' AND {LEFTOVER_DELIVERY} ORDER BY id").fetchall()


def settle_leftover_deliveries(con) -> list[int]:
    """Close quietly, as settled (design #7986, MIGRATION), every queued delivery whose tasks have all
    ended: nobody is told, nothing is typed, and no task is touched. A task moved to someone else is
    not settled here - `send_moved_task` takes the old row back and asks the new owner, and a sweep
    between `edit_task`'s commit and that would leave the new owner never asked. The ids closed."""
    done = []
    for r in con.execute(
            "SELECT * FROM requests WHERE state = 'queued' AND kind = 'task' "
            "AND EXISTS (SELECT 1 FROM task_requests l0 WHERE l0.request_id = requests.id) "
            "AND NOT EXISTS (SELECT 1 FROM task_requests l JOIN tasks t ON t.id = l.task_id "
            "WHERE l.request_id = requests.id AND t.done_evidence IS NULL AND t.discarded_by IS NULL) "
            "ORDER BY id").fetchall():
        if close_if_queued(con, int(r["id"]), "done", "its task is no longer this agent's to do",
                           announced=True, settle=False):
            done.append(int(r["id"]))
    return done


def queued_for(con, target: str):
    """Every request still queued for `target`, ordered by queue_order (its
    delivery position - see `add_request`), not by id."""
    return con.execute(
        f"SELECT * FROM requests WHERE target = ? AND state = 'queued' AND NOT {LEFTOVER_DELIVERY} "
        "ORDER BY queue_order, id",
        (target,),
    ).fetchall()


def undelivered_for(con, target: str):
    """The requests among `queued_for` that `target` has never been shown -
    not the head it already holds unresolved. A closed agent's own wake is
    for these (`lifecycle.would_wake`, T154): waking it back up for a request
    it was already told, and only sits on, would close it and wake it again
    every idle window for as long as that one request stays open."""
    return con.execute(
        f"SELECT * FROM requests WHERE target = ? AND state = 'queued' AND NOT {LEFTOVER_DELIVERY} "
        "AND delivered_at IS NULL ORDER BY queue_order, id",
        (target,),
    ).fetchall()


def head_of_queue(con, target: str, skip: int | None = None):
    """The one request `target` is meant to be working on right now, or None.

    Request #189: an agent is given one request at a time, not its whole
    queue - this is where that is decided, once, for every reader (delivery,
    `ocp inbox`, resolve's hand-off to the next one) to agree on.

    T293: a say is never this - it is delivered ahead of the queue on its
    own (`ask.say`'s `mark_send_now`), and skipping it here means one
    sitting unanswered can never become, or block, the head the one-doing
    rule actually tracks.

    `skip` is `delivery.effective_head`'s own use (T337): the true head,
    blocked on record, is never a row this returns - the id to leave out
    while it looks for what the one-at-a-time rule falls on instead.
    """
    rows = [r for r in queued_for(con, target) if r["kind"] != "say" and r["id"] != skip]
    return rows[0] if rows and rows[0]["edge_ok"] else None


def task_head(con, target: str, skip: int | None = None):
    """The task `target` is to be told about next: the first queued request of
    kind 'task', when its edge has been checked. A message (an ask, a say) is
    never this - it is typed at once, not waited for (T398)."""
    rows = [r for r in queued_for(con, target) if r["kind"] == "task" and r["id"] != skip]
    return rows[0] if rows and rows[0]["edge_ok"] else None


def head_of_queue_or_marked(con, target: str):
    """`head_of_queue`, or - with none - the oldest `send_now_pending` row
    (T293): what a say-only queue still has to tell, for the two readers
    (`waiting_prompt`, `ask.inbox`) that only ever want the one thing to
    show, not `deliver_queued`'s fuller ordering of several at once."""
    return head_of_queue(con, target) or next(iter(send_now_pending(con, target)), None)


def clear_edge(con, rid: int) -> None:
    con.execute("UPDATE requests SET edge_ok = 1 WHERE id = ?", (rid,))


def promote_to_front(con, rid: int) -> None:
    """Move a still-queued request ahead of the other undelivered ones queued
    for its own target (T318), never ahead of one already delivered - #189's
    one-at-a-time head stays whatever the target was already shown."""
    row = con.execute("SELECT target FROM requests WHERE id = ? AND state = 'queued'",
                      (rid,)).fetchone()
    if row is None:
        return
    floor = con.execute(
        "SELECT MIN(queue_order) FROM requests WHERE target = ? AND state = 'queued' "
        "AND delivered_at IS NULL",
        (row["target"],)).fetchone()[0]
    order = float(floor) - 1 if floor is not None else rid
    con.execute("UPDATE requests SET queue_order = ? WHERE id = ?", (order, rid))


def unreached_count(con, agent: str) -> int:
    """What is still owed `agent` because it has not reached it (T398): requests
    never shown to it - a task waiting behind its current one, or a message whose
    typing is held by a closed session - the messages the daemon has yet to type,
    and answers not yet told. Something already typed is not counted, whether or not
    it was answered: 0 means nothing is stuck on the way to the agent."""
    from .pending import list_pending
    return (len(undelivered_for(con, agent)) + len(list_pending(con, agent))
            + len(unannounced_answers(con, agent)))


def queue_depth(con, target: str) -> int:
    return len(queued_for(con, target))


def open_ask_ids_of(con, requester: str) -> list[int]:
    """What this agent asked that nobody has answered yet: what it may be
    waiting on, which is not the same as having forgotten something."""
    return [r[0] for r in con.execute("SELECT id FROM requests WHERE requester = ? "
                                      "AND state = 'queued' ORDER BY id", (requester,))]


def mark_delivered(con, rid: int) -> bool:
    """Record that this request has been shown to its target. Idempotent.

    False if the row was withdrawn or replaced first - the caller must not
    then act as if delivery happened.
    """
    cur = con.execute(
        "UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) "
        "WHERE id = ? AND state = 'queued'",
        (int(time.time()), rid))
    if cur.rowcount:
        # work arrived: the agent-level progress note (T366) is for an idle spell
        row = con.execute("SELECT target FROM requests WHERE id = ?", (rid,)).fetchone()
        cursors.set_meta(con, cursors.agent_progress_key(row["target"]), None)
        if row["target"] == HUMAN:          # the founder has no transcript to land in: drawn is delivered
            from .tasks import stamp_task_delivered
            stamp_task_delivered(con, rid)
    return cur.rowcount > 0


def clear_delivered(con, rid: int) -> None:
    """Undo `mark_delivered` for a request whose typing failed, so it is owed again."""
    con.execute("UPDATE requests SET delivered_at = NULL WHERE id = ? AND state = 'queued'", (rid,))


def mark_typed(con, rid: int, session: str | None) -> None:
    """The send went through: `rid` was typed into `session`'s terminal. It is not
    delivered until `mark_landed` (T418) - a line typed can still be dropped, or
    typed into a session that is then closed."""
    con.execute("UPDATE requests SET typed_at = ?, typed_session = ? WHERE id = ? AND landed_at IS NULL",
                (int(time.time()), session, rid))


def mark_landed(con, rid: int) -> None:
    """The receiving session's transcript holds `rid`: only now is it delivered."""
    con.execute("UPDATE requests SET landed_at = COALESCE(landed_at, ?) WHERE id = ?",
                (int(time.time()), rid))
    from .tasks import stamp_task_delivered
    stamp_task_delivered(con, rid)


def awaiting_landing(con, target: str):
    """Requests typed at `target` that its transcript has not shown yet."""
    return con.execute(
        "SELECT * FROM requests WHERE target = ? AND state = 'queued' AND typed_at IS NOT NULL "
        "AND landed_at IS NULL ORDER BY queue_order, id", (target,)).fetchall()


def owe_again(con, rid: int) -> None:
    """A typed line the transcript never showed is owed again, told afresh by the
    same paths that told it first."""
    con.execute(
        "UPDATE requests SET delivered_at = NULL, typed_at = NULL, typed_session = NULL, "
        "told_out_of_turn = NULL WHERE id = ? AND state = 'queued' AND landed_at IS NULL", (rid,))


def timeboxed_open(con):
    """Requests an agent has been shown and not closed, whose asker has not yet
    been told they ran long. Reports ask nothing, so no clock runs on them, and
    the human's rows are "delivered" when drawn, not worked on. (`kind = 'report'`
    only matches rows written before reports became says.)"""
    return con.execute(
        f"SELECT * FROM requests WHERE state = 'queued' AND landed_at IS NOT NULL AND NOT {LEFTOVER_DELIVERY} "
        "AND timebox_noticed_at IS NULL AND fyi = 0 AND COALESCE(kind, '') != 'report' "
        "AND target != ?", (HUMAN,)).fetchall()


def mark_timebox_noticed(con, rid: int) -> None:
    con.execute("UPDATE requests SET timebox_noticed_at = COALESCE(timebox_noticed_at, ?) "
                "WHERE id = ?", (int(time.time()), rid))


def mark_send_now(con, rid: int) -> bool:
    """Mark a queued, still undelivered request to be told to its target as
    soon as the target can be typed at, even with a delivered head ahead of it.
    Idempotent; False when the row is delivered, closed or missing. The mark
    is a request to the daemon (`deliver_queued`), not a delivery.
    """
    cur = con.execute(
        "UPDATE requests SET send_now = COALESCE(send_now, ?) "
        "WHERE id = ? AND state = 'queued' AND delivered_at IS NULL",
        (int(time.time()), rid))
    return cur.rowcount > 0


def send_now_pending(con, target: str):
    """Marked requests for `target` not yet shown to it, in the order pressed."""
    return con.execute(
        f"SELECT * FROM requests WHERE target = ? AND state = 'queued' AND NOT {LEFTOVER_DELIVERY} "
        "AND send_now IS NOT NULL AND delivered_at IS NULL ORDER BY send_now, id",
        (target,)).fetchall()


def founder_pending(con, target: str):
    """The founder's requests to `target` that have not been shown to it yet,
    oldest first: what `deliver_queued` tells past an open head."""
    return con.execute(
        f"SELECT * FROM requests WHERE target = ? AND requester = ? AND NOT {LEFTOVER_DELIVERY} "
        "AND state = 'queued' AND delivered_at IS NULL ORDER BY queue_order, id",
        (target, HUMAN)).fetchall()


def followups_pending(con, target: str, head_id: int):
    """Requests to `target` that reply in the thread of `head_id` - its open
    head - and have not been shown to it yet, oldest first. What follows up the
    request an agent is working on is not behind it in line."""
    return con.execute(
        "SELECT * FROM requests WHERE target = ? AND state = 'queued' "
        "AND parent_id = ? AND delivered_at IS NULL ORDER BY queue_order, id",
        (target, head_id)).fetchall()


def mark_told_out_of_turn(con, rid: int) -> None:
    """Record that "send now" already told this row, ahead of its head, so
    becoming the head later (T38) is not a second telling."""
    con.execute("UPDATE requests SET told_out_of_turn = 1 WHERE id = ?", (rid,))


def _would_strand_a_proposal(con, rid: int, state: str, verdict: str | None) -> bool:
    """True when closing `rid` into `state` with `verdict` would answer a
    queued roster-proposal decision with something that is not approve or
    reject - the one way `hr.apply` is ever left with a row it can no longer
    check (#1770, #1893 SF1).

    Called from inside every function below that can move a request row out
    of 'queued', rather than at each door onto one of them (a resolve,
    a retire, `ask.cancel`, a replace and `answer()` all reached this
    same gap in turn - #1908, #1919, #1938, #1943): a door added later gets
    this for free instead of needing its own copy of the same check. `hr
    withdraw` is not a door here - it marks its proposal file withdrawn
    before it closes the row, so `is_open_proposal` is already false by the
    time this runs.

    Every request this system closes runs through one of these functions, so
    `hr.is_open_proposal` - a glob and a YAML parse of the whole `hr/`
    directory - would otherwise run on every one of them, not just the
    founder-targeted rows a proposal decision could ever be. A proposal
    decision is always an ask to the human (`hr.propose`), so a row aimed
    anywhere else is checked here for free, off a column already in hand,
    and the directory is never touched for it.
    """
    if state == "queued":
        return False
    from ..team import hr
    row = con.execute("SELECT target FROM requests WHERE id = ?", (rid,)).fetchone()
    if row is None or row["target"] != HUMAN:
        return False
    return hr.is_open_proposal(rid, con) and hr.decision(verdict) == "unclear"


def retire_queued(con, rid: int, state: str, verdict: str | None = None) -> bool:
    """Take a still-queued request out of the queue, delivered or not.

    For rows nobody can act on once a newer one exists - the human's, where
    "delivered" only means it was drawn on a screen. Announced in the same
    write: the sender retired its own row and is not told it was answered.
    """
    if _would_strand_a_proposal(con, rid, state, verdict):
        return False
    now = int(time.time())
    cur = con.execute(
        "UPDATE requests SET state = ?, state_at = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?), announced_at = COALESCE(announced_at, ?) "
        "WHERE id = ? AND state = 'queued'", (stored_state(con, rid, state), now, verdict, now, now, rid))
    if cur.rowcount > 0:
        _settle(con, rid, state, verdict)
    return cur.rowcount > 0


# What the old `dismiss` wrote into the verdict before a dismissal was a state.
# Rows from then still hold it, and read as dismissals through `dismissal`.
LEGACY_DISMISS = "read - no reply"


def dismissal(row) -> tuple[bool, str | None, str | None]:
    """(dismissed, by, reason) for a request row. A row is dismissed when it
    says so in its own columns, or holds the old literal verdict (whose
    author is unknown). A row read from a SELECT that leaves the columns out
    reads as not dismissed unless it is the old literal."""
    keys = row.keys()
    by = row["dismissed_by"] if "dismissed_by" in keys else None
    if by:
        return True, by, row["dismissed_reason"]
    verdict = row["verdict"] if "verdict" in keys else None
    if verdict == LEGACY_DISMISS:
        return True, None, None
    if verdict and verdict.startswith(LEGACY_DISMISS + ": "):
        return True, None, verdict[len(LEGACY_DISMISS) + 2:]
    return False, None, None


def shown_verdict(row) -> str | None:
    """What a screen may print as this row's answer: the verdict, or for a
    dismissal a state label that cannot be read as anyone's words."""
    dismissed, by, reason = dismissal(row)
    if not dismissed:
        return row["verdict"]
    return ("dismissed" + (f" by {by}" if by else "")
            + (f" - {reason}" if reason else ""))


def close_if_queued(con, rid: int, state: str, verdict: str | None,
                    announced: bool = False, dismissed_by: str | None = None,
                    dismissed_reason: str | None = None, answered_by: int | None = None,
                    settle: bool = True) -> bool:
    """Close a request that is still open, atomically. True only if this call
    was the one that closed it: an agent resolving it in the same instant wins,
    and its answer is not overwritten. `announced=True` is `resolve`'s
    `notify=False` case: stamped in this same write rather than a follow-up
    call - a row that is briefly 'done' with `announced_at` still NULL is
    exactly what `unannounced_answers()` looks for, and a sweep landing in
    that window would announce it anyway.

    `answered_by` names a reply row that is itself the answer (T361: a `say --parent`): it is
    marked as settling this request, and no second answer row is written for the same words.

    `settle=False` (T684): the request is closed because its task is being handed on, so the task
    a decision card belongs to is not settled with it - it is still open, with a new holder."""
    if _would_strand_a_proposal(con, rid, state, verdict):
        return False
    now = int(time.time())
    cur = con.execute(
        "UPDATE requests SET state = ?, state_at = ?, verdict = ?, resolved_at = ?, "
        "announced_at = COALESCE(announced_at, ?), dismissed_by = ?, dismissed_reason = ? "
        "WHERE id = ? AND state = 'queued'",
        (stored_state(con, rid, state), now, verdict, now, now if announced else None,
         dismissed_by, dismissed_reason, rid))
    if cur.rowcount > 0:
        if answered_by is not None:
            con.execute("UPDATE requests SET settles = ? WHERE id = ?", (rid, answered_by))
        elif state == "done" and verdict and dismissed_by is None:
            add_answer(con, rid, verdict)
        if settle:
            _settle(con, rid, state, verdict, dismissed_by)
        else:
            from .tasks import refresh_sent_of_request
            refresh_sent_of_request(con, rid)
    return cur.rowcount > 0


def _settle(con, rid: int, state: str, verdict: str | None, dismissed_by: str | None = None) -> None:
    """A founder's decision task follows its request out of the queue."""
    from .tasks import refresh_sent_of_request, settle_decision_tasks
    refresh_sent_of_request(con, rid)
    settle_decision_tasks(con, rid, state, verdict, dismissed_by)


def add_answer(con, rid: int, text: str, at: int | None = None) -> int:
    """Store what answered request `rid` as a say of its own: its own id, from the
    one asked to the one who asked, referencing the request (`parent_id`) and
    marked as the one that settled it (`settles`) - so a reply to it can name it,
    and the request's own verdict is not drawn a second time. It is already read:
    telling the asker is the request's own announcement, not this row's."""
    row = get_request(con, rid)
    when = int(time.time()) if at is None else at
    cur = con.execute(
        "INSERT INTO requests(ts, requester, target, body, kind, chain_depth, parent_id, "
        "settles, chain_id, state, announced_at, resolved_at, edge_ok) "
        "VALUES(?,?,?,?,'say',?,?,?,?,?,?,?,1)",
        (when, row["target"], row["requester"], text, row["chain_depth"], rid, rid,
         row["chain_id"], "done", when, when))
    aid = int(cur.lastrowid)
    con.execute("UPDATE requests SET queue_order = ? WHERE id = ?", (aid, aid))
    return aid


def answer_row_of(con, rid: int):
    """The say that settled request `rid`, if it has one: the message that carries
    what answered it, and the id to number it by."""
    return con.execute("SELECT * FROM requests WHERE settles = ? ORDER BY id LIMIT 1",
                       (rid,)).fetchone()


def append_verdict(con, rid: int, text: str) -> None:
    """Add a line to the verdict of a request already closed, before its requester is told."""
    con.execute("UPDATE requests SET verdict = verdict || ? WHERE id = ?", ("\n\n" + text, rid))
    con.execute("UPDATE requests SET body = body || ? WHERE settles = ?", ("\n\n" + text, rid))


def retire_if_undelivered(con, rid: int, state: str, verdict: str | None = None) -> bool:
    """Move a still-queued, undelivered request to a terminal state, atomically.

    True only if this call is the one that moved it. A plain read-then-write
    (check delivered_at, then update) races a delivery landing in between;
    this closes that window in one statement.
    """
    if _would_strand_a_proposal(con, rid, state, verdict):
        return False
    now = int(time.time())
    cur = con.execute(
        "UPDATE requests SET state = ?, state_at = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?), announced_at = COALESCE(announced_at, ?) "
        "WHERE id = ? AND state = 'queued' AND delivered_at IS NULL",
        (stored_state(con, rid, state), now, verdict, now, now, rid))
    if cur.rowcount > 0:
        _settle(con, rid, state, verdict)
    return cur.rowcount > 0


def get_request(con, rid: int):
    return con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()


def is_still_queued(con, rid: int) -> bool:
    """Whether `rid` is still 'queued' after one of the closers above
    refused it - the only way a caller can tell its own guard (or the
    chokepoint inside them) from a lost race to another close, since both
    leave the closer returning False the same way (#1943)."""
    row = get_request(con, rid)
    return bool(row and row["state"] == "queued")


def oldest_counted_ask(con, requester: str, since_ts: int) -> int | None:
    """When the ask that has to leave the window before a slot frees was sent.

    The same rule as `asks_since`, so the two cannot disagree about which asks
    are in the window.
    """
    row = con.execute(
        "SELECT ts FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied' "
        "AND settles IS NULL ORDER BY ts LIMIT 1", (requester, since_ts),
    ).fetchone()
    return int(row["ts"]) if row else None


def standing_denial(con, requester: str, target: str, body: str, files: str | None,
                    since_ts: int) -> int | None:
    """The id of an identical ask already refused inside the window, if any.

    Identical includes the attachment: the same words with another file is a
    different message, and reusing the first row would drop the file silently.
    """
    row = con.execute(
        "SELECT id FROM requests WHERE requester = ? AND target = ? AND body = ? "
        "AND files IS ? AND state = 'denied' AND ts >= ? ORDER BY id DESC LIMIT 1",
        (requester, target, body, files, since_ts),
    ).fetchone()
    return int(row["id"]) if row else None


def asks_since(con, requester: str, since_ts: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS c FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied' "
        "AND settles IS NULL",
        (requester, since_ts),
    ).fetchone()
    return int(row["c"])
