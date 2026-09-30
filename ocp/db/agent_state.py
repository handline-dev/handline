"""Per-agent state: state, terminal, account, closing, activity, counters."""
from __future__ import annotations

import time

from . import cursors


def set_task(con, agent: str, task: str | None) -> None:
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, task) VALUES(?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET task=excluded.task, updated=excluded.updated",
        (agent, "IDLE", int(time.time()), task),
    )


def set_current_task(con, agent: str, tid: int | None) -> None:
    """T292: the one task board task `agent` is holding right now - set on
    delivery of a `task new` or by `task start`, cleared on done or release.
    Not `set_task`'s `task` column: that one is the free-text prompt a
    spawned terminal starts on, unrelated to the task board."""
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, current_task) VALUES(?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET current_task=excluded.current_task, "
        "updated=excluded.updated",
        (agent, "IDLE", int(time.time()), tid),
    )


def current_task(con, agent: str) -> int | None:
    row = con.execute("SELECT current_task FROM agent_state WHERE agent = ?", (agent,)).fetchone()
    return int(row["current_task"]) if row and row["current_task"] is not None else None


# ---------------------------------------------------------------- agent state
def set_agent_state(con, agent: str, state: str, wake_at: int | None = None,
                    detail: str | None = None) -> None:
    con.execute(
        "INSERT INTO agent_state(agent, state, wake_at, detail, updated) VALUES(?,?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET state=excluded.state, wake_at=excluded.wake_at, "
        "detail=excluded.detail, updated=excluded.updated",
        (agent, state, wake_at, detail, int(time.time())),
    )


def set_state_unless_running(con, agent: str, state: str, detail: str | None = None) -> bool:
    """Write `state`, but only if the row is not RUNNING right now - atomically,
    the same shape as `close_if_queued`. A read-then-write here (check RUNNING,
    then write) leaves a window a hook's own RUNNING can land in between the
    two; this closes it in one statement. True only if this call is the one
    that wrote it - a caller that gets False knows RUNNING is what is there
    instead, without a second read."""
    cur = con.execute(
        "UPDATE agent_state SET state = ?, wake_at = NULL, detail = ?, updated = ? "
        "WHERE agent = ? AND state != 'RUNNING'",
        (state, detail, int(time.time()), agent),
    )
    return cur.rowcount > 0


def set_terminal(con, agent: str, terminal: str | None, worktree: str | None) -> None:
    """Record where an agent lives. This says nothing about what it is doing.

    It ran at `ocp launch`, before execve, and inserted the row as RUNNING - so
    an agent was born working, and `_mark_activity` then declined to rewrite a
    state that was already RUNNING. RUNNING is what stops work being delivered
    and stops a session being closed, so the row that only meant to record a
    terminal handle was taking the agent out of service before it had done
    anything. A new agent is IDLE: it has a terminal and no work.
    """
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, terminal, worktree) VALUES(?,?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET terminal=excluded.terminal, "
        "worktree=excluded.worktree, updated=excluded.updated",
        (agent, "IDLE", int(time.time()), terminal, worktree),
    )


def set_account(con, agent: str, account: str | None, source: str = "recorded") -> None:
    """Record which account a session is on, and how sure of it this is.

    `source="recorded"` (the default) is written by `ocp launch`, by a move
    once its session is up, or by the daemon's check of the running process;
    only the launch writes from inside the process itself, so a move's record
    can disagree with the environment until `daemon._confirmed_reading`
    reconciles it. `source="assumed"` is a guess made before
    that process exists to confirm anything, written so a row is not simply
    null in the window between a spawn and its own launch reporting in -
    `agents.account_reading` is what tells the two apart on read.
    """
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, account, account_source) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET account=excluded.account, "
        "account_source=excluded.account_source, updated=excluded.updated",
        (agent, "IDLE", int(time.time()), account, source),
    )


def set_launch_fingerprint(con, agent: str, fingerprint: str | None) -> None:
    """Record what `launch.fingerprint` computed for the session just execve'd.

    Written from inside that same process, alongside `set_account` - the one
    moment a recipe and a running session are known to match.
    """
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, launch_fingerprint) VALUES(?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET launch_fingerprint=excluded.launch_fingerprint, "
        "updated=excluded.updated",
        (agent, "IDLE", int(time.time()), fingerprint),
    )


