"""Requests between agents: queueing, delivery, retirement, dismissal."""
from __future__ import annotations

import time


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
        "AND state IN ('done', 'rejected') AND announced_at IS NULL ORDER BY id",
        (agent,),
    ).fetchall()


def mark_announced(con, ids: list[int]) -> None:
    now = int(time.time())
    con.executemany("UPDATE requests SET announced_at = ? WHERE id = ?",
                    [(now, int(i)) for i in ids])


# ---------------------------------------------------------------- requests
def add_request(con, requester: str, target: str, body: str, files: str | None,
                relation: str | None, response: str | None, kind: str | None,
                chain_depth: int, parent_id: int | None, state: str = "queued",
                verdict: str | None = None, chain_id: str | None = None,
                first: bool = False, queue_order: float | None = None,
                fyi: bool = False, timebox_s: int | None = None) -> int:
    """Insert a request. `queue_order`, not `id`, decides FIFO position - see
    `queued_for`. `first` and `queue_order` are request #189's --first and
    --replaces.
    """
    cur = con.execute(
        "INSERT INTO requests(ts, requester, target, body, files, relation, response, "
        "kind, chain_depth, parent_id, state, verdict, chain_id, fyi, timebox_s) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), requester, target, body, files, relation, response, kind,
         chain_depth, parent_id, state, verdict, chain_id, int(fyi), timebox_s),
    )
    rid = int(cur.lastrowid)
    if chain_id is None:
        # A request with no parent starts its own chain, named after itself.
        con.execute("UPDATE requests SET chain_id = ? WHERE id = ?", (str(rid), rid))
    order = rid if queue_order is None else queue_order
    if first and state == "queued":
        # Midpoint between the last delivered row and the first undelivered
        # one; queue_order is REAL so there is always room to bisect into.
        row = con.execute(
            "SELECT MIN(CASE WHEN delivered_at IS NULL THEN queue_order END) AS ceiling, "
            "MAX(CASE WHEN delivered_at IS NOT NULL THEN queue_order END) AS floor_ "
            "FROM requests WHERE target = ? AND state = 'queued'", (target,)).fetchone()
        ceiling, floor_ = row["ceiling"], row["floor_"]
        if ceiling is not None and floor_ is not None:
            order = (float(ceiling) + float(floor_)) / 2
        elif ceiling is not None:
            order = float(ceiling) - 1
        elif floor_ is not None:
            order = float(floor_) + 1
    con.execute("UPDATE requests SET queue_order = ? WHERE id = ?", (order, rid))
    return rid


# States a request can leave 'queued' for without ever having reached its
# target: denied by routing, withdrawn by its own requester, or superseded by
# a replacement. None of the three is evidence of a real exchange, so a cycle
# or ping-pong check counting them would refuse a conversation that, from the
# target's side, never happened.
NEVER_REACHED_TARGET = ("denied", "withdrawn", "replaced")


def chain_members(con, chain_id: str, exclude_id: int | None = None) -> list:
    """`exclude_id` is a row still 'queued' that is about to be replaced or
    withdrawn: the caller already knows it, and a cycle or ping-pong check
    counting it before that write lands would count a conversation that, from
    the target's side, never happened.
    """
    clauses = [f"state NOT IN ({','.join('?' * len(NEVER_REACHED_TARGET))})"]
    params: list = [chain_id, *NEVER_REACHED_TARGET]
    if exclude_id is not None:
        clauses.append("id != ?")
        params.append(exclude_id)
    return con.execute(
        f"SELECT * FROM requests WHERE chain_id = ? AND {' AND '.join(clauses)} ORDER BY id",
        params,
    ).fetchall()


def queued_for(con, target: str):
    """Every request still queued for `target`, ordered by queue_order (its
    delivery position - see `add_request`), not by id."""
    return con.execute(
        "SELECT * FROM requests WHERE target = ? AND state = 'queued' "
        "ORDER BY queue_order, id",
        (target,),
    ).fetchall()


def head_of_queue(con, target: str):
    """The one request `target` is meant to be working on right now, or None.

    Request #189: an agent is given one request at a time, not its whole
    queue - this is where that is decided, once, for every reader (delivery,
    `ocp inbox`, resolve's hand-off to the next one) to agree on.
    """
    rows = queued_for(con, target)
    return rows[0] if rows else None


def queue_depth(con, target: str) -> int:
    return len(queued_for(con, target))


def open_asks_of(con, requester: str) -> int:
    """What this agent asked that nobody has answered yet: what it may be
    waiting on, which is not the same as having forgotten something."""
    return con.execute("SELECT COUNT(*) FROM requests WHERE requester = ? "
                       "AND state = 'queued'", (requester,)).fetchone()[0]


def open_escalations_of(con, agent: str) -> int:
    """Escalations raised about this agent that are still waiting on the human."""
    return con.execute("SELECT COUNT(*) FROM requests WHERE requester = ? AND kind = 'escalate' "
                       "AND state = 'queued'", (agent,)).fetchone()[0]


def mark_delivered(con, rid: int) -> bool:
    """Record that this request has been shown to its target. Idempotent.

    False if the row was withdrawn or replaced first - the caller must not
    then act as if delivery happened.
    """
    cur = con.execute(
        "UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) "
        "WHERE id = ? AND state = 'queued'",
        (int(time.time()), rid))
    return cur.rowcount > 0


