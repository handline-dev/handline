"""An agent ocp has work for and cannot give it does not sit silently.

Every path that types at an agent refuses when the screen says a line would not
land, and refusing is right. It is also all that happened: the agent read as
idle, the work stayed queued, and the daemon did not type, tick after tick,
while nobody was told. This is the part that tells them.

A block is a fact about an agent and its work, not a state of the agent. The
daemon keeps trying and carries on the tick the obstacle goes, so nothing here
is a state a person has to clear (STALLED is that); a `delivery_blocks` row
stands while the work waits and the same obstacle holds, is on the founder's
list from SHOW_AFTER_S, and becomes one request to the founder at
ESCALATE_AFTER_S. It is written by the daemon and read by the bridge and
`ocp ps`, which are other processes, so it lives in the database.

ocp names what is in the way and never answers it. A dialog is answered by a
person; that is decided, not an oversight.
"""
from __future__ import annotations

import time

from . import ask as askmod, db, orca, paths, status, waiting

# The same obstacle, with work waiting, this long before it is said. A dialog
# does not clear itself, so the wait is only for the person who is at the
# terminal and answers within a minute or two; longer and an agent sits on the
# founder's own answer with the founder not knowing.
SHOW_AFTER_S = 180
# Long enough that someone at the bridge has seen the list and acted, short
# enough that nobody learns of it from an agent that has been silent for an
# hour. Later than SHOW_AFTER_S: a request stays until someone closes it if
# the daemon cannot, and the list clears itself.
ESCALATE_AFTER_S = 600
# What each check costs an agent is a screen read and a terminal listing, so a
# block is re-read this often and no more; a block that is not due is left as it was.
RECHECK_S = 10

NO_TERMINAL, NO_SESSION, STALLED = "no terminal", "no session", "stalled"

# What to do about each, in the words of the bridge's own keys.
ADVICE = {
    orca.MODAL: "answer it in its terminal (t on the agents panel); ocp will not",
    orca.UNSENT: "send or clear the text in its terminal (t); ocp will not type into it",
    NO_TERMINAL: "t on the agents panel starts one",
    NO_SESSION: "w on the agents panel wakes it",
    STALLED: "u on the agents panel clears it",
}
LABEL = {orca.MODAL: "dialog open", orca.UNSENT: "unsent text", NO_TERMINAL: "no terminal",
         NO_SESSION: "no session", STALLED: "stalled"}

_checked: dict[str, int] = {}


def reason(kind: str, detail: str) -> str:
    """What is in the way, as a sentence."""
    if kind in (orca.MODAL, orca.UNSENT):
        return orca.Blocker(kind, detail).reason()
    return f"{LABEL.get(kind, kind)}: {detail}" if detail else LABEL.get(kind, kind)


def headline(kind: str, detail: str) -> str:
    """The short form for a row with little room: the label and, for a dialog,
    only its title."""
    head = detail.split(" - ")[0] if kind == orca.MODAL else detail
    return f"{LABEL.get(kind, kind)}: {head}" if head else LABEL.get(kind, kind)


def work_waiting(con, agent: str, row, now: int) -> str:
    """What ocp wants to type at this agent and has not: the head of its queue
    while it is untold, answers it asked for (`would_wake`'s own terms), and the
    reminder to resolve a request it has sat idle on. Empty when there is nothing."""
    from . import daemon

    parts = []
    head = db.head_of_queue(con, agent)
    if head and (db.get_cursor(con, f"notify:{agent}") != int(head["id"])
                 or daemon._session_changed(con, agent)):
        behind = db.queue_depth(con, agent) - 1
        parts.append(f"request #{head['id']}" + (f" (+{behind} behind)" if behind > 0 else ""))
    answers = db.unannounced_answers(con, agent)
    if answers:
        parts.append("answer " + ", ".join(f"#{r['id']}" for r in answers[:3])
                     + (f" (+{len(answers) - 3})" if len(answers) > 3 else ""))
    idle = daemon.forgotten_resolve(con, agent, row, now)
    if idle and idle["idle_s"] >= daemon.NUDGE_AFTER_S \
            and db.get_cursor(con, f"nudge:{agent}") != int(idle["head"]["id"]):
        parts.append(f"reminder to resolve #{idle['head']['id']}")
    return "; ".join(parts)


def classify(con, agent: str, row) -> tuple[str, str] | None:
    """(kind, detail) of what is in the way of giving this agent its work, or
    None when nothing is. One screen read at most.

    Left out on purpose: busy, which is what an agent working is and which
    `STUCK_RUNNING_S` already judges; a usage wall, which resolves when the
    window does; a closed session, which the wake path brings back and whose
    failures end in STALLED; and a permission prompt, which the hooks already
    put on the founder's list.
    """
    from . import daemon

    if not row or row["closed_at"] or waiting.of(row):
        return None
    if row["state"] == daemon.STALLED:
        return STALLED, row["detail"] or ""
    handle = row["terminal"]
    if not handle:
        return NO_TERMINAL, ""
    if not orca.available():
        return None
    if not orca.alive(handle):
        return NO_TERMINAL, "its terminal is gone - Orca no longer has it"
    if daemon.wall_reason(con, agent, row):
        return None
    if row["state"] not in (daemon.RUNNING, daemon.HANDOFF) and not daemon.session_present(agent):
        return NO_SESSION, ""
    seen = orca.screen_blocker(handle)
    if seen is None or seen.kind == orca.UNREADABLE:
        return None
    # A working agent's input box has whatever its user is typing into it; only
    # a dialog is in the way there.
    if seen.kind == orca.UNSENT and row["state"] in (daemon.RUNNING, daemon.HANDOFF):
        return None
    return seen.kind, seen.detail


