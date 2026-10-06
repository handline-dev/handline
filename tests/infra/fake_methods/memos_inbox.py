"""task.memo.add, edit and delete, and inbox.mark_read, answer and reply: the refusals the fixtures pin; no task or item exists here."""
from . import err, fake


def _not_found(what):
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": what}}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


@fake("task.memo.add", "task.memo.edit", "task.memo.delete", "inbox.mark_read", "inbox.answer", "inbox.reply")
def memos_inbox(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "inbox.answer" and role == "agent":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "role"}}
    if method.startswith("task.memo."):
        if not _is_int(params.get("task")):
            return "fail", err(-32602, "bad_request", field="task")
        if method != "task.memo.add" and not _is_int(params.get("memo")):
            return "fail", err(-32602, "bad_request", field="memo")
        if method != "task.memo.delete" and not (isinstance(params.get("text"), str) and params["text"].strip()):
            return "fail", err(-32602, "bad_request", field="text")
        return _not_found("task")
    if method == "inbox.mark_read":
        ids = params.get("ids")
        if not isinstance(ids, list) or not ids or not all(_is_int(i) for i in ids):
            return "fail", err(-32602, "bad_request", field="ids")
        return _not_found("item")
    if not _is_int(params.get("id")):
        return "fail", err(-32602, "bad_request", field="id")
    if method == "inbox.answer" and ((params.get("option") is None) == (params.get("text") is None)):
        return "fail", err(-32602, "bad_request", field="text")
    if method == "inbox.reply" and not (isinstance(params.get("text"), str) and params["text"].strip()):
        return "fail", err(-32602, "bad_request", field="text")
    return _not_found("item")
