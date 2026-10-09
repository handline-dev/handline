"""hook.record: a session's hook tells the daemon what happened, and the daemon writes it down and decides
what it means for the agent's state (it serves `hl hook`). The spool (`spool.py`) replays the same call
when the daemon was down."""
from __future__ import annotations

import time

from .. import db
from ..infra import paths
from ..session import events
from . import rpc

MAX_LAG_S = 7 * 24 * 3600


def apply(con, agent: str, kind: str, data: dict, request_id: str, at: int) -> None:
    if agent not in paths.list_agents():
        return  # no agent.yaml backs the name (a session started by hand under an OCP_AGENT nobody registered): there is no agent to move
    events.apply_hook(con, agent, kind, data, request_id, at)


def _record(ctx, params) -> dict:
    agent, kind, data, at = params.get("agent"), params.get("kind"), params.get("data"), params.get("at")
    if not isinstance(agent, str) or not agent:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if not isinstance(kind, str) or not kind:
        raise rpc.bad_request("kind is a hook event name", "kind", rpc.INVALID_PARAMS)
    if not isinstance(data, dict):
        raise rpc.bad_request("data is the hook's own object", "data", rpc.INVALID_PARAMS)
    if isinstance(at, bool) or not isinstance(at, int):
        raise rpc.bad_request("at is when the hook ran, in epoch seconds", "at", rpc.INVALID_PARAMS)
    ctx.require_self(agent)
    con = db.connect()
    try:
        apply(con, agent, kind, data, params["request_id"], at)
    finally:
        con.close()
    return {}


# Its own event is its record, under the agent that made it, so the generic write event is not added.
rpc.METHODS["hook.record"] = rpc.Method(_record, "agent", write=True, own_event=True)