def note_close_failure(con, agent: str, log) -> None:
    """ocp typed `/exit` and the session is still there: if the screen shows why,
    it is on the list at once, since the dialog is ocp's own doing and nobody has
    been given the minutes the list otherwise waits for."""
    handle = db.get_terminal(con, agent)
    seen = orca.screen_blocker(handle) if handle else None
    if not seen or seen.kind != orca.MODAL:
        return
    now = int(time.time())
    if db.block_get(con, agent) is None:
        db.block_open(con, agent, seen.kind, seen.detail,
                      work_waiting(con, agent, db.get_agent_state(con, agent), now), now,
                      show_at=now, origin="close")
        log(f"  {agent}: {reason(seen.kind, seen.detail)} - ocp's own /exit raised it")
    dismiss_own_dialog(agent, handle)


def dismiss_own_dialog(agent: str, handle: str) -> bool:
    """Where an exception would act: cancelling the dialog ocp's own `/exit`
    raised, which undoes ocp's action and decides nothing for the agent. Not
    granted, so it does nothing; whoever grants it changes this function."""
    return False


def sweep(con, log, now: int | None = None) -> None:
    """Once per tick, after the agents: open, keep and end each agent's block,
    and raise the request for those that have outlasted ESCALATE_AFTER_S - one
    request for all that come due in the same pass."""
    now = int(time.time()) if now is None else now
    due = []
    registered = set(paths.list_agents())
    for row in db.all_agent_states(con):
        agent = row["agent"]
        # The human is never typed at, and a row for an agent that is not on the
        # team has nobody to be given anything.
        if agent not in registered:
            continue
        try:
            rec = _track(con, agent, row, now, log)
        except Exception as e:  # noqa: BLE001 - one agent's unreadable screen is not the tick's failure
            log(f"  {agent}: block check failed: {e}")
            continue
        if rec is not None and rec["request_id"] is None \
                and now - rec["since"] >= ESCALATE_AFTER_S:
            due.append(rec)
    if due:
        _raise(con, due, now, log)


def _track(con, agent: str, row, now: int, log):
    rec = db.block_get(con, agent)
    work = work_waiting(con, agent, row, now)
    if not work and not (rec and rec["origin"] == "close"):
        if rec:
            _end(con, agent, rec, "nothing is waiting for it now", log)
        return None
    if rec is not None and now - _checked.get(agent, 0) < RECHECK_S:
        return rec
    _checked[agent] = now
    found = classify(con, agent, row)
    if found is None:
        if rec:
            _end(con, agent, rec, "it can be given its work again", log)
        return None
    kind, detail = found
    if rec is None:
        db.block_open(con, agent, kind, detail, work, now, show_at=now + SHOW_AFTER_S)
        log(f"  {agent}: work is waiting and cannot be given - {reason(kind, detail)}")
    else:
        if rec["kind"] != kind:
            log(f"  {agent}: still cannot be given its work - {reason(kind, detail)}")
        db.block_update(con, agent, kind, detail, work or rec["work"], now)
    return db.block_get(con, agent)


def _end(con, agent: str, rec, why: str, log) -> None:
    db.block_clear(con, agent)
    log(f"  {agent}: no longer blocked ({LABEL.get(rec['kind'], rec['kind'])}) - {why}")
    rid = rec["request_id"]
    if rid and not any(b["request_id"] == rid for b in db.blocks(con)):
        row = db.get_request(con, rid)
        if row is not None and row["state"] == "queued":
            askmod.resolve(rid, f"cleared - {why}", state="done", notify=False)


def _raise(con, recs, now: int, log) -> None:
    lines = []
    for r in recs:
        held = now - r["since"]
        lines.append(f"{r['agent']} cannot be given its work ({status.ago(held)}).\n"
                     f"  In the way: {reason(r['kind'], r['detail'])}\n"
                     f"  Waiting: {r['work'] or 'nothing yet'}\n"
                     f"  To clear it: {ADVICE.get(r['kind'], 'see `ocp ps`')}.")
    text = "[blocked] " + ("\n\n".join(lines)) + \
        "\n\nocp typed nothing at it. This closes itself when it can be given its work."
    rid = askmod.escalate(con, recs[0]["agent"], text, "delivery blocked")
    db.block_set_request(con, [r["agent"] for r in recs], rid)
    log(f"  blocked: raised #{rid} for {', '.join(r['agent'] for r in recs)}")


def founder_rows(con, now: int) -> list[dict]:
    """The blocks that are on the founder's list, in `founder_waits`' shape."""
    return [{"kind": "blocked", "who": r["agent"], "red": True, "since": int(r["since"]),
             "what": f"{headline(r['kind'], r['detail'])} · waiting: {r['work'] or 'nothing yet'}"}
            for r in db.blocks(con) if now >= r["show_at"]]
