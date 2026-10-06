"""input.take, input.release, input.ask, prompt.list and prompt.answer."""
from . import err, fake

KNOWN = ("a.one",)
NOT_FOUND = {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent"}}


@fake("input.take", "input.release", "input.ask", "prompt.list", "prompt.answer")
def inputs(role, method, params):
    if method != "prompt.list" and (not isinstance(params.get("request_id"), str) or not params["request_id"]):
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "input.ask":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "unsupported"}}
    if method == "prompt.answer":
        if not isinstance(params.get("prompt"), str) or not params["prompt"]:
            return "fail", err(-32602, "bad_request", field="prompt")
        choice = params.get("choice")
        if not isinstance(choice, (str, dict, list)) or (isinstance(choice, list) and not all(isinstance(k, str) for k in choice)):
            return "fail", err(-32602, "bad_request", field="choice")
    if params.get("agent") not in KNOWN:
        return "fail", NOT_FOUND
    return "ok", {"prompts": []} if method == "prompt.list" else {"accepted": True, "said": "done"}
