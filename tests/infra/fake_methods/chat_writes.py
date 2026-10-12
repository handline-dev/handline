"""chat.send, chat.dismiss, chat.cancel and chat.seen: the refusals the fixtures pin; no agent and no message exists here."""
from . import delivery, err, fake


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _nf(what):
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": what}}


@fake("chat.send", "chat.dismiss", "chat.cancel", "chat.seen")
def chat_writes(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "chat.send":
        if not _text(params.get("body")):
            return "fail", err(-32602, "bad_request", field="body")
        if not isinstance(params.get("agent"), str) or not params["agent"]:
            return "fail", err(-32602, "bad_request", field="agent")
        if "parent" in params and (not _is_int(params["parent"]) or params.get("task") is not None):
            return "fail", err(-32602, "bad_request", field="parent")
        if "delivery" in params and params["delivery"] not in ("now", "queue"):
            return "fail", err(-32602, "bad_request", field="delivery")
        return delivery.send(params) or _nf("agent")
    if method == "chat.seen":
        if not _is_int(params.get("up_to")):
            return "fail", err(-32602, "bad_request", field="up_to")
        if not isinstance(params.get("agent"), str) or not params["agent"]:
            return "fail", err(-32602, "bad_request", field="agent")
        return _nf("agent")
    if not _is_int(params.get("request")):
        return "fail", err(-32602, "bad_request", field="request")
    if method == "chat.cancel" and not isinstance(params.get("confirm", False), bool):
        return "fail", err(-32602, "bad_request", field="confirm")
    if method == "chat.cancel" and (done := delivery.cancel(params)):
        return done
    return _nf("message")
