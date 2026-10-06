"""tasks.list and task.get: the board as the spec's `Task` rows and one task whole. They serve `hl task list` and
`hl task show`, and what the chat page reads of tasks with raw SQL. They wrap `actions.task_rows`, `task_state`,
`task_blockers` and the daemon's hold reasons; nothing here decides what a state is."""
from __future__ import annotations

import json

from .. import actions, db
from . import agentview, rpc, taskholds

DEFAULT_LIMIT, MAX_LIMIT = 50, 200


def _limit(params) -> int:
    v = params.get("limit", DEFAULT_LIMIT)
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_LIMIT:
        raise rpc.bad_request(f"limit is an integer from 1 to {MAX_LIMIT}", "limit", rpc.INVALID_PARAMS)
    return v


def _int(params, key):
    v = params.get(key)
    if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
        raise rpc.bad_request(f"{key} is an integer", key, rpc.INVALID_PARAMS)
    return v


def _str(params, key):
    v = params.get(key)
    if v is not None and not isinstance(v, str):
        raise rpc.bad_request(f"{key} is a string", key, rpc.INVALID_PARAMS)
    return v


def _flag(params, key) -> bool:
    v = params.get(key, False)
    if not isinstance(v, bool):
        raise rpc.bad_request(f"{key} is true or false", key, rpc.INVALID_PARAMS)
    return v


def _duration(secs: int) -> str:
    """The spec's `Duration` in the largest whole unit: 2h, 90m, 1d."""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def _first_request(con, tid: int):
    return db.first_linked_request(con, tid)


def task_of(con, row, state: str) -> dict:
    """The spec's `Task`: the row as the board shows it, without its body."""
    first = _first_request(con, row["id"])
    blockers = db.task_blockers(con, row["id"])
    return {
        "id": row["id"], "from": row["opened_by"], "to": row["owner"], "title": row["title"], "state": state,
        "relation": (first["relation"] if first is not None else None) or "",
        "response": (first["response"] if first is not None else None) or "",
        "time_box": _duration(row["timeout_s"]) if row["timeout_s"] else None,
        "parent": row["parent_task"], "created": agentview._ts(row["created_at"]),
        "delivered": agentview._ts(row["delivered_at"]), "done": agentview._ts(row["done_at"]),
        "hold_reason": taskholds.wait_line(con, row, state),
        "block_reason": ("waiting on " + ", ".join(f"T{b['id']}" for b in blockers)) if blockers else None,
        "evidence": row["done_evidence"],
    }


def _scope(ctx, params) -> set[str] | None:
    """The owners whose tasks this call shows, as `hl task list` scopes them: an agent sees itself and its reports,
    unless it asks for everyone's; a person sees all."""
    everyone = _flag(params, "everyone")
    return None if everyone or ctx.caller.role != "agent" else actions.reach(ctx.caller.info["name"])


def _tasks_list(ctx, params) -> dict:
    owner, opener, state = _str(params, "owner"), _str(params, "from"), _str(params, "state")
    parent, before, limit, ended = _int(params, "parent"), _int(params, "before"), _limit(params), _flag(params, "all")
    owners = _scope(ctx, params)
    if owner is not None and owners is not None and owner not in owners:
        raise rpc.refused(f"{owner} is not you or one of your reports: everyone shows every team's tasks")
    con = db.connect()
    try:
        rows = actions.task_rows(con, owner=owner, state=state, include_ended=ended, owners=owners)
        rows = [r for r in rows
                if (opener is None or r["opened_by"] == opener)
                and (parent is None or r["parent_task"] == parent)
                and (before is None or r["id"] < before)]
        rows.sort(key=lambda r: -r["id"])
        page = rows[:limit]
        return {"items": [task_of(con, r["row"], r["state"]) for r in page], "more": len(rows) > limit}
    finally:
        con.close()


def _delivery(r) -> dict:
    return {"at": agentview._ts(r["delivered_at"] or r["ts"]), "how": r["kind"] or "request", "outcome": r["state"]}


def _event(row, tid: int, owner: str) -> dict:
    """A recorded event of the task as the spec's `TaskEvent`: an `Event` without a `cursor`, since it is not on the daemon's stream."""
    p = json.loads(row["payload"]) if row["payload"] else {}
    kind = "user" if row["agent"] == db.HUMAN else "agent"
    return {"topic": "tasks", "type": row["kind"], "at": agentview._ts(row["ts"]),
            "actor": {"kind": kind, "name": row["agent"]}, "agent": owner, "title": None,
            "target": {"screen": "tasks", "id": str(tid)}, "data": p if isinstance(p, dict) else {}}


def memo_of(m) -> dict:
    return {"id": m["id"], "task": m["task_id"], "author": m["author"], "at": agentview._ts(m["created_at"]), "text": m["body"]}


def _task_get(ctx, params) -> dict:
    tid = _int(params, "task")
    if tid is None:
        raise rpc.bad_request("task is a task number", "task", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        row = db.get_task(con, tid)
        if row is None:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no task T{tid}", what="task", id=tid)
        task = task_of(con, row, actions.task_state(con, row))
        first = _first_request(con, tid)
        events = con.execute(f"SELECT id, ts, agent, kind, payload FROM events WHERE {db.EVENT_TASK} = ? ORDER BY id", (tid,)).fetchall()
        return {**task, "body": (first["body"] if first is not None else "") or "",
                "deliveries": [_delivery(r) for r in db.task_requests(con, tid)],
                "events": [_event(e, tid, row["owner"]) for e in events],
                "memos": [memo_of(m) for m in db.task_memos(con, tid)],
                "blockers": [{"task": b["id"], "reason": f"T{b['id']} ({b['owner']}) {b['title']}"} for b in db.task_blockers(con, tid)],
                "children": [r["id"] for r in con.execute("SELECT id FROM tasks WHERE parent_task = ? ORDER BY id", (tid,))],
                "handoffs": [{"at": agentview._ts(h["ts"]), "from": h["from"], "to": h["to"], "note": h["note"]}
                             for h in db.task_handoffs(con, tid)]}
    finally:
        con.close()


rpc.METHODS["tasks.list"] = rpc.Method(_tasks_list, "agent")
rpc.METHODS["task.get"] = rpc.Method(_task_get, "agent")
