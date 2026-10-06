"""inbox.list: what waits for the caller as the spec's `InboxItem`. For the founder it is the mailbox the screen
draws (`actions.mailbox_rows`: decisions, replies owed, reports, answers), for an agent its own incoming requests.
It serves `hl inbox` and the bridge's FOR YOU list. `options` and `prompt` are empty until a decision card
stores its options and a screen prompt is an inbox row."""
from __future__ import annotations

import time

from .. import actions, db
from . import agentview, rpc

DEFAULT_LIMIT, MAX_LIMIT, MAX_WAIT_S = 50, 200, 600
POLL_S = 0.5


def _item(con, *, id: int, kind: str, agent: str, body: str, ts: int, read: bool, answered: bool) -> dict:
    tasks = db.tasks_for_request(con, id)
    task = tasks[0] if tasks else None
    return {"id": id, "kind": kind, "agent": agent, "title": actions.first_line(body)[:200], "body": body,
            "evidence": task["done_evidence"] if task is not None else None, "task": task["id"] if task is not None else None,
            "created": agentview._ts(ts), "read": read, "answered": answered, "options": [], "prompt": None}


def _founder_items(con) -> list[dict]:
    out = []
    seen: dict[str, int] = {}
    for m in actions.mailbox_rows(con):
        cursor = seen.setdefault(m["from"], db.get_cursor(con, f"for_you_seen:{m['from']}"))
        kind = "decision" if m["gate"] else (m["kind"] or "ask")
        out.append(_item(con, id=m["id"], kind=kind, agent=m["from"], body=m["body"], ts=m["ts"],
                         read=m["seq"] <= cursor, answered=False))
    return out


def _agent_items(con, agent: str) -> list[dict]:
    rows = con.execute(f"SELECT id, ts, requester, kind, body, read_at, state, {db.ANSWER_SQL} FROM requests "
                       "WHERE target = ? AND edge_ok AND COALESCE(notice, 0) = 0 ORDER BY id", (agent,)).fetchall()
    return [_item(con, id=r["id"], kind=r["kind"] or "ask", agent=r["requester"], body=r["body"], ts=r["ts"],
                  read=r["read_at"] is not None or r["state"] != "queued", answered=r["answer"] is not None) for r in rows]


def _int(params, key, lo=None, hi=None):
    v = params.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise rpc.bad_request(f"{key} is an integer" + (f" from {lo} to {hi}" if hi is not None else ""), key, rpc.INVALID_PARAMS)
    return v


def _page(ctx, params) -> dict:
    unread, kind, agent = params.get("unread", False), params.get("kind"), params.get("agent")
    if not isinstance(unread, bool):
        raise rpc.bad_request("unread is true or false", "unread", rpc.INVALID_PARAMS)
    for k, v in (("kind", kind), ("agent", agent)):
        if v is not None and not isinstance(v, str):
            raise rpc.bad_request(f"{k} is a string", k, rpc.INVALID_PARAMS)
    before, limit = _int(params, "before"), _int(params, "limit", 1, MAX_LIMIT) or DEFAULT_LIMIT
    con = db.connect()
    try:
        items = _agent_items(con, ctx.caller.info["name"]) if ctx.caller.role == "agent" else _founder_items(con)
    finally:
        con.close()
    items = [i for i in items if (not unread or not i["read"]) and (kind is None or i["kind"] == kind)
             and (agent is None or i["agent"] == agent) and (before is None or i["id"] < before)]
    items.sort(key=lambda i: -i["id"])
    return {"items": items[:limit], "more": len(items) > limit}


def _inbox_list(ctx, params) -> dict:
    wait = _int(params, "wait_s", 0, MAX_WAIT_S) or 0
    first = _page(ctx, params)
    if not wait or first["items"]:
        return first
    until = time.time() + wait
    while time.time() < until and not ctx.hung_up():     # held until something arrives, as the spec says; a client that hung up ends it
        time.sleep(POLL_S)
        page = _page(ctx, params)
        if page["items"]:
            return page
    return first


rpc.METHODS["inbox.list"] = rpc.Method(_inbox_list, "agent", waits=True)
