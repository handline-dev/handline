"""inbox.list: what waits for the caller as the spec's `InboxItem`. For the founder it is the mailbox the screen
draws (`actions.mailbox_rows`: decisions, replies owed, reports, answers), for an agent its own incoming requests.
It serves `hl inbox` and the bridge's FOR YOU list. `options` and `prompt` are empty until a decision card
stores its options and a screen prompt is an inbox row.

inbox.mark_read, inbox.answer and inbox.reply are its writes; none has an hl command of its own (the screen and the IDE call
them), so they stand outside the spec's agent-command table, except that an agent's inbox.reply is `hl say --parent` and its
inbox.mark_read is what `hl inbox` does to the head of its own queue. Each records its answer in `write_effects` once its
effect is done (the effects send and settle, which are not one transaction), so a retry answers instead of repeating.
- mark_read: for the founder, every message of an agent up to the newest one named is read (the list forgets by high-water mark, as
  `actions.mark_read` does); a decision or a reply owed is never read away, so such an id, and what lies past it, stays unread and the
  reply says so. For an agent, only the head of its own queue can be read, as `hl inbox` reads it; another id of its own is refused.
- answer: the founder's answer to an item of the mailbox, which is a verdict (`actions.answer_ok`: a decision's, a question's, or a
  reply to an answer). No item carries options yet, so `option` is refused and `text` is the answer.
- reply: a message to whoever sent the item (`hl say --parent`), settling it. An agent replies to its own incoming items; the founder
  to a mailbox item that is not a decision (a decision is answered, not replied to)."""
from __future__ import annotations

import time

from .. import actions, db
from ..actions import requests as requests_actions
from ..team import ask as askmod
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


def _ids(params) -> list[int]:
    ids = params.get("ids")
    if (not isinstance(ids, list) or not ids or len(ids) > MAX_LIMIT
            or any(isinstance(i, bool) or not isinstance(i, int) for i in ids)):
        raise rpc.bad_request(f"ids is a list of 1 to {MAX_LIMIT} item ids", "ids", rpc.INVALID_PARAMS)
    return sorted(set(ids))


def _item_id(params, key: str = "id") -> int:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise rpc.bad_request(f"{key} is an item id", key, rpc.INVALID_PARAMS)
    return v


def _not_found(i) -> rpc.RpcError:
    return rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no item {i} in this inbox", what="item", name=str(i))


def _done(ctx, params, name: str, said: str) -> dict:
    result = {"accepted": True, "said": said}
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.actor, params["request_id"], name, result)
    finally:
        con.close()
    return result


def _mailbox(con) -> dict[int, dict]:
    return {m["id"]: m for m in actions.mailbox_rows(con)}


def _mark_read(ctx, params) -> dict:
    ids = _ids(params)
    con = db.connect()
    try:
        if ctx.caller.role == "agent":
            me = ctx.caller.info["name"]
            for i in ids:
                row = db.get_request(con, i)
                if row is None or row["target"] != me or not row["edge_ok"] or row["notice"]:
                    raise _not_found(i)
            head = db.head_of_queue(con, me)
            if len(ids) != 1 or not head or int(head["id"]) != ids[0]:
                raise rpc.refused("an agent reads its queue in order: only the item `hl inbox` shows can be read", gate="state")
            db.mark_delivered(con, ids[0])
            db.mark_landed(con, ids[0])
            said = f"item {ids[0]} read"
        else:
            box = _mailbox(con)
            for i in ids:
                if i not in box and (db.get_request(con, i) or {"target": None})["target"] != db.HUMAN:
                    raise _not_found(i)          # one the founder already read has left the mailbox: reading it again is nothing to do
            ids = [i for i in ids if i in box]
            unseen = {m["id"]: m for m in actions.unseen_rows(con)}
            by_agent: dict[str, list[dict]] = {}
            for m in unseen.values():
                by_agent.setdefault(m["from"], []).append(m)
            read = 0
            for agent in {box[i]["from"] for i in ids}:
                rows = by_agent.get(agent, [])
                upto = max((box[i]["seq"] for i in ids if box[i]["from"] == agent), default=0)
                owed = min((m["seq"] for m in rows if m["gate"] or m["waiting"]), default=None)
                readable = [m for m in rows if m["seq"] <= upto and not (m["gate"] or m["waiting"]) and (owed is None or m["seq"] < owed)]
                if readable:
                    actions.for_you_seen(con, agent, max(m["seq"] for m in readable))
                    read += len(readable)
            if not read and any(i in unseen for i in ids):
                raise rpc.refused("a decision or a reply owed is answered, never read away", gate="state")
            said = f"{read} read"
    finally:
        con.close()
    return _done(ctx, params, "inbox.mark_read", said)


def _text(params, key: str) -> str:
    v = params.get(key)
    if not isinstance(v, str) or not v.strip():
        raise rpc.bad_request(f"{key} is text and not empty", key, rpc.INVALID_PARAMS)
    return v


def _answer(ctx, params) -> dict:
    rid = _item_id(params)
    option, text = params.get("option"), params.get("text")
    if (option is None) == (text is None):
        raise rpc.bad_request("an answer is an option or text, one of them", "text", rpc.INVALID_PARAMS)
    if option is not None:
        raise rpc.bad_request("no item offers options yet: answer with text", "option", rpc.INVALID_PARAMS)
    _text(params, "text")
    con = db.connect()
    try:
        if rid not in _mailbox(con):
            raise _not_found(rid)
        ok, msg = actions.answer_ok(con, rid, text)
    finally:
        con.close()
    if not ok:
        raise (_not_found(rid) if msg.endswith("is gone") else rpc.refused(msg))
    return _done(ctx, params, "inbox.answer", msg)


def _reply(ctx, params) -> dict:
    rid = _item_id(params)
    text = _text(params, "text")
    me = ctx.caller.actor
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if ctx.caller.role == "agent":
            if row is None or row["target"] != me or not row["edge_ok"] or row["notice"]:
                raise _not_found(rid)
            target = row["requester"]
        else:
            box = _mailbox(con)
            if rid not in box or row is None:
                raise _not_found(rid)
            if requests_actions.decision_task_of(con, row) is not None:
                raise rpc.refused("a decision is answered with inbox.answer, not replied to", gate="state")
            target = box[rid]["from"]
    finally:
        con.close()
    ok, msg, _ = askmod.say(target, text, requester=me, parent_id=rid)
    if not ok:
        raise rpc.refused(msg)
    return _done(ctx, params, "inbox.reply", msg)


rpc.METHODS["inbox.mark_read"] = rpc.Method(_mark_read, "agent", write=True)
rpc.METHODS["inbox.answer"] = rpc.Method(_answer, "member", write=True)
rpc.METHODS["inbox.reply"] = rpc.Method(_reply, "agent", write=True)
