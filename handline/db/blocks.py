"""Blocked-agent records and dialog presses."""
from __future__ import annotations



def block_get(con, agent: str):
    return con.execute("SELECT * FROM delivery_blocks WHERE agent = ?", (agent,)).fetchone()


def blocks(con) -> list:
    return con.execute("SELECT * FROM delivery_blocks ORDER BY since, agent").fetchall()


def block_open(con, agent: str, kind: str, detail: str, work: str, now: int,
               show_at: int, origin: str = "tick", title: str = "") -> None:
    con.execute(
        "INSERT OR REPLACE INTO delivery_blocks"
        "(agent, kind, detail, work, since, show_at, seen, request_id, origin, title) "
        "VALUES(?,?,?,?,?,?,?,NULL,?,?)",
        (agent, kind, detail, work, now, show_at, now, origin, title))


def block_update(con, agent: str, kind: str, detail: str, work: str, now: int,
                 title: str = "") -> None:
    con.execute("UPDATE delivery_blocks SET kind = ?, detail = ?, work = ?, seen = ?, title = ? "
                "WHERE agent = ?", (kind, detail, work, now, title, agent))


def block_set_request(con, agents: list[str], rid: int) -> None:
    con.executemany("UPDATE delivery_blocks SET request_id = ? WHERE agent = ?",
                    [(rid, a) for a in agents])


def press_add(con, agent: str, now: int, title: str, outcome: str, note: str = "") -> None:
    con.execute("INSERT INTO dialog_presses(agent, at, title, outcome, note) VALUES(?,?,?,?,?)",
                (agent, now, title, outcome, note))


def presses_since(con, agent: str, since: int) -> list:
    return con.execute("SELECT * FROM dialog_presses WHERE agent = ? AND at >= ? "
                       "ORDER BY id", (agent, since)).fetchall()


def block_clear(con, agent: str) -> None:
    con.execute("DELETE FROM delivery_blocks WHERE agent = ?", (agent,))
