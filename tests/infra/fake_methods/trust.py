"""trust.propose, trust.list and trust.remove: the fake checks arguments and holds no roots."""
from . import err, fake


def _bad(field):
    return "fail", err(-32602, "bad_request", field=field)


@fake("trust.propose")
def propose(role, method, params):
    if not isinstance(params.get("root"), str):
        return _bad("root")
    if "why" in params and not isinstance(params["why"], str):
        return _bad("why")
    return "ok", {"accepted": True, "said": "proposal 00000009 raised as #9.", "proposal": "00000009"}


@fake("trust.list")
def listing(role, method, params):
    if "proposals" in params and not isinstance(params["proposals"], bool):
        return _bad("proposals")
    return "ok", {"roots": [], "declared": [], "proposals": []}


@fake("trust.remove")
def remove(role, method, params):
    if not isinstance(params.get("root"), str):
        return _bad("root")
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "root"}}
