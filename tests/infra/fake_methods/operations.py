"""agent.create and operation.get."""
from . import err, fake

OPS: dict[str, dict] = {}       # operation id -> the agent it was made for, and the request id that made it


def _fail(code, message, **data):
    return "fail", {"code": code, "message": message, "data": {"code": message, **data}}


@fake("agent.create", "operation.get")
def operations(role, method, params):
    if method == "operation.get":
        op = params.get("operation")
        if not isinstance(op, str) or not op:
            return "fail", err(-32602, "bad_request", field="operation")
        if op not in OPS:
            return _fail(-32001, "not_found", what="operation")
        return "ok", {"id": op, "kind": "agent.create", "state": "failed", "step": "start", "steps_done": 1, "steps_total": 2,
                      "message": "no terminal host", "result": {"agent": OPS[op]["agent"], "registered": True}, "agent": OPS[op]["agent"]}
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if params.get("account") is not None:
        return "fail", err(-32602, "bad_request", field="account")
    if params.get("repo") in (None, "/no/such/repo/here"):
        return _fail(-32001, "not_found", what="repo")
    if params.get("parent") is not None:
        return _fail(-32001, "not_found", what="agent")
    for op, got in OPS.items():
        if got["request_id"] == params["request_id"]:
            return "ok", {"accepted": True, "said": "creating", "operation": op, "replayed": True}
    op = f"op-{len(OPS) + 1}"
    OPS[op] = {"agent": params["name"], "request_id": params["request_id"]}
    return "ok", {"accepted": True, "said": "creating", "operation": op}
