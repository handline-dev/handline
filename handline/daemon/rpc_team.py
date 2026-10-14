"""hr.decide: the founder's decision on an HR proposal. It serves the HR card's Approve and Reject (`hl hr` only proposes).

A proposal's id on the wire is the integer its hex id reads as. An approve names the `subject` hr.list showed (the digest
hr.decide checks), so an edit to the proposal file made after the card was drawn is refused, never approved unseen. The decision
and the change it makes are `hr.decide`'s own, recorded in the protected store; the daemon writes them as the founder's, which
the method's role proves. The result is recorded in `write_effects` once its effect is done, so a retry after a lost reply
answers it and repeats nothing."""
from __future__ import annotations

from .. import db
from ..team import ask as askmod
from ..team import hr, proposals
from . import rpc, rpc_hr


def _wrote(ctx, params, name: str, said: str) -> dict:
    result = {"accepted": True, "said": said}
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
    finally:
        con.close()
    return result


def _proposal(params) -> dict:
    pid = params.get("proposal")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
        raise rpc.bad_request("proposal is a proposal id", "proposal", rpc.INVALID_PARAMS)
    for prop in proposals.listing():
        if isinstance(prop.get("id"), str) and prop["id"].isalnum() and _num(prop["id"]) == pid:
            return prop
    raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no proposal {pid}", what="proposal", name=str(pid))


def _num(pid: str) -> int | None:
    try:
        return int(pid, 16)
    except ValueError:
        return None


def pid_text(prop: dict) -> str:
    return str(prop["id"])


def _decide(ctx, params) -> dict:
    decision, note = params.get("decision"), params.get("note")
    if decision not in ("approve", "reject"):
        raise rpc.bad_request("decision is approve or reject", "decision", rpc.INVALID_PARAMS)
    if note is not None and not isinstance(note, str):
        raise rpc.bad_request("note is text", "note", rpc.INVALID_PARAMS)
    subject = params.get("subject")
    if subject is not None and not isinstance(subject, str):
        raise rpc.bad_request("subject is the digest hr.list showed", "subject", rpc.INVALID_PARAMS)
    prop = _proposal(params)
    if decision == "approve" and not subject:
        raise rpc.bad_request("an approve names the subject hr.list showed", "subject", rpc.INVALID_PARAMS)
    state = rpc_hr.wire(prop)["state"] if rpc_hr.wire(prop) else None
    if state not in rpc_hr.WAITING:         # decided, applied or taken back already: nothing to decide (checked before the digest)
        raise rpc_hr.refusal("proposal_settled", f"proposal {pid_text(prop)} is already {state or prop.get('state')}", prop)
    if decision == "approve" and subject != hr.subject_of(prop):
        raise rpc_hr.refusal("proposal_changed", f"proposal {pid_text(prop)} changed after you read it: read it again before approving", prop)
    ctx_con = db.connect()
    try:
        rpc_hr.check(ctx_con)               # looked at before the decision, so the change below is the one that is said
    finally:
        ctx_con.close()
    ok, said = hr.decide(prop["id"], "approved" if decision == "approve" else "rejected", askmod.HUMAN, note or "",
                         subject or hr.subject_of(prop))
    ctx_con = db.connect()
    try:
        rpc_hr.check(ctx_con)               # said at once, not at the watcher's next look
    finally:
        ctx_con.close()
    if not ok:
        raise rpc.refused(said.strip())
    return _wrote(ctx, params, "hr.decide", said)


rpc.METHODS["hr.decide"] = rpc.Method(_decide, "member", write=True)
