"""Cursors and small key-value state (row ids, not timestamps; see fold.py)."""
from __future__ import annotations

import time


# ---------------------------------------------------------------- cursors
def get_meta(con, name: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else None


def set_meta(con, name: str, value: str | None) -> None:
    con.execute(
        "INSERT INTO meta(name, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (name, value, int(time.time())),
    )


def get_cursor(con, name: str) -> int:
    row = con.execute("SELECT row_id FROM cursors WHERE name = ?", (name,)).fetchone()
    return int(row["row_id"]) if row else 0


def clear_cursor(con, name: str) -> None:
    con.execute("DELETE FROM cursors WHERE name = ?", (name,))


def get_notified_session(con, agent: str) -> str | None:
    row = con.execute("SELECT notified_session FROM agent_state WHERE agent = ?",
                      (agent,)).fetchone()
    return row["notified_session"] if row else None


def set_notified_session(con, agent: str, session_id: str | None) -> None:
    con.execute("UPDATE agent_state SET notified_session = ? WHERE agent = ?",
                (session_id, agent))


def set_cursor(con, name: str, row_id: int) -> None:
    con.execute(
        "INSERT INTO cursors(name, row_id, updated) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET row_id=excluded.row_id, updated=excluded.updated",
        (name, int(row_id), int(time.time())),
    )
