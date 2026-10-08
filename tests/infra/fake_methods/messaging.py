"""say: the refusals the fixtures pin; no agent exists here."""
from . import err, fake


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _refused(why):
    return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "reason": why}}


@fake("say")
def messaging(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    for k in ("target", "body"):
        if not _text(params.get(k)):
            return "fail", err(-32602, "bad_request", field=k)
    return _refused("no such agent")
