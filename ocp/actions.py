"""What a human can do to an agent, in one place.

`ocp top` is the screen the team is run from, so the actions have to live
somewhere that is not the drawing code: a screen that decided anything for
itself would be a second opinion, and this file exists so there is only ever
one. Every function here calls the same thing the daemon or the CLI calls, and
returns the one line to show for what happened.

Two of them cannot be done from here at all. Logging in needs a human to type a
code into claude's own screen, and starting an agent needs a terminal to start
it in - so those return the command to run and let the caller hand the terminal
over.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import (accounts, agents, ask as askmod, daemon, db, fold as foldmod, paths,
               spawn as spawnmod, status, triggers)

# The name a human's own requests are sent under. An agent asking for a ruling
# addresses it, and this is the inbox `ocp top` shows.
HUMAN = "orchestrator"


def wake(con, agent: str) -> str:
    """Bring a closed or parked agent back."""
    ok, why = daemon.wake_agent(con, agent, lambda _m: None)
    return f"{agent}: {why}" if ok else f"{agent}: not woken - {why}"


def close(con, agent: str) -> str:
    """Give the machine back an idle agent's memory, keeping its terminal."""
    st = status.read_for_agent(agent, agents.account_of(agent))
    ok, why = daemon.close_agent(con, agent, lambda _m: None, st)
    return f"{agent}: {why}" if ok else f"{agent}: not closed - {why}"


def unstall(con, agent: str) -> str:
    """Clear a stall, which is the one state the daemon will not leave alone."""
    row = db.get_agent_state(con, agent)
    if not row or row["state"] != daemon.STALLED:
        return f"{agent}: not STALLED ({row['state'] if row else 'no state'})"
    db.set_agent_state(con, agent, daemon.QUEUED, detail="cleared by a human")
    for counter in ("failed_attempts", "throttle_count", "wake_failures"):
        db.reset_counter(con, agent, counter)
    return f"{agent}: STALLED -> QUEUED"


def park(con, agent: str) -> str:
    """Fold, clear the context, and park until the window rolls over."""
    t = triggers.Trigger(True, "by hand", "parked by a human from the screen")
    ok = daemon.perform_handoff(con, agent, t, lambda _m: None)
    row = db.get_agent_state(con, agent)
    state = row["state"] if row else "?"
    return f"{agent}: {state}" if ok else f"{agent}: handoff degraded ({state})"


def fold(con, agent: str) -> str:
    """Fold today's events into the digest - the one model call in the system."""
    ok, msg = foldmod.fold(agent)
    return msg if ok else f"{agent}: fold rejected - {msg}"


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
    ok, msg = spawnmod.spawn(agent, Path(repo or os.getcwd()).resolve(), base=base)
    return msg


def message(con, agent: str, body: str) -> str:
    """Send an agent work, as the human."""
    if not body.strip():
        return "nothing sent: no message typed"
    ok, msg, _ = askmod.ask(agent, body, requester=HUMAN)
    return msg


def answer(con, rid: int, verdict: str) -> str:
    """Close a request addressed to the human with what the human decided."""
    if not verdict.strip():
        return "nothing sent: no answer typed"
    ok, msg = askmod.resolve(rid, verdict)
    return msg


def dismiss(con, rid: int) -> str:
    """Close a request that needed no answer."""
    ok, msg = askmod.resolve(rid, "read", state="done")
    return msg


def messages(con) -> list[dict]:
    """Everything addressed to the human and not yet closed.

    Two things arrive in this one mailbox and they are not the same errand. An
    escalation is a decision only a human can make, and the agent that sent it
    parks until the answer comes - nothing else in this system will ever clear
    it. A verdict or a report is an answer to something already asked, and
    nobody is waiting on it. Both are addressed here, so the difference is
    carried on the row rather than left for the reader to guess: `blocking` is
    read from what the routing recorded at ask time, not inferred from the
    text.

    Decisions first, then senders that are parked, then the rest - and oldest
    first inside each, because the order things arrived in is not the order
    they matter in.
    """
    rows = con.execute(
        "SELECT id, ts, requester, kind, response, body FROM requests "
        "WHERE target = ? AND state = 'queued' ORDER BY id",
        (HUMAN,),
    ).fetchall()
    out = []
    for r in rows:
        m = dict(r)
        # Two facts, both recorded by the routing when the request was made. A
        # gate is a decision the design says only a human makes. `waiting` says
        # the sender asked for a reply and parked on it - which is what it did
        # at the time; whether it is still parked now is the agent's own state,
        # and the screen says what the row records rather than reading one as
        # the other.
        m["gate"] = m["kind"] == "escalate"
        m["waiting"] = m["response"] == "async"
        m["blocking"] = m["gate"] or m["waiting"]
        out.append(m)
    return sorted(out, key=lambda m: (not m["gate"], not m["waiting"], m["id"]))


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


