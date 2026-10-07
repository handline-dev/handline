"""task.done, reopen, discard, merge, queue, draft and release: the refusals the fixtures pin; no task exists here."""
from . import err, fake


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ints(v):
    return isinstance(v, list) and bool(v) and all(_is_int(i) for i in v)


@fake("task.done", "task.reopen", "task.discard", "task.merge", "task.queue", "task.draft", "task.release")
def task_moves(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method in ("task.queue", "task.draft"):
        if not _ints(params.get("tasks")):
            return "fail", err(-32602, "bad_request", field="tasks")
    elif method == "task.merge":
        if not _is_int(params.get("into")):
            return "fail", err(-32602, "bad_request", field="into")
        if not _ints(params.get("tasks")):
            return "fail", err(-32602, "bad_request", field="tasks")
    elif not _is_int(params.get("task")):
        return "fail", err(-32602, "bad_request", field="task")
    elif method == "task.done":
        evidence = params.get("evidence")
        if evidence is not None and not (isinstance(evidence, str) and evidence.strip()):
            return "fail", err(-32602, "bad_request", field="evidence")
        if (evidence is None) == (params.get("request") is None):
            return "fail", err(-32602, "bad_request", field="evidence")
    elif method == "task.discard" and not (isinstance(params.get("reason"), str) and params["reason"].strip()):
        return "fail", err(-32602, "bad_request", field="reason")
    elif method == "task.reopen" and params.get("note") is not None and not isinstance(params["note"], str):
        return "fail", err(-32602, "bad_request", field="note")
    elif method == "task.release":
        for k in ("to", "reason"):
            if params.get(k) is not None:
                return "fail", err(-32602, "bad_request", field=k)
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "task"}}
