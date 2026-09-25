"""Why an agent's queue is not moving."""
from __future__ import annotations

import time

from ..team import agents
from ..infra import timefmt
from ..session import blocked
from ..daemon import delivery as dmn_delivery, locks as dmn_locks, states as dmn_states
from .. import db
from ..usage import load as loadmod
from ..session import observe as obs
from ..session import orca
from ..infra import paths
from ..usage import status

from .routing import HUMAN


# A reason that resolves itself has to be old before it counts as stuck: a
# closed agent is woken on the next tick, and a long turn is what this system
# is for - the daemon calls an agent healthy up to STUCK_RUNNING_S with nothing
# heard. A reason that does not resolve itself counts at once.
STUCK_AFTER_S = dmn_states.STUCK_RUNNING_S


def _delivery_state(con, agent: str, sessions: dict | None,
                    handles: set | None, alive: bool = True) -> tuple[str, str, bool]:
    """Why work is not reaching this agent. (short phrase, reason, does it resolve itself)

    The checks are the daemon's gates - `supervise`'s, then `deliver_queued`'s -
    in the order the daemon applies them, so the panel gives the daemon's reason
    rather than a state the reader has to interpret. The two
    readings it needs - Orca's terminals and the process table - are taken once
    for the whole panel and handed in: asking per row would cost a process
    listing each, and the idle probe is not asked at all, because a "no" from
    it costs its whole timeout and RUNNING already says it on the agent's row.
    """
    if agent not in paths.list_agents():
        return "not registered", f"{agent} is not registered - nothing will ever take this", False
    row = db.get_agent_state(con, agent)
    if not row:
        return "never started", f"{agent} has never been started (t starts it)", False
    if row["state"] == dmn_states.STALLED:
        # First, because `supervise` returns on STALLED before delivery is
        # considered at all. Reporting a missing terminal ahead of it sent the
        # reader to start one, which would not have moved the request.
        return "stalled", f"{agent} is FATAL - the daemon will not touch it (w clears it)", False
    handle = row["terminal"]
    if not handle:
        return "no terminal", f"{agent} has no terminal - nothing can be typed to it (t starts one)", False
    if handles is not None and handle not in handles:
        return "terminal gone", f"{agent}'s terminal is gone - Orca no longer has it", False
    acct = agents.current_account(agent, con)
    pct = status.read(acct).short_trusted if acct else None
    if row["closed_at"]:
        if pct is not None and pct >= status.SHORT_BLOCK:
            return ("closed, account full", f"{agent} is closed and {acct} is at {pct}% - it cannot be "
                    f"launched into", False)
        if not alive:
            return "closed, no daemon", f"{agent} is closed and no daemon is running to wake it", False
        return "closed, waking", f"{agent} is closed; the next tick wakes it", True
    if sessions is not None and not sessions.get(agent):
        return "no session", f"no session is running in {agent}'s terminal", False
    if row["state"] == dmn_states.RUNNING:
        return "mid-turn", f"{agent} is mid-turn - nothing is typed at a working agent", True
    if row["state"] == dmn_states.HANDOFF:
        return "mid-handoff", f"{agent} is mid-handoff", True
    walled = dmn_delivery.wall_reason(con, agent, row)
    if walled:
        return "account at the wall", f"{walled} - delivered when it wakes or moves", True
    return "", "", True


# What the world looked like, and when we last looked. A frame is drawn every
# two seconds; asking Orca and scanning the process table that often costs a
# fifth of a second each time, for readings that do not change that fast - and
# the daemon is already asking the same two questions on its own tick.
_WORLD: dict = {"at": 0.0, "sessions": None, "handles": None}
WORLD_FRESH_S = 10


def _delivery_block(con, agent: str, sessions: dict | None,
                    handles: set | None, alive: bool = True) -> tuple[str, bool]:
    """`_delivery_state` without its short phrase: (reason, does it resolve itself)."""
    _, why, clears = _delivery_state(con, agent, sessions, handles, alive)
    return why, clears


def queue_reason(con, agent: str, rid: int) -> str:
    """Why request `rid`, queued for `agent`, has not reached it, for the chat page."""
    head = db.head_of_queue(con, agent)
    row = db.get_request(con, rid)
    if row and row["send_now"]:
        return _delivery_state(con, agent, *_world())[0] or "sending now"
    if head and int(head["id"]) != rid:
        return f"behind #{head['id']}"
    return _delivery_state(con, agent, *_world())[0] or "waiting for the agent"


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
    alive = bool(dmn_locks.running_elsewhere())
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
            f"deadline in {timefmt.ago(left)}" if left > 0 else "past its deadline",
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
        found = dmn_delivery.forgotten_resolve(con, who, st_row, now)
        if found and found["idle_s"] >= dmn_delivery.FORGOTTEN_STUCK_S \
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
        if row["state"] in (dmn_states.STALLED, dmn_states.DEGRADED) \
                and not (row["agent"] in blks and blks[row["agent"]]["kind"] == blocked.STALLED):
            # FATAL, not the internal name STALLED - the agents panel below
            # reads the same row through tui.screen_state and must not disagree.
            word = "FATAL" if row["state"] == dmn_states.STALLED else row["state"]
            add(f"{row['agent']} is {word}", now - int(row["updated"]),
                row["detail"] or "", row["agent"])
    loads = loadmod.read_all(con)
    for acct in paths.list_accounts():
        ld = loads.get(acct)
        allowed, why = status.read(acct).assignable(ld.active if ld else 0)
        if not allowed:
            add(f"account {acct} takes no work", 0, why)
    return out
