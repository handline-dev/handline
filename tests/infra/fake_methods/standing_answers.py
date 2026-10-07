"""answers.list, answer.add, answer.remove and inbox.get: no standing answer and no request exists here."""
from . import err, fake


def _nf(what):
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": what}}


@fake("answers.list", "answer.add", "answer.remove", "inbox.get")
def standing_answers(role, method, params):
    if method == "answers.list":
        return "ok", {"answers": []}
    if method == "inbox.get":
        if isinstance(params.get("id"), bool) or not isinstance(params.get("id"), int):
            return "fail", err(-32602, "bad_request", field="id")
        return _nf("request")
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "answer.add":
        if not any(isinstance(params.get(k), str) and params[k].strip() for k in ("folder", "vendor", "title", "label")):
            return "fail", err(-32602, "bad_request", field="folder")
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "reason": "not a folder"}}
    if params.get("kind") not in ("trust", "dialog"):
        return "fail", err(-32602, "bad_request", field="kind")
    return _nf("answer")