def set_closed(con, agent: str, session_id: str | None) -> None:
    """Record that this agent's session was closed, and how to get it back."""
    con.execute(
        "UPDATE agent_state SET closed_at = ?, resume_session = ?, updated = ? WHERE agent = ?",
        (int(time.time()), session_id, int(time.time()), agent),
    )


def clear_closed(con, agent: str) -> None:
    """A session is running again, wherever it came from - a wake, a start for
    waiting work, a human's relaunch. It is a new process, so it is told about
    its queue afresh: what an earlier one was told may never have been read,
    and a resume keeps the session id that would say otherwise. A tell recorded
    after the close is not one of those: it went to the session that came back,
    as its launch prompt (`lifecycle._wake_agent`), and stands."""
    row = con.execute("SELECT closed_at FROM agent_state WHERE agent = ?", (agent,)).fetchone()
    told = cursors.cursor_updated(con, f"notify:{agent}")
    if not (row and row["closed_at"] and told and told > row["closed_at"]):
        cursors.forget_told(con, agent)
    con.execute(
        "UPDATE agent_state SET closed_at = NULL, resume_session = NULL, updated = ? "
        "WHERE agent = ?", (int(time.time()), agent),
    )


def last_event_ts(con, agent: str, kind: str) -> int | None:
    row = con.execute("SELECT MAX(ts) AS t FROM events WHERE agent = ? AND kind = ?",
                      (agent, kind)).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


# What moves an agent's activity clock: the agent taking a turn, never an
# event ocp causes without one - ocp's own wake types a prompt, and that
# UserPromptSubmit is rightly in this list, because a real turn follows it.
# An allow-list, not a deny-list - a kind nobody has sorted yet must default
# to "not activity", because the other direction gave a closed agent a clock
# stamped at close time and resumed a dead cache (#1527, #1533, and the same
# bug again through SessionEnd, which ocp's own `/exit` fires at once).
# ask/cancel/commit/raise_decision/report/resolve/withdraw are commands the agent
# itself ran. no_task joins them for the same reason: cmd_resolve only writes
# it on a queued row the resolving agent just answered, in the same command
# as its own "resolve" event, so it can never move a clock "resolve" did not
# already move (discard_task is deliberately NOT here - see the daemon_kinds
# comment in test_only_the_agents_own_events_move_its_activity_clock; #1565
# SF3, the fourth event ocp could attribute to an agent that did nothing).
# Of the hook events ocp captures (ocp/session/events.py CAPTURED),
# UserPromptSubmit, Stop and StopFailure are a live turn happening or ending;
# SubagentStop and PostCompact only ever fire mid a live session, never from
# anything ocp can trigger on its own; PermissionRequest and TeammateIdle both
# mean the agent's own turn ran into something needing a human, same as
# events.py's TURN_BOUNDARIES comment gives for leaving them out of
# TURN_BOUNDARIES. SessionStart and SessionEnd are the two left out: ocp's own
# resume fires the first and ocp's own `/exit` fires the second, so both can
# happen with the agent having done nothing - the exact reasoning events.py
# gives for leaving SessionStart out of ACTIVE_EVENTS, applied here to the
# clock it never reached before.
AGENT_ACTIVITY_KINDS = frozenset({
    "ask", "cancel", "commit", "raise_decision", "report", "resolve", "withdraw",
    "no_task",
    # T292: task_new is the opener asking (its own delivery, C1); task_start
    # and task_note are likewise always the calling agent's own doing -
    # picking up a task or writing a note - never something ocp did to them.
    "task_new", "task_start", "task_note", "task_begin", "task_handoff",
    "UserPromptSubmit", "Stop", "StopFailure", "SubagentStop", "PostCompact",
    "PermissionRequest", "TeammateIdle",
    # T288: fires on the same Stop hook call as "Stop" - the only one of the
    # two written when the stop is actually blocked, so it is this agent's
    # own activity too, not something ocp did to it.
    "stop_gate",
    # T295: fires on the agent's own PreToolUse call, denied or not.
    "edit_gate",
})


def last_activity(con, agent: str) -> int | None:
    """When this agent last did anything we can see. Its own hook events are
    the record - an event ocp causes about it is not activity."""
    placeholders = ",".join("?" * len(AGENT_ACTIVITY_KINDS))
    row = con.execute(
        f"SELECT MAX(ts) AS t FROM events WHERE agent = ? AND kind IN ({placeholders})",
        (agent, *AGENT_ACTIVITY_KINDS)
    ).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


