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
import sys
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

# Where a runaway goes. Exceeding a limit is not a reason to silently drop the
# request on the floor - a human has to see that the team got stuck in a loop
# (team model section 6).
ESCALATION_TARGET = "human"

# A review that bounces on the same file this many times is not a review
# problem any more. Two agents that cannot agree is usually a spec question.
REVIEW_PINGPONG_LIMIT = 3


# Who a shell with no OCP_AGENT belongs to. Every agent's terminal has it set,
# because `ocp launch` sets it before exec'ing claude - so an interactive shell
# without it is a human at their own prompt, and that is who this is.
HUMAN = "human"


def current_agent() -> str | None:
    """Who is asking, or None when nothing says. Set by `ocp launch`."""
    return os.environ.get("OCP_AGENT") or None


def asking_agent() -> str | None:
    """current_agent(), reading a human at their own prompt as the human.

    Only the commands a human types may make that assumption. "I cannot tell
    who you are" and "you are the human" are not the same answer - the human is
    the escalation target and the most privileged node in the
    routing table, so handing it to any process that merely lost the variable
    is a fail-open. A human has a terminal; a script that dropped its
    environment does not, and it gets the refusal it used to get.
    """
    return current_agent() or (HUMAN if sys.stdin.isatty() else None)


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


def _escalate(con, requester: str, target: str, body: str, files: str | None,
              chain_id: str | None, depth: int, parent_id: int | None,
              reason: str) -> int:
    """Raise a runaway to a human instead of dropping it silently."""
    text = (f"[escalation] {requester} -> {target} was stopped: {reason}\n\n"
            f"Original request:\n{body}")
    rid = db.add_request(con, requester, ESCALATION_TARGET, text, files,
                         None, "fire_and_forget", "escalate", depth, parent_id,
                         chain_id=chain_id)
    deliver(rid, requester, ESCALATION_TARGET, text, files,
            team.Decision(True, reason, kind="escalate", response="fire_and_forget",
                          creates_gate=True, chain_depth=depth))
    db.add_event(con, requester, "escalate",
                 {"request_id": rid, "reason": reason, "blocked_target": target})
    return rid


def _cycle_in_chain(con, chain_id: str | None, requester: str, target: str) -> str | None:
    """Detect A->B->A inside one chain (team model section 6).

    Depth alone does not catch this: two agents can bounce a request back and
    forth well inside the depth limit and never finish.

    A chain with the human at one end is exempt. What this stops is a loop no
    one is watching, and the human is who a loop is escalated *to* - refusing
    "you replied to my reply" would make a threaded conversation illegal from
    its second turn, and the escalation it raised would be addressed to the
    same human who is typing.
    """
    if not chain_id or HUMAN in (requester, target):
        return None
    rows = db.chain_members(con, chain_id)
    for r in rows:
        if r["requester"] == target and r["target"] == requester:
            return (f"cycle: {target} already asked {requester} in this chain "
                    f"(request #{r['id']}), and this would send it straight back")
    return None


def _review_pingpong(con, chain_id: str | None, target: str, files: str | None) -> str | None:
    """Count review round-trips on the same file within one chain."""
    if not chain_id or target != "reviewer" or not files:
        return None
    wanted = {f for f in files.split(",") if f}
    if not wanted:
        return None
    trips = 0
    for r in db.chain_members(con, chain_id):
        if r["target"] != "reviewer":
            continue
        have = {f for f in (r["files"] or "").split(",") if f}
        if have & wanted:
            trips += 1
    if trips >= REVIEW_PINGPONG_LIMIT:
        return (f"review ping-pong: {trips} rounds on {', '.join(sorted(wanted))} "
                f"in this chain. Two agents who cannot agree is usually a spec question.")
    return None


def _merge_into_queued(con, requester: str, target: str, body: str,
                       files: str | None) -> int | None:
    """Fold this ask into one already queued for the same target.

    Team model section 5: a review belongs to a PR, not to every commit in it.
    Merging keeps the queue from filling with near-identical asks that would
    each cost a full turn to answer.
    """
    rows = con.execute(
        "SELECT * FROM requests WHERE requester = ? AND target = ? AND state = 'queued' "
        "ORDER BY id DESC LIMIT 1", (requester, target)).fetchone()
    if rows is None:
        return None
    merged_body = rows["body"].rstrip() + "\n\n--- also ---\n" + body.strip()
    merged_files = ",".join(sorted(
        {f for f in ((rows["files"] or "") + "," + (files or "")).split(",") if f}
    )) or None
    con.execute("UPDATE requests SET body = ?, files = ?, ts = ? WHERE id = ?",
                (merged_body, merged_files, int(time.time()), rows["id"]))
    return int(rows["id"])


