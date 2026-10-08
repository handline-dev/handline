"""operation.get: a long-running operation's row, as its last step left it. Nothing in this daemon starts an operation any more (agent.create
was cut); rows an older daemon wrote are still read. A row still `running` has no thread behind it and is ended `failed`."""
from __future__ import annotations

import json
import time

from .. import db
from . import rpc, stream


def _of(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "step": row["step"], "steps_done": row["steps_done"],
            "steps_total": row["steps_total"], "message": row["message"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None, "agent": row["agent"]}


def _load(con, op: str) -> dict | None:
    row = con.execute("SELECT * FROM operations WHERE id = ?", (op,)).fetchone()
    return _of(row) if row else None


def _orphaned(op: str) -> None:
    """End a running operation, whose thread is gone. Conditional on the row still running, so a second reader ends it once."""
    con = db.connect()
    try:
        cur = con.execute("UPDATE operations SET state = 'failed', message = ?, updated_at = ? WHERE id = ? AND state = 'running'",
                          ("the daemon stopped before it finished", int(time.time()), op))
        if cur.rowcount:
            now = _load(con, op)
            stream.emit(con, "operations", "operation.progress", now, agent=now["agent"])
    finally:
        con.close()


def _get(ctx, params) -> dict:
    op = params.get("operation")
    if not isinstance(op, str) or not op:
        raise rpc.bad_request("operation is an operation id", "operation", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        got = _load(con, op)
        if got is None:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no operation {op}", what="operation", name=op)
        if got["state"] == "running":
            _orphaned(op)
            got = _load(con, op)
        return got
    finally:
        con.close()


rpc.METHODS["operation.get"] = rpc.Method(_get, "member")
