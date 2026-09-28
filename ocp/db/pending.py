"""Pending messages (T351): text the daemon still owes an agent's terminal.

The daemon is the only writer - see `daemon.messaging`. A row is deleted the
moment its text is typed, never marked; there is no history kept here, only
what is still owed.
"""
from __future__ import annotations


def add_pending(con, agent: str, text: str, ts: int) -> int:
    cur = con.execute("INSERT INTO pending_messages(agent, text, ts) VALUES(?,?,?)",
                      (agent, text, ts))
    return int(cur.lastrowid)


def list_pending(con, agent: str | None = None) -> list:
    """Every pending row, oldest first - for one agent, or (at daemon start)
    for all of them."""
    if agent is None:
        return con.execute("SELECT * FROM pending_messages ORDER BY id").fetchall()
    return con.execute("SELECT * FROM pending_messages WHERE agent = ? ORDER BY id",
                       (agent,)).fetchall()


def delete_pending(con, row_id: int) -> None:
    con.execute("DELETE FROM pending_messages WHERE id = ?", (row_id,))
