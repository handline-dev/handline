"""say: an agent's message, as a method.

It serves `hl say` and is the legacy socket verb's work (`sends.py`) under the protocol: the sender is the caller the daemon proves (an
agent's own name, `human` for the founder and any device, as the spec's x-identity says), never a name in the payload. The message and the
daemon's record of it are one transaction; the typing at the target follows the commit, on a thread of its own, as the verb always did."""
from __future__ import annotations

from .. import db
from ..infra import textsafe
from . import rpc, sends


def _sender(ctx) -> tuple[str, str | None]:
    """(who the message is from, who is typing): an agent is itself; the founder and a device are the person."""
    me = ctx.caller.actor
    return me, (me if ctx.caller.role == "agent" else None)


def _record(ctx, params, name: str, **more):
    me, rid = ctx.caller.writer, params["request_id"]
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


rpc.METHODS["say"] = rpc.Method(_say, "agent", write=True)
