"""What a human can do to an agent, in one place.

`ocp bridge` is the screen the team is run from, so what it does has to live
somewhere that is not the drawing code: a screen that decided anything for
itself would be a second opinion, and this file exists so there is only ever
one.

Two halves. The acting half - wake, close, unstall, park, fold, start, message,
answer, dismiss - is a line of glue each over the call the daemon or the CLI
makes, and returns the one line to show for what happened. The reading half -
messages, stuck, and the details behind a row - asks the database
the questions a screen asks, and asks the daemon's own predicates for anything
it would otherwise have to judge for itself.

Two of them cannot be done from here at all. Logging in needs a human to type a
code into claude's own screen, and starting an agent needs a terminal to start
it in - so those return the command to run and let the caller hand the terminal
over.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import (accounts, agents, ask as askmod, blocked, daemon, db, events as evmod,
               fold as foldmod, hr, load as loadmod, observe as obs, orca, paths,
               spawn as spawnmod, status, team, triggers, waiting)

# The name a human's own requests are sent under, and the inbox `ocp bridge`
# shows. Taken from `ask`, which is where escalations are addressed: a second
# copy of the name would empty the panel the day it changed.
HUMAN = askmod.HUMAN


def wake(con, agent: str) -> str:
    """Bring a closed or parked agent back."""
    ok, why = daemon.wake_agent(con, agent, lambda _m: None)
    return f"{agent}: {why}" if ok else f"{agent}: not woken - {why}"


def close(con, agent: str) -> str:
    """Give the machine back an idle agent's memory, keeping its terminal."""
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    ok, why = daemon.close_agent(con, agent, lambda _m: None, st)
    return why if ok else f"{agent}: not closed - {why}"


def unstall(con, agent: str) -> str:
    """Clear a stall, which is the one state the daemon will not leave alone."""
    row = db.get_agent_state(con, agent)
    if not row or row["state"] != daemon.STALLED:
        return f"{agent}: not STALLED ({row['state'] if row else 'no state'})"
    db.set_agent_state(con, agent, daemon.QUEUED, detail="cleared by a human")
    for counter in ("failed_attempts", "throttle_count", "wake_failures"):
        db.reset_counter(con, agent, counter)
    db.clear_cursor(con, f"dialog_since:{agent}")
    db.clear_cursor(con, f"dialog_title:{agent}")
    return f"{agent}: STALLED -> QUEUED"


def park(con, agent: str) -> str:
    """Record a handoff, clear the context, and park until the window rolls over.

    `perform_handoff` returns True for three things that are not a handoff -
    merged into one already in flight, too soon after the last, and deferred
    because the agent is mid-turn - so its own log line is the answer, not the
    state afterwards. Reporting the state read RUNNING as though it were a
    result.
    """
    t = triggers.Trigger(True, "by hand", "parked by a human from the screen")
    said: list[str] = []
    ok = daemon.perform_handoff(con, agent, t, said.append)
    if said:
        return said[-1].strip()
    row = db.get_agent_state(con, agent)
    state = row["state"] if row else "?"
    return f"{agent}: {state}" if ok else f"{agent}: handoff degraded ({state})"


def fold(con, agent: str) -> str:
    """Fold today's events into the digest - the one model call in the system."""
    ok, msg = foldmod.fold(agent)
    return msg if ok else f"{agent}: fold rejected - {msg}"


# How long to wait for a launched agent to show up in the process table before
# saying it did not. `ocp launch` execs claude, which takes a few seconds to be
# visible; the daemon's own wake waits about this long for the same reason.
STARTED_TIMEOUT_S = 20


def start(con, agent: str, repo: str | None = None, base: str = "main") -> str:
    """Give an agent a worktree and start it there.

    The one thing the daemon cannot do for an agent with no terminal: there is
    nothing to type into, so work queued for it waits forever. Everything else
    on this screen assumes a terminal already exists; this is where one comes
    from.
    """
    row = db.get_agent_state(con, agent)
    if row and row["terminal"]:
        return f"{agent}: already has a terminal ({row['terminal']}) - wake it instead"
    wt = worktree_path(row["worktree"] if row else None)
    if wt:
        # It has somewhere to run already; it lost the window it ran in.
        ok, msg = spawnmod.start_in(agent, wt)
    else:
        ok, msg = spawnmod.spawn(agent, Path(repo or os.getcwd()).resolve(), base=base)
    if not ok:
        return msg
    # A terminal is not a session. `ocp launch` execs claude, and claude can
    # exit on its own - a bad flag, a config it will not accept, an account
    # mid-login - leaving the shell prompt the command was typed at. That looks
    # exactly like a terminal where nothing ran, and saying "started" over it
    # sends the reader looking for an agent that is not there.
    if not daemon.wait_for_session(agent, STARTED_TIMEOUT_S):
        return (f"{msg}\n  but no session is running in it after {STARTED_TIMEOUT_S}s - "
                f"the terminal is there and claude is not. Go and look (t): "
                f"whatever it printed is on that shell.")
    return msg


def worktree_path(recorded: str | None) -> str | None:
    """Where this agent works, out of what its row holds. See `spawn`."""
    return spawnmod.worktree_path(recorded)


def terminal(con, agent: str, repo: str | None = None) -> str:
    """Put the reader in this agent's terminal, starting one if there is none.

    The screen is for the decisions that are one keypress wide. The rest - read
    the diff with it, argue about an approach, watch it work - is a
    conversation, and the place to have one is the agent's own session. Typing
    a paragraph into `m` to avoid going there is how a decision gets made
    badly.

    "It has no terminal" is not an answer to "take me to it": an agent with
    nowhere to run is exactly the one a human is going there to fix, and being
    told to press a different key is a step this can take itself.
    """
    row = db.get_agent_state(con, agent)
    handle = row["terminal"] if row else None
    said = ""
    if not handle:
        said = start(con, agent, repo) + " · "
        row = db.get_agent_state(con, agent)
        handle = row["terminal"] if row else None
        if not handle:
            return said.rstrip(" ·")       # `start` said why
    if not orca.available():
        return said + "orca is not on PATH - nothing to switch to"
    ok, why = orca.focus(handle)
    return said + (f"in {agent}'s terminal - come back to this tab when you are done"
                   if ok else f"could not switch to {agent}: {why}")


def message(con, agent: str, body: str) -> str:
    """Send an agent work, as the human."""
    if not body.strip():
        return "nothing sent: no message typed"
    ok, msg, _ = askmod.ask(agent, body, requester=HUMAN)
    return msg


