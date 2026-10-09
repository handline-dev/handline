"""trust.propose, trust.list and trust.remove: `hl trust add`, `list`, `proposals` and `remove`, each the store's own call (`team/trust.py`) with what the command prints as `said`.

An agent's trust.propose raises a decision for the founder; the founder's declares the root directly, as the command does. Removing a root is
the founder's: the roots live in the protected store, which an agent session cannot write, so the method is not an agent's either."""
from __future__ import annotations

import os

from .. import db
from ..team import ask as askmod
from ..team import trust
from . import rpc


def _me(ctx) -> str:
    """Who the store sees: the founder as human, an agent by name. A paired device is neither, so it proposes nothing."""
    if ctx.caller.role == "founder":
        return askmod.HUMAN
    if ctx.caller.role != "agent":
        raise rpc.refused("a trust root is proposed by an agent or declared by the founder, not from a device", "role")
    return ctx.caller.actor


def _wrote(ctx, params, name: str, result: dict) -> dict:
    """The result recorded once its effect is done, so a retry after a lost reply answers it and repeats nothing."""
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
    finally:
        con.close()
    return result


def _text(params, field: str, required: bool = True) -> str | None:
    v = params.get(field)
    if v is None and not required:
        return None
    if not isinstance(v, str) or (required and not v.strip()):
        raise rpc.bad_request(f"{field} is text", field, rpc.INVALID_PARAMS)
    return v


def _root(params) -> str:
    """A root the caller spelled out in full: the store resolves a relative one, or a `~`, against the daemon's own directory and home, not the caller's."""
    root = _text(params, "root")
    if not os.path.isabs(root):
        raise rpc.bad_request("root is an absolute path; the client resolves it against its own folder", "root", rpc.INVALID_PARAMS)
    return root


def _propose(ctx, params) -> dict:
    root, why = _root(params), _text(params, "why", required=False)
    me = _me(ctx)
    if me == askmod.HUMAN:
        ok, said = trust.enact(root, why or "added by the founder")
        result = {"accepted": True, "said": said}
    else:
        ok, said, proposal = trust.propose_with_id(root, why or "", requester=me)
        result = {"accepted": True, "said": said}
        if ok:
            result["proposal"] = proposal
    if not ok:
        raise rpc.refused(said)
    return _wrote(ctx, params, "trust.propose", result)


def _list(ctx, params) -> dict:
    wanted = params.get("proposals")
    if wanted is not None and not isinstance(wanted, bool):
        raise rpc.bad_request("proposals is true or false", "proposals", rpc.INVALID_PARAMS)
    rows = trust.list_roots()
    proposals = []
    for p in trust.listing() if wanted else []:
        row = {"id": str(p.get("id", "")), "root": str(p.get("root", "")), "state": str(p.get("state") or "?")}
        if p.get("request") is not None:
            row["request"] = p["request"]
        if p.get("requested_by"):
            row["requested_by"] = str(p["requested_by"])
        proposals.append(row)
    return {"roots": [str(r.get("root")) for r in rows],
            "declared": [{"root": str(r.get("root")), "why": str(r.get("why", "")), "added_by": str(r.get("added_by", "?"))} for r in rows],
            "proposals": proposals}


def _remove(ctx, params) -> dict:
    root = _root(params)
    ok, said = trust.remove(root)
    if not ok:
        if "is not a declared root" in said:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", said, what="root", name=root)
        raise rpc.refused(said)
    return _wrote(ctx, params, "trust.remove", {"accepted": True, "said": said})


rpc.METHODS["trust.propose"] = rpc.Method(_propose, "agent", write=True)
rpc.METHODS["trust.list"] = rpc.Method(_list, "agent")
rpc.METHODS["trust.remove"] = rpc.Method(_remove, "founder", write=True)
