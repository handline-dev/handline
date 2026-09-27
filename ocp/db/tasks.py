"""Tasks, their links to requests, and what blocks what."""
from __future__ import annotations

import time


# RULE: a task row holds these fields and nothing else. Never a copy of a
# request's body, a state, a date a request already carries, or a summary of
# an answer - the board reads those from `requests` each time it draws, so
# there is one place each fact lives and nothing to drift. Adding a column to
# `tasks` means adding it here, and a test fails until both agree.
TASK_FIELDS = ("id", "title", "owner", "parent_task", "opened_by",
               "done_evidence", "done_at", "done_via",
               "edited_by", "edited_at", "created_at", "closed_by", "closed_at",
               "discarded_by", "discard_reason", "priority", "merged_into",
               "queued_by", "queued_at", "expects")

DEFAULT_PRIORITY = 2


def new_task(con, title: str, owner: str, opened_by: str,
             parent_task: int | None = None, priority: int = DEFAULT_PRIORITY,
             expects: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO tasks(title, owner, parent_task, opened_by, created_at, priority, expects) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, owner, parent_task, opened_by, int(time.time()), priority, expects))
    return int(cur.lastrowid)


def get_task(con, tid: int):
    return con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()


def open_tasks(con) -> list:
    return con.execute(
        "SELECT * FROM tasks WHERE closed_by IS NULL ORDER BY priority, id").fetchall()


def all_tasks(con) -> list:
    return con.execute("SELECT * FROM tasks ORDER BY priority, id").fetchall()


def open_child_tasks(con, tid: int) -> list:
    return con.execute(
        "SELECT * FROM tasks WHERE parent_task = ? AND closed_by IS NULL ORDER BY priority, id",
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
        "WHERE id = ? AND done_evidence IS NULL AND closed_by IS NULL",
        (evidence, int(time.time()), via, tid))
    return cur.rowcount > 0


def close_task(con, tid: int, closed_by: str) -> bool:
    """Close a task, atomically - the asker's sign-off, final. True only if
    this call was the one that closed it. `closed_by` is an identity, never
    evidence (that is done_evidence, written earlier by mark_task_done)."""
    cur = con.execute(
        "UPDATE tasks SET closed_by = ?, closed_at = ? WHERE id = ? AND closed_by IS NULL",
        (closed_by, int(time.time()), tid))
    return cur.rowcount > 0


def discard_task(con, tid: int, by: str, reason: str) -> bool:
    """Discard a task that is not done, atomically. True only if this call
    was the one that ended it. Written as a close too (see `tasks`), so it
    leaves every open set the same way a close does."""
    cur = con.execute(
        "UPDATE tasks SET closed_by = ?, closed_at = ?, discarded_by = ?, "
        "discard_reason = ? WHERE id = ? AND closed_by IS NULL AND done_evidence IS NULL",
        (by, int(time.time()), by, reason, tid))
    return cur.rowcount > 0


def set_task_queued(con, tid: int, by: str | None) -> bool:
    """Approve a task to run (`by` set) or send it back to draft (None),
    atomically. True only if it changed: a task already ended, or already in
    that state, matches no row."""
    cur = con.execute(
        "UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND closed_by IS NULL "
        "AND done_evidence IS NULL AND queued_by IS " + ("NULL" if by else "NOT NULL"),
        (by, int(time.time()) if by else None, tid))
    return cur.rowcount > 0


def reopen_task(con, tid: int) -> bool:
    """Send a done task back to doing, atomically - the opposite of marking
    it done. True only if this call was the one that cleared it. Refuses (by
    matching no row) a task that is not currently done, or one already
    closed - closing is final."""
    cur = con.execute(
        "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL "
        "WHERE id = ? AND done_evidence IS NOT NULL AND closed_by IS NULL",
        (tid,))
    return cur.rowcount > 0


def tasks_needing_close(con) -> list:
    """Done tasks not yet closed - #71's 'done - waiting for check' group."""
    return con.execute(
        "SELECT * FROM tasks WHERE done_evidence IS NOT NULL AND closed_by IS NULL "
        "ORDER BY priority, id").fetchall()


def edit_task(con, tid: int, by: str, owner: str | None = None,
              title: str | None = None, priority: int | None = None) -> None:
    """Write a changed owner and/or title, stamped with who and when - the
    same evidence close_task leaves on closed_by/closed_at. Caller has
    already checked permission and that something actually changed; this
    only writes."""
    sets = ["edited_by = ?", "edited_at = ?"]
    params: list = [by, int(time.time())]
    if owner is not None:
        sets.append("owner = ?")
        params.append(owner)
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if priority is not None:
        sets.append("priority = ?")
        params.append(priority)
    params.append(tid)
    con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)


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
    """The still-open tasks blocking `tid`, whatever `task_blocks` names -
    a row surviving a blocker's own close is a bug elsewhere, not a reason
    to trust it here."""
    return con.execute(
        "SELECT t.* FROM tasks t JOIN task_blocks b ON b.blocks_on = t.id "
        "WHERE b.task_id = ? AND t.closed_by IS NULL ORDER BY t.id", (tid,)).fetchall()


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


def task_requests(con, tid: int) -> list:
    """The requests linked to a task, as they are now."""
    return con.execute(
        "SELECT r.* FROM requests r JOIN task_requests l ON l.request_id = r.id "
        "WHERE l.task_id = ? ORDER BY r.id", (tid,)).fetchall()


def merge_task(con, tid: int, into: int, by: str) -> bool:
    """Close `tid` as merged into `into`, atomically. True only if this call
    was the one that ended it."""
    cur = con.execute(
        "UPDATE tasks SET closed_by = ?, closed_at = ?, merged_into = ? "
        "WHERE id = ? AND closed_by IS NULL AND done_evidence IS NULL",
        (by, int(time.time()), into, tid))
    return cur.rowcount > 0


def move_task_links(con, tid: int, into: int) -> None:
    """Hand `tid`'s linked requests, sub-tasks and the edges that name it as
    a blocker to `into`. A request already linked to both stays linked once."""
    con.execute("INSERT OR IGNORE INTO task_requests(task_id, request_id) "
                "SELECT ?, request_id FROM task_requests WHERE task_id = ?", (into, tid))
    con.execute("DELETE FROM task_requests WHERE task_id = ?", (tid,))
    con.execute("UPDATE tasks SET parent_task = ? WHERE parent_task = ? AND id != ?",
                (into, tid, into))
    con.execute("UPDATE OR IGNORE task_blocks SET blocks_on = ? WHERE blocks_on = ? AND task_id != ?",
                (into, tid, into))
    con.execute("DELETE FROM task_blocks WHERE blocks_on = ?", (tid,))


def task_last_activity(con, tid: int) -> int:
    """The latest of the task's creation, edit, done and close and the newest
    time on any request linked to it."""
    r = con.execute(
        "SELECT MAX(COALESCE(t.created_at, 0), COALESCE(t.edited_at, 0), "
        "COALESCE(t.done_at, 0), COALESCE(t.closed_at, 0), "
        "COALESCE((SELECT MAX(MAX(q.ts), COALESCE(MAX(q.resolved_at), 0)) FROM requests q "
        "JOIN task_requests l ON l.request_id = q.id WHERE l.task_id = t.id), 0)) "
        "FROM tasks t WHERE t.id = ?", (tid,)).fetchone()
    return int(r[0] or 0)