def answer(con, rid: int, verdict: str) -> str:
    """Reply to one row of the human's mailbox, whichever way it is pointing.

    Two rows land in that mailbox and the same key has to work on both. A
    request *to* the human is closed with a verdict, which is what the agent
    parked on. An answer to something the human asked is already closed, so
    replying to it is a new request back to the agent that answered - in that
    answer's thread, which is the one place in this system where a parent id is
    known for certain rather than guessed at.
    """
    if not verdict.strip():
        return "nothing sent: no answer typed"
    row = db.get_request(con, rid)
    if row is None:
        return f"#{rid} is gone"
    if _is_answer_to_human(row):
        ok, msg, _ = askmod.ask(row["target"], verdict, requester=HUMAN, parent_id=rid)
        if ok:
            # Replied to is read: leaving it in the mailbox would ask the human
            # to dismiss what they have just answered.
            db.mark_announced(con, [rid])
        return msg
    ok, msg = askmod.resolve(rid, verdict)
    return msg


def dismiss(con, rid: int, me: str | None = None) -> str:
    """Take one row off the mailbox without replying to it.

    A row that has already left 'queued' carries somebody's real verdict.
    Read by whoever asked for it (`me`, when known), it is marked told - the
    same thing the daemon does when it reads an answer out to an agent - and
    that marking is idempotent, so dismissing the same row twice is as
    harmless as once. Read instead by the one who *wrote* that verdict (the
    target dismissing its own answered request), there is nothing left to
    do: stamping it told would announce it on the real requester's behalf,
    and the requester would never actually be told.
    """
    row = db.get_request(con, rid)
    if row is None:
        return f"#{rid} is gone"
    if row["state"] != "queued":
        if me is not None and me == row["target"]:
            return f"#{rid} was already answered - nothing more for you to do"
        db.mark_announced(con, [rid])
        return f"#{rid} marked read"
    if row["response"] != "async":
        # Nobody is waiting on this one, so nobody is told: no push_answer, no
        # wake. Typing "read" at an agent as though it were an answer costs it
        # a turn to discover that it is not one - manager was mid-sentence
        # when a dismissal arrived as "#60 from human: read". resolve() itself
        # stamps announced_at, in the same write as the state change.
        ok, msg = askmod.resolve(rid, "read - no reply", state="done", notify=False)
        return f"{msg} (dismissed; {row['requester']} is not waiting on it)"
    # An `async` sender parked on a reply and nothing else will ever unpark it,
    # so it is told - and what it is told says it was read, not answered.
    ok, msg = askmod.resolve(rid, "read - no reply", state="done")
    return msg


def waiting_ids(con, agent: str) -> list[int]:
    """Every request queued for an agent, which is what /cancel can name."""
    return [int(r["id"]) for r in db.queued_for(con, agent)]


def cancel_plan(con, rid: int) -> tuple[bool, str]:
    """What cancelling this request would do, and who is told: the sentence a
    confirm shows before anything is closed."""
    row = db.get_request(con, rid)
    if row is None:
        return False, f"#{rid} is gone"
    if row["state"] != "queued":
        return False, f"#{rid} is already {row['state']}"
    target, requester = row["target"], row["requester"]
    if requester == HUMAN and not row["delivered_at"]:
        return True, f"withdraw #{rid} to {target}: not delivered yet, so nobody is told"
    behind = [q for q in db.queued_for(con, target) if q["id"] != rid]
    told = ([f"{target} is told to stop"] if row["delivered_at"] else []) + [
        "you are not told" if requester == HUMAN
        else f"{requester} is told '{askmod.CANCEL_VERDICT}'"]
    return True, (f"cancel #{rid} ({'delivered to' if row['delivered_at'] else 'queued for'} "
                  f"{target}): closed as rejected, '{askmod.CANCEL_VERDICT}'; "
                  + ", ".join(told)
                  + (f"; #{behind[0]['id']} delivers next" if row["delivered_at"] and behind
                     else ""))


def cancel(con, rid: int) -> str:
    """Take a request out of an agent's queue as the founder. See `ask.cancel`."""
    ok, msg = askmod.cancel(rid)
    return msg if ok else f"not cancelled - {msg}"


def _is_answer_to_human(row) -> bool:
    """Is this row an answer that came back to the human, rather than a request?

    The human asked it and it is no longer queued, so what it holds now is the
    agent's verdict. The distinction decides what the reply key does and what
    dismissing writes, so it is read off the row rather than inferred from
    which panel the cursor was in.
    """
    return row["requester"] == HUMAN and row["state"] != "queued"


def messages(con) -> list[dict]:
    """The human's whole mailbox: what is waiting on them, and what came back.

    Three things arrive here and they are not the same errand. An escalation is
    a decision the routing says only a human makes, and nothing else in this
    system will ever clear it. A request sent `async` asked for a reply and its
    sender parked on one. A report asked for neither. Which of those a row is
    comes from what the routing recorded when it was made, not from reading the
    text.

    The fourth is not a request at all: it is an answer to something the human
    asked. `ocp resolve` writes the verdict on the row and the daemon reads it
    out to the agent that asked; the human has no terminal to be read out to, so
    their answers have to be read here or nowhere. Half a mailbox teaches the
    team to talk around it - with this one drawing only one direction, two
    agents had learnt to send a second, unrelated request repeating what they
    had already answered.

    Decisions first, then the ones that asked for a reply, then the rest -
    oldest first inside each, because the order things arrived in is not the
    order they matter in.
    """
    out = []
    for r in con.execute(
        "SELECT id, ts, requester, target, kind, response, body, parent_id "
        "FROM requests WHERE target = ? AND state = 'queued' ORDER BY id",
        (HUMAN,),
    ).fetchall():
        # Both rows are built by hand and to one shape: a reader of this list
        # must not have to know which query a row came out of, and "from" is
        # the sender of a request one way and the answerer the other.
        #
        # `gate` and `waiting` are facts the routing recorded when the request
        # was made. A gate is a decision the design says only a human makes.
        # `waiting` says the sender asked for a reply and parked on it - which
        # is what it did at the time; whether it is still parked now is the
        # agent's own state, and the screen says what the row records rather
        # than reading one as the other.
        out.append({
            "id": r["id"], "ts": r["ts"], "from": r["requester"],
            "kind": r["kind"], "body": r["body"], "parent_id": r["parent_id"],
            "gate": r["kind"] == "escalate",
            "waiting": r["response"] == "async",
            "answer": False,
        })

    for r in db.unannounced_answers(con, HUMAN):
        out.append({
            "id": r["id"],
            # When the answer landed, not when the question was asked. Those
            # are two days apart on this machine's oldest rows.
            "ts": r["resolved_at"] or r["ts"],
            "from": r["target"],
            # What the row's own state says came back. A refusal is an answer -
            # it is the one that means do something else - and drawing it as an
            # answer would lose the only word that distinguishes them.
            "kind": "answer" if r["state"] == "done" else "refused",
            "body": r["verdict"] or "(answered with nothing written)",
            "gate": False,
            "waiting": False,
            "answer": True,
            "parent_id": r["parent_id"],
        })
    # Oldest first inside each bucket, by when the row landed rather than by
    # its id: an answer carries the time it came back, and a reply to an old
    # question has a low id and is this morning's news.
    return sorted(out, key=lambda m: (not m["gate"], not m["waiting"], m["ts"], m["id"]))


