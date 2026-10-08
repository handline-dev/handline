"""subscribe, unsubscribe and events.feed. `subscribe` serves the live half of `hl events --follow` and the
UI's refresh; `events.feed` serves `hl events` and the notification list."""
from __future__ import annotations

from .. import db
from . import rpc, stream

FEED_DEFAULT, FEED_MAX = 50, 200


def _topics(params, field="topics", required=True) -> list[str]:
    topics = params.get(field)
    if topics is None and not required:
        return ["notifications"]
    if not isinstance(topics, list) or not topics or not all(isinstance(t, str) for t in topics):
        raise rpc.bad_request("topics is a list of topic names", field, rpc.INVALID_PARAMS)
    unknown = [t for t in topics if t not in stream.TOPICS]
    if unknown:
        raise rpc.bad_request(f"no such topic: {unknown[0]}", field, rpc.INVALID_PARAMS)
    return topics


# What an agent may not follow: the reads behind these topics (chat.*, operation.get) are for a member.
MEMBER_TOPICS = ("chat", "operations")


def _allowed(ctx, topics: list[str]) -> None:
    for t in topics:
        if t in MEMBER_TOPICS and not ctx.caller.allows("member"):
            raise rpc.refused(f"the {t} topic is for a member or above; this caller is {ctx.caller.role}", gate="role")


def _int(params, field):
    v = params.get(field)
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
        raise rpc.bad_request(f"{field} is a cursor, a non-negative integer", field, rpc.INVALID_PARAMS)
    return v


def _subscribe(ctx, params) -> dict:
    topics, since = _topics(params), _int(params, "since")
    _allowed(ctx, topics)
    agent = params.get("agent")
    if agent is not None and not isinstance(agent, str):
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    sub = stream.Subscription(ctx.send_line, ctx.hang_up, topics, agent)
    con = db.connect()
    try:
        top = stream.HUB.subscribe(sub, con, since)
    finally:
        con.close()
    ctx.subs.append(sub)
    ctx.after_reply.append(sub.start)
    ctx.on_close.append(lambda: stream.HUB.drop(sub))
    return {"cursor": top}


def _unsubscribe(ctx, params) -> dict:
    gone = set(_topics(params))
    for sub in list(ctx.subs):
        sub.topics -= gone
        if not sub.topics:
            ctx.subs.remove(sub)
            stream.HUB.drop(sub)
    return {}


def _feed(ctx, params) -> dict:
    topics, since, limit = _topics(params, required=False), _int(params, "since"), params.get("limit", FEED_DEFAULT)
    _allowed(ctx, topics)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise rpc.bad_request("limit is a positive integer", "limit", rpc.INVALID_PARAMS)
    limit = min(limit, FEED_MAX)
    con = db.connect()
    try:
        if since is None:
            events = stream.latest(con, topics, limit)
            gap = False
        else:
            gap = since < stream.pruned_through(con)
            events = stream.since(con, max(since, stream.pruned_through(con)), topics, limit=limit)
        return {"events": events, "cursor": events[-1]["cursor"] if events else max(since or 0, stream.newest(con)), "gap": gap}
    finally:
        con.close()


rpc.METHODS["subscribe"] = rpc.Method(_subscribe, "agent")
rpc.METHODS["unsubscribe"] = rpc.Method(_unsubscribe, "agent")
rpc.METHODS["events.feed"] = rpc.Method(_feed, "agent")
