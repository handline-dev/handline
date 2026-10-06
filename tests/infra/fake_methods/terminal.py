"""terminal.attach, terminal.detach, terminal.history, terminal.resize."""
from . import err, fake

NOT_FOUND = {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "terminal"}}
AGENTS, HANDLES = ("a.one", "a.term"), ("%1", "t-a.term")
SNAPSHOT = {"terminal": "t-a.term", "format": "text", "seq": 1, "cols": 80, "rows": 24, "lines": [{"text": "$ "}], "cursor": None,
            "history": {"first": 0, "lines": [], "more": False}, "holder": {"kind": "daemon", "name": "daemon", "since": None},
            "holders": [{"kind": "daemon", "name": "daemon", "since": None}]}


def _int(params, key, lo, hi):
    v = params.get(key)
    return not isinstance(v, bool) and isinstance(v, int) and lo <= v <= hi


@fake("terminal.attach", "terminal.detach", "terminal.history", "terminal.resize")
def terminal(role, method, params):
    if method == "terminal.resize" and (not isinstance(params.get("request_id"), str) or not params["request_id"]):
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "terminal.attach":
        if (params.get("agent") is None) == (params.get("terminal") is None):
            return "fail", err(-32602, "bad_request", field="agent")
        if "history" in params and not _int(params, "history", 0, 2000):
            return "fail", err(-32602, "bad_request", field="history")
        if params.get("agent") not in (None, *AGENTS) or params.get("terminal") not in (None, *HANDLES):
            return "fail", NOT_FOUND
        return "ok", SNAPSHOT
    if not isinstance(params.get("terminal"), str) or not params["terminal"]:
        return "fail", err(-32602, "bad_request", field="terminal")
    if method == "terminal.history":
        for k, hi in (("before", 2**40), ("limit", 2000)):
            if not _int(params, k, 1 if k == "limit" else 0, hi):
                return "fail", err(-32602, "bad_request", field=k)
    if method == "terminal.resize":
        for k, lo, hi in (("cols", 10, 500), ("rows", 3, 200)):
            if not _int(params, k, lo, hi):
                return "fail", err(-32602, "bad_request", field=k)
    if params["terminal"] not in HANDLES:
        return "fail", NOT_FOUND
    if method == "terminal.resize":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "input_holder"}}
    return "ok", {} if method == "terminal.detach" else {"first": 0, "lines": [], "more": False}