def founder_waits(con) -> list[dict]:
    """Everything that stops until the founder does something, oldest first.

    Each is {kind, who, what, since, red}. A permission prompt and an
    unanswered escalation are the founder's move (red). A roster proposal is
    shown from the moment it is raised until it is applied or refused: once
    approved it is waiting on whoever applies it, so it stays but not in red.
    """
    out: list[dict] = []
    for row in db.all_agent_states(con):
        w = waiting.of(row)
        if w:
            out.append({"kind": "permission", "who": row["agent"], "red": True,
                        "what": " ".join(x for x in (f"{w.get('tool') or 'tool'}:",
                                                      w.get("command")) if x),
                        "since": int(w["since"])})

    out += blocked.founder_rows(con, int(time.time()))
    # Its request is the same fact a second time, said where the founder is not
    # looking; the row above already is.
    proposed: set[int] = {int(b["request_id"]) for b in db.blocks(con) if b["request_id"]}
    for prop in hr.listing():
        if prop.get("state") != "proposed":
            continue
        req = db.get_request(con, int(prop.get("request") or 0))
        if req is None:
            continue
        proposed.add(int(req["id"]))
        if req["state"] == "queued":
            how, red = "awaiting your decision", True
        elif req["state"] == "done":
            call = hr.decision(req["verdict"])
            if call == "rejected":
                continue
            how, red = (("approved, not yet applied", False) if call == "approved"
                        else ("answered, but not approve or reject", True))
        else:
            continue
        out.append({"kind": "proposal", "who": prop.get("requested_by") or "?", "red": red,
                    "what": f"hr {prop['kind']} {prop['agent']} - {how}",
                    "since": int(req["ts"])})

    for r in con.execute(
        "SELECT id, ts, requester, body FROM requests WHERE target = ? "
        "AND kind = 'escalate' AND state = 'queued' ORDER BY id", (HUMAN,)).fetchall():
        if int(r["id"]) in proposed:
            continue
        out.append({"kind": "escalation", "who": r["requester"], "red": True,
                    "what": f"#{r['id']} {first_line(r['body'])}", "since": int(r["ts"])})
    return sorted(out, key=lambda w: (not w["red"], w["since"]))


def opened_by_human(con, rid: int) -> None:
    """Reading a report is what closes it: it asked nothing, so nothing is left."""
    row = db.get_request(con, rid)
    if row and row["fyi"] and row["target"] == HUMAN:
        db.retire_queued(con, rid, "done", "read")


def message_detail(con, rid: int) -> tuple[str, list[tuple[str, list[str]]]] | None:
    """One message opened, with what it is a reply to underneath it.

    A verdict is unreadable alone: "**MERGE.**" is a complete answer and says
    nothing without the branch it is about. What a message answers is known
    exactly for a row the human asked - it is that row's own body - and for
    anything else the exchange with that agent, in order, is the nearest true
    thing: who said what to whom, not a guess at which sentence replies to
    which.
    """
    row = db.get_request(con, rid)
    if row is None:
        return None
    answer = _is_answer_to_human(row)
    other = row["target"] if answer else row["requester"]
    when = time.strftime("%Y-%m-%d %H:%M",
                         time.localtime((row["resolved_at"] if answer else None) or row["ts"]))
    kind = ("answer" if row["state"] == "done" else "refusal") if answer \
        else (row["kind"] or "ask")
    title = f"#{rid} from {other} · {kind} · {when}"
    text = (row["verdict"] if answer else row["body"]) or ""
    sections: list[tuple[str, list[str]]] = [("", text.splitlines() or ["(empty)"])]

    quoted = {rid}
    if answer:
        asked = time.strftime("%m-%d %H:%M", time.localtime(row["ts"]))
        sections.append((f"WHAT YOU ASKED · {asked}",
                         _clip((row["body"] or "").splitlines(), rid)))
    elif row["parent_id"]:
        parent = db.get_request(con, row["parent_id"])
        if parent is not None:
            quoted.add(int(parent["id"]))
            sections.append((f"IN REPLY TO #{parent['id']}",
                             _clip((parent["body"] or "").splitlines(), parent["id"])))

    # What is already quoted above is not also listed below: the same message
    # twice on one page reads as two messages.
    earlier = _exchange(con, other, quoted)
    if earlier:
        sections.append((f"EARLIER WITH {other.upper()}", earlier))
    return title, sections


# How much of a quoted message is shown before it is standing in the way of the
# one being read. `ocp read` shows any of them whole.
QUOTE_LINES = 20


def _clip(lines: list[str], rid: int) -> list[str]:
    if len(lines) <= QUOTE_LINES:
        return lines or ["(empty)"]
    return lines[:QUOTE_LINES] + [f"… {len(lines) - QUOTE_LINES} more lines - `ocp read {rid}`"]


# How far back the exchange with one agent is drawn. Enough to place a verdict;
# not so much that the message being read scrolls off its own page.
EXCHANGE_ROWS = 6


# How much of a line of the exchange is drawn. These are index lines - one per
# thing said - and an agent's report is often one paragraph with no newline in
# it, so its "first line" is the whole report and would wrap across the page it
# is meant to be an index of.
EXCHANGE_WIDTH = 60


