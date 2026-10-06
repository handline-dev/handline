"""agent.create and operation.get: a new agent registered, given a worktree and started, as one long-running operation.
`agent.create` answers at once with the operation's id; the work runs on a thread of its own and each step is a row update
and an `operation.progress` event on the `operations` topic, so a client watching the topic or asking `operation.get` sees
it finish or fail. It serves `hl agent register` followed by `hl spawn`, which stay as they are.

The steps are `register` (agent.yaml, ROLE.md) and `start` (worktree and terminal). A failure in `start` leaves the agent
registered, as `hl agent register` then `hl spawn` would, and the operation says so. The operation row and the request_id's
reply are written in one transaction before the thread starts, so a retry after a lost reply answers with the same operation and
starts nothing again; an operation the daemon died in the middle of is found `running` with no thread and reported `failed`. A second create of a name
while the first is running is refused (gate state) in that same transaction.
`name` must be `<parent>.<something>` when `parent` is given, as a parent is the part of a name before its last dot;
`account` is refused until a session can be started on a named account, as `session.start` does."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from .. import db
from ..db import effects
from ..infra import paths, textsafe
from ..team import agents
from ..team import spawn as spawnmod
from ..team import team as teammod
from . import rpc, stream

LIVE: set[str] = set()
_live_lock = threading.Lock()
_idle = threading.Condition(_live_lock)
STEPS = ("register", "start")
KEEP_S = 7 * 24 * 3600


def _of(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "step": row["step"], "steps_done": row["steps_done"],
            "steps_total": row["steps_total"], "message": row["message"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None, "agent": row["agent"]}


def _load(con, op: str) -> dict | None:
    row = con.execute("SELECT * FROM operations WHERE id = ?", (op,)).fetchone()
    return _of(row) if row else None


def _progress(op: str, **fields) -> None:
    """Change an operation's row and tell the `operations` topic, in that order."""
    con = db.connect()
    try:
        sets = {**fields, "updated_at": int(time.time())}
        if "result" in sets:
            sets["result_json"] = json.dumps(sets.pop("result"))
        con.execute(f"UPDATE operations SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), op))
        now = _load(con, op)
        stream.emit(con, "operations", "operation.progress", now, agent=now["agent"])
    finally:
        con.close()


def _run(op: str, name: str, repo: str, base: str, kw: dict) -> None:
    step = STEPS[0]
    try:
        _progress(op, step=step, message=f"registering {name}")
        agents.register(name, repo=repo, **kw)
        step = STEPS[1]
        _progress(op, step=step, steps_done=1, message=f"starting {name}")
        ok, said = spawnmod.spawn(name, Path(repo), base=base)
        if ok:
            _progress(op, state="done", step=None, steps_done=2, message=said.strip(), result={"agent": name})
        else:
            _progress(op, state="failed", message=f"{name} is registered; starting it failed: {said.strip()}",
                      result={"agent": name, "registered": True})
    except Exception as e:  # noqa: BLE001 - whatever stopped it is the operation's message, never a dead thread
        _progress(op, state="failed", message=f"{step} failed: {e}", result={"agent": name, "registered": step == STEPS[1]})
    finally:
        with _live_lock:
            LIVE.discard(op)
            _idle.notify_all()


def wait_idle(timeout: float = 10.0) -> bool:
    """Wait until no agent.create thread is running; False if one still is after `timeout` seconds."""
    with _live_lock:
        return _idle.wait_for(lambda: not LIVE, timeout)


def _text(params: dict, key: str, required: bool = False) -> str | None:
    v = params.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise rpc.bad_request(f"{key} is text", key, rpc.INVALID_PARAMS)
    return v


def _create(ctx, params) -> dict:
    name, role, repo = _text(params, "name", True), _text(params, "role", True), _text(params, "repo", True)
    base, model, effort = _text(params, "base"), _text(params, "model"), _text(params, "effort")
    parent = _text(params, "parent")
    if params.get("account") is not None:
        raise rpc.bad_request("a new agent's account is chosen when it starts; none can be named yet", "account", rpc.INVALID_PARAMS)
    if effort and effort not in agents.VALID_EFFORT:
        raise rpc.bad_request(f"effort is one of {', '.join(agents.VALID_EFFORT)}", "effort", rpc.INVALID_PARAMS)
    if "/" in name or name.startswith(".") or textsafe.has_unsafe(name) or not agents.is_plain_text(name):
        raise rpc.bad_request("name is an agent's name, plain text", "name", rpc.INVALID_PARAMS)
    if name in paths.list_agents():
        raise rpc.refused(f"{name} is already an agent", gate="state")
    if parent is not None:
        if parent not in paths.list_agents():
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no agent {parent}", what="agent", name=parent)
        if teammod.parent_name(name) != parent:
            raise rpc.bad_request(f"name is {parent}.<something> under that parent", "name", rpc.INVALID_PARAMS)
    if not Path(repo).is_dir():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no repo {repo}", what="repo", name=repo)
    kw = {k: v for k, v in (("role", role), ("model", model), ("effort", effort)) if v}
    op = uuid.uuid4().hex
    now = int(time.time())
    with _live_lock:
        LIVE.add(op)                # before the row exists, so nothing reads a running row of a live operation as dead
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")          # the check for a create in flight, the row and the retry's answer are one step
        try:
            for (other,) in con.execute("SELECT id FROM operations WHERE kind = 'agent.create' AND agent = ? AND state = 'running'", (name,)):
                with _live_lock:
                    going = other in LIVE
                if going:
                    raise rpc.refused(f"{name} is already being created", gate="state")
            con.execute("DELETE FROM operations WHERE updated_at < ?", (now - KEEP_S,))
            con.execute("INSERT INTO operations(id, kind, state, step, steps_done, steps_total, message, agent, created_at, updated_at) "
                        "VALUES(?, 'agent.create', 'running', NULL, 0, ?, ?, ?, ?, ?)",
                        (op, len(STEPS), f"creating {name}", name, now, now))
            result = {"accepted": True, "said": f"creating {name}", "operation": op}
            effects.record_effect(con, ctx.caller.actor, params["request_id"], "agent.create", result)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        stream.emit(con, "operations", "operation.progress", _load(con, op), agent=name)
    except BaseException:
        with _live_lock:
            LIVE.discard(op)
        raise
    finally:
        con.close()
    threading.Thread(target=_run, args=(op, name, str(Path(repo).resolve()), base or "main", kw), daemon=True,
                     name=f"agent.create-{name}").start()
    return result


def _orphaned(op: str) -> None:
    """End a running operation whose thread is gone. Conditional on the row still running, so a thread's own final write is never undone."""
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
    with _live_lock:
        live = op in LIVE       # asked before the row is read: a thread writes its last state, then leaves LIVE
    con = db.connect()
    try:
        got = _load(con, op)
        if got is None:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no operation {op}", what="operation", name=op)
        if got["state"] == "running" and not live:
            _orphaned(op)
            got = _load(con, op)
        return got
    finally:
        con.close()


rpc.METHODS["agent.create"] = rpc.Method(_create, "founder", write=True)
rpc.METHODS["operation.get"] = rpc.Method(_get, "member")
