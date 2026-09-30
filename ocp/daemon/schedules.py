"""T605: messages the daemon types at an agent on a cron schedule, set per agent.

An agent must not schedule its own wakeups (a loop dies with its session), so the daemon does it. An
agent's `schedules` in agent.yaml is a JSON list of {"cron", "message"}; whoever sets it is the
founder (agent.yaml is sealed). When a schedule comes due the message is typed like a say: a closed
session is woken and a missing one started (`messaging.tell_now`). A schedule that fell due while the
agent was RUNNING waits for it to stop; several missed times are one message, and one still undelivered
is never followed by another. A level read from a cursor per schedule, the last time it was handled.
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
MAX_CATCHUP = 2000          # times looked at in one go after a long silence: they are one message anyway


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


def of(agent: str) -> list[dict]:
    """This agent's schedules; none for an agent whose agent.yaml cannot be read."""
    try:
        return parse((agents.read(agent) or {}).get("schedules"))
    except Exception:  # noqa: BLE001 - a bad agent.yaml is doctor's to report, not this sweep's
        return []


def sid(item: dict) -> str:
    """What identifies a schedule: editing its cron or its message makes it a new one."""
    return hashlib.sha1(f"{item['cron']}\n{item['message']}".encode()).hexdigest()[:10]


def latest_due(expr: str, after: int, now: int) -> int | None:
    """The latest time of `expr` in (after, now], or None."""
    c, last, t = cron.parse(expr), None, after
    for _ in range(MAX_CATCHUP):
        n = cron.next_after(c, t)
        if n is None or n > now:
            break
        last, t = n, n
    return last


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


def _agent(con, agent: str, items: list[dict], log, now: int) -> None:
    for item in items:
        key = sid(item)
        at, rid_key = f"schedule_at:{agent}:{key}", f"schedule_rid:{agent}:{key}"
        handled = db.get_cursor(con, at)
        if not handled:                       # seen for the first time: it counts from now, not from the past
            db.set_cursor(con, at, now - now % 60)
            continue
        slot = latest_due(item["cron"], handled, now)
        if slot is None:
            continue
        pending = db.get_request(con, db.get_cursor(con, rid_key)) if db.get_cursor(con, rid_key) else None
        if pending is not None and pending["state"] == "queued" and not pending["delivered_at"]:
            db.set_cursor(con, at, slot)      # the last one is still unread: this time is that one
            continue
        row = db.get_agent_state(con, agent)
        if not _ready(row):
            continue                          # due, and stays due until the agent stops
        body = f"[schedule {item['cron']}] {item['message']}"
        rid = db.add_request(con, REQUESTER, agent, body, None, None, "fire_and_forget", "say", 0, None)
        db.mark_send_now(con, rid)
        askmod.deliver(rid, REQUESTER, agent, body, None,
                       team.Decision(True, "schedule", kind="say", response="fire_and_forget",
                                     creates_gate=False, chain_depth=0))
        db.set_cursor(con, at, slot)
        db.set_cursor(con, rid_key, rid)
        db.add_event(con, agent, "schedule_fired", {"cron": item["cron"], "request_id": rid})
        log(f"  {agent}: schedule '{item['cron']}' is due - message #{rid} sent")
        askmod.push(con, agent, rid)
