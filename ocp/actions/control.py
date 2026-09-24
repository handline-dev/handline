"""Acting on an agent: wake, close, unstall, park, fold, start, move, and `perform`."""
from __future__ import annotations

import os
from pathlib import Path

from .. import accounts
from .. import agents
from .. import daemon
from .. import db
from .. import fold as foldmod
from .. import orca
from .. import spawn as spawnmod
from .. import status
from .. import triggers
from .. import vendors
from .. import waiting

from .conversation import chat_reply_ok


def wake(con, agent: str) -> str:
    """Bring a closed or parked agent back - or, on a FATAL (STALLED) one,
    clear the stall (founder #1620/#1631: unstall folded into wake, behind
    the same confirmation `can()` already asks for a STALLED row)."""
    row = db.get_agent_state(con, agent)
    if row and row["state"] == daemon.STALLED:
        return unstall(con, agent)
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
    t = triggers.Trigger(True, daemon.BY_HAND_MARK, "parked by a human from the screen")
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


def login_argv(acct: str, force: bool = False) -> tuple[str, list[str], dict]:
    """(what happened, argv, env) for handing the terminal to claude.

    Returns an empty argv when there is nothing to run, so the caller shows the
    line and stays where it is.
    """
    action, msg = accounts.login_plan(acct, force=force)
    if action != "login":
        return msg, [], {}
    return (f"logging '{acct}' in - finish in claude, then come back",
            vendors.for_account(acct).login_argv(), accounts.env_for(acct))


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
    busy_why = f"{agent} is {state}" + (" - only a human clears that (w)"
                                        if state == daemon.STALLED else "")
    if key == "wake":
        if closed or state == daemon.STALLED:
            return True, ""
        if state in (daemon.PARKED, daemon.THROTTLED, daemon.DEGRADED):
            return daemon.park_wakeable(row)
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
            return False, f"{agent} has no live session to move"
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


# ------------------------------------------------- acting without holding a screen
# Everything above returns when it is done, and some of it is not quick: it waits
# for an agent to finish its turn, for a session to appear, for a model. The
# bridge runs a verb in a process of its own (`ocp.actrun`) and says, while it
# runs, what it can be waiting for and how long the longest of those waits is.
# The verbs are the ones a key on the bridge acts with; nothing else can be asked.
ASYNC_VERBS = ("wake", "close", "park", "fold", "unstall", "terminal", "message", "answer",
               "dismiss", "cancel", "move", "chat_reply")

_IDLE_WAIT_S = orca.IDLE_TIMEOUT_MS // 1000 + 30       # wait_idle's own subprocess bound
_DELIVERY_S = daemon.WAKE_SESSION_TIMEOUT_S + _IDLE_WAIT_S + 60

# verb -> (what it can be waiting for, the longest that can take in seconds). The
# longest is the sum of the waits on the path, each bounded by the constant that
# bounds it; it is not a promise about a typical run, it is the point past which
# something is wrong - and the point `ocp.actrun` is stopped, not just flagged
# (jobs.py). Verbs that only write the record say nothing and are given a minute.
WAITS: dict[str, tuple[str, int]] = {
    "wake": ("its session to start and settle", _DELIVERY_S),
    "close": ("it to finish its turn, then its session to end",
              _IDLE_WAIT_S + 30 + daemon.END_WAIT_S + 15),
    "park": ("its handoff to be recorded and its context cleared", _DELIVERY_S),
    "move": ("its handoff, then its session to end and start elsewhere", 2 * _DELIVERY_S),
    "fold": ("the model to fold the day's events", foldmod.FOLD_TIMEOUT_S + 30),
    "terminal": ("a terminal to be made and its session to start",
                 120 + STARTED_TIMEOUT_S + _IDLE_WAIT_S),
    "message": ("its terminal to be idle, then the message to be typed", _DELIVERY_S),
    "chat_reply": ("its terminal to be idle, then the message to be typed", _DELIVERY_S),
    "answer": ("the terminal it is delivered to to be idle", _DELIVERY_S),
    "cancel": ("both ends to be told", _DELIVERY_S),
    "dismiss": ("", 60),
    "unstall": ("", 60),
}


def perform(verb: str, *args, **kwargs) -> tuple[bool, str]:
    """Run one of `ASYNC_VERBS` on a connection of its own. Returns (ok, said).

    Every verb here says what happened in words, and `ok` is True for all but
    `chat_reply`: their refusal is already in the words ("is not yours to
    dismiss"), and `ocp.actrun`'s "ok" turning that into a red "failed" line
    would misdescribe a well-formed answer. `chat_reply` is the one place a
    screen needs the real bool - the box it was typed in has to know whether
    to clear itself or keep what was typed - so it alone crosses through
    `chat_reply_ok` rather than the plain wrapper the others use.
    """
    if verb not in ASYNC_VERBS:
        raise ValueError(f"not an action: {verb!r}")
    # The verbs live in different modules of this package; look them up by name
    # on the package, which is where a caller (or a test) that replaces one does.
    from .. import actions
    con = db.connect()
    try:
        if verb == "chat_reply":
            return chat_reply_ok(con, *args, **kwargs)
        result = getattr(actions, verb)(con, *args, **kwargs)
        return True, (result[1] if isinstance(result, tuple) else result)
    finally:
        con.close()
