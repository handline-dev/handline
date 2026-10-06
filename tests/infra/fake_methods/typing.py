"""session.input, session.keys, session.type and session.interrupt."""
from . import err, fake

KNOWN = ("a.one",)
KEYS = ("esc", "enter", "up", "down", "tab", "shift-tab", "ctrl-c", *"0123456789")
GATES = {"a.locked": "terminal_lock", "a.held": "input_holder", "a.walled": "wall", "a.dialog": "screen_blocker"}


@fake("session.input", "session.keys", "session.type", "session.interrupt")
def typing(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method in ("session.input", "session.type"):
        t = params.get("text")
        if not isinstance(t, str) or not t or any(not (c.isprintable() or c == "\t" or (c == "\n" and method == "session.input")) for c in t):
            return "fail", err(-32602, "bad_request", field="text")
    if method == "session.keys":
        k = params.get("keys")
        if not isinstance(k, list) or not k or any(x not in KEYS for x in k):
            return "fail", err(-32602, "bad_request", field="keys")
    agent = params.get("agent")
    gate = GATES.get(agent)
    if gate and (gate in ("terminal_lock", "input_holder") or method == "session.input"):
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": gate}}
    if agent not in KNOWN and agent not in GATES:
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent"}}
    return "ok", {"accepted": True, "said": "sent"}
