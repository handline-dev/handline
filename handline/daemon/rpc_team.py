"""hr.decide and worktree.set_status: the founder's decision on an HR proposal, and a worktree's status on the terminal host.
hr.decide serves the HR card's Approve and Reject (`hl hr` only proposes); worktree.set_status serves no hl command.

A proposal's id on the wire is the integer its hex id reads as. An approve names the `subject` hr.list showed (the digest
hr.decide checks), so an edit to the proposal file made after the card was drawn is refused, never approved unseen. The decision
and the change it makes are `hr.decide`'s own, recorded in the protected store; the daemon writes them as the founder's, which
the method's role proves. A worktree is one an agent's record names; the status is the host's workspace status, which the host
keeps and git does not (worktrees.list's `status` is git's), so a host that keeps none refuses. Each result is recorded in
`write_effects` once its effect is done, so a retry after a lost reply answers it and repeats nothing."""
from __future__ import annotations

import os

from .. import db
from ..infra import paths, textsafe
from ..session import term
from ..team import ask as askmod
from ..team import hr
from ..team import spawn as spawnmod
from . import rpc

STATUS_MAX = 40


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
    for prop in hr.listing():
        if isinstance(prop.get("id"), str) and prop["id"].isalnum() and _num(prop["id"]) == pid:
            return prop
    raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no proposal {pid}", what="proposal", name=str(pid))


def _num(pid: str) -> int | None:
    try:
        return int(pid, 16)
    except ValueError:
        return None


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
    ok, said = hr.decide(prop["id"], "approved" if decision == "approve" else "rejected", askmod.HUMAN, note or "",
                         subject or hr.subject_of(prop))
    if not ok:
        raise rpc.refused(said.strip())
    return _wrote(ctx, params, "hr.decide", said)


def _worktree(params) -> str:
    path = params.get("path")
    if not isinstance(path, str) or not path:
        raise rpc.bad_request("path is a worktree's path", "path", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        for name in paths.list_agents():
            row = db.get_agent_state(con, name)
            wt = spawnmod.worktree_path(row["worktree"] if row else None)
            if wt and os.path.realpath(wt) == os.path.realpath(path):
                return wt
    finally:
        con.close()
    raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no worktree {path}", what="worktree", name=path)


def _set_status(ctx, params) -> dict:
    status = params.get("status")
    if not isinstance(status, str) or not status.strip() or textsafe.has_unsafe(status.strip()) or len(status.strip()) > STATUS_MAX:
        raise rpc.bad_request(f"status is one plain line of at most {STATUS_MAX} characters", "status", rpc.INVALID_PARAMS)
    wt = _worktree(params)
    if not term.available():
        raise rpc.refused(f"{term.missing()} - no host keeps a worktree status", gate="state")
    ok, why = term.set_worktree(wt, status=status.strip())
    if not ok:
        raise rpc.refused(f"the terminal host did not set the status: {why.strip()}", gate="state")
    return _wrote(ctx, params, "worktree.set_status", f"{wt}: status is {status.strip()}")


rpc.METHODS["hr.decide"] = rpc.Method(_decide, "founder", write=True)
rpc.METHODS["worktree.set_status"] = rpc.Method(_set_status, "member", write=True)