def _exchange(con, other: str, skip: set[int]) -> list[str]:
    """What the human and this agent have said to each other, oldest last.

    Both directions out of one query: the pair is the thread. A row that was
    answered is two things said, so its verdict is drawn as its own line - the
    reply is what makes the exchange read as a conversation rather than a list
    of things the human sent.
    """
    rows = con.execute(
        "SELECT id, ts, resolved_at, requester, target, state, body, verdict "
        "FROM requests WHERE ((requester = ? AND target = ?) OR (requester = ? AND target = ?)) "
        "AND state != 'denied' ORDER BY id DESC LIMIT ?",
        (HUMAN, other, other, HUMAN, EXCHANGE_ROWS + len(skip)),
    ).fetchall()
    out: list[str] = []
    for r in reversed(rows):
        if int(r["id"]) in skip:
            continue
        mine = r["requester"] == HUMAN
        arrow = f"you → {other}" if mine else f"{other} → you"
        out.append(f"#{r['id']:<5} {arrow:<24} {_at(r['ts'])}  {_gist(r['body'])}")
        if r["verdict"]:
            who = other if mine else "you"
            out.append(f"{'':<6} ↩ {who:<22} {_at(r['resolved_at'] or r['ts'])}  "
                       f"{_gist(r['verdict'])}")
    return out


def _gist(text: str | None) -> str:
    line = first_line(text)
    return line if len(line) <= EXCHANGE_WIDTH else line[:EXCHANGE_WIDTH - 1] + "…"


def _at(ts) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts or 0))


def first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return "(empty)"


# A reason that resolves itself has to be old before it counts as stuck: a
# closed agent is woken on the next tick, and a long turn is what this system
# is for - the daemon calls an agent healthy up to STUCK_RUNNING_S with nothing
# heard. A reason that does not resolve itself counts at once.
STUCK_AFTER_S = daemon.STUCK_RUNNING_S


def _delivery_block(con, agent: str, sessions: dict | None,
                    handles: set | None, alive: bool = True) -> tuple[str, bool]:
    """Why work is not reaching this agent. (reason, does it resolve itself)

    The checks are the daemon's gates - `supervise`'s, then `deliver_queued`'s -
    in the order the daemon applies them, so the panel gives the daemon's reason
    rather than a state the reader has to interpret. The two
    readings it needs - Orca's terminals and the process table - are taken once
    for the whole panel and handed in: asking per row would cost a process
    listing each, and the idle probe is not asked at all, because a "no" from
    it costs its whole timeout and RUNNING already says it on the agent's row.
    """
    if agent not in paths.list_agents():
        return f"{agent} is not registered - nothing will ever take this", False
    row = db.get_agent_state(con, agent)
    if not row:
        return f"{agent} has never been started (t starts it)", False
    if row["state"] == daemon.STALLED:
        # First, because `supervise` returns on STALLED before delivery is
        # considered at all. Reporting a missing terminal ahead of it sent the
        # reader to start one, which would not have moved the request.
        return f"{agent} is STALLED - the daemon will not touch it (u clears it)", False
    handle = row["terminal"]
    if not handle:
        return f"{agent} has no terminal - nothing can be typed to it (t starts one)", False
    if handles is not None and handle not in handles:
        return f"{agent}'s terminal is gone - Orca no longer has it", False
    try:
        acct = agents.current_account(agent, con)
        pct = status.read(acct).trusted_pct_5h
    except Exception:  # noqa: BLE001
        acct, pct = "?", None
    if row["closed_at"]:
        if pct is not None and pct >= status.PCT_5H_BLOCK:
            return (f"{agent} is closed and {acct} is at {pct}% - it cannot be "
                    f"launched into", False)
        if not alive:
            return f"{agent} is closed and no daemon is running to wake it", False
        return f"{agent} is closed; the next tick wakes it", True
    if sessions is not None and not sessions.get(agent):
        return f"no session is running in {agent}'s terminal", False
    if row["state"] == daemon.RUNNING:
        return f"{agent} is mid-turn - nothing is typed at a working agent", True
    if row["state"] == daemon.HANDOFF:
        return f"{agent} is mid-handoff", True
    walled = daemon.wall_reason(con, agent, row)
    if walled:
        return f"{walled} - delivered when it wakes or moves", True
    return "", True


# What the world looked like, and when we last looked. A frame is drawn every
# two seconds; asking Orca and scanning the process table that often costs a
# fifth of a second each time, for readings that do not change that fast - and
# the daemon is already asking the same two questions on its own tick.
_WORLD: dict = {"at": 0.0, "sessions": None, "handles": None}
WORLD_FRESH_S = 10


def _world(ttl: int = WORLD_FRESH_S) -> tuple[dict | None, set | None]:
    """Orca's terminals and the running sessions, at most `ttl` seconds old.

    Only taken when something on the screen turns on them: a screen with
    nothing queued asks nothing and costs nothing.
    """
    if time.time() - _WORLD["at"] > ttl:
        _WORLD["sessions"] = obs.live_sessions()
        _WORLD["handles"] = orca.live_terminals() if orca.available() else None
        _WORLD["at"] = time.time()
    return _WORLD["sessions"], _WORLD["handles"]


