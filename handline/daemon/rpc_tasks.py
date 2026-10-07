"""task.new, task.edit, task.withdraw, task.cancel, task.memo.add, edit and delete, and task.done, reopen, discard, merge, queue, draft
and release: a task opened, reshaped, moved or ended through the daemon. They
wrap the actions the commands call; each records its result in `write_effects` inside the transaction of its
effect, so a retry after a crash between the two answers instead of repeating (`rpc._write`).

task.new and task.edit serve `hl task new` and `hl task edit`. task.withdraw is an opener throwing its own task
away and task.cancel the founder ending one with its open requests, both `hl task discard`'s two callers.
task.memo.add, edit and delete serve `hl task memo add`, `edit` and `delete`: the memo and its `write_effects` row are one transaction, and the
holder of a doing task is told after it commits (a crash between loses the telling, never the memo or its answer).
task.done, reopen, discard, merge, queue, draft and release serve the `hl task` commands of those names, and the UI's mark done,
reopen, discard, merge and queue buttons. Each action takes the record of the write and makes it in the transaction of the write; the telling follows the commit. queue and draft change all their tasks or none, where the command goes on past a refusal.
`relation`, `response`, `reply` (task.new), `waits_on` (task.edit) and `to`, `reason` (task.release) have no action behind them yet and are refused."""
from __future__ import annotations

from pathlib import Path

from .. import actions, db
from ..infra import paths, textsafe
from ..team import agents
from ..team import ask as askmod
from . import rpc

_NOT_SERVED = ("relation", "response", "reply", "waits_on")
_NOT_SERVED_RELEASE = ("to", "reason")


def _task_id(params, key: str = "task") -> int:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise rpc.bad_request(f"{key} is a task number", key, rpc.INVALID_PARAMS)
    return v


def _text(params, key: str, required: bool = True) -> str | None:
    v = params.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or (required and not v.strip()):
        raise rpc.bad_request(f"{key} is text" + (" and not empty" if required else ""), key, rpc.INVALID_PARAMS)
    return v


def _span(params, key: str = "time_box") -> int | None:
    v = _text(params, key, required=False)
    if v is None:
        return None
    try:
        return actions.parse_task_timeout(v)
    except ValueError as e:
        raise rpc.bad_request(str(e), key, rpc.INVALID_PARAMS) from None


def _unserved(params, keys=_NOT_SERVED) -> None:
    for k in keys:
        if params.get(k) is not None:
            raise rpc.bad_request(f"{k} is not served yet", k, rpc.INVALID_PARAMS)


def _fail(msg: str) -> rpc.RpcError:
    """A refusal of an action, as the wire says it: a missing task is not_found, anything else refused."""
    if msg.startswith("no task T"):
        return rpc.RpcError(rpc.NOT_FOUND, "not_found", msg, what="task")
    return rpc.refused(msg)


def _accepted(said: str, **more) -> dict:
    return {"accepted": True, "said": said, **more}


