"""chat.send, chat.answer, chat.dismiss, chat.withdraw, chat.cancel and chat.seen: the chat page's writes, through the daemon.

chat.send is the person's message to an agent (`hl say` is the agent's, `chat_reply` the screen's): a plain message, or with `task` a
task the agent is given (title and time box, the message its brief). It goes through delivery as any request does (queued, typed,
landed) and needs no input hold. chat.answer answers what an agent asked the person (a card, a question, an answer to something they
asked), as `inbox.answer` does for the mailbox; chat.dismiss takes a message off without answering; chat.withdraw is `hl withdraw`: the
caller's own request taken back before it was delivered. chat.cancel is the person's taking back of a request they sent, delivered or not
(`/cancel`), and chat.seen records what of an agent's messages they have in front of them. chat.send with `parent` answers the message
it replies to when that one waits on the person, and is a say under it otherwise. Each writes its effect and its `write_effects` record in one transaction, so a
retry after a crash answers and repeats nothing; the telling follows the commit."""
from __future__ import annotations

from .. import actions, db
from ..infra import paths, textsafe
from ..team import ask as askmod
from . import rpc, rpc_tasks

_ASKED = "no such message"


def _record(ctx, params, name: str, **more):
    """What an action calls in its transaction: the answer kept under this request_id."""
    me, rid = ctx.caller.writer, params["request_id"]
    return lambda con, said, *_: db.record_effect(con, me, rid, name, {"accepted": True, "said": said, **more})


def _request_id(params, key: str = "request") -> int:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise rpc.bad_request(f"{key} is a message number", key, rpc.INVALID_PARAMS)
    return v


def _text(params, key: str, required: bool = True) -> str | None:
    v = params.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise rpc.bad_request(f"{key} is text and not empty", key, rpc.INVALID_PARAMS)
    return v


def _message(con, rid: int):
    row = db.get_request(con, rid)
    if row is None:
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no message {rid}", what="message", name=str(rid))
    return row


def _send(ctx, params) -> dict:
    body, agent = _text(params, "body"), params.get("agent")
    task, parent = params.get("task"), params.get("parent")
    if not isinstance(agent, str) or not agent:
        raise rpc.bad_request("agent is an agent's name", "agent", rpc.INVALID_PARAMS)
    if parent is not None:
        if task is not None:
            raise rpc.bad_request("a message under another is not a task", "parent", rpc.INVALID_PARAMS)
        parent = _request_id(params, "parent")
    secs = None
    if task is not None:
        if not isinstance(task, dict):
            raise rpc.bad_request("task is {title, time_box}", "task", rpc.INVALID_PARAMS)
        title = _text(task, "title")
        secs = rpc_tasks._span(task, "time_box")
    if agent not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {agent}", what="agent", name=agent)
    me = ctx.caller.actor
    if parent is not None:
        return _send_under(ctx, params, agent, parent, textsafe.text(body).strip())
    if task is not None:
        def effect(con, tid: int) -> None:
            first = db.first_linked_request(con, tid)
            db.record_effect(con, ctx.caller.writer, params["request_id"], "chat.send",
                             {"accepted": True, "said": f"task T{tid} opened for {agent}", "request": int(first["id"])})
        ok, msg, tid = actions.task_new(me, agent, textsafe.oneline(title).strip(), textsafe.text(body).strip(), timeout_s=secs, effect=effect)
        if not ok:
            raise rpc.refused(msg)
        con = db.connect()
        try:
            rid = int(db.first_linked_request(con, tid)["id"])
        finally:
            con.close()
        return {"accepted": True, "said": msg, "request": rid}
    sent: list[int] = []

    def within(con, note, rid=None):
        sent.append(rid)
        db.record_effect(con, ctx.caller.writer, params["request_id"], "chat.send", {"accepted": True, "said": note, "request": rid})
    ok, msg, rid = askmod.say(agent, textsafe.text(body).strip(), requester=me, within=within)
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg, "request": rid}


def _waits_on_person(row) -> bool:
    return row["target"] == db.HUMAN or actions._is_answer_to_human(row)