def stuck(con) -> list[dict]:
    """What is not moving, and what is in the way. (what, age_s, why)

    This is the panel that used to draw "in flight". Almost nothing ever is: a
    handoff takes seconds, and everything else it showed was a request that had
    been sitting for a day and a half because its target had no terminal to be
    told with. Drawing those as traffic said the system was busy when it was
    stuck.

    Membership is "the daemon cannot deliver this", not "this is old". Age is a
    column, except for the reasons that clear themselves - being told and not
    having answered yet is the ordinary state of a queue, and it only becomes a
    finding when it has outlasted the longest turn the daemon calls healthy.

    Nothing clears itself while the daemon is down, so with no daemon the
    grace those reasons are given is not given: a request that is one minute
    old and waiting on a tick that will never come is stuck now, not in half
    an hour.
    """
    now = int(time.time())
    alive = bool(daemon.running_elsewhere())
    out: list[dict] = []

    def add(what: str, age: int, why: str, agent: str | None = None) -> None:
        # Every row that is about an agent carries which one, so the keys that
        # fix it can be pressed where the problem is read rather than after
        # finding the same name again in the list above.
        out.append({"what": what, "age": age, "why": why, "agent": agent})

    # First, because it is the reason for most of what follows. With no daemon
    # nothing is delivered, nothing is woken and nothing is parked: every other
    # row here is then a symptom, and "the next tick wakes it" is a sentence
    # about a tick that is not coming.
    if not alive:
        add("the daemon is not running", 0,
            "nothing here moves on its own - `ocp daemon`, or `ocp service install`")

    for h in db.in_flight_handoffs(con):
        left = (h["deadline"] or now) - now
        add(f"handoff {h['agent']} at '{h['step']}'", 0,
            f"deadline in {status.ago(left)}" if left > 0 else "past its deadline",
            h["agent"])

    queued = con.execute(
        "SELECT id, requester, target, ts FROM requests WHERE state = 'queued' "
        "AND target != ? ORDER BY ts", (HUMAN,),
    ).fetchall()
    sessions, handles = _world() if queued else (None, None)
    # An agent ocp has work for and cannot give it, with what is in the way read
    # off its screen: one row for it says so, where a row per request would each
    # give the state's guess ("mid-turn") and none of them the dialog.
    blks = {b["agent"]: b for b in db.blocks(con)}
    # An agent that has been asked to resolve its head request and has not: one
    # row for it says which request and what waits behind it, where the rows
    # below would each say that a request "was told and has not answered" - true
    # of the head, and false of everything queued behind it, which is waiting
    # for the head and not for anyone's attention.
    forgotten: dict[str, dict] = {}
    for st_row in db.all_agent_states(con):
        who = st_row["agent"]
        found = daemon.forgotten_resolve(con, who, st_row, now)
        if found and found["idle_s"] >= daemon.FORGOTTEN_STUCK_S \
                and db.get_cursor(con, f"nudge:{who}") == int(found["head"]["id"]) \
                and (sessions is None or sessions.get(who)):
            forgotten[who] = found
            ids = [f"#{q['id']}" for q in db.queued_for(con, who)[1:4]]
            behind = (f"; {found['behind']} waiting behind it ({', '.join(ids)}"
                      f"{', ...' if found['behind'] > len(ids) else ''})") if found["behind"] else ""
            add(f"{who} idle on #{found['head']['id']}", found["idle_s"],
                f"delivered, nudged, still open and it asked nothing{behind}", who)
    for r in queued:
        if r["target"] in forgotten or r["target"] in blks:
            continue
        age, what = now - int(r["ts"]), f"#{r['id']} {r['requester']} → {r['target']}"
        why, self_clearing = _delivery_block(con, r["target"], sessions, handles, alive)
        if not why:
            # The daemon can deliver it. Either it is about to, or it has
            # already been told and has not answered - which is a queue working
            # until it has gone on too long.
            if age < STUCK_AFTER_S and alive:
                continue
            head = db.head_of_queue(con, r["target"])
            told = db.get_cursor(con, f"notify:{r['target']}") >= int(r["id"])
            if told:
                add(what, age, f"{r['target']} was told and has not answered", r["target"])
            elif head is not None and int(head["id"]) != int(r["id"]):
                # Only the head of a queue is told; the rest wait their turn.
                add(what, age, f"waiting behind #{head['id']}", r["target"])
            else:
                # Nothing structural is in the way and it still has not been
                # told, which the checks above cannot explain: the idle probe
                # never said yes - a permission prompt, a wedged TUI, a RUNNING
                # its hooks never cleared - or the send itself failed. Silence
                # here was the panel saying nothing is wrong for two days.
                add(what, age, f"{r['target']} has not been told yet - if this stays, "
                               f"its idle probe or the send is failing", r["target"])
            continue
        if self_clearing and alive:
            # Already told and now working on it is a queue doing its job,
            # whatever the request's age: age counts from when it was asked,
            # and an agent that has just been handed a two-day-old request is
            # not stuck on it.
            if db.get_cursor(con, f"notify:{r['target']}") >= int(r["id"]):
                continue
            if age < STUCK_AFTER_S:
                continue
        add(what, age, why, r["target"])

    for b in blks.values():
        add(f"{b['agent']} cannot be given its work", now - int(b["since"]),
            f"{blocked.reason(b['kind'], b['detail'])} - waiting: {b['work'] or 'nothing yet'}",
            b["agent"])

    for row in db.all_agent_states(con):
        if row["state"] in (daemon.STALLED, daemon.DEGRADED) \
                and not (row["agent"] in blks and blks[row["agent"]]["kind"] == blocked.STALLED):
            add(f"{row['agent']} is {row['state']}", now - int(row["updated"]),
                row["detail"] or "", row["agent"])
    loads = loadmod.read_all(con)
    for acct in paths.list_accounts():
        ld = loads.get(acct)
        allowed, why = status.read(acct).assignable(ld.active if ld else 0)
        if not allowed:
            add(f"account {acct} takes no work", 0, why)
    return out


# team.yaml as the screen reads it, re-parsed only when the file changes. A
# frame is drawn every two seconds and the routing table is a human-edited file
# that changes a few times a week. Keyed on the file, not just its clock: one
# process can read two homes, and two homes' files are the same age.
_TEAM: dict = {"key": None, "team": None}


def routing() -> "team.Team | None":
    """The routing table, or None when it cannot be read.

    None rather than an exception: the screen draws a team that has no
    hierarchy the same way it drew every team before there was one, and a typo
    in team.yaml must not take the dashboard down with it - `ocp doctor` is
    where a broken routing table is a finding.
    """
    p = paths.team_file()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if _TEAM["key"] != key:
        try:
            _TEAM["team"] = team.Team.load()
        except Exception:  # noqa: BLE001
            _TEAM["team"] = None
        _TEAM["key"] = key
    return _TEAM["team"]


def supervisor_of(t, agent: str) -> str | None:
    """Which agent this one answers to, out of the routing table.

    A supervisor edge is the relation the team model already writes down -
    `manager -> dev.*` is what makes the devs the manager's - so the tree is
    read from the policy the daemon routes on rather than from a second list
    that would drift from it.

    Only an edge whose `from` names one agent counts. A wildcard source is a
    permission, not a chain of command: `* -> reviewer` says anyone may ask the
    reviewer, and the human's own `human -> *` would otherwise make the
    whole team its children and draw a tree that says nothing.
    """
    if t is None:
        return None
    best: tuple[int, str] | None = None
    for e in t.edges:
        if e.get("relation") != "supervisor":
            continue
        src, dst = str(e.get("from", "")), str(e.get("to", ""))
        if "*" in src or src == agent or src == HUMAN:
            continue
        if not t.matches(dst, agent):
            continue
        # The most specific edge wins, the way a route does: a name beats a
        # team, and a team beats everyone.
        rank = 0 if dst == agent else (1 if dst.endswith(".*") else 2)
        if best is None or rank < best[0]:
            best = (rank, src)
    return best[1] if best else None


