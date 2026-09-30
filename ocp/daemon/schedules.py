"""T605: messages the daemon types at an agent on a cron schedule, set per agent.

An agent must not schedule its own wakeups (a loop dies with its session), so the daemon does it. An
agent's `schedules` in agent.yaml is a JSON list of {"cron", "message"}; whoever sets it is the
founder (agent.yaml is sealed). When a schedule comes due the message is typed like a say: a closed
session is woken and a missing one started (`messaging.tell_now`). A schedule that fell due while the
agent was RUNNING waits for it to stop; several missed times are one message, and one still undelivered
is never followed by another. A level read from a cursor per schedule: the minute it was last handled,
after which any time of the expression up to now is due.
"""
from __future__ import annotations

import hashlib
import json
import time

from .. import db
from ..infra import cron, paths
from ..team import agents, ask as askmod, team
from .states import HANDOFF, RUNNING, STALLED

REQUESTER = "schedule"


def parse(text) -> list[dict]:
    """The schedules in an agent's `schedules` value, validated; ValueError says what is wrong with it."""
    if text in (None, ""):
        return []
    try:
        items = json.loads(text) if isinstance(text, str) else text
    except ValueError as e:
        raise ValueError(f"schedules is not JSON: {e}") from e
    if not isinstance(items, list):
        raise ValueError("schedules is a list of {cron, message}")
    out = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict) or set(it) != {"cron", "message"}:
            raise ValueError(f"schedule {i} is {{cron, message}} and nothing else")
        cron.parse(str(it["cron"]))
        msg = " ".join(str(it["message"]).split())
        if not msg:
            raise ValueError(f"schedule {i} has no message")
        out.append({"cron": " ".join(str(it["cron"]).split()), "message": msg})
    return out


def load(agent: str) -> list[dict]:
    """This agent's schedules; ValueError when its value is bad, and whatever agents.read raises."""
    return parse((agents.read(agent) or {}).get("schedules"))


def of(agent: str) -> list[dict]:
    """`load`, and none for an agent whose agent.yaml or value cannot be read - the sweep's view."""
    try:
        return load(agent)
    except Exception:  # noqa: BLE001 - a bad agent.yaml is doctor's to report, not this sweep's
        return []


def sid(item: dict) -> str:
    """What identifies a schedule: editing its cron or its message makes it a new one."""
    return hashlib.sha1(f"{item['cron']}\n{item['message']}".encode()).hexdigest()[:10]


def _ready(row) -> bool:
    """Is there somewhere to type it now: a session that is not mid-turn, or none (started or woken to read it)."""
    if not row:
        return True
    if row["state"] in (RUNNING, HANDOFF, STALLED):
        return False
    return True


def sweep(con, log, now: int) -> None:
    for agent in paths.list_agents():
        try:
            _agent(con, agent, of(agent), log, now)
        except Exception as e:  # noqa: BLE001 - one agent's schedule is not the tick's failure
            log(f"  {agent}: schedule failed: {type(e).__name__}: {e}")


def _forget_removed(con, agent: str, keep: set[str]) -> None:
    """Drop the cursors of schedules the agent no longer has, so one added back later is new."""
    for (name,) in con.execute("SELECT name FROM cursors WHERE name LIKE ? OR name LIKE ?",
                               (f"schedule_at:{agent}:%", f"schedule_rid:{agent}:%")).fetchall():
        if name.rsplit(":", 1)[1] not in keep:
            db.clear_cursor(con, name)


def _agent(con, agent: str, items: list[dict], log, now: int) -> None:
    _forget_removed(con, agent, {sid(i) for i in items})
    minute = now - now % 60
    for item in items:
        key = sid(item)
        at, rid_key = f"schedule_at:{agent}:{key}", f"schedule_rid:{agent}:{key}"
        handled = db.get_cursor(con, at)
        if not handled:                       # seen for the first time: it counts from now, not from the past
            db.set_cursor(con, at, minute)
            continue
        due = cron.next_after(cron.parse(item["cron"]), handled)
        if due is None or due > now:
            continue
        pending = db.get_request(con, db.get_cursor(con, rid_key)) if db.get_cursor(con, rid_key) else None
        if pending is not None and pending["state"] == "queued" and not pending["delivered_at"]:
            db.set_cursor(con, at, minute)    # the last one is still unread: these times are that one
            continue
        row = db.get_agent_state(con, agent)
        if not _ready(row):
            continue                          # due, and stays due until the agent stops
        body = f"[schedule {item['cron']}] {item['message']}"
        rid = db.add_request(con, REQUESTER, agent, body, None, None, "fire_and_forget", "say", 0, None)
        db.mark_send_now(con, rid)
        askmod.deliver(rid, REQUESTER, agent, body, None,
                       team.Decision(True, "schedule", kind="say", response="fire_and_forget",
                                     chain_depth=0))
        db.set_cursor(con, at, minute)
        db.set_cursor(con, rid_key, rid)
        db.add_event(con, agent, "schedule_fired", {"cron": item["cron"], "request_id": rid})
        log(f"  {agent}: schedule '{item['cron']}' is due - message #{rid} sent")
        askmod.push(con, agent, rid)