def _new(ctx, params) -> dict:
    _unserved(params)
    if ctx.caller.role != "agent":
        raise rpc.refused("a task is opened by an agent, never in the founder's name")
    owner, title, body = _text(params, "owner"), _text(params, "title"), _text(params, "body")
    secs = _span(params)
    if secs is None:
        raise rpc.bad_request("time_box is required", "time_box", rpc.INVALID_PARAMS)
    if owner != db.HUMAN and owner not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no agent named {owner}", what="agent", name=owner)
    prio, expects, parent, blocker = params.get("priority"), params.get("expects"), params.get("parent"), params.get("blocked_by")
    try:
        prio = None if prio is None else actions.parse_priority(prio)
        expects = None if expects is None else actions.parse_expects(expects)
    except ValueError as e:
        raise rpc.bad_request(str(e), None, rpc.INVALID_PARAMS) from None
    for k, v in (("parent", parent), ("blocked_by", blocker)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise rpc.bad_request(f"{k} is a task number", k, rpc.INVALID_PARAMS)
    if blocker is not None:         # refused before anything is opened, as the command does
        con = db.connect()
        try:
            why = actions._blocker_refusal(con, blocker)
        finally:
            con.close()
        if why:
            raise _fail(why)
    me = ctx.caller.actor

    def effect(con, tid: int) -> None:
        if blocker is not None:
            actions.add_block(con, tid, blocker, me)
        db.record_effect(con, ctx.caller.writer, params["request_id"], "task.new", _accepted(f"task T{tid} opened for {owner}", task=tid))

    ok, msg, tid = actions.task_new(me, owner, textsafe.oneline(title).strip(), textsafe.text(body).strip(), parent=parent,
                                    expects=expects, priority=prio, timeout_s=secs, effect=effect)
    if not ok:
        raise _fail(msg)
    return _accepted(msg, task=tid)


def _edit(ctx, params) -> dict:
    _unserved(params)
    tid = _task_id(params)
    owner, title, body = (_text(params, k, required=False) for k in ("owner", "title", "body"))
    secs = _span(params)
    if owner is None and title is None and body is None and secs is None:
        raise rpc.bad_request("edit needs an owner, a title, a body or a time_box", None, rpc.INVALID_PARAMS)
    if owner is not None and owner != db.HUMAN and owner not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no agent named {owner}", what="agent", name=owner)
    me = ctx.caller.actor
    con = db.connect()
    try:
        was_owner = (db.get_task(con, tid) or {"owner": None})["owner"]
        con.execute("BEGIN IMMEDIATE")
        try:
            ok, msg = actions.edit_task(con, tid, me, owner=owner, title=title, timeout_s=secs, body=body)
            if ok:
                db.record_effect(con, ctx.caller.writer, params["request_id"], "task.edit", _accepted(msg))
            con.execute("COMMIT" if ok else "ROLLBACK")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        if not ok:
            raise _fail(msg)
        if body is not None:
            actions.tell_body_edited(con, tid, me)
        if owner is not None:       # the task goes to its new owner now, outside the transaction
            actions.send_moved_task(con, tid, was_owner, me)
    finally:
        con.close()
    return _accepted(msg)


def _end(ctx, params, reason: str, stop: bool, name: str) -> dict:
    tid = _task_id(params)
    me = ctx.caller.actor
    con = db.connect()
    try:
        ok, msg = actions.discard_task(con, tid, me, reason, stop=stop,
                                       effect=lambda c: db.record_effect(c, ctx.caller.writer, params["request_id"], name, _accepted(msg_of(tid, me, reason))))
    finally:
        con.close()
    if not ok:
        raise _fail(msg)
    return _accepted(msg)


def msg_of(tid: int, by: str, reason: str) -> str:
    return f"task T{tid} discarded by {by}: {textsafe.oneline(reason).strip()}"


def _withdraw(ctx, params) -> dict:
    return _end(ctx, params, "withdrawn by whoever opened it", False, "task.withdraw")


def _cancel(ctx, params) -> dict:
    reason = _text(params, "reason", required=False) or "cancelled by the founder"
    return _end(ctx, params, reason, True, "task.cancel")


def _memo_write(ctx, params, name: str, run) -> dict:
    """One memo change in one transaction with its answer; `run(con, me)` returns (ok, message, result extras, what to tell, text)."""
    tid = _task_id(params)
    me = ctx.caller.actor
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if db.get_task(con, tid) is None:
                raise _fail(f"no task T{tid}")
            ok, msg, more, told = run(con, me)
            if not ok:
                raise _fail(msg)
            result = _accepted(msg, **more)
            db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        actions.tell_memo_holder(con, tid, me, *told)
    finally:
        con.close()
    return result


def _memo_id(params) -> int:
    mid = params.get("memo")
    if isinstance(mid, bool) or not isinstance(mid, int):
        raise rpc.bad_request("memo is a memo number", "memo", rpc.INVALID_PARAMS)
    return mid


def _memo_row(con, params, tid: int):
    mid = _memo_id(params)
    row = db.get_memo(con, mid)
    if row is None or row["task_id"] != tid:
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"T{tid} has no memo {mid}", what="memo", name=str(mid))
    return mid


def _memo_add(ctx, params) -> dict:
    text = _text(params, "text")

    def run(con, me):
        ok, msg, mid = actions.memo_add(con, params["task"], me, text, tell=False)
        return ok, msg, {"memo": mid}, ("added", text)
    return _memo_write(ctx, params, "task.memo.add", run)


def _memo_edit(ctx, params) -> dict:
    text = _text(params, "text")
    _memo_id(params)

    def run(con, me):
        mid = _memo_row(con, params, params["task"])
        ok, msg = actions.memo_edit(con, mid, me, text, tell=False)
        return ok, msg, {}, ("edited", text)
    return _memo_write(ctx, params, "task.memo.edit", run)


def _memo_delete(ctx, params) -> dict:
    _memo_id(params)

    def run(con, me):
        mid = _memo_row(con, params, params["task"])
        ok, msg = actions.memo_delete(con, mid, me, tell=False)
        return ok, msg, {}, ("deleted", "")
    return _memo_write(ctx, params, "task.memo.delete", run)


def _recorded(ctx, params, name: str):
    """What an action calls inside its write's transaction: the answer kept under this request_id."""
    me, rid = ctx.caller.writer, params["request_id"]
    return lambda con, msg: db.record_effect(con, me, rid, name, _accepted(msg))


