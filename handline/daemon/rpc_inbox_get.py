"""inbox.get: one request read whole, for `hl read`: what was asked and what came back. Any agent may read any request, as `hl read` has
always allowed (nothing typed into one is private to its two ends), except one addressed to it that its routing table has not yet
passed. The item is the spec's InboxItem, the one inbox.list gives, seen from the caller: `agent` is the other end.

`wait_s` holds the call until the request is settled (landed, or one of the states that never reached its target), at most 600 seconds.
It is served on a thread of its own (`waits`), so the calls behind it on the connection are answered meanwhile, and a client that
hangs up ends the wait."""
from __future__ import annotations

import time

from .. import db
from . import rpc, rpc_inbox

SETTLED = ("landed", *db.NEVER_REACHED_TARGET)
POLL_S = 0.5


def _item_of(con, row, viewer: str) -> dict:
    other = row["requester"] if row["target"] == viewer else row["target"]
    if viewer == db.HUMAN or other == db.HUMAN:
        other = row["requester"] if row["target"] == db.HUMAN else row["target"]
    full = con.execute(f"SELECT *, {db.ANSWER_SQL}, {db.DISMISSED_SQL} FROM requests WHERE id = ?", (row["id"],)).fetchone()
    shown = db.shown_verdict(full)
    return rpc_inbox._item(con, id=row["id"], kind=row["kind"] or "ask", agent=other, body=row["body"] or "", ts=row["ts"],
                           read=row["read_at"] is not None or row["state"] != "queued", answered=shown is not None)


def _get(ctx, params) -> dict:
    rid = params.get("id")
    if isinstance(rid, bool) or not isinstance(rid, int):
        raise rpc.bad_request("id is a request number", "id", rpc.INVALID_PARAMS)
    wait = rpc_inbox._int(params, "wait_s", 0, rpc_inbox.MAX_WAIT_S) or 0
    viewer = ctx.caller.info["name"] if ctx.caller.role == "agent" else db.HUMAN
    until = time.time() + wait
    while True:
        con = db.connect()
        try:
            row = db.get_request(con, rid)
            if row is None or (viewer == row["target"] and viewer != db.HUMAN and not row["edge_ok"]):
                raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no request {rid}", what="request", name=str(rid))
            if row["state"] in SETTLED or not wait or time.time() >= until or ctx.hung_up():
                return _item_of(con, row, viewer)
        finally:
            con.close()
        time.sleep(POLL_S)


rpc.METHODS["inbox.get"] = rpc.Method(_get, "agent", waits=True)
