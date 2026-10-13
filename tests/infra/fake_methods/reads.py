"""daemon.status, tasks.list, task.get, inbox.list."""
from . import err, fake, task_page


TS = "2026-01-01T00:00:00Z"


def _task(id, owner, state, **kw) -> dict:
    return {"id": id, "from": "a.one", "to": owner, "title": f"task {id}", "state": state, "relation": "", "response": "", "time_box": "1h",
            "parent": None, "created": TS, "delivered": None, "done": None, "hold_reason": None, "block_reason": None, "evidence": None,
            "blocked_by": [], "time": {"length_s": 3600, "default": False, "start": TS, "end": TS, "overdue_since": None, "told": 0, "left_s": None}, **kw}


# The owners the tasks_list_fields fixtures name (--var tasks_*_owner) and the tasks each owns.
LIST_FIELDS = {
    "a.t.blocked": [_task(11, "a.t.blocked", "doing"),
                    _task(12, "a.t.blocked", "queued", blocked_by=[11], block_reason="waiting on its blocker",
                          time={"length_s": 3600, "default": True, "start": None, "end": None, "overdue_since": None, "told": 0, "left_s": None})],
    "a.t.timed": [_task(21, "a.t.timed", "doing")],
    "a.t.overdue": [_task(31, "a.t.overdue", "doing", time={"length_s": 3600, "default": False, "start": TS, "end": TS, "overdue_since": TS, "told": 1, "left_s": None})],
    "a.t.paused": [_task(51, "a.t.paused", "doing", blocked_by=[11], block_reason="waiting on its blocker",
                         time={"length_s": 3600, "default": False, "start": None, "end": None, "overdue_since": None, "told": 0, "left_s": 1200})],
    "a.t.done": [_task(41, "a.t.done", "done", done=TS, time=None)],
}


def _list_fields(params) -> dict:
    rows = [r for r in LIST_FIELDS.get(params.get("owner"), []) if params.get("all") is True or r["state"] not in ("done", "discarded")]
    rows = sorted((r for r in rows if "before" not in params or r["id"] < params["before"]), key=lambda r: -r["id"])
    limit = params.get("limit", 50)
    return {"items": rows[:limit], "more": len(rows) > limit}


@fake("daemon.status", "tasks.list", "task.get", "inbox.list")
def reads(role, method, params):
    for k in ("limit", "wait_s"):
        if k in params and (isinstance(params[k], bool) or not isinstance(params[k], int) or params[k] < (0 if k == "wait_s" else 1)
                            or (k == "wait_s" and params[k] > 600)):
            return "fail", err(-32602, "bad_request", field=k)
    if method == "daemon.status":
        return "ok", {"alive": True, "version": "fake", "protocol": 1, "schema": 1, "started_at": "2026-01-01T00:00:00Z",
                      "terminal_host": {"name": "fake", "health": "ok"}, "agents": {"total": 0, "by_state": {}},
                      "inbox": {"unread": 0}}
    if method == "task.get":
        if isinstance(params.get("task"), bool) or not isinstance(params.get("task"), int):
            return "fail", err(-32602, "bad_request", field="task")
        if params["task"] == 1 and task_page.opened():
            return "ok", task_page.detail()
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "task"}}
    if method == "tasks.list" and params.get("owner") in LIST_FIELDS:
        return "ok", _list_fields(params)
    if method == "tasks.list" and task_page.opened():
        return "ok", {"items": [task_page.row()], "more": False}
    if method == "inbox.list":          # past the bounds the daemon's own branch answers: its filters and its wait
        return None
    return "ok", {"items": [], "more": False}
