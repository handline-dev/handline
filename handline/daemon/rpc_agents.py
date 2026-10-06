"""agents.list, agent.get and agent.timeline: the stored view of each agent, served as the spec defines it.
They serve `hl ps` (agents.list, agent.get) and the agent's chat page (agent.timeline)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import actions, db
from ..infra import gitrun, paths, textsafe
from ..session import launch
from . import agentview, rpc

DEFAULT_LIMIT, MAX_LIMIT = 50, 200


def _named(con, params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _view(con, name: str) -> dict:
    """The stored view; one made now for an agent registered since the last tick."""
    return agentview.stored(con, name) or agentview.compute(con, name)


def _agents_list(ctx, params) -> dict:
    con = db.connect()
    try:
        have = {v["name"]: v for v in agentview.stored_all(con)}
        return {"agents": [have.get(n) or agentview.compute(con, n) for n in paths.list_agents()]}
    finally:
        con.close()


def _prompt(con, name: str) -> dict | None:
    block = actions.open_block_of(con, name)
    if not block:
        return None
    detail = block.get("detail") or {}
    return {"id": block["fingerprint"], "agent": name, "kind": block["kind"], "tool": detail.get("tool") or None,
            "command": detail.get("command") or None, "reason": detail.get("reason") or None,
            "lines": list(block["panel"]), "options": [], "since": agentview._ts(block.get("first_seen") or block["last_seen"])}


def _agent_get(ctx, params) -> dict:
    con = db.connect()
    try:
        name = _named(con, params)
        row = db.get_agent_state(con, name)
        path = row["worktree"] if row else None
        branch = None
        if path:
            rc, out, _ = gitrun.run(path, "rev-parse", "--abbrev-ref", "HEAD")
            branch = textsafe.oneline(out) if rc == 0 else None
        account = launch.find_room(name)
        return {**_view(con, name),
                "worktree": {"path": path, "branch": branch or ""} if path else None,
                "terminal": row["terminal"] if row else None,
                "session_id": row["resume_session"] if row else None,
                "session_open": bool(row and row["terminal"] and not row["closed_at"]),
                "input_holders": [{"kind": "daemon", "name": "daemon", "since": None}],
                "account_pick": {"account": account,
                                 "reason": "the account a session start would be placed on" if account
                                 else "no account can take work"},
                "prompt": _prompt(con, name)}
    finally:
        con.close()


def _entry(row) -> dict:
    payload = json.loads(row["payload"]) if row["payload"] and row["payload"][0] == "{" else {}
    actor = payload.get("actor")
    return {"id": row["id"], "at": agentview._ts(row["ts"]), "kind": row["kind"],
            "actor": actor if isinstance(actor, dict) else None,
            "text": str(payload.get("text") or payload.get("detail") or payload.get("method") or row["kind"]),
            "data": payload}


def _agent_timeline(ctx, params) -> dict:
    limit = params.get("limit", DEFAULT_LIMIT)
    before = params.get("before")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise rpc.bad_request("limit is a positive integer", "limit", rpc.INVALID_PARAMS)
    if before is not None and (isinstance(before, bool) or not isinstance(before, int)):
        raise rpc.bad_request("before is an event id", "before", rpc.INVALID_PARAMS)
    limit = min(limit, MAX_LIMIT)
    con = db.connect()
    try:
        name = _named(con, params)
        rows = con.execute("SELECT * FROM events WHERE agent = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                           (name, before, before, limit + 1)).fetchall()
        return {"items": [_entry(r) for r in rows[:limit]], "more": len(rows) > limit}
    finally:
        con.close()


rpc.METHODS["agents.list"] = rpc.Method(_agents_list, "agent")
rpc.METHODS["agent.get"] = rpc.Method(_agent_get, "agent")
rpc.METHODS["agent.timeline"] = rpc.Method(_agent_timeline, "member")
