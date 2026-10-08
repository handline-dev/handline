"""What a chat is made of, read from the requests: the page of one agent's chat, where a message sits, and the one number that says
anything in it changed. The agent chat screen draws from these and so does the daemon's chat.* methods."""
from __future__ import annotations

from typing import NamedTuple

from .. import db
from . import tasks as _tasks
from .routing import HUMAN

# States of a request that never reached its target: withdrawn, replaced, denied.
# They are not part of the conversation, so no chat query reads them.
_GONE = "(" + ", ".join(repr(x) for x in db.NEVER_REACHED_TARGET) + ")"

# Requests read per page. The newest page is what opens; older ones are read when the
# chat is scrolled to within a screen of its top. Small, because drawing what is not in
# view is most of what a page costs.
PAGE = 10


def _scope(agent: str, everyone: bool) -> tuple[str, tuple]:
    """Which requests a chat is made of: the ones between the human and `agent`,
    or, for `everyone`, every one `agent` sent or received."""
    human = HUMAN
    if everyone:
        return "(requester = ? OR target = ?)", (agent, agent)
    return ("((requester = ? AND target = ?) OR (requester = ? AND target = ?))",
            (human, agent, agent, human))


_SELECT = (f"SELECT id, ts, state_at, requester, target, state, body, {db.ANSWER_SQL}, "
           f"{db.DISMISSED_SQL}, delivered_at, typed_at, landed_at, parent_id, kind, settles, "
           "(SELECT group_concat(task_id) FROM task_requests WHERE request_id = requests.id) AS tasks, "
           "(SELECT t.title FROM tasks t JOIN task_requests q ON q.task_id = t.id "
           "WHERE q.request_id = requests.id ORDER BY t.id LIMIT 1) AS task_title, "
           "(SELECT MIN(task_id) FROM task_requests WHERE request_id = requests.id) AS task_id, "
           "(SELECT a.id FROM requests a WHERE a.settles = requests.id "
           "ORDER BY a.id LIMIT 1) AS answer_id, "
           # the founder's question on a task: drawn on that task's page, only its answer is a message here
           "EXISTS (SELECT 1 FROM events WHERE dedup_key = 'task_question:' || requests.id) AS task_question "
           "FROM requests")


def request_row(con, rid: int) -> dict | None:
    """One request as a page of a chat reads it, whether or not any chat draws it."""
    rows = _with_task_state(con, con.execute(_SELECT + " WHERE id = ?", (rid,)).fetchall())
    return rows[0] if rows else None


def requests_page(con, agent: str, everyone: bool, before: int | None = None,
                  since: int | None = None, limit: int = PAGE) -> tuple[list, bool]:
    """Requests of a chat, oldest first, and whether older ones remain.
    `before` reads the `limit` requests just older than that id (the newest
    `limit` when None); `since` reads every request from that id on, which is
    how a window already on screen is brought up to date."""
    where, params = _scope(agent, everyone)
    sql = f"{_SELECT} WHERE state NOT IN {_GONE} AND settles IS NULL AND {where}"
    if since is not None:
        rows = con.execute(sql + " AND id >= ? ORDER BY id", (*params, since)).fetchall()
        return _with_task_state(con, rows), False
    if before is not None:
        sql += " AND id < ?"
        params = (*params, before)
    rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    return _with_task_state(con, rows[:limit][::-1]), len(rows) > limit


def requests_after(con, agent: str, everyone: bool, after: int, limit: int) -> tuple[list, bool]:
    """The `limit` requests of a chat just newer than `after`, oldest first, and whether newer ones remain."""
    where, params = _scope(agent, everyone)
    rows = con.execute(f"{_SELECT} WHERE state NOT IN {_GONE} AND settles IS NULL AND {where} AND id > ? ORDER BY id LIMIT ?",
                       (*params, after, limit + 1)).fetchall()
    return _with_task_state(con, rows[:limit]), len(rows) > limit


class Located(NamedTuple):
    """Where a message number lives: `here` (this chat, `tab` says which of its two), in the `chat`
    of `agent` on `tab`, or `alone` (no chat shows it). `request` is the request it is part of."""
    kind: str
    request: int = 0
    agent: str = ""
    tab: str = "mine"


def locate_message(con, mid: int, agent: str, everyone: bool) -> Located:
    """The chat `mid` (a request's number, or the number of the answer that settles one) is drawn in:
    this one when its two ends are the founder and `agent` (or, in the all-chat, `agent` and anyone),
    else the all-chat of this agent when it is one end, else the chat of an agent at either end - the
    founder's own chat with it when the other end is the founder. One no chat draws (a withdrawn or
    never-reached request) is `alone`."""
    row = con.execute("SELECT id, requester, target, state, settles FROM requests WHERE id = ?", (mid,)).fetchone()
    if row is None:
        return Located("alone")
    req = row["settles"] or row["id"]
    if row["settles"]:
        row = con.execute("SELECT id, requester, target, state FROM requests WHERE id = ?", (req,)).fetchone()
        if row is None:
            return Located("alone")
    if row["state"] in db.NEVER_REACHED_TARGET:
        return Located("alone", req)
    ends = (row["requester"], row["target"])
    human = HUMAN
    founders = human in ends
    if agent in ends:      # the all-chat holds the founder's messages too, so it is not left for them
        return Located("here", req, agent, "all" if everyone or not founders else "mine")
    elsewhere = [n for n in ends if n != human]
    if not elsewhere:
        return Located("alone", req)
    return Located("chat", req, elsewhere[0], "mine" if founders else "all")


def page_back_to(con, agent: str, everyone: bool, want: int | None, limit: int = PAGE) -> tuple[list, bool]:
    """The newest page of a chat and, while `want` (a request number) is older than what it holds,
    the pages before it: the rows to draw with that request among them, and whether older ones remain."""
    rows, more = requests_page(con, agent, everyone, limit=limit)
    while want and more and rows and rows[0]["id"] > want:
        older, more = requests_page(con, agent, everyone, before=rows[0]["id"], limit=limit)
        rows = older + rows
    return rows, more


def _with_task_state(con, rows) -> list[dict]:
    """The rows as dicts; a task delivery (the only row drawn as a card) also gets the state of
    its task, read live, not stored."""
    out = []
    for r in rows:
        d = dict(r)
        t = con.execute("SELECT * FROM tasks WHERE id = ?", (d["task_id"],)).fetchone() if d["task_id"] and d["kind"] == "task" else None
        d["task_state"] = _tasks.task_state(con, t) if t else None
        out.append(d)
    return out
