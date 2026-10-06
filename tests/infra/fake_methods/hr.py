"""hr.list, and the decisions hr.decide makes on the two proposals this fake holds: 7 waits, 8 was rejected."""
from . import err, fake

SUBJECT = "digest-7"


def _proposal(pid, state, settled):
    return {"id": pid, "agent": "a.new", "kind": "add", "title": "add a.new", "diff": "+ a.new", "subject": SUBJECT, "state": state,
            "created": "2026-01-01T00:00:00Z", "settled": settled, "requested_by": "a.boss", "reason": "needs a hand"}


def _held(state=None):
    both = {"open": _proposal(7, "open", None), "rejected": _proposal(8, "rejected", "2026-01-02T00:00:00Z")}
    return list(both.values()) if state is None else [v for k, v in both.items() if k == state]


@fake("hr.list")
def hr(role, method, params):
    if "state" in params and not isinstance(params["state"], str):
        return "fail", err(-32602, "bad_request", field="state")
    return "ok", {"proposals": _held(params.get("state"))}


def decide(params):
    """The fake's answer to an hr.decide whose decision is valid, or None for a proposal it does not hold."""
    refused = lambda gate, prop: ("fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": gate, "reason": gate, "proposal": prop}})
    if params.get("proposal") == 8:
        return refused("proposal_settled", _held("rejected")[0])
    if params.get("proposal") != 7:
        return None
    if params["decision"] == "approve" and params.get("subject") != SUBJECT:
        return refused("proposal_changed", _held("open")[0])
    state = "approved" if params["decision"] == "approve" else "rejected"
    prop = {**_proposal(7, state, "2026-01-03T00:00:00Z")}
    notice = {"jsonrpc": "2.0", "method": "event", "params": {
        "cursor": 9, "topic": "hr", "type": "hr.settled", "at": "2026-01-03T00:00:00Z", "actor": None, "agent": prop["agent"],
        "title": prop["title"], "target": {"screen": "hr", "id": "7"}, "data": {"proposal": prop}}}
    return "ok", {"accepted": True, "said": "done"}, [notice]
