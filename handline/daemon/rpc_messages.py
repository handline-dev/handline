"""say, ask, push, push.answer and push.cancelled: an agent's messages and the nudges the daemon types after them, as methods.

say serves `hl say` and ask the question that needs an answer; both are the legacy socket verbs' work (`sends.py`) under the protocol: the
sender is the caller the daemon proves (an agent's own name, `human` for the founder and any device, as the spec's x-identity says), never a
name in the payload. The message and the daemon's record of it are one transaction; the typing at the target follows the commit, on a
thread of its own, as the verbs always did.

push, push.answer and push.cancelled only ask the daemon to type something that is already written. They record their answer first and
act after: a crash between loses one nudge, which the tick sends anyway, and a retry answers without nudging twice. push names a request
only when it is the caller's own."""
from __future__ import annotations

from .. import db
from ..infra import paths, textsafe
from ..team import ask as askmod
from . import rpc, sends


def _sender(ctx) -> tuple[str, str | None]:
    """(who the message is from, who is typing): an agent is itself; the founder and a device are the person."""
    me = ctx.caller.actor
    return me, (me if ctx.caller.role == "agent" else None)


def _record(ctx, params, name: str, **more):
    me, rid = ctx.caller.actor, params["request_id"]
    return lambda con, said, request=None: db.record_effect(con, me, rid, name, {"accepted": True, "said": said,
                                                                                   **({"request": request} if request is not None else {}), **more})


def _text(params, key: str) -> str:
    v = params.get(key)
    if not isinstance(v, str) or not v.strip():
        raise rpc.bad_request(f"{key} is text and not empty", key, rpc.INVALID_PARAMS)
    return v


def _int(params, key: str) -> int | None:
    v = params.get(key)
    if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
        raise rpc.bad_request(f"{key} is a number", key, rpc.INVALID_PARAMS)
    return v


def _say(ctx, params) -> dict:
    target, body = _text(params, "target"), _text(params, "body")
    task, parent, no_task = _int(params, "task"), _int(params, "parent"), params.get("no_task")
    if no_task is not None and (not isinstance(no_task, str) or not no_task.strip()):
        raise rpc.bad_request("no_task is the reason, as text", "no_task", rpc.INVALID_PARAMS)
    me, speaker = _sender(ctx)
    con = db.connect()
    try:
        ok, msg = sends.say_verb(con, ctx.log, requester=me, speaker=speaker, target=target, body=textsafe.text(body), task=task,
                                 no_task=no_task, parent=parent, effect=_record(ctx, params, "say"))
    finally:
        con.close()
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg}


def _ask(ctx, params) -> dict:
    target, title, body = _text(params, "target"), _text(params, "title"), _text(params, "body")
    if params.get("options") is not None:
        raise rpc.bad_request("options are not served yet: no card stores them", "options", rpc.INVALID_PARAMS)
    me, speaker = _sender(ctx)
    record = _record(ctx, params, "ask")
    asked: list[int] = []

    def within(con, rid, note):
        asked.append(rid)
        record(con, note, rid)
    ok, msg, rid = askmod.ask(target, f"{textsafe.oneline(title).strip()}\n\n{textsafe.text(body).strip()}", requester=me, speaker=speaker,
                              notify=False, within=within)
    if not ok:
        raise rpc.refused(msg)
    sends._after(rid, target, ctx.log)
    return {"accepted": True, "said": msg, "request": rid}


def _push(ctx, params) -> dict:
    target, rid = _text(params, "target"), _int(params, "request")
    why = sends.push_check(target)
    if why:
        raise rpc.refused(why)
    me, _ = _sender(ctx)
    con = db.connect()
    try:
        if rid is not None:
            row = db.get_request(con, rid)
            if row is None:
                raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no request #{rid}", what="request", name=str(rid))
            if row["requester"] != me:
                raise rpc.refused(f"#{rid} is not yours to push", gate="state")
        _record(ctx, params, "push")(con, sends._HANDED)
    finally:
        con.close()
    return {"accepted": True, "said": sends.push_act(target, rid, ctx.log)}


def _push_answer(ctx, params) -> dict:
    requester = _text(params, "requester")
    why = sends.push_answer_check(requester)
    if why:
        raise rpc.refused(why)
    con = db.connect()
    try:
        _record(ctx, params, "push.answer")(con, sends._HANDED)
    finally:
        con.close()
    return {"accepted": True, "said": sends.push_answer_act(requester, ctx.log)}


def _push_cancelled(ctx, params) -> dict:
    rid = _int(params, "request")
    if rid is None:
        raise rpc.bad_request("request is a request number", "request", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        if db.get_request(con, rid) is None:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no request #{rid}", what="request", name=str(rid))
        row, why = sends.notify_cancelled_check(con, rid)
        if why:
            raise rpc.refused(why)
        _record(ctx, params, "push.cancelled")(con, sends._HANDED)     # recorded only once the checks pass: a refusal is never kept as a success
    finally:
        con.close()
    return {"accepted": True, "said": sends.notify_cancelled_act(row, ctx.log)}


rpc.METHODS["say"] = rpc.Method(_say, "agent", write=True)
rpc.METHODS["ask"] = rpc.Method(_ask, "agent", write=True)
rpc.METHODS["push"] = rpc.Method(_push, "agent", write=True)
rpc.METHODS["push.answer"] = rpc.Method(_push_answer, "agent", write=True)
rpc.METHODS["push.cancelled"] = rpc.Method(_push_cancelled, "agent", write=True)
