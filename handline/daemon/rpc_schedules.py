"""schedule.add, schedule.edit and schedule.remove: the founder's per-agent schedules, written through the daemon. They serve
`hl schedule add`, `edit` and `remove`, which write the same agent.yaml value the same way (actions/schedule.py).

A schedule's id is the number of its agent plus its cron and message (`schedule_id`), so changing either makes a new one,
as `schedules.sid` does, and two agents with the same schedule have two ids. add and edit answer the id the schedule has
now. Each result is recorded in `write_effects` once the file is written, so a retry after a lost reply answers it and does
not add a second schedule or find the edited one gone."""
from __future__ import annotations

import hashlib

from .. import actions, db
from ..infra import paths
from . import rpc
from ..team import schedules


def schedule_id(agent: str, item: dict) -> int:
    """The integer the spec gives a schedule: ten hex digits of sha1 over the agent and `schedules.sid`."""
    return int(hashlib.sha1(f"{agent}\n{schedules.sid(item)}".encode()).hexdigest()[:10], 16)


def _agent(params, key: str) -> str:
    name = params.get(key)
    if not isinstance(name, str) or not name:
        raise rpc.bad_request(f"{key} is an agent's name", key, rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _text(params, key: str, required: bool) -> str | None:
    v = params.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise rpc.bad_request(f"{key} is text and not empty", key, rpc.INVALID_PARAMS)
    return v


def _find(params) -> tuple[str, int, dict]:
    """(agent, 1-based index, schedule) of the schedule the call names; not_found when no agent has it."""
    sid = params.get("schedule")
    if isinstance(sid, bool) or not isinstance(sid, int):
        raise rpc.bad_request("schedule is a schedule id", "schedule", rpc.INVALID_PARAMS)
    for agent in paths.list_agents():
        for i, item in enumerate(schedules.of(agent), 1):
            if schedule_id(agent, item) == sid:
                return agent, i, item
    raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no schedule {sid}", what="schedule", name=str(sid))


def _wrote(ctx, params, name: str, said: str, **more) -> dict:
    result = {"accepted": True, "said": said, **more}
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
    finally:
        con.close()
    return result


def _bad(e: Exception, field: str = "cron") -> rpc.RpcError:
    return rpc.bad_request(str(e), field, rpc.INVALID_PARAMS)


def _normal(cron: str, message: str) -> dict:
    """The schedule as it is kept: the cron and the message with their spaces squeezed."""
    return {"cron": " ".join(cron.split()), "message": " ".join(message.split())}


def _add(ctx, params) -> dict:
    agent, cron, message = _agent(params, "target"), _text(params, "cron", True), _text(params, "message", True)
    item = _normal(cron, message)
    try:
        if item in schedules.load(agent):
            raise rpc.refused(f"{agent} has this schedule already", gate="state")
        actions.schedule_add(agent, cron, message)
    except ValueError as e:
        raise _bad(e) from None
    return _wrote(ctx, params, "schedule.add", f"{agent}: scheduled {item['cron']}", schedule=schedule_id(agent, item))


def _edit(ctx, params) -> dict:
    agent, index, was = _find(params)
    target = _agent(params, "target") if params.get("target") is not None else agent
    cron, message = _text(params, "cron", False) or was["cron"], _text(params, "message", False) or was["message"]
    item = _normal(cron, message)
    try:
        if target == agent:
            if item != was and item in schedules.load(agent):
                raise rpc.refused(f"{agent} has this schedule already", gate="state")
            actions.schedule_edit(agent, index, cron, message)
        else:
            if item in schedules.load(target):
                raise rpc.refused(f"{target} has this schedule already", gate="state")
            actions.schedule_add(target, cron, message)
            actions.schedule_remove(agent, index)
    except ValueError as e:
        raise _bad(e) from None
    return _wrote(ctx, params, "schedule.edit", f"{target}: schedule is now {item['cron']}", schedule=schedule_id(target, item))


def _remove(ctx, params) -> dict:
    agent, index, item = _find(params)
    actions.schedule_remove(agent, index)
    return _wrote(ctx, params, "schedule.remove", f"{agent}: removed {item['cron']}")


rpc.METHODS["schedule.add"] = rpc.Method(_add, "founder", write=True)
rpc.METHODS["schedule.edit"] = rpc.Method(_edit, "founder", write=True)
rpc.METHODS["schedule.remove"] = rpc.Method(_remove, "founder", write=True)