def agent_tree() -> list[tuple[str, int]]:
    """Every registered agent as (name, depth), supervisors above their reports.

    An agent whose supervisor is not registered, or that is in a cycle, is
    drawn at the root: a name missing from this list reads as an agent that is
    not there, so nothing may drop out of it.
    """
    names = paths.list_agents()
    t = routing()
    boss = {}
    for n in names:
        s = supervisor_of(t, n)
        boss[n] = s if s in names and s != n else None
    # A cycle would recurse forever. Anything that cannot be walked up to a
    # root in as many steps as there are agents is one, and it is drawn flat.
    for n in list(boss):
        seen, cur = {n}, boss[n]
        while cur is not None:
            if cur in seen:
                boss[n] = None
                break
            seen.add(cur)
            cur = boss.get(cur)

    out: list[tuple[str, int]] = []

    def walk(parent: str | None, depth: int) -> None:
        for n in names:
            if boss[n] == parent:
                out.append((n, depth))
                walk(n, depth + 1)

    walk(None, 0)
    return out


def login_argv(acct: str, force: bool = False) -> tuple[str, list[str], dict]:
    """(what happened, argv, env) for handing the terminal to claude.

    Returns an empty argv when there is nothing to run, so the caller shows the
    line and stays where it is.
    """
    action, msg = accounts.login_plan(acct, force=force)
    if action != "login":
        return msg, [], {}
    return (f"logging '{acct}' in - finish in claude, then come back",
            ["claude", "/login"], accounts.env_for(acct))


def move(con, agent: str, account: str) -> str:
    """Force this agent's live session onto `account`, recording its handoff first."""
    ok, msg = daemon.force_move(con, agent, account, "human (bridge)", lambda _m: None)
    return msg


def can(con, agent: str, key: str) -> tuple[bool, str]:
    """Whether an action is worth offering for this agent, and why not.

    It asks the same rows the action does, so the two cannot drift into
    disagreeing about what is possible - and it says which row said no. A
    refusal that does not explain itself sends the reader to press the key
    again, or to the wrong fix: "does not apply right now" on an agent with no
    terminal is the one screen that could have said `t starts one`.
    """
    row = db.get_agent_state(con, agent)
    if not row:
        if key in ("message", "start", "terminal"):
            return True, ""
        return False, f"{agent} has never been started (t starts it and takes you there)"
    closed = bool(row["closed_at"])
    state = row["state"]
    # The states nothing may be done to from here. RUNNING and HANDOFF are
    # mid-something, and STALLED is the one state the daemon deliberately does
    # not touch - handing a stalled agent off would clear the stall a human was
    # meant to read.
    busy = state in (daemon.RUNNING, daemon.HANDOFF, daemon.STALLED)
    busy_why = f"{agent} is {state}" + (" - only a human clears that (u)"
                                        if state == daemon.STALLED else "")
    if key == "wake":
        if closed or state in (daemon.PARKED, daemon.THROTTLED, daemon.DEGRADED):
            return True, ""
        return False, f"{agent} is {state}, not parked or closed - there is nothing to wake"
    if key == "close":
        # Work waiting is the one case where closing only adds delay: the very
        # next tick wakes it again. `should_close` refuses for the same reason.
        if closed:
            return False, f"{agent} is already closed (w brings it back)"
        if not row["terminal"]:
            return False, f"{agent} has no terminal - there is no session to close (t starts one)"
        if busy:
            return False, busy_why
        waiting = db.queued_for(con, agent)
        if waiting:
            return False, (f"{agent} has {len(waiting)} request(s) waiting - closing it now "
                           f"only delays them, the next tick wakes it again")
        return True, ""
    if key == "unstall":
        if state == daemon.STALLED:
            return True, ""
        return False, f"{agent} is not STALLED - it is {state}"
    if key == "park":
        if closed:
            return False, f"{agent} is closed - its context is already gone (w brings it back)"
        if not row["terminal"]:
            return False, f"{agent} has no terminal - there is no session to park (t starts one)"
        if busy:
            return False, busy_why
        return True, ""
    if key == "fold":
        # A fold reads events and runs `claude -p`; it never types into the
        # terminal. An agent whose terminal is gone is one of the likelier ones
        # to want folded.
        return (False, busy_why) if busy else (True, "")
    if key == "move":
        if closed or not row["terminal"]:
            return False, (f"{agent} has no live session to move - it starts on its "
                           f"preferred account")
        if state in (daemon.HANDOFF, daemon.STALLED):
            return False, f"{agent} is {state}"
        return True, ""
    if key == "start":
        if row["terminal"]:
            return False, (f"{agent} already has a terminal ({row['terminal']}) - "
                           f"t goes to it, w wakes it")
        return True, ""
    if key == "terminal":
        # Always. Going to watch an agent work is reasonable whatever it is
        # doing, including mid-turn, and an agent with no terminal gets one on
        # the way - the action does that, so there is nothing to refuse.
        return True, ""
    return True, ""


# ------------------------------------------------------------ the conversation
# Chains shown in the tab, newest last. A chain is a thread; an agent that has
# been at work for a week has more of them than a page can hold.
CHAT_CHAINS = 8
CHAT_TAB = "CONVERSATION"
OTHERS_TAB = "ALSO QUEUED FOR IT"
UNTOLD_TAB = "ANSWERS IT HAS NOT BEEN TOLD"


def _chat_rows(con, agent: str) -> list:
    """Every row of every chain the human and `agent` have a row in."""
    return con.execute(
        "SELECT id, ts, resolved_at, requester, target, state, body, verdict, "
        "chain_id, delivered_at, fyi FROM requests "
        "WHERE state != 'denied' AND chain_id IN ("
        "  SELECT chain_id FROM requests WHERE "
        "  (requester = ? AND target = ?) OR (requester = ? AND target = ?)) "
        "ORDER BY id", (HUMAN, agent, agent, HUMAN)).fetchall()


class Row(str):
    """A row of an agent's page that the screen lays out from its fields.

    As a string it is the plain reading, which is all a caller that only
    searches needs. The fields are what the bridge draws from at the width the
    terminal has now, so a row is wrapped where it is drawn and its text never
    passes through anything that styles it.
    """


class Fact(Row):
    """Labelled values that belong together: `account alpha   model sonnet`."""
    pairs: list[tuple[str, str]]

    def __new__(cls, *pairs: tuple[str, str]):
        self = super().__new__(cls, "   ".join(f"{k} {v}" for k, v in pairs))
        self.pairs = list(pairs)
        return self


class Entry(Row):
    """A request as a block: its number, who it is with, and all of its text."""
    id: int
    who: str
    text: str
    ts: int

    def __new__(cls, id: int, who: str, text: str, ts: int):
        self = super().__new__(cls, f"#{id} from {who}: {text}")
        self.id, self.who, self.text, self.ts = id, who, text, ts or 0
        return self


