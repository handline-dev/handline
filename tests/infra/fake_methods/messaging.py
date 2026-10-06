"""say, ask, push, push.answer and push.cancelled: the refusals the fixtures pin; no agent and no request exists here."""
from . import err, fake


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _refused(why):
    return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "reason": why}}


@fake("say", "ask", "push", "push.answer", "push.cancelled")
def messaging(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "push.cancelled":
        if isinstance(params.get("request"), bool) or not isinstance(params.get("request"), int):
            return "fail", err(-32602, "bad_request", field="request")
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "request"}}
    key = "requester" if method == "push.answer" else "target"
    if not _text(params.get(key)):
        return "fail", err(-32602, "bad_request", field=key)
    if method == "say" and not _text(params.get("body")):
        return "fail", err(-32602, "bad_request", field="body")
    if method == "ask":
        for k in ("title", "body"):
            if not _text(params.get(k)):
                return "fail", err(-32602, "bad_request", field=k)
        if params.get("options") is not None:
            return "fail", err(-32602, "bad_request", field="options")
    return _refused("no such agent")
