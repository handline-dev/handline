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
