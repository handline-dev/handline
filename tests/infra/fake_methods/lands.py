"""land, land.pass, land.authorize: the refusals the fixtures pin. The fake never has a session's vouch or key."""
from . import err, fake


def _refused(gate):
    return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": gate, "reason": gate}}


@fake("land", "land.pass", "land.authorize")
def lands(role, method, params):
    if role != "agent":
        return _refused("role")
    if method == "land.authorize":
        if params.get("land_verb") not in ("land", "land_pass"):
            return "fail", err(-32602, "bad_request", field="land_verb")
        return _refused("not_self")
    return _refused("session_token")