def timeboxed_open(con):
    """Requests an agent has been shown and not closed, whose asker has not yet
    been told they ran long. Reports ask nothing, so no clock runs on them, and
    the human's rows are "delivered" when drawn, not worked on."""
    return con.execute(
        "SELECT * FROM requests WHERE state = 'queued' AND delivered_at IS NOT NULL "
        "AND timebox_noticed_at IS NULL AND fyi = 0 AND COALESCE(kind, '') != 'report' "
        "AND target != ?", ("human",)).fetchall()


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
        "SELECT * FROM requests WHERE target = ? AND state = 'queued' "
        "AND send_now IS NOT NULL AND delivered_at IS NULL ORDER BY send_now, id",
        (target,)).fetchall()


def founder_pending(con, target: str):
    """The founder's requests to `target` that have not been shown to it yet,
    oldest first: what `deliver_queued` tells past an open head to an agent
    that is only waiting on background work."""
    return con.execute(
        "SELECT * FROM requests WHERE target = ? AND requester = 'human' "
        "AND state = 'queued' AND delivered_at IS NULL ORDER BY queue_order, id",
        (target,)).fetchall()


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
    queued roster-proposal escalation with something that is not approve or
    reject - the one way `hr.apply` is ever left with a row it can no longer
    check (#1770, #1893 SF1).

    Called from inside every function below that can move a request row out
    of 'queued', rather than at each door onto one of them (`ocp resolve`,
    `--retires`, `ask.cancel`, `--replaces` and `answer()` all reached this
    same gap in turn - #1908, #1919, #1938, #1943): a door added later gets
    this for free instead of needing its own copy of the same check. `hr
    withdraw` is not a door here - it marks its proposal file withdrawn
    before it closes the row, so `is_open_proposal` is already false by the
    time this runs.

    Every request this system closes runs through one of these functions, so
    `hr.is_open_proposal` - a glob and a YAML parse of the whole `hr/`
    directory - would otherwise run on every one of them, not just the
    founder-targeted rows a proposal escalation could ever be. A proposal
    escalation is always `ocp ask human ...` (`hr.propose`), so a row aimed
    anywhere else is checked here for free, off a column already in hand,
    and the directory is never touched for it.
    """
    if state == "queued":
        return False
    from ..team import ask as askmod, hr
    row = con.execute("SELECT target FROM requests WHERE id = ?", (rid,)).fetchone()
    if row is None or row["target"] != askmod.HUMAN:
        return False
    return hr.is_open_proposal(rid) and hr.decision(verdict) == "unclear"


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
        "UPDATE requests SET state = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?), announced_at = COALESCE(announced_at, ?) "
        "WHERE id = ? AND state = 'queued'", (state, verdict, now, now, rid))
    return cur.rowcount > 0


# What `ocp dismiss` wrote into the verdict before a dismissal was a state.
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
                    dismissed_reason: str | None = None) -> bool:
    """Close a request that is still open, atomically. True only if this call
    was the one that closed it: an agent resolving it in the same instant wins,
    and its answer is not overwritten. `announced=True` is `resolve`'s
    `notify=False` case: stamped in this same write rather than a follow-up
    call - a row that is briefly 'done' with `announced_at` still NULL is
    exactly what `unannounced_answers()` looks for, and a sweep landing in
    that window would announce it anyway."""
    if _would_strand_a_proposal(con, rid, state, verdict):
        return False
    now = int(time.time())
    cur = con.execute(
        "UPDATE requests SET state = ?, verdict = ?, resolved_at = ?, "
        "announced_at = COALESCE(announced_at, ?), dismissed_by = ?, dismissed_reason = ? "
        "WHERE id = ? AND state = 'queued'",
        (state, verdict, now, now if announced else None, dismissed_by, dismissed_reason, rid))
    return cur.rowcount > 0


def retire_if_undelivered(con, rid: int, state: str, verdict: str | None = None) -> bool:
    """Move a still-queued, undelivered request to a terminal state, atomically.

    True only if this call is the one that moved it. A plain read-then-write
    (check delivered_at, then update) races a delivery landing in between;
    this closes that window in one statement.
    """
    if _would_strand_a_proposal(con, rid, state, verdict):
        return False
    cur = con.execute(
        "UPDATE requests SET state = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?) "
        "WHERE id = ? AND state = 'queued' AND delivered_at IS NULL",
        (state, verdict, int(time.time()), rid))
    return cur.rowcount > 0


def set_moot_by(con, rid: int, by: int) -> bool:
    """Point a retired row at the id (request or task) that retired it.

    Set once: True only the first time, so a later call naming a different id
    cannot quietly overwrite an earlier record of why this row is moot.
    """
    cur = con.execute("UPDATE requests SET moot_by = ? WHERE id = ? AND moot_by IS NULL",
                      (by, rid))
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
        "ORDER BY ts LIMIT 1", (requester, since_ts),
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
        "SELECT COUNT(*) AS c FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied'",
        (requester, since_ts),
    ).fetchone()
    return int(row["c"])
