"""Acting on an agent: wake, close, unstall, park, fold, start, move, and `perform`."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..team import accounts
from ..team import agents
from ..daemon import delivery as dmn_delivery, handoff as dmn_handoff, lifecycle as dmn_lifecycle, states as dmn_states
from .. import db
from ..infra import lock, waits
from ..session import fold as foldmod
from ..session import orca
from ..team import spawn as spawnmod
from ..usage import status
from ..session import triggers
from .. import vendors
from ..session import waiting

from .conversation import chat_reply_ok


def wake(con, agent: str) -> str:
    """Bring a closed or parked agent back - or, on a FATAL (STALLED) one,
    clear the stall (founder #1620/#1631: unstall folded into wake, behind
    the same confirmation `can()` already asks for a STALLED row)."""
    row = db.get_agent_state(con, agent)
    if row and row["state"] == dmn_states.STALLED:
        return unstall(con, agent)
    ok, why = dmn_lifecycle.wake_agent(con, agent, lambda _m: None)
    return f"{agent}: {why}" if ok else f"{agent}: not woken - {why}"


def close_result(con, agent: str, fresh: bool = False) -> tuple[bool, str]:
    """`close`, with whether it closed. `fresh` also drops the session id, so the next wake is a new
    conversation."""
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    close_fn = dmn_lifecycle.close_fresh if fresh else dmn_lifecycle.close_agent
    ok, why = close_fn(con, agent, lambda _m: None, st)
    return ok, why if ok else f"{agent}: not closed - {why}"


def restart_result(con, agent: str, fresh: bool = False, force: bool = False,
                   by: str = db.HUMAN) -> tuple[bool, str]:
    """`dmn_lifecycle.restart_agent`, for the daemon's `restart` verb."""
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    ok, why = dmn_lifecycle.restart_agent(con, agent, lambda _m: None, st, fresh=fresh, force=force, by=by or db.HUMAN)
    return ok, why if ok else f"{agent}: not restarted - {why}"


def restart(con, agent: str, fresh: bool = False, force: bool = False) -> str:
    """End this agent's session and start it again in the same terminal."""
    return restart_result(con, agent, fresh, force)[1]


def close(con, agent: str, fresh: bool = False) -> str:
    """Give the machine back an idle agent's memory, keeping its terminal."""
    return close_result(con, agent, fresh)[1]


