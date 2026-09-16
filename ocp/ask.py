"""ocp ask - the only command agents use (team model section 3).

    ocp ask <target> "<request>" [--files a.ts,b.ts]

The agent supplies a target and a message. Everything else is decided here:
whether the edge is allowed, the intake mode, chain depth, rate limiting, and
what happens to the requester. No model is involved in any of it.

A denied ask returns the reason, so the agent learns the policy rather than
guessing at it or trying to route around it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import db, paths, team

# States an agent may not move itself out of. STALLED is a task defect and
# only a human clears it (section 8.6); without this an agent could launder
# its own STALLED away just by sending a request.
TERMINAL_STATES = {"STALLED"}

# How long a requester waits on an async reply before the daemon re-evaluates
# it anyway. Not a timeout on the request - just a guarantee that a lost reply
# cannot strand an agent.
AWAIT_FALLBACK_S = 3600


def current_agent() -> str | None:
    """Who is asking. Set by `ocp launch` when it execs claude."""
    return os.environ.get("OCP_AGENT")


def _terminal(con, agent: str) -> bool:
    row = db.get_agent_state(con, agent)
    return bool(row and row["state"] in TERMINAL_STATES)


def _inbox(target: str) -> Path:
    d = paths.agent_dir(target) / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def deliver(rid: int, requester: str, target: str, body: str,
            files: str | None, decision: team.Decision) -> Path:
    """Drop the request into the target's inbox.

    The file is the durable record an agent reads on wake; the database row is
    what the daemon schedules from. Both carry the same id so a crash between
    them is recoverable in either direction.
    """
    payload = {
        "id": rid,
        "ts": int(time.time()),
        "from": requester,
        "to": target,
        "relation": decision.relation,
        "kind": decision.kind,
        "response": decision.response,
        "intake": decision.intake,
        "chain_depth": decision.chain_depth,
        "files": [f for f in (files or "").split(",") if f],
        "body": body,
    }
    p = _inbox(target) / f"{rid:06d}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return p


def ask(target: str, body: str, files: str | None = None, requester: str | None = None,
        parent_id: int | None = None) -> tuple[bool, str, int | None]:
    """Route and, if allowed, enqueue. Returns (allowed, message, request id)."""
    requester = requester or current_agent()
    if not requester:
        return False, ("cannot tell who is asking: OCP_AGENT is unset. "
                       "Agents are started with `ocp launch <agent>`, which sets it."), None

    t = team.Team.load()
    con = db.connect()
    try:
        depth = 0
        if parent_id:
            parent = db.get_request(con, parent_id)
            if parent is None:
                return False, f"parent request {parent_id} does not exist", None
            depth = int(parent["chain_depth"]) + 1

        decision = t.route(requester, target, chain_depth=depth)
        if not decision.allowed:
            db.add_request(con, requester, target, body, files, None, None, None,
                           depth, parent_id, state="denied", verdict=decision.reason)
            return False, decision.reason, None

        limited, note = t.rate_limited(con, requester)
        if limited:
            db.add_request(con, requester, target, body, files, decision.relation,
                           decision.response, decision.kind, depth, parent_id,
                           state="denied", verdict=note)
            return False, note, None

        rid = db.add_request(con, requester, target, body, files, decision.relation,
                             decision.response, decision.kind, depth, parent_id)
        deliver(rid, requester, target, body, files, decision)
        db.add_event(con, requester, "ask", {
            "request_id": rid, "target": target, "relation": decision.relation,
            "response": decision.response, "chain_depth": depth,
        })

        # async: the requester parks rather than hold an account while it
        # waits. The fallback wake_at is there so a reply that never arrives
        # cannot strand it (the permanent stop section 8.4 warns about).
        if decision.response == "async" and not _terminal(con, requester):
            db.set_agent_state(con, requester, "PARKED",
                               wake_at=int(time.time()) + AWAIT_FALLBACK_S,
                               detail=f"awaiting reply to request {rid} from {target}")

        note = f"queued as #{rid} for {target} (intake={decision.intake}, " \
               f"response={decision.response}, {note})"
        if decision.creates_gate:
            note += " [escalation: needs a human gate]"
        return True, note, rid
    finally:
        con.close()


def inbox(agent: str, unread_only: bool = True) -> list[dict]:
    con = db.connect()
    try:
        rows = db.queued_for(con, agent) if unread_only else con.execute(
            "SELECT * FROM requests WHERE target = ? ORDER BY id", (agent,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def resolve(rid: int, verdict: str, state: str = "done") -> tuple[bool, str]:
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None:
            return False, f"request {rid} does not exist"
        db.set_request_state(con, rid, state, verdict)
        db.add_event(con, row["target"], "resolve",
                     {"request_id": rid, "state": state, "requester": row["requester"]})

        # The reply is the wake. QUEUED is the design's state for "ready to be
        # dispatched"; the daemon picks it up from there (section 8.2).
        # Only queue the requester when nothing else it asked for is still
        # outstanding, and never out of a terminal state.
        if row["response"] == "async" and not _terminal(con, row["requester"]):
            outstanding = con.execute(
                "SELECT COUNT(*) c FROM requests WHERE requester = ? AND state = 'queued' "
                "AND response = 'async'", (row["requester"],)).fetchone()["c"]
            if outstanding == 0:
                db.set_agent_state(con, row["requester"], "QUEUED", wake_at=None,
                                   detail=f"request {rid} resolved: {state}")
        p = _inbox(row["target"]) / f"{rid:06d}.json"
        if p.exists():
            p.unlink()
        return True, f"request #{rid} -> {state}"
    finally:
        con.close()