def _send_under(ctx, params, agent: str, parent: int, body: str) -> dict:
    """A message that replies to `parent`: its answer when it waits on the person, else a say to the agent under it."""
    con = db.connect()
    try:
        row = _message(con, parent)
        if {row["target"], row["requester"]} != {db.HUMAN, agent}:
            raise rpc.refused(f"#{parent} is not between the person and {agent}", gate="state")
        if _waits_on_person(row):
            ok, msg = actions.answer_ok(con, parent, body, effect=_record(ctx, params, "chat.send", request=parent))
            if not ok:
                raise rpc.refused(msg)
            return {"accepted": True, "said": msg, "request": parent}
    finally:
        con.close()

    def within(con, note, rid=None):
        db.record_effect(con, ctx.caller.writer, params["request_id"], "chat.send", {"accepted": True, "said": note, "request": rid})
    ok, msg, rid = askmod.say(agent, body, requester=ctx.caller.actor, parent_id=parent, within=within)
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg, "request": rid}


def _answer(ctx, params) -> dict:
    rid, text = _request_id(params), _text(params, "text")
    con = db.connect()
    try:
        row = _message(con, rid)
        if not _waits_on_person(row):
            raise rpc.refused(f"#{rid} asked nothing of the person: there is nothing to answer", gate="state")
        ok, msg = actions.answer_ok(con, rid, text, effect=_record(ctx, params, "chat.answer"))
    finally:
        con.close()
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg}


def _dismiss(ctx, params) -> dict:
    rid = _request_id(params)
    con = db.connect()
    try:
        row = _message(con, rid)
        if db.HUMAN not in (row["target"], row["requester"]):
            raise rpc.refused(f"#{rid} is not between the person and an agent", gate="state")
        ok, msg = actions.dismiss(con, rid, me=ctx.caller.actor, effect=_record(ctx, params, "chat.dismiss"))
    finally:
        con.close()
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg}


def _withdraw(ctx, params) -> dict:
    rid, reason = _request_id(params), params.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise rpc.bad_request("reason is text and not empty, or left out", "reason", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        _message(con, rid)
    finally:
        con.close()
    ok, msg = askmod.withdraw(rid, requester=ctx.caller.actor, reason=reason, effect=_record(ctx, params, "chat.withdraw"))
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg}


def _cancel(ctx, params) -> dict:
    rid, confirm = _request_id(params), params.get("confirm", False)
    if not isinstance(confirm, bool):
        raise rpc.bad_request("confirm is true or false", "confirm", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        row = _message(con, rid)
        if row["requester"] != db.HUMAN:
            raise rpc.refused(f"#{rid} is not a message the person sent", gate="state")
        ok, plan = actions.cancel_plan(con, rid)
        if not ok:
            raise rpc.refused(plan, gate="state")
        if row["delivered_at"] and not confirm:
            raise rpc.RpcError(rpc.REFUSED, "refused", plan, reason=plan, gate="confirm", plan=plan)
    finally:
        con.close()
    ok, msg = askmod.cancel(rid, effect=_record(ctx, params, "chat.cancel"))
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg}


def _seen(ctx, params) -> dict:
    upto = _request_id(params, "up_to")
    con = db.connect()
    try:
        agent = _agent_named(params)
        said = f"{agent}'s messages up to #{upto} are seen"
        con.execute("BEGIN IMMEDIATE")
        try:
            actions.for_you_seen(con, agent, upto)
            db.record_effect(con, ctx.caller.writer, params["request_id"], "chat.seen", {"accepted": True, "said": said})
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return {"accepted": True, "said": said}


def _agent_named(params) -> str:
    agent = params.get("agent")
    if not isinstance(agent, str) or not agent:
        raise rpc.bad_request("agent is an agent's name", "agent", rpc.INVALID_PARAMS)
    if agent not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {agent}", what="agent", name=agent)
    return agent


rpc.METHODS["chat.send"] = rpc.Method(_send, "member", write=True)
rpc.METHODS["chat.answer"] = rpc.Method(_answer, "member", write=True)
rpc.METHODS["chat.dismiss"] = rpc.Method(_dismiss, "member", write=True)
rpc.METHODS["chat.withdraw"] = rpc.Method(_withdraw, "agent", write=True)
rpc.METHODS["chat.cancel"] = rpc.Method(_cancel, "member", write=True)
rpc.METHODS["chat.seen"] = rpc.Method(_seen, "member", write=True)
