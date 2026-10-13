"""task.begin, task.start, task.handoff, task.progress: the refusals the fixtures pin."""
from . import CURRENT, err, fake


@fake("task.begin", "task.start", "task.handoff", "task.progress")
def taskverbs(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "task.begin":
        if params.get("why") is not None and not isinstance(params["why"], str):
            return "fail", err(-32602, "bad_request", field="why")
        if params.get("body") is not None:
            return "fail", err(-32602, "bad_request", field="body")
        if role != "agent":
            return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "reason": "not an agent"}}
        return "ok", {"accepted": True, "said": "begun", "task": 1}
    if method == "task.progress" and not isinstance(params.get("note"), str):
        return "fail", err(-32602, "bad_request", field="note")
    if method == "task.progress" and params.get("task") is not None and (isinstance(params["task"], bool) or not isinstance(params["task"], int)):
        return "fail", err(-32602, "bad_request", field="task")
    if method == "task.progress" and params.get("task") is None:
        CURRENT.ctx.state["progress"] = params["note"] or None
        return "ok", {"accepted": True, "said": "progress kept"}
    if method == "task.handoff":
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent"}}
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "task"}}


@fake("progress.get")
def progress_get(role, method, params):
    return "ok", {"note": CURRENT.ctx.state.get("progress")}
