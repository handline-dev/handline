"""task.begin, task.start, task.handoff and task.progress: the verbs an agent uses on its own tasks, through the daemon. They wrap the actions
the commands call (`hl task begin`, `start`, `handoff`, `hl progress`); each records its result in `write_effects` under the writer, in the
transaction of its effect where the action has one transaction (begin, start, progress), so a retry after a crash between the two answers instead
of repeating (`rpc._write`).

task.handoff cannot: its delivery is made by `ask`, on a connection of its own. It leaves an intent first and records its result last; a retry
that finds the intent and the task already with `to` answers without handing it over again. A crash between the delivery and the move can,
on that retry, deliver the note a second time: the move itself is repeated safely."""
from __future__ import annotations

from .. import actions, db
from ..infra import paths, textsafe
from ..session import handoff as handoffmod
from . import rpc, rpc_tasks

_BODY_NOT_SERVED = "body is not served yet: a task's words are its title and the founder's message it links"


def _intent(ctx, params, name: str) -> str:
    return f"intent:{name}:{ctx.caller.writer}:{params['request_id']}"


def _in_one_transaction(ctx, params, name: str, run) -> dict:
    """`run(con)` returns (ok, said, extras); on ok its answer is recorded in the same transaction."""
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            ok, said, more = run(con)
            if not ok:
                con.execute("ROLLBACK")
                raise rpc_tasks._fail(said)
            result = rpc_tasks._accepted(said, **more)
            db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        return result
    finally:
        con.close()


def _begin(ctx, params) -> dict:
    title, why = rpc_tasks._text(params, "title"), rpc_tasks._text(params, "why", required=False)
    if params.get("body") is not None:
        raise rpc.bad_request(_BODY_NOT_SERVED, "body", rpc.INVALID_PARAMS)
    message = rpc_tasks._task_id(params, "message")
    prio, expects = params.get("priority"), params.get("expects")
    try:
        prio = None if prio is None else actions.parse_priority(prio)
        expects = actions.parse_expects(expects) if expects is not None else "report"
    except ValueError as e:
        raise rpc.bad_request(str(e), None, rpc.INVALID_PARAMS) from None
    me = ctx.caller.actor
    clean = textsafe.oneline(title).strip()
    if not clean:
        raise rpc.bad_request("a task needs a title", "title", rpc.INVALID_PARAMS)

    def run(con):
        ok, msg, tid = actions.task_begin(con, me, clean, message, why, priority=prio, expects=expects)
        return ok, msg, {"task": tid}
    return _in_one_transaction(ctx, params, "task.begin", run)


def _start(ctx, params) -> dict:
    tid = rpc_tasks._task_id(params)
    me = ctx.caller.actor
    return _in_one_transaction(ctx, params, "task.start", lambda con: (*actions.task_start(con, me, tid), {}))


def _progress(ctx, params) -> dict:
    tid = None if params.get("task") is None else rpc_tasks._task_id(params)
    note = rpc_tasks._text(params, "note", required=False)
    if note is None:
        raise rpc.bad_request("note is text (empty clears it)", "note", rpc.INVALID_PARAMS)
    me = ctx.caller.actor
    if len(textsafe.text(note).strip()) > handoffmod.PROGRESS_MAX:
        raise rpc.bad_request(f"note is at most {handoffmod.PROGRESS_MAX} characters: keep what is done and what is left", "note", rpc.INVALID_PARAMS)

    def run(con):
        if tid is None:         # what `hl progress` does: the note goes where the caller's own state says, task held or not
            ok, where = handoffmod.set_progress(con, me, note)
            return ok, f"progress on {where} kept; the handoff and the wake prompt carry it", {}
        row = db.get_task(con, tid)
        if row is None:
            return False, f"no task T{tid}", {}
        if row["owner"] != me:
            raise rpc.refused(f"task T{tid} is {row['owner']}'s: progress is kept by whoever holds it", gate="state")
        if db.current_task(con, me) in (tid, None):
            ok, where = handoffmod.set_progress(con, me, note)     # the same place `hl progress` keeps it
            return ok, f"progress on {where} kept; the handoff and the wake prompt carry it", {}
        db.set_meta(con, handoffmod._task_progress_key(tid), textsafe.text(note).strip() or None)
        return True, f"progress on T{tid} kept; the handoff and the wake prompt carry it", {}
    return _in_one_transaction(ctx, params, "task.progress", run)


def _progress_get(ctx, params) -> dict:
    if ctx.caller.role != "agent":
        raise rpc.refused("a progress note is an agent's own: only an agent has one")
    con = db.connect()
    try:
        return {"note": handoffmod.get_progress(con, ctx.caller.actor)}
    finally:
        con.close()


def _handoff(ctx, params) -> dict:
    tid, note = rpc_tasks._task_id(params), rpc_tasks._text(params, "note")
    to = rpc_tasks._text(params, "to")
    if to != db.HUMAN and to not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no agent named {to}", what="agent", name=to)
    me = ctx.caller.actor
    key = _intent(ctx, params, "task.handoff")
    con = db.connect()
    try:
        row = db.get_task(con, tid)
        if row is None:
            raise rpc_tasks._fail(f"no task T{tid}")
        again = db.cursor_updated(con, key) is not None
        if again and row["owner"] == to and any(h.get("to") == to for h in db.task_handoffs(con, tid)[-1:]):
            msg = f"T{tid} handed to {to}"      # a retry of a call that did the whole move and died before it said so
            ok = True
        else:
            db.set_cursor(con, key, 1)
            ok, msg = actions.task_handoff(con, me, tid, to, note)
        if not ok:
            db.clear_cursor(con, key)
            raise rpc_tasks._fail(msg)
        result = rpc_tasks._accepted(msg)
        db.record_effect(con, ctx.caller.writer, params["request_id"], "task.handoff", result)
        db.clear_cursor(con, key)
        return result
    finally:
        con.close()


rpc.METHODS["task.begin"] = rpc.Method(_begin, "agent", write=True)
rpc.METHODS["task.start"] = rpc.Method(_start, "agent", write=True)
rpc.METHODS["task.handoff"] = rpc.Method(_handoff, "agent", write=True)
rpc.METHODS["task.progress"] = rpc.Method(_progress, "agent", write=True)
rpc.METHODS["progress.get"] = rpc.Method(_progress_get, "agent")
