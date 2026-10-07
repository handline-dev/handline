"""chat.send_now: the founder's queued message is typed to its agent now, ahead of the daemon's delivery order. It serves
the chat page's "send now" button (`actions.send_now`); no hl command does. The request keeps its id and goes on as sent."""
from __future__ import annotations

from .. import db
from ..team import ask as askmod
from . import rpc


def _send_now(ctx, params) -> dict:
    rid = params.get("request")
    if isinstance(rid, bool) or not isinstance(rid, int):
        raise rpc.bad_request("request is the integer id of a queued message", "request", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None or row["requester"] != db.HUMAN:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no message {rid} of yours", what="request", name=str(rid))
        said = f"message {rid} is told to {row['target']} now"
        con.execute("BEGIN IMMEDIATE")
        try:
            marked = db.mark_send_now(con, rid)
            if marked:
                db.record_effect(con, ctx.caller.writer, params["request_id"], "chat.send_now", {"accepted": True, "said": said})
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        if not marked:
            raise rpc.refused(f"message {rid} is no longer waiting: it was sent or closed")
        askmod.push(con, row["target"])
        return {"accepted": True, "said": said}
    finally:
        con.close()


rpc.METHODS["chat.send_now"] = rpc.Method(_send_now, "member", write=True)
