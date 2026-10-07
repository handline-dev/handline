"""agent.strip and chat.refs: the two reads the Textual chat page makes against the db beyond the chat methods.

agent.strip is the line under an agent's chat header: model, account, context used and its limit, the life left in the prompt cache
(`cache_left_s`, negative once it has lapsed), and the agent's state as the six screen words (`screen_word`, `tui.screen_state`'s and
not the wire `state`) with the text drawn beside it. chat.refs is what a `#` in the composer can point at: the agent's newest requests,
sent or received, that were not withdrawn or turned away; its tasks are `tasks.list` (owner or from). A request that no chat shows
is read with `chat.locate`, whose not_found carries its from, to and body."""
from __future__ import annotations

import time

from .. import db
from ..infra import paths, textsafe
from ..team import agents
from ..usage import status
from . import handoff_note, rpc
from .screenstate import screen_state

REFS_SHOWN = 30
LINE_MAX = 60


def _agent(params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _strip(ctx, params) -> dict:
    name, now = _agent(params), int(time.time())
    con = db.connect()
    try:
        account = agents.current_account(name, con)
        row = db.get_agent_state(con, name)
        word, detail = screen_state(con, row) if row else (None, "")
        left = handoff_note.cache_left(con, name, now)
    finally:
        con.close()
    out = {"agent": name, "model": None, "account": account, "ctx_pct": None, "ctx_used": None, "ctx_limit": None,
           "cache_left_s": None if left is None else int(left), "screen_word": word, "screen_detail": detail}
    if not account:
        return out
    st = status.read_for_agent(name, account)
    out["model"], out["ctx_pct"], out["ctx_used"] = st.model_id, st.trusted_ctx_pct, st.trusted_ctx_used
    try:
        out["ctx_limit"] = status.effective_context_limit(agents.read(name), st.model_id)
    except Exception:  # noqa: BLE001 - an unreadable config leaves the limit unknown
        pass
    return out


def _refs(ctx, params) -> dict:
    name = _agent(params)
    gone = "(" + ", ".join(repr(x) for x in db.NEVER_REACHED_TARGET) + ")"
    con = db.connect()
    try:
        rows = con.execute(f"SELECT id, requester, target, body FROM requests WHERE state NOT IN {gone} AND "
                           "(requester = ? OR target = ?) ORDER BY id DESC LIMIT ?", (name, name, REFS_SHOWN)).fetchall()
    finally:
        con.close()
    return {"requests": [{"request": r["id"], "from": r["requester"], "to": r["target"],
                          "line": textsafe.oneline(next(iter((r["body"] or "").strip().splitlines()), ""))[:LINE_MAX]} for r in rows]}


rpc.METHODS["agent.strip"] = rpc.Method(_strip, "member")
rpc.METHODS["chat.refs"] = rpc.Method(_refs, "member")
