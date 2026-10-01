"""Tasks, their links to requests, and what blocks what."""
from __future__ import annotations

import json
import time

from .events import add_event
from .requests import HUMAN
from .schema import EVENT_TASK


# RULE: a task row holds these fields and nothing else. Never a copy of a
# request's body, a state, a date a request already carries, or a summary of
# an answer - the board reads those from `requests` each time it draws, so
# there is one place each fact lives and nothing to drift. Adding a column to
# `tasks` means adding it here, and a test fails until both agree.
# The exception is the delivery of the task itself (T720, T737, design #7986): `delivered_at`
# and `sent_at` are the task's own facts, moving off the requests step by step.
TASK_FIELDS = ("id", "title", "owner", "parent_task", "opened_by",
               "done_evidence", "done_at", "done_via",
               "edited_by", "edited_at", "created_at", "closed_by", "closed_at",
               "discarded_by", "discard_reason", "discarded_at", "priority", "merged_into",
               "queued_by", "queued_at", "expects", "timeout_s", "timeout_at", "delivered_at", "sent_at")

DEFAULT_PRIORITY = 2


def new_task(con, title: str, owner: str, opened_by: str,
             parent_task: int | None = None, priority: int = DEFAULT_PRIORITY,
             expects: str | None = None, timeout_s: int | None = None) -> int:
    """`timeout_s` is optional here (many tests build a task without one) -
    `ocp task new` is what actually requires it, at the CLI (T286).

    timeout_at starts NULL even when `timeout_s` is given: the clock only
    runs while the task is doing (T286's revision, founder #1627), and a
    task is never doing the moment it is opened - nothing is linked to it
    yet. The daemon sweep starts it once a request is."""
    cur = con.execute(
        "INSERT INTO tasks(title, owner, parent_task, opened_by, created_at, priority, expects, "
        "timeout_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, owner, parent_task, opened_by, int(time.time()), priority, expects, timeout_s))
    return int(cur.lastrowid)


def add_memo(con, tid: int, author: str, body: str) -> int:
    cur = con.execute("INSERT INTO task_memos(task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                      (tid, author, body, time.time()))
    return int(cur.lastrowid)


def get_memo(con, mid: int):
    return con.execute("SELECT * FROM task_memos WHERE id = ?", (mid,)).fetchone()


def task_memos(con, tid: int) -> list:
    """The task's memos, oldest first."""
    return con.execute("SELECT * FROM task_memos WHERE task_id = ? ORDER BY id", (tid,)).fetchall()


def edit_memo(con, mid: int, body: str) -> None:
    con.execute("UPDATE task_memos SET body = ?, edited_at = ? WHERE id = ?",
                (body, int(time.time()), mid))


def delete_memo(con, mid: int) -> None:
    con.execute("DELETE FROM task_memos WHERE id = ?", (mid,))


def get_task(con, tid: int):
    return con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()


# A task is open until it is done or discarded (T568): those are its only two ends.
OPEN_SQL = "done_evidence IS NULL AND discarded_by IS NULL"


def task_ended(row) -> bool:
    """Done or discarded: the two ways a task ends."""
    return bool(row["done_evidence"] or row["discarded_by"])


def open_tasks(con) -> list:
    return con.execute(f"SELECT * FROM tasks WHERE {OPEN_SQL} ORDER BY priority, id").fetchall()


def all_tasks(con) -> list:
    return con.execute("SELECT * FROM tasks ORDER BY priority, id").fetchall()


def open_child_tasks(con, tid: int) -> list:
    return con.execute(
        f"SELECT * FROM tasks WHERE parent_task = ? AND {OPEN_SQL} ORDER BY priority, id",
        (tid,)).fetchall()


def mark_task_done(con, tid: int, evidence: str, via: str = "reason") -> bool:
    """Mark a task done, atomically. True only if this call was the one that
    wrote the first evidence, so a second call racing this one loses instead
    of overwriting it. `via` is 'sha', 'request' or 'reason' - which of
    mark_task_done's exclusive evidence forms the caller was given, not a
    guess made later from the text (a --reason forced past looks_like_sha
    would guess wrong)."""
    cur = con.execute(
        "UPDATE tasks SET done_evidence = ?, done_at = ?, done_via = ? "
        "WHERE id = ? AND done_evidence IS NULL AND discarded_by IS NULL",
        (evidence, int(time.time()), via, tid))
    return cur.rowcount > 0


def discard_task(con, tid: int, by: str, reason: str) -> bool:
    """Discard a task that is not done, atomically. True only if this call
    was the one that ended it."""
    cur = con.execute(
        "UPDATE tasks SET discarded_by = ?, discarded_at = ?, discard_reason = ? "
        f"WHERE id = ? AND {OPEN_SQL}", (by, int(time.time()), reason, tid))
    return cur.rowcount > 0


def set_task_queued(con, tid: int, by: str | None) -> bool:
    """Approve a task to run (`by` set) or send it back to draft (None),
    atomically. True only if it changed: a task already ended, or already in
    that state, matches no row."""
    cur = con.execute(
        f"UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND {OPEN_SQL} "
        "AND queued_by IS " + ("NULL" if by else "NOT NULL"),
        (by, int(time.time()) if by else None, tid))
    return cur.rowcount > 0


def reopen_task(con, tid: int, is_doing: bool) -> bool:
    """Send a done task back to open, atomically; True only if this call cleared it
    (a task not done matches no row). `is_doing` is the caller's
    read of `reads_doing`: a doing task's clock restarts from now rather than firing
    on a deadline that passed while it sat done."""
    now = int(time.time())
    if is_doing:
        cur = con.execute(
            "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL, "
            "timeout_at = CASE WHEN timeout_s IS NOT NULL THEN ? + timeout_s ELSE NULL END "
            "WHERE id = ? AND done_evidence IS NOT NULL", (now, tid))
    else:
        cur = con.execute(
            "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL, "
            "timeout_at = NULL WHERE id = ? AND done_evidence IS NOT NULL",
            (tid,))
    return cur.rowcount > 0


def tasks_to_clock(con) -> list:
    """Open tasks (not done, not discarded) - T286's daemon sweep reads every one
    each tick to start, pause or fire its clock, since whether it is currently
    doing (the only state the clock runs in) is not a column this can filter
    on in SQL. Every task, not only those opened with a timeout: one opened
    inside ocp (a finding, a hold, `task begin`) never passed `task new -t`
    and takes the default once it is doing (T301)."""
    return con.execute(
        f"SELECT * FROM tasks WHERE {OPEN_SQL} ORDER BY id").fetchall()


def advance_task_timeout(con, tid: int, now: int, default_s: int) -> None:
    """Start (timeout_at was NULL) or re-arm (it just fired) a task's clock:
    its next deadline is timeout_s from `now`, `default_s` for a task that was
    never given one - which is then written, so the notice and `task edit`
    both see the length in force. The notice repeats every timeout_s a doing
    task is still open, until it is done or its timeout is extended
    (`edit_task`, which resets timeout_at the same way)."""
    con.execute(
        "UPDATE tasks SET timeout_s = COALESCE(timeout_s, ?), "
        "timeout_at = ? + COALESCE(timeout_s, ?) WHERE id = ?", (default_s, now, default_s, tid))


def pause_task_timeout(con, tid: int) -> None:
    """Stop a task's clock: no deadline runs while it is not doing. Idempotent
    - a task with no clock running matches no row."""
    con.execute("UPDATE tasks SET timeout_at = NULL WHERE id = ? AND timeout_at IS NOT NULL",
                (tid,))


def edit_task(con, tid: int, by: str, owner: str | None = None,
              title: str | None = None, priority: int | None = None,
              timeout_s: int | None = None, is_doing: bool = False) -> None:
    """Write a changed owner and/or title, stamped with who and when - the
    same evidence a discard leaves on discarded_by/discarded_at. Caller has
    already checked permission and that something actually changed; this
    only writes.

    `timeout_s`, given, always writes - unlike the others it is not "changed
    if different": T286's --timeout extends the task's timeout counting from
    this edit even when the length given is the same as before. Its clock
    (timeout_at) only runs while doing (`is_doing`, the caller's read of that
    at the time of this edit) - resumed from now if so, left paused (NULL)
    otherwise, the same rule the daemon sweep applies every tick."""
    now = int(time.time())
    sets = ["edited_by = ?", "edited_at = ?"]
    params: list = [by, now]
    if owner is not None:
        sets += ["owner = ?", "delivered_at = NULL"]       # the new owner has not had it yet
        params.append(owner)
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if priority is not None:
        sets.append("priority = ?")
        params.append(priority)
    if timeout_s is not None:
        sets += ["timeout_s = ?", "timeout_at = ?"]
        params += [timeout_s, now + timeout_s if is_doing else None]
    params.append(tid)
    con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)


def first_linked_request(con, tid: int):
    """The request T<tid> was opened with: the first one linked to it, in link order (the link's
    rowid, not the request id - a later link of an older request, `say --task`, does not come first)."""
    return con.execute("SELECT r.* FROM task_requests l JOIN requests r ON r.id = l.request_id "
                       "WHERE l.task_id = ? ORDER BY l.rowid LIMIT 1", (tid,)).fetchone()


def set_request_body(con, rid: int, body: str) -> None:
    con.execute("UPDATE requests SET body = ? WHERE id = ?", (body, rid))


def task_body_edits(con, tid: int) -> list[dict]:
    """T689: the edits that changed T<tid>'s body, in the order they happened: {ts, by, old, new}."""
    out = []
    for r in con.execute("SELECT ts, agent, payload FROM events WHERE kind = 'edit_task' "
                         f"AND {EVENT_TASK} = ? ORDER BY id", (tid,)):
        b = (json.loads(r["payload"]) or {}).get("body")
        if isinstance(b, dict):
            out.append({"ts": r["ts"], "by": r["agent"], "old": b.get("old", ""), "new": b.get("new", "")})
    return out


def task_handoffs(con, tid: int) -> list[dict]:
    """T<tid>'s handoffs in the order they happened: {ts, from, to, note, request (the request that carried it, when recorded), verdict}
    (a verdict only on a handoff made before verdicts were removed), and `at`, the moment to
    the fraction of a second (`ts` for one made before it was recorded). What the task's page shows."""
    out = []
    for r in con.execute("SELECT ts, payload FROM events WHERE kind = 'task_handoff' "
                         f"AND {EVENT_TASK} = ? ORDER BY id", (tid,)):
        p = json.loads(r["payload"])
        out.append({"ts": r["ts"], "from": p.get("from"), "to": p.get("to"), "note": p.get("note", ""),
                    "at": p.get("at", r["ts"]), "verdict": p.get("verdict"), "request": p.get("request_id")})
    return out


def recent_handoff_targets(con, by: str, scan: int = 200) -> list[str]:
    """Who `by` last handed tasks to, newest first, each once (from its last `scan` handoffs)."""
    out: list[str] = []
    for r in con.execute("SELECT payload FROM events WHERE kind = 'task_handoff' AND agent = ? "
                         "ORDER BY id DESC LIMIT ?", (by, scan)):
        to = json.loads(r["payload"] or "{}").get("to")
        if to and to not in out:
            out.append(to)
    return out


def link_request(con, tid: int, rid: int) -> None:
    con.execute("INSERT OR IGNORE INTO task_requests(task_id, request_id) VALUES (?, ?)",
                (tid, rid))


def tasks_for_request(con, rid: int) -> list:
    """The tasks a request is linked to, oldest first."""
    return con.execute(
        "SELECT t.* FROM tasks t JOIN task_requests l ON l.task_id = t.id "
        "WHERE l.request_id = ? ORDER BY t.id", (rid,)).fetchall()


def add_block(con, tid: int, blocks_on: int) -> None:
    con.execute("INSERT OR IGNORE INTO task_blocks(task_id, blocks_on, created_at) "
                "VALUES (?, ?, ?)", (tid, blocks_on, int(time.time())))


def remove_block(con, tid: int, blocks_on: int) -> bool:
    cur = con.execute("DELETE FROM task_blocks WHERE task_id = ? AND blocks_on = ?",
                      (tid, blocks_on))
    return cur.rowcount > 0


def task_blockers(con, tid: int) -> list:
    """The tasks still blocking `tid`, whatever `task_blocks` names - a row surviving
    a blocker's own close is a bug elsewhere, not a reason to trust it here. A blocker
    that is done has finished the work: it no longer blocks (T397), and blocks again
    if it is reopened."""
    return con.execute(
        "SELECT t.* FROM tasks t JOIN task_blocks b ON b.blocks_on = t.id "
        "WHERE b.task_id = ? AND b.dormant = 0 AND t.discarded_by IS NULL AND t.done_evidence IS NULL "
        "ORDER BY t.id", (tid,)).fetchall()


def sync_self_blocks(con, tid: int, old_owner: str) -> list[tuple[int, int]]:
    """After `tid` changed owner from `old_owner` (T707): a wait that one agent now holds
    both ends of goes dormant - it stops counting, nobody else could finish it - and a
    dormant one whose ends differ again counts again. A same-owner wait added on purpose
    (both ends already one agent's) is left as it was. The (blocked, blocker) pairs that
    went dormant."""
    went = []
    for r in con.execute(
            "SELECT b.task_id, b.blocks_on, b.dormant, a.owner AS ao, c.owner AS co "
            "FROM task_blocks b JOIN tasks a ON a.id = b.task_id JOIN tasks c ON c.id = b.blocks_on "
            "WHERE b.task_id = ?1 OR b.blocks_on = ?1", (tid,)).fetchall():
        other = r["co"] if r["task_id"] == tid else r["ao"]
        if r["ao"] == r["co"] and other != old_owner:
            con.execute("UPDATE task_blocks SET dormant = 1 WHERE task_id = ? AND blocks_on = ?",
                        (r["task_id"], r["blocks_on"]))
            went.append((r["task_id"], r["blocks_on"]))
        elif r["ao"] != r["co"] and r["dormant"]:
            con.execute("UPDATE task_blocks SET dormant = 0 WHERE task_id = ? AND blocks_on = ?",
                        (r["task_id"], r["blocks_on"]))
    return went


def stamp_task_delivered(con, rid: int) -> None:
    """T720: request `rid` reached its target's session (or, for the founder, was drawn): the
    task it delivers to that same owner is delivered from now. Once - the first stamp stays."""
    con.execute(
        "UPDATE tasks SET delivered_at = ? WHERE delivered_at IS NULL AND owner = "
        "(SELECT target FROM requests WHERE id = ? AND (kind = 'task' OR (target = ? AND COALESCE(kind, '') NOT IN ('say', 'report')))) AND id IN "
        "(SELECT task_id FROM task_requests WHERE request_id = ?)", (int(time.time()), rid, HUMAN, rid))


def reparent_children(con, tid: int) -> list[int]:
    """Move `tid`'s open children up to its own parent (or to the top), and the wait `tid`
    holds on each with them (T707). The ids moved."""
    parent = con.execute("SELECT parent_task FROM tasks WHERE id = ?", (tid,)).fetchone()["parent_task"]
    kids = [k["id"] for k in open_child_tasks(con, tid)]
    for k in kids:
        con.execute("UPDATE tasks SET parent_task = ? WHERE id = ?", (parent, k))
        con.execute("DELETE FROM task_blocks WHERE task_id = ? AND blocks_on = ?", (tid, k))
        if parent is not None:
            add_block(con, parent, k)
    return kids


def blocks_on_ids(con, tid: int) -> list[int]:
    """The raw edges `tid` itself names - what cycle detection walks.
    `task_blockers` filters to the ones whose blocker is still open, because
    that is the board's question ("what am I waiting on"); a cycle is a
    question about the edges themselves, so this reads every one of them."""
    return [r["blocks_on"] for r in con.execute(
        "SELECT blocks_on FROM task_blocks WHERE task_id = ?", (tid,)).fetchall()]


def tasks_blocked_by(con, tid: int) -> list:
    """Every task with a (possibly stale) block row naming `tid`, closed or
    not - the caller decides what to do with each."""
    return con.execute(
        "SELECT task_id FROM task_blocks WHERE blocks_on = ? ORDER BY task_id",
        (tid,)).fetchall()


def open_decision_tasks_of(con, agent: str) -> int:
    """Decision tasks (owner `HUMAN`, T292 C3) `agent` opened whose linked
    request to the founder is still 'queued' - not `task_state`'s
    OPEN_STATES (T288, #1748), which counts an already-answered 'done' as
    open too."""
    return con.execute(
        "SELECT COUNT(*) FROM tasks t JOIN task_requests l ON l.task_id = t.id "
        "JOIN requests r ON r.id = l.request_id "
        "WHERE t.owner = ? AND t.opened_by = ? AND t.discarded_by IS NULL "
        "AND r.state = 'queued'", (HUMAN, agent)).fetchone()[0]


def task_requests(con, tid: int) -> list:
    """The requests linked to a task, as they are now."""
    return con.execute(
        "SELECT r.* FROM requests r JOIN task_requests l ON l.request_id = r.id "
        "WHERE l.task_id = ? ORDER BY r.id", (tid,)).fetchall()


def merge_task(con, tid: int, into: int, by: str) -> bool:
    """Discard `tid` as merged into `into`, atomically. True only if this call
    was the one that ended it."""
    cur = con.execute(
        "UPDATE tasks SET discarded_by = ?, discarded_at = ?, discard_reason = ?, merged_into = ? "
        f"WHERE id = ? AND {OPEN_SQL}",
        (by, int(time.time()), f"merged into T{into}", into, tid))
    return cur.rowcount > 0


def move_task_links(con, tid: int, into: int) -> None:
    """Hand `tid`'s linked requests, memos, sub-tasks and the edges that name it as
    a blocker to `into`. A request already linked to both stays linked once.

    T294: `tid`'s own children reparent to `into` above, so any block `tid`
    holds on one of them (the auto-block a child task always gets on its
    parent) moves to `into` too - the child still blocks whoever its parent
    now is, not a merged-away id nobody reads `task_blockers` on again."""
    con.execute("INSERT OR IGNORE INTO task_requests(task_id, request_id) "
                "SELECT ?, request_id FROM task_requests WHERE task_id = ?", (into, tid))
    con.execute("DELETE FROM task_requests WHERE task_id = ?", (tid,))
    con.execute("UPDATE tasks SET parent_task = ? WHERE parent_task = ? AND id != ?",
                (into, tid, into))
    con.execute("UPDATE OR IGNORE task_blocks SET blocks_on = ? WHERE blocks_on = ? AND task_id != ?",
                (into, tid, into))
    con.execute("DELETE FROM task_blocks WHERE blocks_on = ?", (tid,))
    con.execute("UPDATE OR IGNORE task_blocks SET task_id = ? WHERE task_id = ? AND blocks_on != ?",
                (into, tid, into))
    con.execute("DELETE FROM task_blocks WHERE task_id = ?", (tid,))
    con.execute("UPDATE task_memos SET task_id = ? WHERE task_id = ?", (into, tid))


def task_last_activity(con, tid: int) -> int:
    """The latest of the task's creation, edit, done and discard and the newest
    time on any request linked to it."""
    r = con.execute(
        "SELECT MAX(COALESCE(t.created_at, 0), COALESCE(t.edited_at, 0), "
        "COALESCE(t.done_at, 0), COALESCE(t.discarded_at, 0), "
        "COALESCE((SELECT MAX(MAX(q.ts), COALESCE(MAX(q.resolved_at), 0)) FROM requests q "
        "JOIN task_requests l ON l.request_id = q.id WHERE l.task_id = t.id), 0)) "
        "FROM tasks t WHERE t.id = ?", (tid,)).fetchone()
    return int(r[0] or 0)


DECISION_CARD_SQL = ("SELECT t.id FROM tasks t WHERE t.owner = ? "
                     "AND t.discarded_by IS NULL AND t.done_evidence IS NULL AND "
                     "(SELECT MIN(request_id) FROM task_requests WHERE task_id = t.id) = ?")


# The requests that are the card of an open task of the founder's (`?` is HUMAN): what
# the founder is being asked to decide, whatever kind the card row is.
OPEN_DECISION_CARDS_SQL = ("(SELECT MIN(l.request_id) FROM task_requests l JOIN tasks t ON t.id = l.task_id "
                           "WHERE t.owner = ? AND t.discarded_by IS NULL AND t.done_evidence IS NULL "
                           "GROUP BY t.id)")


def open_decision_card_ids(con, agent: str | None = None, prefix: str | None = None,
                           body: str | None = None) -> list[int]:
    """The queued cards of the founder's open decision tasks, oldest first - about
    `agent` (its requester) when given, whose body starts with `prefix` when given, and
    is exactly `body` when given."""
    sql = ("SELECT id FROM requests WHERE target = ? AND state = 'queued' "
           f"AND id IN {OPEN_DECISION_CARDS_SQL}")
    args: list = [HUMAN, HUMAN]
    if agent is not None:
        sql += " AND requester = ?"
        args.append(agent)
    if prefix is not None:
        sql += " AND substr(body, 1, ?) = ?"
        args += [len(prefix), prefix]
    if body is not None:
        sql += " AND body = ?"
        args.append(body)
    return [r[0] for r in con.execute(sql + " ORDER BY id", args)]


def open_decisions_of(con, agent: str) -> int:
    """Decisions raised about this agent that are still waiting on the founder."""
    return len(open_decision_card_ids(con, agent))


def open_decision_ids(con, agent: str, prefix: str) -> list[int]:
    """The still-waiting decision cards about this agent whose body starts with `prefix`."""
    return open_decision_card_ids(con, agent, prefix)


def decision_task_of_card(con, rid: int) -> int | None:
    """The open task of the founder's whose card is request `rid`: the request
    `task_new` opened for it - the first linked, never one linked later (a side
    question about the task is not the task). What the task expects does not
    matter: the founder's reply in its card is their answer to it."""
    row = con.execute(DECISION_CARD_SQL, (HUMAN, rid)).fetchone()
    return int(row["id"]) if row else None


def decision_card_of(con, tid: int):
    """The request that is task `tid`'s card, whatever its state, or None."""
    return con.execute(
        "SELECT r.* FROM requests r WHERE r.id = (SELECT MIN(request_id) FROM task_requests "
        "WHERE task_id = ?)", (tid,)).fetchone()


def settle_decision_tasks(con, rid: int, state: str, verdict: str | None,
                          dismissed_by: str | None = None) -> None:
    """A request that is the card of a decision task has left 'queued': the task
    goes with it, however the request was closed. An answer is the decision -
    the task is done with it as evidence; anything else (withdrawn,
    replaced, refused, dismissed) leaves nothing to decide, so it is discarded.
    Called from every write that moves a request out of the queue, so no path
    leaves a decision on the founder's board that nothing can answer. Only the
    task's own card settles it: a question linked to a task later is left alone."""
    tid = decision_task_of_card(con, rid)
    if tid is None:
        return
    if state == "done" and verdict and dismissed_by is None:
        mark_task_done(con, tid, verdict, "reason")
    else:
        discard_task(con, tid, HUMAN, f"its request was {'dismissed' if dismissed_by else state}"
                     + (f": {verdict}" if verdict and not dismissed_by else ""))
    add_event(con, HUMAN, "settle_decision_task", {"task_id": tid, "request_id": rid, "state": state})