def can(con, agent: str, key: str) -> bool:
    """Whether an action is worth offering for this agent right now.

    The screen greys out what would only answer with a refusal - but it asks
    the same rows the action does, so the two cannot drift into disagreeing
    about what is possible.
    """
    row = db.get_agent_state(con, agent)
    if not row:
        return key in ("message", "start")
    closed = bool(row["closed_at"])
    if key == "wake":
        return closed or row["state"] in (daemon.PARKED, daemon.THROTTLED, daemon.DEGRADED)
    if key == "close":
        return not closed and bool(row["terminal"])
    if key == "unstall":
        return row["state"] == daemon.STALLED
    if key == "park":
        return not closed and bool(row["terminal"]) and row["state"] != daemon.HANDOFF
    if key == "start":
        return not row["terminal"]
    return True


def agent_detail(con, agent: str) -> list[str]:
    """Everything the screen shows about one agent when it is opened."""
    out: list[str] = []
    row = db.get_agent_state(con, agent)
    try:
        cfg = agents.read(agent)
        acct = agents.account_of(agent)
    except Exception as e:  # noqa: BLE001
        return [f"config error: {e}"]
    st = status.read_for_agent(agent, acct)
    out.append(f"account {acct}   model {cfg.get('model')}   "
               f"effort {cfg.get('effort') or agents.DEFAULT_EFFORT}")
    if row:
        out.append(f"state {row['state']}   ctx {st.ctx_pct}%   "
                   f"terminal {row['terminal'] or '-'}")
        out.append(f"detail {row['detail'] or '-'}")
        if row["closed_at"]:
            out.append(f"closed, resume {row['resume_session'] or 'unknown'}")
    out.append("")
    waiting = db.queued_for(con, agent)
    out.append(f"inbox: {len(waiting)} waiting")
    for r in waiting[:5]:
        first = (r["body"] or "").splitlines()[0] if r["body"] else ""
        out.append(f"  #{r['id']} from {r['requester']}: {first}")
    out.append("")
    hp = foldmod.handoff_path(agent)
    if hp.exists():
        out.append("handoff:")
        out += ["  " + ln for ln in hp.read_text().splitlines()[:12]]
    else:
        out.append("handoff: none written")
    out.append("")
    out.append("recent:")
    for e in con.execute(
        "SELECT ts, kind, payload FROM events WHERE agent = ? ORDER BY id DESC LIMIT 10",
        (agent,),
    ).fetchall():
        out.append(f"  {e['kind']:<20}{(e['payload'] or '')[:60]}")
    return out


def account_detail(con, acct: str) -> list[str]:
    """Everything the screen shows about one account when it is opened."""
    st = status.read(acct)
    allowed, why = st.assignable()
    out = [f"account {acct}",
           f"login    {'logged in' if accounts.logged_in(acct) else 'NOT logged in'}",
           f"config   {paths.account_claude(acct)}",
           f"5h       {st.trusted_pct_5h}% ({st.pct_5h_source})   7d {st.pct_7d}%",
           f"snapshot {'-' if st.age_s is None else str(int(st.age_s)) + 's old'}"
           f"   session {st.session_id or '-'}",
           f"assign   {'yes' if allowed else 'no'} - {why}",
           ""]
    mine = [a for a in paths.list_agents() if _account_of(a) == acct]
    out.append(f"agents on it: {', '.join(mine) or 'none'}")
    for a in mine:
        row = db.get_agent_state(con, a)
        if row:
            closed = " ·closed" if row["closed_at"] else ""
            out.append(f"  {a:<12}{row['state']}{closed}   {row['detail'] or ''}")
    return out


def _account_of(agent: str) -> str | None:
    try:
        return agents.account_of(agent)
    except Exception:  # noqa: BLE001 - an unreadable agent belongs to no account here
        return None


def registered_agents() -> list[str]:
    return paths.list_agents()
