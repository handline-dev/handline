"""Requests between agents (team model section 3): what `ocp task new` and `ocp say` write.

The agent supplies a target and a message. Everything else is decided here:
whether the edge is allowed, chain depth, rate limiting, and
what happens to the requester. No model is involved in any of it.

A denied request returns the reason, so the agent learns the policy rather than
guessing at it or trying to route around it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .. import db
from ..infra import atomic, paths, textsafe
from ..infra.yamlcompat import ConfigError
from . import team
from ..infra import timefmt

# States an agent may not move itself out of. STALLED is a task defect and
# only a human clears it (section 8.6); without this an agent could launder
# its own STALLED away just by sending a request.
TERMINAL_STATES = {"STALLED"}

# A review that bounces on the same file this many times is not a review
# problem any more. Two agents that cannot agree is usually a spec question.
REVIEW_PINGPONG_LIMIT = 3


# Who a shell with no OCP_AGENT belongs to. Every agent's terminal has it set,
# because `ocp launch` sets it before exec'ing claude - so an interactive shell
# without it is a human at their own prompt, and that is who this is.
HUMAN = db.HUMAN


def current_agent() -> str | None:
    """Who is asking, or None when nothing says. Set by `ocp launch`."""
    return os.environ.get("OCP_AGENT") or None


def asking_agent() -> str | None:
    """current_agent(), reading a human at their own prompt as the human.

    Only the commands a human types may make that assumption. "I cannot tell
    who you are" and "you are the human" are not the same answer - the human is
    the founder and the most privileged node in the
    routing table, so handing it to any process that merely lost the variable
    is a fail-open. A human has a terminal; a script that dropped its
    environment does not, and it gets the refusal it used to get.
    """
    return current_agent() or (HUMAN if sys.stdin.isatty() else None)


def _terminal(con, agent: str) -> bool:
    row = db.get_agent_state(con, agent)
    return bool(row and row["state"] in TERMINAL_STATES)


def forget_inbox(target: str, rid: int) -> None:
    """Remove the inbox file a request wrote for `target`, once the request is out of the queue."""
    p = _inbox(target) / f"{rid:06d}.json"
    if p.exists():
        p.unlink()


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
        "chain_depth": decision.chain_depth,
        "files": [f for f in (files or "").split(",") if f],
        "body": body,
    }
    p = _inbox(target) / f"{rid:06d}.json"
    atomic.write_text(p, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p


def _stop_runaway(con, requester: str, target: str, body: str, files: str | None,
              chain_id: str | None, depth: int, parent_id: int | None,
              reason: str) -> int:
    """Raise a runaway to a human instead of dropping it silently."""
    text = (f"[stopped] {requester} -> {target} was stopped: {reason}\n\n"
            f"Original request:\n{body}")
    return raise_decision(con, requester, text, reason, files, chain_id=chain_id, depth=depth,
                    parent_id=parent_id, event_extra={"blocked_target": target})


def raise_decision(con, agent: str, text: str, reason: str, files: str | None = None, *,
             chain_id: str | None = None, depth: int = 0, parent_id: int | None = None,
             event_extra: dict | None = None) -> int:
    """Put something only a human can act on in front of them: a decision task of
    the founder's, opened by `agent`, with the request that shows it as its card.

    `agent` is who it is about: the requester of a runaway, or an agent the
    daemon found for itself that it cannot give its work. `reason` is the short
    form kept on the event for `ocp events`. Fire and forget, so nothing is
    typed back at `agent` when the human reads it. Once per shape: exactly the same
    text still waiting from the same agent is that decision, not a second one;
    anything different is its own card, so nothing an agent wrote is lost.
    """
    title = textsafe.oneline(text.strip().split("\n", 1)[0])[:80] or "a decision"
    same = db.open_decision_card_ids(con, agent, body=text)
    if same:
        return same[0]
    rid = db.add_request(con, agent, HUMAN, text, files,
                         None, "fire_and_forget", "task", depth, parent_id,
                         chain_id=chain_id)
    tid = db.new_task(con, title, HUMAN, agent, expects="decision")
    db.set_task_queued(con, tid, HUMAN)
    db.link_request(con, tid, rid)
    deliver(rid, agent, HUMAN, text, files,
            team.Decision(True, reason, kind="task", response="fire_and_forget",
                          chain_depth=depth))
    db.add_event(con, agent, "raise_decision",
                 {"request_id": rid, "task_id": tid, "reason": reason, **(event_extra or {})})
    return rid


def report(con, agent: str, text: str, reason: str) -> int:
    """Tell the human something that asks nothing: a report, closed by reading it.

    `agent` is who it is about. Fire and forget and `fyi`, so nothing is typed
    back at `agent` and nothing waits on the human.
    """
    rid = db.add_request(con, agent, HUMAN, text, None, None, "fire_and_forget",
                         "say", 0, None, fyi=True)
    deliver(rid, agent, HUMAN, text, None,
            team.Decision(True, reason, kind="say", response="fire_and_forget",
                          chain_depth=0))
    db.add_event(con, agent, "report", {"request_id": rid, "reason": reason})
    return rid


def _cycle_in_chain(con, chain_id: str | None, requester: str, target: str,
                    kind: str | None = None) -> str | None:
    """Detect A->B->A inside one chain (team model section 6).

    Depth alone does not catch this: two agents can bounce a request back and
    forth well inside the depth limit and never finish.

    A chain with the human at one end is exempt. What this stops is a loop no
    one is watching, and the human is who a stopped loop is raised *to* - refusing
    "you replied to my reply" would make a threaded conversation illegal from
    its second turn, and the decision it raised would be addressed to the
    same human who is typing.

    T293: a say is exempt too - #1655's "a reply to a say is a say" makes
    A->B->A its ordinary shape, not a runaway; `max_chain_depth` is still the
    backstop, and the daemon (#1657) reports a pair exchanging too many.
    """
    if not chain_id or HUMAN in (requester, target) or kind == SAY_KIND:
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


# T292/T293: the `kind` overrides `ask` accepts for a task or a say, regardless
# of what the routing edge itself carries. `enforce_edges` (daemon delivery)
# must not treat that override as a changed edge, so it skips the kind half of
# its re-check for these - it still checks relation and response.
SAY_KIND = "say"
OVERRIDE_KINDS = {"task", SAY_KIND}


# `speaker=` left out means "whoever's environment this runs in" (OCP_AGENT);
# the daemon serving a socket request has no such environment and passes the
# caller's own, None included.
_ENV = object()


def ask(target: str, body: str, files: str | None = None, requester: str | None = None,
        parent_id: int | None = None, fyi: bool = False,
        kind: str | None = None,
        send_now: bool = False,
        on_behalf_of: bool = False, speaker=_ENV,
        notify: bool = True, card: bool = True) -> tuple[bool, str, int | None]:
    """Route and, if allowed, enqueue. Returns (allowed, message, request id).

    `kind` overrides the routing edge's own `decision.kind` on the request
    row - T292's `task new` uses it to write 'task' regardless of what the
    edge between opener and owner happens to carry, since a task is a task
    whichever edge assigned it.
    `send_now` marks the new row (`db.mark_send_now`) on this same
    connection - `say` needs that mark, and a second connection for it could
    leave it unmarked, and unreachable, if one crashed between the two.

    `on_behalf_of` skips the human-impersonation guard below for a
    `requester` this call already verified from our own database, not a
    live claim by whoever's session happens to be running it. Only for a
    system-triggered, already-authorized delivery (`advance_current_task`).

    `speaker` is who is really typing the command (see `_ENV`). `notify` False
    leaves telling the target to the caller - the daemon, which types the
    notice itself once this has returned the row (T351).
    """
    requester = requester or asking_agent()
    if fyi and kind is None:            # a report asks nothing: it is a say to the founder (T361)
        kind = SAY_KIND
    if fyi and target != HUMAN:
        return False, f"--fyi is a report to {HUMAN}; {target} is not {HUMAN}", None
    # Nobody may send as the human. That name carries two
    # privileges - the routing table lets it reach every agent, and a chain
    # through it is exempt from the loop guards, because the guards exist to
    # reach a human and one is typing. An agent that could claim the name would
    # hold both: one spoofed hop turns a refused A->B->A into a legal loop.
    # `ocp launch` sets OCP_AGENT, so a caller that has one is not the human.
    speaker = current_agent() if speaker is _ENV else speaker
    if requester == HUMAN and speaker not in (None, HUMAN) and not on_behalf_of:
        return False, (f"{speaker} may not ask as {HUMAN}: that name is the human's, "
                       f"and it is exempt from the loop guards."), None
    if not requester:
        return False, ("cannot tell who is asking: OCP_AGENT is unset and this is "
                       "not an interactive shell. Agents are started with "
                       "`ocp launch <agent>`, which sets it."), None

    t = team.Team.load()
    con = db.connect()
    try:
        depth, chain_id, is_reply = 0, None, False
        if parent_id:
            parent = db.get_request(con, parent_id)
            if parent is None:
                return False, f"parent request {parent_id} does not exist", None
            depth = int(parent["chain_depth"]) + 1
            chain_id = parent["chain_id"]
            is_reply = replies_to(parent, requester, target)
            # The human's turn starts the count again, and keeps the thread.
            # The limit is there so a runaway ends up in front of a human; a
            # reply typed by that human is the limit having worked, not a hop
            # towards it.
            if requester == HUMAN:
                depth = 0

        # A loop is stopped and raised to the founder, never silently dropped: a request
        # that vanishes leaves the team stuck with nobody knowing why.
        for detect in (_cycle_in_chain(con, chain_id, requester, target, kind=kind),
                       _review_pingpong(con, chain_id, target, files)):
            if detect:
                eid = _stop_runaway(con, requester, target, body, files, chain_id,
                                depth, parent_id, detect)
                return False, f"{detect}\nRaised to the founder as #{eid}.", None

        decision = t.route(requester, target, chain_depth=depth, reply=is_reply)
        if not decision.allowed:
            if db.standing_denial(con, requester, target, body, files,
                                  int(time.time()) - team.ASK_WINDOW_S) is None:
                db.add_request(con, requester, target, body, files, None, None, kind or SAY_KIND,
                               depth, parent_id, state="denied", verdict=decision.reason,
                               chain_id=chain_id)
            # Hitting the depth limit is a runaway, not an ordinary refusal.
            if "chain depth" in decision.reason and target != HUMAN:
                eid = _stop_runaway(con, requester, target, body, files, chain_id,
                                depth, parent_id, decision.reason)
                return False, (f"{decision.reason}\nRaised to the founder "
                               f"as #{eid}."), None
            return False, decision.reason, None

        limited, note = t.rate_limited(con, requester)
        if limited:
            now = int(time.time())
            window = now - team.ASK_WINDOW_S
            # The first refusal keeps the text, so nothing an agent wrote is
            # lost by being refused. An identical one behind it is the same
            # refusal, not a second event: a retry loop spent 35 request
            # numbers in six seconds, and every row it wrote was a message
            # that had never been sent to anyone.
            rid = db.standing_denial(con, requester, target, body, files, window)
            if rid is None:
                rid = db.add_request(con, requester, target, body, files, decision.relation,
                                     decision.response, kind or decision.kind or SAY_KIND, depth, parent_id,
                                     state="denied", verdict=note, chain_id=chain_id)
            oldest = db.oldest_counted_ask(con, requester, window)
            free_at = now + team.ASK_WINDOW_S if oldest is None else oldest + team.ASK_WINDOW_S
            # Section 8.6: too many asks is a resource problem, so it throttles
            # and clears itself when a slot frees, rather than needing a human
            # the way a task defect does.
            if not _terminal(con, requester):
                db.set_agent_state(con, requester, "THROTTLED",
                                   wake_at=free_at, detail=note)
            when = timefmt.stamp(free_at, timefmt.CLOCK)
            return False, (f"{note} - {requester} is THROTTLED. The window rolls, so the "
                           f"next slot frees at {when}; wait for it rather than retrying, "
                           f"because a refusal sends nothing. Your text is kept as #{rid}."), None

        rid = db.add_request(con, requester, target, body, files, decision.relation,
                             decision.response, kind or decision.kind,
                             depth, parent_id,
                             chain_id=chain_id, fyi=fyi)
        if send_now:
            db.mark_send_now(con, rid)
        if card and target == HUMAN and not fyi and kind is None and decision.kind == "task":
            # What is asked of the founder is a decision task, and this row its card;
            # `task new` opens its own task and says `card=False`.
            tid = db.new_task(con, textsafe.oneline(body.strip().split("\n", 1)[0])[:80] or "a decision",
                              HUMAN, requester, expects="decision")
            db.set_task_queued(con, tid, HUMAN)
            db.link_request(con, tid, rid)
        deliver(rid, requester, target, body, files, decision)
        if fyi:
            # One live report per sender: the newer one says what the older did.
            for prev in con.execute(
                    "SELECT id FROM requests WHERE requester = ? AND target = ? "
                    "AND fyi = 1 AND state = 'queued' AND id != ?",
                    (requester, HUMAN, rid)).fetchall():
                db.retire_queued(con, int(prev["id"]), "replaced", f"replaced by #{rid}")
        db.add_event(con, requester, "ask", {
            "request_id": rid, "target": target, "relation": decision.relation,
            "response": decision.response, "chain_depth": depth,
        })

        note = f"queued as #{rid} for {target} (response={decision.response}, {note})"
        if notify:
            note += "\n" + push(con, target, rid)
        return True, note, rid
    finally:
        con.close()


def replies_to(parent, requester: str, target: str) -> bool:
    """Does a request from `requester` to `target` answer `parent`: one `target` sent to
    `requester`. Such a reply needs no edge back (`Team.route`)."""
    return parent is not None and parent["target"] == requester and parent["requester"] == target


def say(target: str, body: str, requester: str | None = None,
        parent_id: int | None = None, speaker=_ENV,
        notify: bool = True) -> tuple[bool, str, int | None]:
    """T293 (#1655/#1657): a question answered from a look-up, not new work
    - always ahead of the queue, never a head, and the sender is never parked
    on it (T628: nobody parks waiting on a peer). `parent_id` replies to and
    settles the request it answers (`_close_replied`) - otherwise the table
    only ever grows.
    """
    ok, msg, rid = ask(target, body, requester=requester, parent_id=parent_id,
                      kind=SAY_KIND,
                      speaker=speaker, notify=notify)
    if ok and parent_id is not None and rid is not None:
        _close_replied(parent_id, rid, body)
    return ok, msg, rid


def _close_replied(parent_id: int, reply_id: int, reply_body: str) -> None:
    """A reply settles the request it answers (T361), silently - the reply is what the asker
    is told, and it is marked as the answer, so there is one message. Only when it comes from
    the request's own target, to its requester, and the request is no roster proposal (those
    are decided with approve or reject) and no task (that is handed on or finished)."""
    from . import hr, trust
    con = db.connect()
    try:
        parent, reply = db.get_request(con, parent_id), db.get_request(con, reply_id)
        proposal = parent is not None and (hr.open_proposal_of(parent_id) or trust.open_proposal_of(parent_id))
    finally:
        con.close()
    if (parent is not None and parent["state"] == "queued" and not proposal
            and parent["kind"] != "task"        # a task ends by handoff or done, never by a reply
            and reply is not None and reply["requester"] == parent["target"]
            and reply["target"] == parent["requester"]):
        resolve(parent_id, reply_body, notify=False, answered_by=reply_id)


# T408: True only in the daemon's own process. Every other process - an agent's sandbox has no
# Orca and no process table - hands the start or wake to the daemon, which does it at once.
IN_DAEMON = False
_HAND_OFF_TIMEOUT_S = 10.0      # the verbs answer before they type, so a slow answer means no daemon


def _hand_off(verb: str, **payload) -> str | None:
    """What the daemon said, once it took `verb`; None when this is the daemon or it did not answer."""
    if IN_DAEMON:
        return None
    from ..daemon import client

    reply = client.send({"verb": verb, **payload}, timeout=_HAND_OFF_TIMEOUT_S)
    if reply and reply.get("ok"):
        return "  " + str(reply.get("said", "")).strip()
    return None


def push(con, target: str, rid: int | None = None) -> str:
    """Get the request to the agent now, rather than at the next daemon tick.

    `rid`, when it names a message (anything but a task), is typed at once
    (`messaging.tell_now`, T398), and so is a task marked send-now - a handoff (T596), or one the
    founder pressed send now on; any other task still waits for a free agent, one at a time.

    One command has to do the whole job. An agent that asks a subordinate for
    something and is told "queued" has not dispatched anything - it has left a
    note for a daemon, and if that agent has no terminal the note is for a
    human. Being unable to finish the job is what sends an agent looking for a
    subagent instead.

    Everything here is the daemon's own function, so a request delivered by the
    sender and one delivered by the tick are the same delivery. The daemon
    stays the safety net: anything this could not finish is still queued.
    """
    if target == HUMAN:
        return "it is on the founder's screen (`ocp bridge`)"
    if target not in paths.list_agents():
        return f"{target} is not registered with ocp - nothing can be started for it"
    handed = _hand_off("push", target=target, rid=rid)
    return handed if handed is not None else push_here(con, target, rid)


def push_here(con, target: str, rid: int | None = None) -> str:
    """`push` done in this process - the daemon's socket verb calls this, never `push`, so it cannot hand the work back to itself."""
    from ..daemon import delivery as dmn_delivery, lifecycle as dmn_lifecycle, messaging

    sent = db.get_request(con, rid) if rid is not None else None
    if sent is not None and (sent["kind"] != "task" or sent["send_now"]):      # T596: a marked task is a handoff
        told = messaging.tell_now(con, rid, lambda _m: None)
        return f"  delivered to {target}" if told == "typed" else f"  {target}: {told}"
    said = []
    row = db.get_agent_state(con, target)
    if not (row and row["terminal"]):
        # No row at all is the same case as a row with no terminal: an agent
        # that has never been started has nowhere to be told anything.
        said.append(dmn_lifecycle._start_for_work(con, target, lambda _m: None))
        row = db.get_agent_state(con, target)
    if row and row["closed_at"]:
        ok, why = dmn_lifecycle.wake_agent(con, target, lambda _m: None)
        said.append(f"{target}: {why}")
    if dmn_delivery.deliver_queued(con, target, lambda _m: None):
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
    if requester == HUMAN:
        return "  the founder reads it on `ocp bridge`"
    if requester not in paths.list_agents():
        return f"  {requester} is not registered with ocp - nothing to tell"
    handed = _hand_off("push_answer", requester=requester)
    return handed if handed is not None else push_answer_here(con, requester)


def push_answer_here(con, requester: str) -> str:
    """`push_answer` done in this process - see `push_here`."""
    from ..daemon import delivery as dmn_delivery, lifecycle as dmn_lifecycle

    row = db.get_agent_state(con, requester)
    said = []
    if row and row["closed_at"]:
        ok, why = dmn_lifecycle.wake_agent(con, requester, lambda _m: None)
        said.append(f"{requester}: {why}")
    if dmn_delivery.deliver_answers(con, requester, lambda _m: None):
        said.append(f"{requester} has been told")
    elif not said:
        said.append(f"{requester} will be told when it is free")
    return "  " + "; ".join(x.strip() for x in said if x)


def _delivery_reason(con, target: str) -> tuple[str, bool]:
    """Why delivery did not happen, in the daemon's own words."""
    from .. import actions

    return actions._delivery_block(con, target, *actions._world())


def inbox(agent: str, unread_only: bool = True) -> list[dict]:
    """The one request currently waiting for `agent`, or with
    unread_only=False, its whole history in every state - minus rows whose
    edge nobody has checked yet, which `agent` is not to see."""
    con = db.connect()
    try:
        if unread_only:
            # The agent's effective head (T337, auditor #2028's sweep) - a
            # true head blocked on record is not what is actually waiting
            # for it; `effective_head_or_marked` falls back to a marked say
            # (T293) with nothing else queued, never a head itself, still
            # the one thing waiting.
            from ..daemon import delivery  # local: daemon.delivery imports this module
            head = delivery.effective_head_or_marked(con, agent)
            return [dict(head)] if head else []
        rows = con.execute(
            "SELECT * FROM requests WHERE target = ? AND edge_ok ORDER BY id", (agent,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def withdraw(rid: int, requester: str | None = None,
             reason: str | None = None) -> tuple[bool, str]:
    """Take back your own request before it has been delivered (request #189).
    `reason` is stored as the row's verdict, as typed.

    Once delivered, a reply in its thread is the only way to change it.
    """
    requester = requester or asking_agent()
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None:
            return False, f"request {rid} does not exist"
        if row["requester"] != requester:
            return False, (f"#{rid} is {row['requester']} -> {row['target']}, "
                           f"not yours to withdraw")
        if row["state"] != "queued" and not (row["state"] == "landed" and row["delivered_at"]):
            return False, f"#{rid} is already {row['state']} - nothing to withdraw"
        linked = db.tasks_for_request(con, rid) if row["kind"] == "task" else []
        release = f", or `ocp task release T{linked[0]['id']}`" if linked else ""
        too_late = (f"#{rid} was already delivered to {row['target']} - too late to withdraw; "
                   f"tell {row['target']} with a message "
                   f"(`ocp say {row['target']} \"...\" --parent {rid}`){release}")
        if row["delivered_at"]:
            return False, too_late
        # Atomic: the write itself is the gate against a delivery landing
        # between the check above and here. Labelled "withdrawn: ", the same
        # as "replaced by #N", so it reads as machine text - a bare typed
        # reason here was drawn as the target's own answer, in the founder's
        # name on their own screen (#1348).
        verdict = f"withdrawn: {reason}" if reason else None
        if not db.retire_if_undelivered(con, rid, "withdrawn", verdict):
            # `db.is_still_queued` alone would not tell the two failures
            # here apart: `retire_if_undelivered`'s own race - a delivery
            # landing between the read above and this write, the one its
            # docstring exists to close - leaves the row queued too.
            # `hr.is_open_proposal` is what actually happened.
            from . import hr
            if hr.is_open_proposal(rid, con):
                # #1943: a roster proposal's own decision task is `requester`'s
                # to withdraw here too - ordinary ownership, since it is
                # `requester`'s own row - but the ocp/db/requests.py chokepoint refuses
                # this generic verdict on it the same way it refuses every
                # other non-decision close. `hr withdraw` is the door built
                # for it: it marks the proposal withdrawn first, which is
                # what lets its own close through.
                return False, (f"#{rid} is a roster proposal - `ocp hr withdraw` it "
                               f"instead, not `ocp withdraw`")
            return False, too_late
        p = _inbox(row["target"]) / f"{rid:06d}.json"
        if p.exists():
            p.unlink()
        db.add_event(con, requester, "withdraw", {"request_id": rid, "target": row["target"],
                                                   "reason": reason})
        return True, f"request #{rid} to {row['target']} withdrawn"
    finally:
        con.close()


# What a cancelled request says, to the requester as its answer and to anyone
# who reads the row: the founder took it out of the queue.
CANCEL_VERDICT = "withdrawn by the founder"


def _close_and_tell(con, rid: int, row, verdict: str, notify: bool):
    """Close a still-queued request as rejected with `verdict`, telling its
    target to stop if it was delivered and its requester through the
    ordinary answer path - the write `cancel` and `retire_open` share, so
    the two can never drift on what closing a request out from under
    someone actually does. Whatever queued behind it moves up, the same as
    an ordinary resolve. Closed here, in the one write that can move it out
    of 'queued', so an agent resolving it at the same instant keeps its own
    answer and this loses instead of overwriting it - the caller runs
    nothing below unless this call was the one that closed the row.

    None if this call lost that race; else `(target, after_close_msg, said)`,
    `said` being what was told to the target, if anything.
    """
    from ..daemon import delivery as dmn_delivery

    target = row["target"]
    if not db.close_if_queued(con, rid, "rejected", verdict, announced=not notify):
        return None
    said = []
    if row["delivered_at"]:
        typed, why = dmn_delivery.notify_cancelled(con, row, verdict)
        said.append(f"{target} was told to stop" if typed else
                    f"{target} could not be told now ({why}); resolving #{rid} "
                    f"is refused to it, so it cannot undo this")
    msg = _after_close(con, rid, row, "rejected", notify)
    return target, msg, said


def cancel(rid: int) -> tuple[bool, str]:
    """The founder takes a request out of an agent's queue.

    `withdraw` is the requester's own and stops at delivery, which left no way
    to clear a delivered head that its agent never resolved: everything behind
    it waited. This is that way. A founder's own request that was never
    delivered is simply withdrawn. Anything else is closed as rejected with
    `CANCEL_VERDICT`, never deleted: its number and history stay, its requester
    is told through the ordinary answer path, and the target - which may be
    mid-work on it - is typed a notice with delivery's gates when it had been
    delivered. Whatever is behind it delivers next.
    """
    from . import hr

    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None:
            return False, f"request {rid} does not exist"
        if row["state"] != "queued":
            tasks = db.tasks_for_request(con, rid) if row["kind"] == "task" else []
            stop = f" - stop its task with `ocp task discard T{tasks[0]['id']}` or hand it back" if tasks else ""
            return False, f"#{rid} is already {row['state']} - nothing to cancel{stop}"
        if row["requester"] == HUMAN and not row["delivered_at"]:
            return withdraw(rid, requester=HUMAN)
        if hr.is_open_proposal(rid, con):
            # Same family as #1908/#1919: CANCEL_VERDICT is not approve or
            # reject either, so cancelling a roster proposal would strand
            # it exactly as an unguarded resolve or dismiss would.
            return False, (f"#{rid} is a roster proposal - answer `approve` or "
                           f"`reject`, it cannot be cancelled")
        notify = row["requester"] != HUMAN
        closed = _close_and_tell(con, rid, row, CANCEL_VERDICT, notify)
        if closed is None:
            if db.is_still_queued(con, rid):
                return False, f"#{rid} was not cancelled - it is still queued"
            return False, f"#{rid} was closed while this ran - nothing to cancel"
        target, msg, said = closed
        db.add_event(con, HUMAN, "cancel", {"request_id": rid, "target": target,
                                            "requester": row["requester"],
                                            "delivered": bool(row["delivered_at"])})
        return True, (f"#{rid} to {target} cancelled"
                      + (f" ({'; '.join(said)})" if said else "") + "\n" + msg)
    finally:
        con.close()


def retire_open(con, rid: int, verdict: str, notify: bool | None = None) -> tuple[bool, str]:
    """Close a request that is still queued, out from under whoever holds it -
    `cancel`'s write (`_close_and_tell`), reached from `ocp task release`
    (T286) so it can end a task's own open requests the same way instead of
    merely unlinking them from the board. Closed as rejected with `verdict`;
    the target is told to stop if it had already been delivered, the
    requester through the ordinary answer path, and whatever queued behind
    it moves up. True only if this call is the one that closed it.

    `notify` left out follows `cancel`: the requester is told through the
    answer path unless it is the founder, who is not sent a rejection of his
    own request for something an agent did to its task (T302).
    """
    from . import hr

    row = db.get_request(con, rid)
    if row is None:
        return False, f"#{rid} is gone"
    if notify is None:
        notify = row["requester"] != HUMAN
    if row["state"] != "queued":
        return False, f"#{rid} is already {row['state']} - nothing to retire"
    if hr.is_open_proposal(rid, con):
        return False, f"#{rid} is a roster proposal - answer `approve` or `reject`"
    closed = _close_and_tell(con, rid, row, verdict, notify)
    if closed is None:
        if db.is_still_queued(con, rid):
            return False, f"#{rid} was not retired - it is still queued"
        return False, f"#{rid} was closed while this ran - nothing to retire"
    target, msg, said = closed
    return True, (f"#{rid} to {target} retired"
                  + (f" ({'; '.join(said)})" if said else "") + "\n" + msg)


def _after_close(con, rid: int, row, state: str, notify: bool) -> str:
    """What follows moving a request out of 'queued', shared by `resolve` and
    `cancel` so there is one write to the row and one place that wakes
    everyone it affects: the event log, the requester (if told), and
    whatever was behind it in the target's own queue."""
    db.add_event(con, row["target"], "resolve",
                 {"request_id": rid, "state": state, "requester": row["requester"]})

    # The reply is the wake. QUEUED is the design's state for "ready to be
    # dispatched"; the daemon picks it up from there (section 8.2).
    # Only queue the requester when nothing else it asked for is still
    # outstanding, and never out of a terminal state. And not out of a
    # usage park: an answer is not evidence the window rolled over, and
    # queueing it would open the session to the very typing the park is
    # there to prevent.
    from ..daemon import delivery as dmn_delivery, evaluate as dmn_evaluate

    if row["response"] == "async" and row["requester"] != HUMAN \
            and not _terminal(con, row["requester"]) \
            and not dmn_delivery.wall_reason(con, row["requester"]):
        outstanding = con.execute(
            "SELECT COUNT(*) c FROM requests WHERE requester = ? AND state = 'queued' "
            "AND response = 'async'", (row["requester"],)).fetchone()["c"]
        if outstanding == 0:
            # QUEUED only if something is queued or untold: a quiet dismissal
            # leaves neither, and the asker just goes back to idle.
            dmn_evaluate.queued_or_idle(con, row["requester"], f"request {rid} resolved: {state}")
    forget_inbox(row["target"], rid)
    said = push_answer(con, row["requester"]) if notify else ""
    # T318: this row may have been what kept the target's current task (T292)
    # worth holding - a no-op for a plain, task-less request, or for the
    # founder (advance_current_task's own HUMAN guard).
    from ..actions import tasks as taskmod
    taskmod.advance_current_task(con, row["target"])
    # The next request in target's own queue, if any, becomes deliverable
    # only now - tell them right away, the same as asking does. This runs
    # whether or not the requester was told: a dismiss skips the
    # requester's answer, never the target's own queue.
    if db.head_of_queue(con, row["target"]):
        said += ("\n" if said else "") + push(con, row["target"])
    return f"request #{rid} -> {state}" + (f"\n{said}" if said else "")


def resolve(rid: int, verdict: str | None, state: str = "done", notify: bool = True,
            open_tasks=None, dismissed_by: str | None = None,
            dismissed_reason: str | None = None,
            answered_by: int | None = None, settle: bool = True) -> tuple[bool, str]:
    """`settle=False`: close the request without settling the decision task it is the card of
    (a handoff closes the old holder's request, T684).

    `answered_by` is the reply row that is itself the answer (`say --parent`, T361).

    `notify=False` is `dismiss`'s case: settle the row without telling the requester.

    `open_tasks(con) -> list[int]` opens tasks in the same transaction as the
    close, and their numbers are appended to the verdict: a close that loses
    the race to another answer takes the tasks back with it."""
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None:
            return False, f"request {rid} does not exist"
        from . import hr, trust
        proposal = None
        if row["state"] == "queued":
            proposal = next(((m, i) for m, i in ((hr, hr.open_proposal_of(rid)),
                                                 (trust, trust.open_proposal_of(rid))) if i), None)
        if row["state"] == "queued" and proposal is None:
            kind = hr.unknown_kind_of(rid)
            if kind:
                return False, (f"#{rid} is a roster proposal of kind {kind!r}, which this "
                               f"process does not know, so it was not answered. Restart the "
                               f"daemon and the bridge (they run older code) and answer again.")
        if open_tasks is not None:
            con.execute("BEGIN IMMEDIATE")
        try:
            if open_tasks is not None:
                tids = open_tasks(con)
                verdict += "\n\nOpened as task(s): " + ", ".join(f"T{t}" for t in tids)
            # Conditional on 'queued', the same as `cancel`'s close: two answers
            # landing on the same row at once must not have the second overwrite
            # the first, whichever path either comes through.
            closed = db.close_if_queued(con, rid, state, verdict, announced=not notify,
                                        dismissed_by=dismissed_by,
                                        dismissed_reason=dismissed_reason,
                                        answered_by=answered_by, settle=settle)
        except Exception:
            if open_tasks is not None:
                con.execute("ROLLBACK")
            raise
        if open_tasks is not None:
            con.execute("COMMIT" if closed else "ROLLBACK")
        if not closed:
            fresh = db.get_request(con, rid)
            if fresh["state"] == "queued":
                # The chokepoint in db.close_if_queued refused this verdict
                # rather than losing a race - a caller without its own,
                # friendlier check (see actions.check_open_proposal) lands
                # here.
                return False, f"#{rid} was not resolved - it is still queued"
            return False, f"#{rid} is already {fresh['state']} - nothing to resolve"
        applied = _apply_answered(con, row, *proposal, verdict) if proposal else ""
        return True, (applied + "\n" if applied else "") + _after_close(con, rid, row, state, notify)
    finally:
        con.close()


def _apply_answered(con, row, mod, pid: str, verdict: str | None) -> str:
    """The founder answered roster or trust proposal `pid` (`mod` is `hr` or
    `trust`). An approve is carried out now, and the outcome goes on the answer
    the proposer is about to be told; a failed apply is also reported to the
    founder, and the proposal stays `proposed` so `apply` or `withdraw` can
    still settle it. A reject is only recorded on the proposal's file."""
    from . import decisions, hr, trust

    call = hr.decision(verdict)
    kind = "trust" if mod is trust else "hr"
    if call in ("approved", "rejected"):
        try:
            prop = mod.load_proposal(pid)
        except (OSError, ConfigError, UnicodeDecodeError) as e:
            return f"proposal {pid} cannot be read: {e}"
        if not prop:
            return f"no proposal {pid}"
        subject = mod.subject_of(prop)
        if call == "approved":
            subject, why_not = mod.answered(prop, row)
            if why_not:
                return f"proposal {pid} was not applied: {why_not}"
        try:
            decisions.record(kind, pid, hr.task_of_card(con, row["id"]) or 0, call, subject)
        except PermissionError as e:
            return f"proposal {pid} was not applied: {e}"
    if call == "rejected":
        mod.apply(pid, con)
        return ""
    if call != "approved":
        return ""
    ok, said = mod.apply(pid, con)
    line = f"proposal {pid} " + ("applied: " if ok else "could not be applied: ") + said
    db.append_verdict(con, row["id"], line)
    if not ok:
        report(con, row["requester"], f"{line}\nThe proposal is still open; "
               f"`ocp {kind} apply {pid}` retries it"
               + (f", `ocp hr withdraw {pid}` drops it." if kind == "hr" else "."),
               "proposal approved, apply failed")
    return line