def last_activity_before(con, agent: str, ts: int) -> int | None:
    """`last_activity`, as of the moment `ts`: the latest of its own events before it."""
    placeholders = ",".join("?" * len(AGENT_ACTIVITY_KINDS))
    row = con.execute(
        f"SELECT MAX(ts) AS t FROM events WHERE agent = ? AND ts < ? AND kind IN ({placeholders})",
        (agent, ts, *AGENT_ACTIVITY_KINDS)
    ).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


def last_activity_id(con, agent: str, kinds: frozenset[str]) -> int | None:
    """The row id of this agent's most recent event of one of `kinds`, or None.

    Same idea as `last_activity`, but by `events.id` rather than `ts`: two
    calls a real agent makes moments apart land in the same whole second more
    often than not, and a caller ordering "did X happen after event N"
    (`waiting.of`) needs an answer a tied clock cannot give. Row ids are
    assigned in insertion order and never tie.

    Unlike `last_activity`, `kinds` is the caller's own set rather than
    AGENT_ACTIVITY_KINDS: its one caller trusts only a few of those kinds as
    evidence a stuck agent moved on (see `waiting.MOVED_ON_KINDS`), and a
    generic exclude-from-everything parameter for one caller and one value
    was more surface than the job needed.
    """
    placeholders = ",".join("?" * len(kinds))
    row = con.execute(
        f"SELECT MAX(id) AS i FROM events WHERE agent = ? AND kind IN ({placeholders})",
        (agent, *kinds)
    ).fetchone()
    return int(row["i"]) if row and row["i"] is not None else None


def last_account_activity(con, account: str) -> int | None:
    """When a session on this account last took a turn.

    What the usage poll asks before it asks the endpoint (D11): rate limits move
    when a turn is taken and at no other time, so an account with no turn since
    its last reading has nothing new to report. Only the hook events that bound
    a turn count - the daemon's own bookkeeping spends nothing.

    Not a promise that nothing was spent: a session outside ocp (a human's
    `claude -p`, `ocp fold`'s one call) leaves no event here, which is why the
    poll also has a cadence it keeps whatever this says.
    """
    row = con.execute(
        "SELECT MAX(ts) AS t FROM events WHERE account = ? AND kind IN "
        "('UserPromptSubmit', 'Stop', 'StopFailure', 'PostCompact', 'SessionStart')",
        (account,)).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


def get_terminal(con, agent: str) -> str | None:
    row = con.execute("SELECT terminal FROM agent_state WHERE agent = ?", (agent,)).fetchone()
    return row["terminal"] if row else None


def agent_of_terminal(con, terminal: str) -> str | None:
    """The agent last recorded in this terminal."""
    row = con.execute("SELECT agent FROM agent_state WHERE terminal = ? "
                      "ORDER BY updated DESC LIMIT 1", (terminal,)).fetchone()
    return row["agent"] if row else None


def bump_counter(con, agent: str, column: str, by: int = 1) -> None:
    if column not in ("failed_attempts", "continuations", "throttle_count",
                      "wake_failures"):
        raise ValueError(f"not a counter column: {column}")
    con.execute(
        f"INSERT INTO agent_state(agent, state, updated, {column}) VALUES(?,?,?,?) "
        f"ON CONFLICT(agent) DO UPDATE SET {column} = agent_state.{column} + ?, updated = ?",
        (agent, "IDLE", int(time.time()), by, by, int(time.time())),
    )


def reset_counter(con, agent: str, column: str) -> None:
    if column not in ("failed_attempts", "continuations", "throttle_count",
                      "wake_failures"):
        raise ValueError(f"not a counter column: {column}")
    con.execute(f"UPDATE agent_state SET {column} = 0 WHERE agent = ?", (agent,))


def due_agents(con, now: int):
    """Agents whose wake_at has come due. The persisted scan that section 8.4
    relies on: an in-memory timer is an optimization, never the source of truth."""
    return con.execute(
        "SELECT * FROM agent_state WHERE wake_at IS NOT NULL AND wake_at <= ? ORDER BY wake_at",
        (now,),
    ).fetchall()


def get_agent_state(con, agent: str):
    return con.execute("SELECT * FROM agent_state WHERE agent = ?", (agent,)).fetchone()


def all_agent_states(con):
    return con.execute("SELECT * FROM agent_state ORDER BY agent").fetchall()