def ask(target: str, body: str, files: str | None = None, requester: str | None = None,
        parent_id: int | None = None, mergeable: bool = False) -> tuple[bool, str, int | None]:
    """Route and, if allowed, enqueue. Returns (allowed, message, request id)."""
    requester = requester or asking_agent()
    # Nobody may send as the human. That name carries two
    # privileges - the routing table lets it reach every agent, and a chain
    # through it is exempt from the loop guards, because the guards exist to
    # reach a human and one is typing. An agent that could claim the name would
    # hold both: one spoofed hop turns a refused A->B->A into a legal loop.
    # `ocp launch` sets OCP_AGENT, so a caller that has one is not the human.
    speaker = current_agent()
    if requester == HUMAN and speaker not in (None, HUMAN):
        return False, (f"{speaker} may not ask as {HUMAN}: that name is the human's, "
                       f"and it is exempt from the loop guards."), None
    if not requester:
        return False, ("cannot tell who is asking: OCP_AGENT is unset and this is "
                       "not an interactive shell. Agents are started with "
                       "`ocp launch <agent>`, which sets it; otherwise say who "
                       "is asking with --of."), None

    t = team.Team.load()
    con = db.connect()
    try:
        depth, chain_id = 0, None
        if parent_id:
            parent = db.get_request(con, parent_id)
            if parent is None:
                return False, f"parent request {parent_id} does not exist", None
            depth = int(parent["chain_depth"]) + 1
            chain_id = parent["chain_id"]
            # The human's turn starts the count again, and keeps the thread.
            # The limit is there so a runaway ends up in front of a human; a
            # reply typed by that human is the limit having worked, not a hop
            # towards it.
            if requester == HUMAN:
                depth = 0

        # A loop is stopped and escalated, never silently dropped: a request
        # that vanishes leaves the team stuck with nobody knowing why.
        for detect in (_cycle_in_chain(con, chain_id, requester, target),
                       _review_pingpong(con, chain_id, target, files)):
            if detect:
                eid = _escalate(con, requester, target, body, files, chain_id,
                                depth, parent_id, detect)
                return False, f"{detect}\nEscalated to {ESCALATION_TARGET} as #{eid}.", None

        decision = t.route(requester, target, chain_depth=depth)
        if not decision.allowed:
            db.add_request(con, requester, target, body, files, None, None, None,
                           depth, parent_id, state="denied", verdict=decision.reason,
                           chain_id=chain_id)
            # Hitting the depth limit is a runaway, not an ordinary refusal.
            if "chain depth" in decision.reason and target != ESCALATION_TARGET:
                eid = _escalate(con, requester, target, body, files, chain_id,
                                depth, parent_id, decision.reason)
                return False, (f"{decision.reason}\nEscalated to {ESCALATION_TARGET} "
                               f"as #{eid}."), None
            return False, decision.reason, None

        limited, note = t.rate_limited(con, requester)
        if limited:
            now = int(time.time())
            window = now - team.ASK_WINDOW_S
            # The first refusal keeps the text, so nothing an agent wrote is
            # lost by being refused. An identical one behind it is the same
            # refusal, not a second event: a retry loop against this limit
            # spent 35 request numbers in six seconds, and every row it wrote
            # was a message that had never been sent to anyone.
            rid = db.standing_denial(con, requester, target, body, window)
            if rid is None:
                rid = db.add_request(con, requester, target, body, files, decision.relation,
                                     decision.response, decision.kind, depth, parent_id,
                                     state="denied", verdict=note, chain_id=chain_id)
            oldest = db.oldest_counted_ask(con, requester, window)
            free_at = (oldest + team.ASK_WINDOW_S) if oldest else now + team.ASK_WINDOW_S
            # Section 8.6: too many asks is a resource problem, so it throttles
            # and clears itself when a slot frees, rather than needing a human
            # the way a task defect does.
            if not _terminal(con, requester):
                db.set_agent_state(con, requester, "THROTTLED",
                                   wake_at=free_at, detail=note)
            when = time.strftime("%H:%M", time.localtime(free_at))
            return False, (f"{note} - {requester} is THROTTLED. The window rolls, so the "
                           f"next slot frees at {when}; wait for it rather than retrying, "
                           f"because a refusal sends nothing. Your text is kept as #{rid}."), None

        if mergeable:
            merged = _merge_into_queued(con, requester, target, body, files)
            if merged is not None:
                row = db.get_request(con, merged)
                deliver(merged, requester, target, row["body"], row["files"], decision)
                return True, f"merged into the queued request #{merged} for {target}", merged

        rid = db.add_request(con, requester, target, body, files, decision.relation,
                             decision.response, decision.kind, depth, parent_id,
                             chain_id=chain_id)
        deliver(rid, requester, target, body, files, decision)
        db.add_event(con, requester, "ask", {
            "request_id": rid, "target": target, "relation": decision.relation,
            "response": decision.response, "chain_depth": depth,
        })

        # async: the requester parks rather than hold an account while it
        # waits. The fallback wake_at is there so a reply that never arrives
        # cannot strand it (the permanent stop section 8.4 warns about).
        #
        # The human is not one of them. They hold no account, there is no
        # session to park and nothing will ever wake them, so a state row in
        # their name is bookkeeping about nobody - and it is drawn: `ocp ps`
        # listed the founder among the agents, parked, for as long as it sat
        # there.
        if decision.response == "async" and requester != HUMAN \
                and not _terminal(con, requester):
            db.set_agent_state(con, requester, "PARKED",
                               wake_at=int(time.time()) + AWAIT_FALLBACK_S,
                               detail=f"awaiting reply to request {rid} from {target}")

        note = f"queued as #{rid} for {target} (intake={decision.intake}, " \
               f"response={decision.response}, {note})"
        if decision.creates_gate:
            note += " [escalation: needs a human gate]"
        else:
            note += "\n" + push(con, target)
        return True, note, rid
    finally:
        con.close()


