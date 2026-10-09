"""task.new, task.edit, task.withdraw, task.cancel."""
from . import CURRENT, err, fake


@fake("task.new", "task.edit", "task.withdraw", "task.cancel")
def tasks(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if params.get("waits_on") is not None:
        return "fail", err(-32602, "bad_request", field="waits_on")
    if params.get("priority") is not None and (isinstance(params["priority"], bool) or params["priority"] not in (1, 2, 3)):
        return "fail", err(-32602, "bad_request", field="priority")
    if method == "task.new":
        if role != "agent" and params.get("owner") == "human":
            return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "reason": "for an agent, not for themself"}}
        if params.get("time_box") not in ("30m", "1h", "2h"):
            return "fail", err(-32602, "bad_request", field="time_box")
        CURRENT.ctx.state["task_page"] = True
        return "ok", {"accepted": True, "said": "opened", "task": 1}
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "task"}}
