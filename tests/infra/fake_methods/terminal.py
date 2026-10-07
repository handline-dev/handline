"""terminal.attach, terminal.detach, terminal.history, terminal.resize, terminal.input. Format bytes is not faked: it needs a tmux host."""
import base64
import binascii

from . import CURRENT, err, fake, tmux

NOT_FOUND = {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "terminal"}}
AGENTS, HANDLES = ("a.one", "a.term", tmux.AGENT, tmux.TEXT_ONLY), ("%1", "t-a.term", tmux.HANDLE)
SNAPSHOT = {"terminal": "t-a.term", "format": "text", "seq": 1, "cols": 80, "rows": 24, "lines": [{"text": "$ "}], "cursor": None,
            "history": {"first": 0, "lines": [], "more": False}, "holder": {"kind": "daemon", "name": "daemon", "since": None},
            "holders": [{"kind": "daemon", "name": "daemon", "since": None}]}


def _int(params, key, lo, hi):
    v = params.get(key)
    return not isinstance(v, bool) and isinstance(v, int) and lo <= v <= hi


def _data_ok(v):
    try:
        return isinstance(v, str) and 1 <= len(base64.b64decode(v, validate=True)) <= 16384
    except (binascii.Error, ValueError):
        return False


@fake("terminal.attach", "terminal.detach", "terminal.history", "terminal.resize", "terminal.input")
def terminal(role, method, params):
    if "format" in params and params["format"] not in ("text", "bytes"):
        return "fail", err(-32602, "bad_request", field="format")
    if method in ("terminal.resize", "terminal.input") and (not isinstance(params.get("request_id"), str) or not params["request_id"]):
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "terminal.attach":
        if (params.get("agent") is None) == (params.get("terminal") is None):
            return "fail", err(-32602, "bad_request", field="agent")
        if "history" in params and not _int(params, "history", 0, 2000):
            return "fail", err(-32602, "bad_request", field="history")
        if params.get("agent") not in (None, *AGENTS) or params.get("terminal") not in (None, *HANDLES):
            return "fail", NOT_FOUND
        via = tmux.attach(CURRENT.ctx, params)
        if via is not None:
            return via
        return "ok", SNAPSHOT
    if not isinstance(params.get("terminal"), str) or not params["terminal"]:
        return "fail", err(-32602, "bad_request", field="terminal")
    if method == "terminal.history":
        for k, hi in (("before", 2**40), ("limit", 2000)):
            if not _int(params, k, 1 if k == "limit" else 0, hi):
                return "fail", err(-32602, "bad_request", field=k)
    if method == "terminal.input" and not _data_ok(params.get("data")):
        return "fail", err(-32602, "bad_request", field="data")
    if method == "terminal.resize":
        for k, lo, hi in (("cols", 10, 500), ("rows", 3, 200)):
            if not _int(params, k, lo, hi):
                return "fail", err(-32602, "bad_request", field=k)
    if params["terminal"] not in HANDLES:
        return "fail", NOT_FOUND
    if params["terminal"] == tmux.HANDLE and CURRENT.ctx is not None:
        via = tmux.terminal(CURRENT.ctx, method, params)
        if via is not None:
            return via
    if method in ("terminal.resize", "terminal.input"):
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "input_holder"}}
    return "ok", {} if method == "terminal.detach" else {"first": 0, "lines": [], "more": False}
