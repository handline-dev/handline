"""chat.send, chat.answer, chat.dismiss and chat.withdraw: the refusals the fixtures pin; no agent and no message exists here."""
from . import err, fake


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _nf(what):
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": what}}


@fake("chat.send", "chat.answer", "chat.dismiss", "chat.withdraw")
def chat_writes(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "chat.send":
        if not _text(params.get("body")):
            return "fail", err(-32602, "bad_request", field="body")
        if not isinstance(params.get("agent"), str) or not params["agent"]:
            return "fail", err(-32602, "bad_request", field="agent")
        return _nf("agent")
    if not _is_int(params.get("request")):
        return "fail", err(-32602, "bad_request", field="request")
    if method == "chat.answer" and not _text(params.get("text")):
        return "fail", err(-32602, "bad_request", field="text")
    if method == "chat.withdraw" and params.get("reason") is not None and not _text(params["reason"]):
        return "fail", err(-32602, "bad_request", field="reason")
    return _nf("message")
