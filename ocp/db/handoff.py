"""Handoff progress and the text a resumed session is given."""
from __future__ import annotations

import time


# Handoff steps, in order (section 8.9).
def start_handoff(con, hid: str, agent: str, session_id: str | None, trigger_kind: str,
                  deadline: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO handoff(id, agent, session_id, trigger_kind, step, "
        "deadline, state, detail, updated) "
        "VALUES(?,?,?,?,'requested',?,'in_flight',NULL,?)",
        (hid, agent, session_id, trigger_kind, deadline, int(time.time())),
    )


def advance_handoff(con, hid: str, step: str,
                    state: str | None = None, detail: str | None = None) -> None:
    """Commit one step. Each step is committed so a restart can resume from it."""
    con.execute(
        "UPDATE handoff SET step = ?, state = COALESCE(?, state), "
        "detail = COALESCE(?, detail), updated = ? WHERE id = ?",
        (step, state, detail, int(time.time()), hid),
    )


# A record is a page or two; one far past that is cut, and an agent keeps its most recent few.
SESSION_TEXT_MAX_CHARS = 20_000
SESSION_STARTS_KEPT = 50
# A fresh session names itself on its first hook; one later than this was not started by that launch.
BIND_WINDOW_S = 600


def note_session_start(con, agent: str, cause: str, resumed: bool, session_id: str | None,
                       text: str = "", prompt: str = "") -> int:
    """Keep that a session was started, why, and what it was given. Never cleared; only the oldest
    beyond SESSION_STARTS_KEPT go. A fresh launch has no session id yet (`bind_session_start`)."""
    cur = con.execute("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (agent, int(time.time()), session_id, cause, int(resumed),
                       text[:SESSION_TEXT_MAX_CHARS], prompt[:SESSION_TEXT_MAX_CHARS]))
    con.execute("DELETE FROM session_starts WHERE agent = ? AND id NOT IN "
                "(SELECT id FROM session_starts WHERE agent = ? ORDER BY id DESC LIMIT ?)",
                (agent, agent, SESSION_STARTS_KEPT))
    return int(cur.lastrowid)


def bind_session_start(con, agent: str, session_id: str) -> None:
    """A fresh session's SessionStart names it: the newest fresh start of this agent's that no session
    has claimed, if one was written just now, is the one it came from."""
    row = con.execute("SELECT id FROM session_starts WHERE agent = ? AND session_id IS NULL AND resumed = 0 "
                      "AND ts >= ? ORDER BY id DESC LIMIT 1", (agent, int(time.time()) - BIND_WINDOW_S)).fetchone()
    if row:
        con.execute("UPDATE session_starts SET session_id = ? WHERE id = ?", (session_id, row["id"]))


def session_starts(con, agent: str, limit: int | None = None):
    """Every session start of `agent`, oldest first: rows of (ts, session_id, cause, resumed, text,
    prompt). `resumed` is 1 for the old conversation, 0 for a new one; `text` is the handoff record the
    session was given, '' where there was none; `session_id` is None for a launch whose session has not
    announced itself. With `limit`, the newest that many (still oldest first)."""
    rows = con.execute("SELECT ts, session_id, cause, resumed, text, prompt FROM session_starts WHERE agent = ? "
                       "ORDER BY id DESC" + (" LIMIT ?" if limit else ""),
                       (agent, limit) if limit else (agent,)).fetchall()
    return rows[::-1]


def set_handoff(con, agent: str, text: str | None) -> None:
    """Keep the record, or clear it with None. Keeping one for an agent that has
    no row yet creates it idle: a handoff is written by hand for an agent that
    has never run."""
    if text is None:
        con.execute("UPDATE agent_state SET handoff = NULL WHERE agent = ?", (agent,))
        return
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, handoff) VALUES(?, 'IDLE', ?, ?) "
        "ON CONFLICT(agent) DO UPDATE SET handoff = excluded.handoff",
        (agent, int(time.time()), text))


def get_handoff(con, agent: str) -> str | None:
    row = con.execute("SELECT handoff FROM agent_state WHERE agent = ?", (agent,)).fetchone()
    return row["handoff"] if row and row["handoff"] else None


def in_flight_handoffs(con):
    return con.execute(
        "SELECT * FROM handoff WHERE state = 'in_flight' ORDER BY updated"
    ).fetchall()


def in_flight_for(con, agent: str):
    return con.execute(
        "SELECT * FROM handoff WHERE agent = ? AND state = 'in_flight' ORDER BY updated LIMIT 1",
        (agent,),
    ).fetchone()


def last_handoff(con, agent: str):
    """This agent's most recently updated handoff, in flight or not, or None.

    One query, so the "was it too soon" check and the "what did the last
    park say" read agree on which row is last instead of each writing its
    own ORDER BY.
    """
    return con.execute(
        "SELECT * FROM handoff WHERE agent = ? ORDER BY updated DESC LIMIT 1",
        (agent,),
    ).fetchone()
