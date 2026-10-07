"""chat.messages, chat.requests, chat.transcript, chat.locate and chat.search: the refusals the fixtures pin; no agent and no message exists here."""
from . import err, fake


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _nf(what):
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": what}}


@fake("chat.messages", "chat.requests", "chat.transcript", "chat.locate", "chat.search")
def chat(role, method, params):
    if method == "chat.search":
        if not (isinstance(params.get("query"), str) and params["query"].strip()):
            return "fail", err(-32602, "bad_request", field="query")
        return "ok", {"messages": []}
    if method == "chat.locate":
        if not _is_int(params.get("request")):
            return "fail", err(-32602, "bad_request", field="request")
        return _nf("message")
    if method in ("chat.messages", "chat.requests") and params.get("scope") not in ("mine", "all"):
        return "fail", err(-32602, "bad_request", field="scope")
    if method == "chat.requests" and params.get("before") is not None and params.get("since") is not None:
        return "fail", err(-32602, "bad_request", field="since")
    limit = params.get("limit")
    if limit is not None and (not _is_int(limit) or not 1 <= limit <= 200):
        return "fail", err(-32602, "bad_request", field="limit")
    if not isinstance(params.get("agent"), str):
        return "fail", err(-32602, "bad_request", field="agent")
    return _nf("agent")
