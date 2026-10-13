"""tasks.list and task.get: the board as the spec's `Task` rows and one task whole. They serve `hl task list` and
`hl task show`, and what the chat page reads of tasks with raw SQL. They wrap `actions.task_rows`, `task_state`,
`task_blockers` and the daemon's hold reasons; nothing here decides what a state is."""
from __future__ import annotations

import json
import time

from .. import actions, db
from ..infra import paths, textsafe
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


def _last_activity(con, row) -> int:
    """The newest of the task's creation, edit, done and discard and the newest time on a request linked to it."""
    linked = con.execute(f"SELECT MAX(COALESCE(r.ts, 0), COALESCE({db.touched_sql('r')}, 0)) FROM task_requests l JOIN requests r "
                         "ON r.id = l.request_id WHERE l.task_id = ?", (row["id"],)).fetchall()
    return int(max([row["created_at"] or 0, row["edited_at"] or 0, row["done_at"] or 0, row["discarded_at"] or 0,
                    *(v[0] or 0 for v in linked)]))


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
        "blocked_by": [b["id"] for b in blockers], "time": _clock(con, row, state),
        "evidence": row["done_evidence"], "priority": row["priority"],
        "last_activity": agentview._ts(_last_activity(con, row)),
        "edited_by": row["edited_by"], "asker": actions.task_asker(con, row),
        "discarded": ({"by": row["discarded_by"], "reason": row["discard_reason"] or "", "at": agentview._ts(row["discarded_at"])}
                      if row["discarded_by"] else None),
        "report": ({"by": row["owner"], "evidence": row["report"], "at": agentview._ts(row["reported_at"])}
                   if state == "reported" else None),
        "mismatch": actions.task_mismatch(con, row, state),
        "requests": [{"id": r["id"], "from": r["requester"], "to": r["target"], "state": r["state"], "at": agentview._ts(r["ts"])}
                     for r in db.task_requests(con, row["id"])],
    }


def _scope(ctx, params) -> set[str] | None:
    """The names whose tasks this call shows, as assignee or owner, as `hl task list` scopes them: an agent sees itself
    and its reports, unless it asks for everyone's; a person sees all."""
    everyone = _flag(params, "everyone")
    return None if everyone or ctx.caller.role != "agent" else actions.reach(ctx.caller.info["name"])


def _tasks_list(ctx, params) -> dict:
    owner, closer, opener, state = _str(params, "owner"), _str(params, "reporter"), _str(params, "from"), _str(params, "state")
    parent, before, limit, ended = _int(params, "parent"), _int(params, "before"), _limit(params), _flag(params, "all")
    gaps = _flag(params, "reason_gaps")
    owners = _scope(ctx, params)
    for name in (owner, closer):
        if name is not None and owners is not None and name not in owners:
            raise rpc.refused(f"{name} is not you or one of your reports: everyone shows every team's tasks")
    con = db.connect()
    try:
        rows = actions.task_rows(con, owner=owner, state=state, include_ended=ended, owners=owners, closer=closer)
        rows = [r for r in rows
                if (opener is None or r["opened_by"] == opener)
                and (parent is None or r["parent_task"] == parent)
                and (before is None or r["id"] < before)]
        rows.sort(key=lambda r: -r["id"])
        page = rows[:limit]
        out = {"items": [task_of(con, r["row"], r["state"]) for r in page], "more": len(rows) > limit}
        if gaps:        # only when asked: it counts a month of requests, seconds on a large database
            out["reason_gaps"] = actions.reason_gaps(con)
        return out
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
    return {"id": m["id"], "task": m["task_id"], "author": m["author"], "at": agentview._ts(m["created_at"]), "text": m["body"],
            "edited_at": agentview._ts(m["edited_at"]) if m["edited_at"] else None}


def _clock(con, row, state: str) -> dict | None:
    """The task's time box as the spec's `time`; None once it is over. The overdue moment is the first missed deadline."""
    if state in ("done", "discarded"):
        return None
    now = time.time()
    t = actions.task_timeout(con, row, state, now)
    return {"length_s": t["length"], "default": t["default"], "start": agentview._ts(t["start"]), "end": agentview._ts(t["end"]),
            "overdue_since": None if t["over"] is None else agentview._ts(now - t["over"]), "told": t["told"],
            "left_s": row["timeout_left"] if row["timeout_at"] is None else None}


def _timeline(con, tid: int) -> list[dict]:
    return [{"at": agentview._ts(e["at"]), "who": e["who"], "kind": e["kind"], "text": textsafe.text(e["text"] or ""),
             "memo": e["memo"], "edited_at": agentview._ts(e["edited_at"])} for e in actions.task_timeline(con, tid)]


