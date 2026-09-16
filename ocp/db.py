"""SQLite state: events, requests, cursors, agent state, handoff progress.

Two design rules are load-bearing here.

Cursors are row ids, never timestamps (section 5). With a time-based cursor an
event that arrives while a fold is running is either skipped forever or folded
twice, depending on which side of the boundary it lands on. Row ids are
monotonic and assigned by the database, so neither can happen.

Usage events carry a dedup key (section 4, assumption E). One API response
becomes several JSONL lines, each repeating the same usage object. Counting
them all inflated burn rate by 2.4x in the design's sample, which would push
runway down and park every agent.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         INTEGER NOT NULL,
  agent      TEXT    NOT NULL,
  account    TEXT,
  kind       TEXT    NOT NULL,
  payload    TEXT,
  dedup_key  TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS events_agent_id ON events(agent, id);

CREATE TABLE IF NOT EXISTS requests (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          INTEGER NOT NULL,
  requester   TEXT    NOT NULL,
  target      TEXT    NOT NULL,
  body        TEXT    NOT NULL,
  files       TEXT,
  relation    TEXT,
  response    TEXT,
  kind        TEXT,
  chain_depth INTEGER NOT NULL DEFAULT 0,
  parent_id   INTEGER,
  state       TEXT    NOT NULL DEFAULT 'queued',
  verdict     TEXT
);
CREATE INDEX IF NOT EXISTS requests_target ON requests(target, state);
CREATE INDEX IF NOT EXISTS requests_requester ON requests(requester, ts);

-- Fold and digest cursors. Advanced only after the output validates.
CREATE TABLE IF NOT EXISTS cursors (
  name    TEXT PRIMARY KEY,
  row_id  INTEGER NOT NULL,
  updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state (
  agent   TEXT PRIMARY KEY,
  state   TEXT NOT NULL,
  wake_at INTEGER,
  detail  TEXT,
  updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS handoff (
  agent   TEXT PRIMARY KEY,
  stage   TEXT NOT NULL,
  detail  TEXT,
  updated INTEGER NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or paths.db_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=10.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    # WAL so the daemon reading cannot block an agent writing.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- events
def add_event(con, agent: str, kind: str, payload: dict | None = None,
              account: str | None = None, dedup_key: str | None = None) -> int | None:
    """Append an event. Returns the row id, or None if deduped away."""
    try:
        cur = con.execute(
            "INSERT INTO events(ts, agent, account, kind, payload, dedup_key) "
            "VALUES(?,?,?,?,?,?)",
            (int(time.time()), agent, account, kind,
             json.dumps(payload, ensure_ascii=False) if payload else None, dedup_key),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # duplicate dedup_key - the same API response seen again


def events_after(con, row_id: int, agent: str | None = None, limit: int = 10000):
    if agent:
        q = "SELECT * FROM events WHERE id > ? AND agent = ? ORDER BY id LIMIT ?"
        return con.execute(q, (row_id, agent, limit)).fetchall()
    return con.execute(
        "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (row_id, limit)
    ).fetchall()


def max_event_id(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
    return int(row["m"])


# ---------------------------------------------------------------- cursors
def get_cursor(con, name: str) -> int:
    row = con.execute("SELECT row_id FROM cursors WHERE name = ?", (name,)).fetchone()
    return int(row["row_id"]) if row else 0


def set_cursor(con, name: str, row_id: int) -> None:
    con.execute(
        "INSERT INTO cursors(name, row_id, updated) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET row_id=excluded.row_id, updated=excluded.updated",
        (name, int(row_id), int(time.time())),
    )


# ---------------------------------------------------------------- requests
def add_request(con, requester: str, target: str, body: str, files: str | None,
                relation: str | None, response: str | None, kind: str | None,
                chain_depth: int, parent_id: int | None, state: str = "queued",
                verdict: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO requests(ts, requester, target, body, files, relation, response, "
        "kind, chain_depth, parent_id, state, verdict) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), requester, target, body, files, relation, response, kind,
         chain_depth, parent_id, state, verdict),
    )
    return int(cur.lastrowid)


def queued_for(con, target: str):
    return con.execute(
        "SELECT * FROM requests WHERE target = ? AND state = 'queued' ORDER BY id",
        (target,),
    ).fetchall()


def get_request(con, rid: int):
    return con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()


def set_request_state(con, rid: int, state: str, verdict: str | None = None) -> None:
    con.execute("UPDATE requests SET state = ?, verdict = COALESCE(?, verdict) WHERE id = ?",
                (state, verdict, rid))


def asks_since(con, requester: str, since_ts: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS c FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied'",
        (requester, since_ts),
    ).fetchone()
    return int(row["c"])


# ---------------------------------------------------------------- agent state
def set_agent_state(con, agent: str, state: str, wake_at: int | None = None,
                    detail: str | None = None) -> None:
    con.execute(
        "INSERT INTO agent_state(agent, state, wake_at, detail, updated) VALUES(?,?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET state=excluded.state, wake_at=excluded.wake_at, "
        "detail=excluded.detail, updated=excluded.updated",
        (agent, state, wake_at, detail, int(time.time())),
    )


def get_agent_state(con, agent: str):
    return con.execute("SELECT * FROM agent_state WHERE agent = ?", (agent,)).fetchone()


def all_agent_states(con):
    return con.execute("SELECT * FROM agent_state ORDER BY agent").fetchall()


def set_handoff(con, agent: str, stage: str, detail: str | None = None) -> None:
    con.execute(
        "INSERT INTO handoff(agent, stage, detail, updated) VALUES(?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET stage=excluded.stage, detail=excluded.detail, "
        "updated=excluded.updated",
        (agent, stage, detail, int(time.time())),
    )


def get_handoff(con, agent: str):
    return con.execute("SELECT * FROM handoff WHERE agent = ?", (agent,)).fetchone()
