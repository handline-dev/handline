"""input.take, input.release and input.ask: who types at an agent's terminal (daemon/inputhold.py). A person or device
takes it to type or answer without the daemon typing beside them, and gives it back, or loses it by being idle for
`input_hold_idle_s`. No hl command serves them: they are the IDE's and web UI's own. `input.ask` is reserved."""
from __future__ import annotations

from .. import db
from ..infra import paths
from . import inputhold, rpc, rpc_gates


def _agent(params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _take(ctx, params) -> dict:
    agent = _agent(params)
    con = db.connect()
    try:
        rpc_gates.input_holder(ctx, con, agent)
        if not inputhold.take(con, agent, rpc_gates.holder_kind(ctx), ctx.caller.info["name"]):     # another took it first
            rpc_gates.input_holder(ctx, con, agent)
            raise rpc.refused(f"{agent}'s terminal was taken by someone else", gate="input_holder")
        return {"accepted": True, "said": f"{agent}: you hold its input", "holder": inputhold.view(con, agent)[0]}
    finally:
        con.close()


def _release(ctx, params) -> dict:
    agent = _agent(params)
    con = db.connect()
    try:
        row = inputhold.holder(con, agent)
        if row is None:
            return {"accepted": True, "said": f"{agent}: the daemon already holds its input"}
        rpc_gates.input_holder(ctx, con, agent)         # only its holder gives it back
        inputhold.release(con, agent)
        return {"accepted": True, "said": f"{agent}: its input is the daemon's again"}
    finally:
        con.close()


def _ask(ctx, params) -> dict:
    raise rpc.refused("asking the holder for input is not implemented", gate="unsupported")


rpc.METHODS["input.take"] = rpc.Method(_take, "member", write=True)
rpc.METHODS["input.release"] = rpc.Method(_release, "member", write=True)
rpc.METHODS["input.ask"] = rpc.Method(_ask, "member", write=True)