def _detail(con, row, state: str) -> dict:
    parent = db.get_task(con, row["parent_task"]) if row["parent_task"] else None
    kids = con.execute("SELECT * FROM tasks WHERE parent_task = ? ORDER BY id", (row["id"],)).fetchall()
    recent, team, others = actions.question_targets(row["owner"], paths.list_agents(), actions.recent_chatted(con))
    return {"asker": actions.task_asker(con, row),
            "discarded": ({"by": row["discarded_by"], "reason": row["discard_reason"] or "", "at": agentview._ts(row["discarded_at"])}
                          if row["discarded_by"] else None),
            "merged_into": row["merged_into"],
            "parent_title": parent["title"] if parent else None,
            "subtasks": [{"id": k["id"], "title": k["title"], "owner": k["owner"], "state": actions.task_state(con, k)} for k in kids],
            "timeline": _timeline(con, row["id"]), "people": {"recent": recent, "team": team, "others": others}}


def _assignments(events) -> list[dict]:
    """Each change of who works the task (the owner column) or who answers for it (the reporter) an edit made, oldest first."""
    out = []
    for e in events:
        if e["kind"] != "edit_task":
            continue
        try:
            p = json.loads(e["payload"] or "{}")
        except ValueError:
            continue
        for key, what in (("owner", "assignee"), ("reporter", "owner")):
            if isinstance(p.get(key), dict):
                out.append({"at": agentview._ts(e["ts"]), "by": e["agent"], "what": what, "from": p[key].get("old"), "to": p[key].get("new")})
    return out


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
        return {**task, **_detail(con, row, task["state"]), "body": (first["body"] if first is not None else "") or "",
                "deliveries": [_delivery(r) for r in db.task_requests(con, tid)],
                "events": [_event(e, tid, row["owner"]) for e in events],
                "memos": [memo_of(m) for m in db.task_memos(con, tid)],
                "blockers": [{"task": b["id"], "reason": f"T{b['id']} ({b['owner']}) {b['title']}", "owner": b["owner"], "title": b["title"]}
                             for b in db.task_blockers(con, tid)],
                "children": [r["id"] for r in con.execute("SELECT id FROM tasks WHERE parent_task = ? ORDER BY id", (tid,))],
                "handoffs": [{"at": agentview._ts(h["ts"]), "from": h["from"], "to": h["to"], "note": h["note"]}
                             for h in db.task_handoffs(con, tid)],
                "assignments": _assignments(events)}
    finally:
        con.close()


def _now(con, t, me: str) -> dict:
    """Who has the task now, as `hl task board` reads it from the linked requests (cmd_task_board)."""
    reqs = db.task_requests(con, t["id"])
    held = sorted({r["target"] for r in reqs if actions._asks(r)})
    kids = sorted({r["target"] for c in db.open_child_tasks(con, t["id"]) for r in db.task_requests(con, c["id"]) if actions._asks(r)})
    last = reqs[-1] if reqs else None
    back = last is not None and last["state"] == "landed" and last["target"] not in (me, db.HUMAN)
    blockers = db.task_blockers(con, t["id"])
    linked = {r["id"] for r in reqs}
    holds = [c for c in db.open_child_tasks(con, t["id"]) if linked & {r["id"] for r in db.task_requests(con, c["id"])}]
    if blockers:
        return {"kind": "waiting", "agents": [], "tasks": [b["id"] for b in blockers]}
    if held:
        return {"kind": "held", "agents": held, "tasks": []}
    if kids:
        return {"kind": "sub_task", "agents": kids, "tasks": []}
    if holds:
        return {"kind": "held_as_sub_task", "agents": [c["owner"] for c in holds], "tasks": [c["id"] for c in holds]}
    return {"kind": "result_in" if back else "nobody", "agents": [], "tasks": []}


def _board(ctx, params) -> dict:
    """The open tasks the caller works, or owns while someone outside its reports works them (`mine`), and those its reports
    work (`reports`), each with who holds it now. The founder's board is the tasks assigned to it, unless it asks for
    everyone's: every task begun on its word answers to it."""
    me, everyone = ctx.caller.actor, _flag(params, "everyone")
    owners = None if everyone else {me} if me == db.HUMAN else actions.reach(me)
    con = db.connect()
    try:
        out = {"mine": [], "reports": []}
        for t in db.open_tasks(con):
            reach = owners is None or t["owner"] in owners
            if not reach and (me == db.HUMAN or actions.task_asker(con, t) not in owners):
                continue
            row = {"task": task_of(con, t, actions.task_state(con, t)), "holder": None, "now": _now(con, t, me)}
            row["holder"] = (row["now"]["agents"] or [None])[0]
            out["mine" if t["owner"] == me or not reach else "reports"].append(row)
        return out
    finally:
        con.close()


rpc.METHODS["task.board"] = rpc.Method(_board, "agent")
rpc.METHODS["tasks.list"] = rpc.Method(_tasks_list, "agent")
rpc.METHODS["task.get"] = rpc.Method(_task_get, "agent")
