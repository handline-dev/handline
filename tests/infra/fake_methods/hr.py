"""hr.list."""
from . import err, fake


@fake("hr.list")
def hr(role, method, params):
    if "state" in params and not isinstance(params["state"], str):
        return "fail", err(-32602, "bad_request", field="state")
    return "ok", {"proposals": []}