def unstall(con, agent: str) -> str:
    """Clear a stall, which is the one state the daemon will not leave alone."""
    row = db.get_agent_state(con, agent)
    if not row or row["state"] != dmn_states.STALLED:
        return f"{agent}: not STALLED ({row['state'] if row else 'no state'})"
    db.set_agent_state(con, agent, dmn_states.QUEUED, detail="cleared by a human")
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
    t = triggers.Trigger(True, dmn_states.BY_HAND_MARK, "parked by a human from the screen")
    said: list[str] = []
    ok = dmn_handoff.perform_handoff(con, agent, t, said.append)
    if said:
        return said[-1].strip()
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
    from. `start_in`/`spawn` confirm a session actually started (T146 pt.2),
    so their own `msg` is already why when one did not.

    Locked (T351) the same as wake/close/park/move, unlike them not by a
    layer underneath - nothing an agent with no terminal yet has to spawn
    into would otherwise stop two starts landing at once and making it two.
    """
    with lock.terminal(agent) as mine:
        if not mine:
            return f"{agent}: someone else is starting or waking it; try again"
        row = db.get_agent_state(con, agent)
        if row and row["terminal"]:
            return f"{agent}: already has a terminal ({row['terminal']}) - wake it instead"
        if dmn_lifecycle.resumable_without_terminal(row):
            # Its window closed with its session (T407): resume it, whether or not anything waits.
            return f"{agent}: {dmn_lifecycle._wake_agent(con, agent, lambda _m: None, force=True)[1]}"
        wt = spawnmod.worktree_path(row["worktree"] if row else None)
        if wt:
            # It has somewhere to run already; it lost the window it ran in.
            _, msg = spawnmod.start_in(agent, wt)
        else:
            _, msg = spawnmod.spawn(agent, Path(repo or os.getcwd()).resolve(), base=base)
        return msg


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
    env = {k: v for k, v in accounts.env_for(acct).items() if not k.startswith(_SESSION_ENV)}
    return (f"logging '{acct}' in - finish in claude, then come back",
            vendors.for_account(acct).login_argv(), env)


# Set by a claude session for its own children; a login started from a terminal
# that was itself opened by one would inherit it. CLAUDE_CONFIG_DIR is the account's.
_SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_PID", "CLAUDE_TMPDIR", "CLAUDE_EFFORT")


def tail_of_typescript(raw: str, lines: int = 2) -> str:
    """The last few lines a terminal session printed, escapes and blanks removed: what
    claude said before it exited, for a toast."""
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    kept = [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip()]
    return " | ".join(kept[-lines:])


def move(con, agent: str, account: str) -> str:
    """Force this agent's live session onto `account`, recording its handoff first."""
    ok, msg = dmn_handoff.force_move(con, agent, account, "human (bridge)", lambda _m: None)
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
    busy = state in (dmn_states.RUNNING, dmn_states.HANDOFF, dmn_states.STALLED)
    busy_why = f"{agent} is {state}" + (" - only a human clears that (w)"
                                        if state == dmn_states.STALLED else "")
    if key == "wake":
        if state == dmn_states.STALLED:
            return True, ""
        if closed:
            # The wake itself refuses this (T124), so the key is not offered.
            return ((True, "") if dmn_lifecycle.waiting_for(con, agent)
                    else (False, f"{agent}: {dmn_lifecycle.NOTHING_WAITING}"))
        if state in (dmn_states.PARKED, dmn_states.THROTTLED, dmn_states.DEGRADED):
            return dmn_states.park_wakeable(row)
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
    if key == "restart":
        if not closed and not row["terminal"]:
            return False, f"{agent} has no terminal - there is no session to restart (t starts one)"
        if busy:
            return False, busy_why + " - `hl restart --force` waits for the turn to end"
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
        if state in (dmn_states.HANDOFF, dmn_states.STALLED):
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
# bridge runs a verb in a process of its own (`handline.ui.actrun`) and says, while it
# runs, what it can be waiting for and how long the longest of those waits is.
# The verbs are the ones a key on the bridge acts with; nothing else can be asked.
ASYNC_VERBS = ("wake", "close", "restart", "park", "fold", "unstall", "terminal", "message", "answer",
               "dismiss", "cancel", "move", "chat_reply")

_IDLE_WAIT_S = orca.IDLE_TIMEOUT_MS // 1000 + 30       # wait_idle's own subprocess bound
_DELIVERY_S = waits.WAKE_SESSION_TIMEOUT_S + _IDLE_WAIT_S + 60

# verb -> (what it can be waiting for, the longest that can take in seconds). The
# longest is the sum of the waits on the path, each bounded by the constant that
# bounds it; it is not a promise about a typical run, it is the point past which
# something is wrong - and the point `handline.ui.actrun` is stopped, not just flagged
# (jobs.py). Verbs that only write the record say nothing and are given a minute.
WAITS: dict[str, tuple[str, int]] = {
    "wake": ("its session to start and settle", _DELIVERY_S),
    "close": ("it to finish its turn, then its session to end",
              _IDLE_WAIT_S + 30 + waits.END_WAIT_S + 15),
    "restart": ("its session to end, then a new one to start and settle",
                _IDLE_WAIT_S + 30 + waits.END_WAIT_S + 15 + _DELIVERY_S),
    "park": ("its handoff to be recorded and its context cleared", _DELIVERY_S),
    "move": ("its handoff, then its session to end and start elsewhere", 2 * _DELIVERY_S),
    "fold": ("the model to fold the day's events", foldmod.FOLD_TIMEOUT_S + 30),
    "terminal": ("a terminal to be made and its session to start",
                 120 + spawnmod.STARTED_TIMEOUT_S + _IDLE_WAIT_S),
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
    dismiss"), and `handline.ui.actrun`'s "ok" turning that into a red "failed" line
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