def push(con, target: str) -> str:
    """Get the request to the agent now, rather than at the next daemon tick.

    One command has to do the whole job. An agent that asks a subordinate for
    something and is told "queued" has not dispatched anything - it has left a
    note for a daemon, and if that agent has no terminal the note is for a
    human. Being unable to finish the job is what sends an agent looking for a
    subagent instead.

    Everything here is the daemon's own function, so a request delivered by the
    sender and one delivered by the tick are the same delivery. The daemon
    stays the safety net: anything this could not finish is still queued.
    """
    from . import daemon

    if target == HUMAN:
        return "it is on the founder's screen (`ocp bridge`)"
    if target not in paths.list_agents():
        return f"{target} is not registered with ocp - nothing can be started for it"
    said = []
    row = db.get_agent_state(con, target)
    if not (row and row["terminal"]):
        # No row at all is the same case as a row with no terminal: an agent
        # that has never been started has nowhere to be told anything.
        said.append(daemon._start_for_work(con, target, lambda _m: None))
        row = db.get_agent_state(con, target)
    if row and row["closed_at"]:
        ok, why = daemon.wake_agent(con, target, lambda _m: None)
        said.append(f"{target}: {why}")
    if daemon.deliver_queued(con, target, lambda _m: None):
        said.append(f"delivered to {target}")
    elif not said:
        why, _clears = _delivery_reason(con, target)
        said.append(why or f"{target} has it; it will read it when it is free")
    return "  " + "; ".join(x.strip() for x in said if x)


def push_answer(con, requester: str) -> str:
    """Put the answer in front of whoever asked, now.

    The other half of `push`. An agent that finishes a job and resolves the
    request has reported to its parent - and until this, that report sat on a
    row until a daemon tick happened to read it out. Down and up are the same
    loop, so they are finished the same way.
    """
    from . import daemon

    if requester == HUMAN:
        return "  the founder reads it on `ocp bridge`"
    if requester not in paths.list_agents():
        return f"  {requester} is not registered with ocp - nothing to tell"
    row = db.get_agent_state(con, requester)
    said = []
    if row and row["closed_at"]:
        ok, why = daemon.wake_agent(con, requester, lambda _m: None)
        said.append(f"{requester}: {why}")
    if daemon.deliver_answers(con, requester, lambda _m: None):
        said.append(f"{requester} has been told")
    elif not said:
        said.append(f"{requester} will be told when it is free")
    return "  " + "; ".join(x.strip() for x in said if x)


def _delivery_reason(con, target: str) -> tuple[str, bool]:
    """Why delivery did not happen, in the daemon's own words."""
    from . import actions

    return actions._delivery_block(con, target, *actions._world())


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
        if row["response"] == "async" and row["requester"] != HUMAN \
                and not _terminal(con, row["requester"]):
            outstanding = con.execute(
                "SELECT COUNT(*) c FROM requests WHERE requester = ? AND state = 'queued' "
                "AND response = 'async'", (row["requester"],)).fetchone()["c"]
            if outstanding == 0:
                db.set_agent_state(con, row["requester"], "QUEUED", wake_at=None,
                                   detail=f"request {rid} resolved: {state}")
        p = _inbox(row["target"]) / f"{rid:06d}.json"
        if p.exists():
            p.unlink()
        return True, f"request #{rid} -> {state}\n" + push_answer(con, row["requester"])
    finally:
        con.close()