def _task_ids(params, key: str) -> list[int]:
    v = params.get(key)
    if not isinstance(v, list) or not v or any(isinstance(i, bool) or not isinstance(i, int) for i in v):
        raise rpc.bad_request(f"{key} is a list of task numbers", key, rpc.INVALID_PARAMS)
    return v


def _run(ctx, params, act) -> dict:
    """`act(con, me)` returns (ok, message); the refusal or the answer is the wire's."""
    con = db.connect()
    try:
        ok, msg = act(con, ctx.caller.actor)
    finally:
        con.close()
    if not ok:
        raise _fail(msg)
    return _accepted(msg)


def _done(ctx, params) -> dict:
    tid = _task_id(params)
    evidence = _text(params, "evidence", required=False)
    if evidence is not None and not evidence.strip():
        raise rpc.bad_request("evidence is text and not empty", "evidence", rpc.INVALID_PARAMS)
    request = params.get("request")
    if request is not None and (isinstance(request, bool) or not isinstance(request, int)):
        raise rpc.bad_request("request is a request number", "request", rpc.INVALID_PARAMS)
    if (evidence is None) == (request is None):
        raise rpc.bad_request("done takes exactly one of evidence or request", "evidence", rpc.INVALID_PARAMS)
    if params.get("reason") is not None and not isinstance(params["reason"], bool):
        raise rpc.bad_request("reason is true or false", "reason", rpc.INVALID_PARAMS)
    sha = evidence if evidence is not None and not params.get("reason") and actions.looks_like_sha(evidence) else None
    words = None if sha else evidence
    repo = _text(params, "repo", required=False) or (agents.repo_of(ctx.caller.actor) if sha else None)
    if sha and repo is None:
        raise rpc.bad_request("a sha is checked against a repo: name one", "repo", rpc.INVALID_PARAMS)
    return _run(ctx, params, lambda con, me: actions.mark_task_done(
        con, tid, me, sha=sha, request_id=request, reason=words, repo=Path(repo) if repo else None,
        effect=_recorded(ctx, params, "task.done")))


def _reopen(ctx, params) -> dict:
    tid, note = _task_id(params), _text(params, "note", required=False) or ""
    return _run(ctx, params, lambda con, me: actions.reopen_task(con, tid, me, note, effect=_recorded(ctx, params, "task.reopen")))


def _discard(ctx, params) -> dict:
    return _end(ctx, params, _text(params, "reason"), False, "task.discard")


def _merge(ctx, params) -> dict:
    keep, others = _task_id(params, "into"), _task_ids(params, "tasks")
    return _run(ctx, params, lambda con, me: actions.merge_tasks(con, keep, others, me, effect=_recorded(ctx, params, "task.merge")))


def _queue(queued: bool, name: str):
    def handler(ctx, params) -> dict:
        tids = _task_ids(params, "tasks")
        return _run(ctx, params, lambda con, me: actions.set_queued_all(con, tids, me, queued, effect=_recorded(ctx, params, name)))
    return handler


def _release(ctx, params) -> dict:
    _unserved(params, _NOT_SERVED_RELEASE)
    tid = _task_id(params)
    return _run(ctx, params, lambda con, me: actions.release_task(con, tid, me, effect=_recorded(ctx, params, "task.release")))


rpc.METHODS["task.new"] = rpc.Method(_new, "agent", write=True)
rpc.METHODS["task.edit"] = rpc.Method(_edit, "agent", write=True)
rpc.METHODS["task.withdraw"] = rpc.Method(_withdraw, "agent", write=True)
rpc.METHODS["task.cancel"] = rpc.Method(_cancel, "member", write=True)
rpc.METHODS["task.memo.add"] = rpc.Method(_memo_add, "agent", write=True)
rpc.METHODS["task.memo.edit"] = rpc.Method(_memo_edit, "agent", write=True)
rpc.METHODS["task.memo.delete"] = rpc.Method(_memo_delete, "agent", write=True)
rpc.METHODS["task.done"] = rpc.Method(_done, "agent", write=True)
rpc.METHODS["task.reopen"] = rpc.Method(_reopen, "agent", write=True)
rpc.METHODS["task.discard"] = rpc.Method(_discard, "agent", write=True)
rpc.METHODS["task.merge"] = rpc.Method(_merge, "agent", write=True)
rpc.METHODS["task.queue"] = rpc.Method(_queue(True, "task.queue"), "agent", write=True)
rpc.METHODS["task.draft"] = rpc.Method(_queue(False, "task.draft"), "agent", write=True)
rpc.METHODS["task.release"] = rpc.Method(_release, "agent", write=True)
