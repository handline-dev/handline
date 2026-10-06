"""session.start, wake, stop, restart, park and terminal.close."""
from . import err, fake

KNOWN = ("a.one",)


@fake("session.start", "session.wake", "session.stop", "session.restart", "session.park", "terminal.close")
def session(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "session.stop" and params.get("mode") not in ("exit", "close", "kill"):
        return "fail", err(-32602, "bad_request", field="mode")
    if method == "session.stop" and params.get("fresh") and params.get("mode") != "close":
        return "fail", err(-32602, "bad_request", field="fresh")
    if method == "session.stop" and params.get("mode") == "kill" and role == "agent":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "role", "reason": "kill is not for an agent session"}}
    if method == "session.start" and params.get("account") is not None:
        return "fail", err(-32602, "bad_request", field="account")
    if params.get("agent") == "a.locked":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "terminal_lock"}}
    if params.get("agent") == "a.running":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "state"}}
    if params.get("agent") not in KNOWN:
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent"}}
    return "ok", {"accepted": True, "said": "done"}