class Event(Row):
    """One thing the daemon or the agent did, with its whole payload."""
    ts: int
    kind: str
    payload: str

    def __new__(cls, ts: int, kind: str, payload: str):
        self = super().__new__(
            cls, f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {kind:<18}{payload}")
        self.ts, self.kind, self.payload = ts or 0, kind, payload
        return self


class Said(Row):
    """One row of a conversation: a message, or the rule that starts a thread.
    As a string it is who, then what."""
    kind: str          # "msg" or "thread"
    who: str           # the speaker; "a → b" between two agents
    side: str          # "you", "agent" (the one this tab is about) or "other"
    text: str
    ts: int
    tail: str          # its delivery state, or the request an answer closes
    waiting: bool      # the agent is waiting on the human for it

    def __new__(cls, kind: str, who: str, text: str = "", side: str = "", ts: int = 0,
                tail: str = "", waiting: bool = False):
        self = super().__new__(cls, f"{who}  {text}" if kind == "msg" else who)
        self.kind, self.who, self.side, self.text = kind, who, side, text
        self.ts, self.tail, self.waiting = ts, tail, waiting
        return self


def conversation_and_drawn(con, agent: str) -> tuple[list[str], set[int]]:
    """The human's conversation with `agent`, in time order, a thread at a time,
    and the ids of the requests it drew - only the newest CHAT_CHAINS chains
    are drawn, so a request in an older one is not in it.

    A thread is a chain_id. Every row of a chain is drawn, not only the rows
    between the two of them, so what the agent asked a third agent on the way to
    answering is visible where it happened. One row is two messages - the ask
    and, once closed, its verdict - and each is placed at its own time.
    """
    chains: dict[str, list] = {}
    for r in _chat_rows(con, agent):
        chains.setdefault(str(r["chain_id"]), []).append(r)

    def said(speaker: str, to: str, text, ts, tail: str = "", waiting: bool = False) -> Said:
        # What is not between the human and this agent is neither's: the agent
        # asking a third agent is drawn as that, not as the agent speaking to them.
        between = HUMAN in (speaker, to)
        side = "you" if speaker == HUMAN else "agent" if between else "other"
        who = "you" if speaker == HUMAN else speaker if between else f"{speaker} → {to}"
        body = (text or "").strip("\n")
        return Said("msg", who, body if body.strip() else "(empty)", side, ts or 0, tail,
                    waiting)

    threads = []
    for cid, rows in chains.items():
        msgs = []
        for r in rows:
            tail = ""
            if r["state"] == "queued":
                tail = ("waiting for you" if r["target"] == HUMAN else
                        f"delivered {time.strftime('%H:%M', time.localtime(r['delivered_at']))}"
                        if r["delivered_at"] else "queued")
                if r["target"] == agent:
                    tail += f" · #{r['id']}"       # what /cancel names
            msgs.append((r["ts"], r["id"], 0,
                         said(r["requester"], r["target"], r["body"], r["ts"], tail,
                              r["state"] == "queued" and r["target"] == HUMAN)))
            if r["verdict"]:
                at = r["resolved_at"] or r["ts"]
                msgs.append((at, r["id"], 1, said(r["target"], r["requester"], r["verdict"],
                                                  at, f"answers #{r['id']}")))
        msgs.sort(key=lambda m: m[:3])
        threads.append((max(m[0] for m in msgs), cid, msgs))
    threads.sort(key=lambda t: t[0])
    out: list[str] = []
    drawn: set[int] = set()
    for _, cid, msgs in threads[-CHAT_CHAINS:]:
        out.append(Said("thread", f"── chain {cid}"))
        out += [m[3] for m in msgs]
        drawn |= {m[1] for m in msgs}
    if not out:
        out.append(f"nothing yet between you and {agent}")
    how, rid = chat_target(con, agent)
    goes = {"answers": f" · answers #{rid}", "under": f" · goes under #{rid}",
            "new": " · starts a new thread"}[how]
    out += ["", f"↵ write to {agent}{goes} · e in $EDITOR"]
    return out, drawn


def chat_parent(con, agent: str) -> int | None:
    """The row a new message from the human goes under: the latest one between them."""
    row = con.execute(
        "SELECT MAX(id) AS m FROM requests WHERE state != 'denied' AND "
        "((requester = ? AND target = ?) OR (requester = ? AND target = ?))",
        (HUMAN, agent, agent, HUMAN)).fetchone()
    return int(row["m"]) if row and row["m"] else None


def chat_target(con, agent: str) -> tuple[str, int | None]:
    """The row a message from the human in this tab goes to, and what it does there.

    ('answers', id): the oldest question the agent has open for the human. It is
    parked on that row, so a reply is its verdict - an ask under it would leave
    it queued and the agent waiting. A report (fyi) is not a question, and
    neither is the human's own row, so neither can be what a reply answers.
    ('under', id): otherwise a new ask in the thread of the latest row between
    them. ('new', None): nothing between them yet.
    """
    q = con.execute(
        "SELECT MIN(id) AS m FROM requests WHERE requester = ? AND target = ? "
        "AND state = 'queued' AND fyi = 0", (agent, HUMAN)).fetchone()
    if q and q["m"]:
        return "answers", int(q["m"])
    rid = chat_parent(con, agent)
    return ("under", rid) if rid else ("new", None)


def chat_reply(con, agent: str, body: str) -> str:
    """Send the human's message where `chat_target` says it goes."""
    if not body.strip():
        return "nothing sent: no message typed"
    how, rid = chat_target(con, agent)
    if how == "new":
        return message(con, agent, body)
    row = db.get_request(con, rid)
    if how == "answers" or _is_answer_to_human(row):
        return answer(con, rid, body)
    ok, msg, _ = askmod.ask(agent, body, requester=HUMAN, parent_id=rid)
    return msg


def _seen_key(agent: str) -> str:
    return f"seen:{agent}"


def chat_unread(con, agent: str) -> int:
    """What `agent` has said to the human since the tab was last open.

    Counted from a time, not a row id: an answer lands on an old row, so ids
    arrive out of order and an id cursor would step past the older one for good.
    Times are whole seconds, so what lands in the very second the tab was marked
    counts as read; the other way, what was just read would stay new.
    """
    seen = db.get_cursor(con, _seen_key(agent))
    row = con.execute(
        "SELECT COUNT(*) AS n FROM requests WHERE state != 'denied' AND "
        "((requester = ? AND target = ? AND ts > ?) OR "
        " (requester = ? AND target = ? AND verdict IS NOT NULL AND resolved_at > ?))",
        (agent, HUMAN, seen, HUMAN, agent, seen)).fetchone()
    return int(row["n"])


def chat_seen(con, agent: str) -> None:
    db.set_cursor(con, _seen_key(agent), int(time.time()))


def agent_detail(con, agent: str) -> list[tuple[str, list[str]]]:
    """What is behind an agent's row, in sections a screen can draw.

    The head, then the conversation the human has with it, then whatever is
    waiting on it or owed to it that is not part of that conversation - a
    request from another agent is not something the human said to it. What it
    knows and what it has done are read on request (`agent_knows`, `agent_done`).
    """
    row = db.get_agent_state(con, agent)
    try:
        cfg = agents.read(agent)
        acct = agents.current_account(agent, con)
    except Exception as e:  # noqa: BLE001
        return [("", [f"config error: {e}"])]
    st = status.read_for_agent(agent, acct)
    # Where it is, and - when they differ - where it would rather be. agent.yaml
    # is a preference: a session started while that account was at the wall is
    # on another one, and a reader who only saw the preference would be watching
    # an account the agent is not spending on.
    prefers = agents.account_of(agent)
    where = acct if acct == prefers else f"{acct} (prefers {prefers})"
    head = [Fact(("account", where), ("model", str(cfg.get("model"))),
                 ("effort", cfg.get("effort") or agents.DEFAULT_EFFORT),
                 ("ctx", "?" if st.ctx_pct is None else str(st.ctx_pct) + "%"))]
    if row:
        closed = "  ·closed" if row["closed_at"] else ""
        head.append(Fact(("state", f"{row['state']}{closed}"),
                         *([("wakes", time.strftime("%H:%M", time.localtime(row["wake_at"])))]
                           if row["wake_at"] else [])))
        if row["detail"]:
            head.append(row["detail"])

    # What the conversation drew is not repeated; every other queued request
    # and untold answer is listed under it, including one in a chain too old
    # for the conversation to draw.
    chat, drawn = conversation_and_drawn(con, agent)
    others = [Entry(r["id"], r["requester"], r["body"] or "", r["ts"])
              for r in db.queued_for(con, agent) if r["id"] not in drawn]
    untold = [Entry(r["id"], r["target"], r["verdict"] or r["state"], r["resolved_at"] or r["ts"])
              for r in db.unannounced_answers(con, agent) if r["id"] not in drawn]
    if others or untold:
        # Where the reader is left, at the end of the conversation.
        chat += [f"below: {len(others)} also queued for it, {len(untold)} "
                 "answers it has not been told - press G to read them"]
    sections = [("", head)]
    n = chat_unread(con, agent)
    sections.append((CHAT_TAB + (f" ({n if n < 100 else '99+'} new)" if n else ""), chat))
    if others:
        sections.append((f"{OTHERS_TAB} ({len(others)})", others))
    if untold:
        sections.append((f"{UNTOLD_TAB} ({len(untold)})", untold))
    return sections


def agent_knows(con, agent: str) -> list[str]:
    """The handoff an agent would be resumed with."""
    kept = db.get_handoff(con, agent)
    return kept.splitlines() if kept else ["no handoff waiting - it has not been parked"]


def agent_done(con, agent: str) -> list:
    """The last events recorded for an agent, newest first."""
    recent = [Event(e["ts"], e["kind"], e["payload"] or "")
              for e in con.execute(
                  "SELECT ts, kind, payload FROM events WHERE agent = ? "
                  "ORDER BY id DESC LIMIT 12", (agent,)).fetchall()]
    return recent or ["nothing recorded yet"]


def agent_where(con, agent: str) -> str:
    """The terminal handle, and the session it would resume if closed - a
    technical id, never on the head where a person reads at a glance, but
    still one keystroke away for the one thing it is for: an agent stuck on
    a screen only the handle can reach."""
    row = db.get_agent_state(con, agent)
    if not row:
        return f"{agent} has never been started"
    said = f"terminal {row['terminal'] or 'none'}"
    if row["closed_at"]:
        said += f"  ·  resumes session {row['resume_session'] or 'unknown'}"
    return said


def agent_worktree(con, agent: str) -> str:
    """Where this agent's repo lives on disk - off the head for the same
    reason as the terminal handle: nothing a person types, only reads when
    they need to go there themselves."""
    row = db.get_agent_state(con, agent)
    if not row or not row["worktree"]:
        return f"{agent} has no worktree"
    return worktree_path(row["worktree"]) or row["worktree"]


def account_detail(con, acct: str, logged_in=None) -> list[tuple[str, list[str]]]:
    """What is behind an account's row, in the same shape as an agent's.

    `logged_in` is the caller's login check: the bridge redraws this page every
    couple of seconds and passes its cached one, since the real one asks the
    keychain.
    """
    logged_in = logged_in or accounts.logged_in
    st = status.read(acct)
    loads = loadmod.read_all(con)
    _, ranked = status.pick_account([a for a in paths.list_accounts() if logged_in(a)],
                                    load=loads)
    is_in = logged_in(acct)
    allowed, why = status.account_verdict(acct, st, loads.get(acct), ranked, is_in)
    head = [f"login    {'logged in' if is_in else 'NOT logged in'}",
            f"config   {paths.account_claude(acct)}",
            f"5h       {st.trusted_pct_5h}% ({st.pct_5h_source})   7d {st.pct_7d}%",
            f"snapshot {'-' if st.age_s is None else status.ago(st.age_s) + ' old'}"
            f"   session {st.session_id or '-'}",
            f"assign   {'yes' if allowed else 'no'} - {why}"]
    live = status.live_cost(acct)
    head.append("spend    " + ("nothing running to read it from" if live is None else
                               f"${live:.2f} across the sessions running now - not the "
                               f"account's total, which nothing here can see"))
    mine = [a for a in paths.list_agents() if _account_of(a, con) == acct]
    rows = []
    for a in mine:
        row = db.get_agent_state(con, a)
        closed = " ·closed" if row and row["closed_at"] else ""
        rows.append(f"{a:<12}{(row['state'] if row else 'never started')}{closed}"
                    f"   {(row['detail'] if row else '') or ''}")
    return [("", head), (f"AGENTS ON IT ({len(mine)})", rows or ["none"])]


def _account_of(agent: str, con=None) -> str | None:
    """Where this agent's session is, for the list of who is on an account.

    The live account, like every other runtime reading: an agent that moved is
    on the account it moved to, and listing it under the one it left puts it a
    keystroke away from its own page saying otherwise.
    """
    try:
        return agents.current_account(agent, con)
    except Exception:  # noqa: BLE001 - an unreadable agent belongs to no account here
        return None

