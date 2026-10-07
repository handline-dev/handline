"""daemon.status, tasks.list, task.get, inbox.list."""
from . import err, fake, task_page


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
    if method == "tasks.list" and task_page.opened():
        return "ok", {"items": [task_page.row()], "more": False}
    if method == "inbox.list":          # past the bounds the daemon's own branch answers: its filters and its wait
        return None
    return "ok", {"items": [], "more": False}
