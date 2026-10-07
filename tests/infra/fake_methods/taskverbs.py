"""task.begin, task.start, task.handoff, task.progress: the refusals the fixtures pin."""
from . import err, fake


@fake("task.begin", "task.start", "task.handoff", "task.progress")
def taskverbs(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "task.begin":
        if not isinstance(params.get("why"), str) or not params["why"].strip():
            return "fail", err(-32602, "bad_request", field="why")
        if params.get("body") is not None:
            return "fail", err(-32602, "bad_request", field="body")
        if role != "agent":
            return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "reason": "not an agent"}}
        return "ok", {"accepted": True, "said": "begun", "task": 1}
    if method == "task.progress" and not isinstance(params.get("note"), str):
        return "fail", err(-32602, "bad_request", field="note")
    if method == "task.handoff":
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent"}}
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "task"}}


@fake("progress.get")
def progress_get(role, method, params):
    return "ok", {"note": None}
