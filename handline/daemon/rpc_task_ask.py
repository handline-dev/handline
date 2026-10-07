"""task.ask: the founder asks an agent about a task from its page (`actions.ask_on_task`): a say linked to the task, whose holder does
not change, and the agent's reply joins the task. No hl command serves it: an agent answers with `hl say --parent`. The message, its link
to the task, the task_question event and the kept reply are written in one transaction."""
from __future__ import annotations

from .. import actions, db
from ..infra import paths, textsafe
from ..team import ask as askmod
from . import rpc


def _ask(ctx, params) -> dict:
    tid, to, text = params.get("task"), params.get("to"), params.get("text")
    if isinstance(tid, bool) or not isinstance(tid, int):
        raise rpc.bad_request("task is a task number", "task", rpc.INVALID_PARAMS)
    if not isinstance(to, str) or not to:
        raise rpc.bad_request("to is an agent's name", "to", rpc.INVALID_PARAMS)
    if not isinstance(text, str) or not text.strip():
        raise rpc.bad_request("text is the question, not empty", "text", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        if db.get_task(con, tid) is None:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no task T{tid}", what="task", id=tid)
    finally:
        con.close()
    if to not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {to}", what="agent", name=to)
    body = actions.question_body(tid, textsafe.text(text).strip())

    def within(con, note, rid):
        db.link_request(con, tid, rid)
        db.add_event(con, db.HUMAN, "task_question", {"task": tid, "request": rid}, dedup_key=f"task_question:{rid}")
        db.record_effect(con, ctx.caller.writer, params["request_id"], "task.ask", {"accepted": True, "said": note, "request": rid})
    ok, msg, rid = askmod.say(to, body, requester=db.HUMAN, within=within)
    if not ok:
        raise rpc.refused(msg)
    return {"accepted": True, "said": msg, "request": rid}


rpc.METHODS["task.ask"] = rpc.Method(_ask, "member", write=True)
