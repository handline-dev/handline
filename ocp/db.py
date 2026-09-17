"""SQLite state: events, requests, cursors, agent state, handoff progress.

Cursors are row ids, not timestamps; see fold.py for why that matters.

Usage events carry a dedup key (assumption E): one API response becomes several
JSONL lines repeating the same usage object, which inflated burn rate by 2.4x
in the design's sample.
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
  -- Every ask carries the id of the conversation it belongs to, so a cycle
  -- (A->B->A) can be seen inside one chain rather than guessed at from depth.
  chain_id    TEXT,
  state       TEXT    NOT NULL DEFAULT 'queued',
  verdict     TEXT
);
CREATE INDEX IF NOT EXISTS requests_target ON requests(target, state);
CREATE INDEX IF NOT EXISTS requests_requester ON requests(requester, ts);
CREATE INDEX IF NOT EXISTS requests_chain ON requests(chain_id, id);

-- Advanced only after the fold output validates.
CREATE TABLE IF NOT EXISTS cursors (
  name    TEXT PRIMARY KEY,
  row_id  INTEGER NOT NULL,
  updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state (
  agent           TEXT PRIMARY KEY,
  state           TEXT NOT NULL,
  wake_at         INTEGER,
  detail          TEXT,
  updated         INTEGER NOT NULL,
  -- Section 8.5: these count different things on purpose. failed_attempts
  -- counts real failures and is capped; continuations come from handoff and
  -- are unlimited, because capping them makes the system stop sooner the
  -- harder it is working.
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  continuations   INTEGER NOT NULL DEFAULT 0,
  throttle_count  INTEGER NOT NULL DEFAULT 0,
  -- The Orca terminal this agent runs in, so the daemon can clear its context
  -- and hand it back its own handoff without destroying the agent.
  terminal        TEXT,
  worktree        TEXT
);

-- Section 8.9: handoff progress is persisted per step, so a daemon restart
-- resumes from the step it reached instead of starting over.
CREATE TABLE IF NOT EXISTS handoff (
  id          TEXT PRIMARY KEY,
  agent       TEXT NOT NULL,
  session_id  TEXT,
  trigger_kind TEXT,
  cursor_from INTEGER,
  cursor_to   INTEGER,
  step        TEXT NOT NULL,
  deadline    INTEGER,
  state       TEXT NOT NULL,
  detail      TEXT,
  updated     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS handoff_agent ON handoff(agent, state);
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
    # This runs before the schema: new indexes reference columns an old table
    # may not have yet, and CREATE INDEX would fail before any migration had a
    # chance to act.
    _pre_schema_migrate(con)
    con.executescript(SCHEMA)
    _add_missing_columns(con)
    return con


def _cols(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _pre_schema_migrate(con) -> None:
    """Make an older database safe for the current schema to run against.

    Two kinds of change need to happen here rather than after: a table whose
    shape changed incompatibly, and a column a new index is about to reference.

    Only handoff is dropped. Its rows are transient progress state, so losing
    them is safe - anything in flight is re-derived by the daemon's
    reconciliation pass. Events, requests and cursors are durable and are never
    dropped; they get columns added instead.
    """
    handoff_cols = _cols(con, "handoff")
    if handoff_cols and "step" not in handoff_cols:
        con.execute("DROP TABLE IF EXISTS handoff")

    request_cols = _cols(con, "requests")
    if request_cols and "chain_id" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN chain_id TEXT")
        # Existing rows keep their own id as their chain, which is what a
        # parentless request would have had anyway.
        con.execute("UPDATE requests SET chain_id = CAST(id AS TEXT) WHERE chain_id IS NULL")


def _add_missing_columns(con) -> None:
    state_cols = _cols(con, "agent_state")
    for col in ("failed_attempts", "continuations", "throttle_count"):
        if col not in state_cols:
            con.execute(f"ALTER TABLE agent_state ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    for col in ("terminal", "worktree"):
        if col not in state_cols:
            con.execute(f"ALTER TABLE agent_state ADD COLUMN {col} TEXT")
    # A parked agent may also have had its session closed to give the machine
    # its memory back. closed_at says it happened; resume_session is the
    # session to bring back, which is the whole reason closing is cheap.
    if "closed_at" not in state_cols:
        con.execute("ALTER TABLE agent_state ADD COLUMN closed_at INTEGER")
    if "resume_session" not in state_cols:
        con.execute("ALTER TABLE agent_state ADD COLUMN resume_session TEXT")



# ---------------------------------------------------------------- events
def add_event(con, agent: str, kind: str, payload: dict | None = None,
              account: str | None = None, dedup_key: str | None = None) -> int | None:
    """Append an event. Returns the row id, or None if deduped away."""
    # ON CONFLICT names dedup_key specifically. A blanket IntegrityError catch
    # here would also swallow NOT NULL violations, dropping events with no trace
    # and reporting them as if they had been deduped.
    cur = con.execute(
        "INSERT INTO events(ts, agent, account, kind, payload, dedup_key) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(dedup_key) DO NOTHING",
        (int(time.time()), agent, account, kind,
         json.dumps(payload, ensure_ascii=False) if payload else None, dedup_key),
    )
    return cur.lastrowid if cur.rowcount else None


# Fold's own bookkeeping rows. They are kept for audit but excluded from the
# delta a fold reads, so a fold never ends up summarizing its own bookkeeping
# (and so the cursor cannot chase its own tail).
FOLD_META_KINDS = ("fold", "fold_failed")


def events_after(con, row_id: int, agent: str | None = None, limit: int = 10000,
                 exclude_kinds: tuple[str, ...] = ()):
    clauses = ["id > ?"]
    params: list = [row_id]
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    if exclude_kinds:
        clauses.append(f"kind NOT IN ({','.join('?' * len(exclude_kinds))})")
        params.extend(exclude_kinds)
    params.append(limit)
    q = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?"
    return con.execute(q, params).fetchall()


def latest_events(con, agent: str | None = None, limit: int = 50):
    """The most recent events, returned oldest first so they read downward.

    events_after() is the fold's cursor primitive and starts at the beginning
    on purpose. An operator asking what just happened wants the other end.
    """
    q = "SELECT * FROM events"
    params: list = []
    if agent:
        q += " WHERE agent = ?"
        params.append(agent)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return list(reversed(con.execute(q, params).fetchall()))


def count_events(con, agent: str | None = None, after: int = 0) -> int:
    clauses, params = [], []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    if after:
        clauses.append("id > ?")
        params.append(int(after))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return int(con.execute(f"SELECT COUNT(*) AS c FROM events{where}", params).fetchone()["c"])


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
                verdict: str | None = None, chain_id: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO requests(ts, requester, target, body, files, relation, response, "
        "kind, chain_depth, parent_id, state, verdict, chain_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), requester, target, body, files, relation, response, kind,
         chain_depth, parent_id, state, verdict, chain_id),
    )
    rid = int(cur.lastrowid)
    if chain_id is None:
        # A request with no parent starts its own chain, named after itself.
        con.execute("UPDATE requests SET chain_id = ? WHERE id = ?", (str(rid), rid))
    return rid


def chain_of(con, rid: int) -> list:
    """Every request in the same chain, oldest first."""
    row = get_request(con, rid)
    if row is None:
        return []
    return con.execute(
        "SELECT * FROM requests WHERE chain_id = ? AND state != 'denied' ORDER BY id",
        (row["chain_id"],),
    ).fetchall()


def chain_members(con, chain_id: str) -> list:
    return con.execute(
        "SELECT * FROM requests WHERE chain_id = ? AND state != 'denied' ORDER BY id",
        (chain_id,),
    ).fetchall()


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


def set_terminal(con, agent: str, terminal: str | None, worktree: str | None) -> None:
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, terminal, worktree) VALUES(?,?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET terminal=excluded.terminal, "
        "worktree=excluded.worktree, updated=excluded.updated",
        (agent, "RUNNING", int(time.time()), terminal, worktree),
    )


def set_closed(con, agent: str, session_id: str | None) -> None:
    """Record that this agent's session was closed, and how to get it back."""
    con.execute(
        "UPDATE agent_state SET closed_at = ?, resume_session = ?, updated = ? WHERE agent = ?",
        (int(time.time()), session_id, int(time.time()), agent),
    )


def clear_closed(con, agent: str) -> None:
    con.execute(
        "UPDATE agent_state SET closed_at = NULL, resume_session = NULL, updated = ? "
        "WHERE agent = ?", (int(time.time()), agent),
    )


def last_activity(con, agent: str) -> int | None:
    """When this agent last did anything we can see. Its own hook events are
    the record - the daemon's bookkeeping about an agent is not activity."""
    row = con.execute(
        "SELECT MAX(ts) AS t FROM events WHERE agent = ? AND kind NOT IN "
        "('fold', 'fold_failed', 'handoff', 'park', 'close', 'wake', 'notified')", (agent,)
    ).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


def get_terminal(con, agent: str) -> str | None:
    row = con.execute("SELECT terminal FROM agent_state WHERE agent = ?", (agent,)).fetchone()
    return row["terminal"] if row else None


def bump_counter(con, agent: str, column: str, by: int = 1) -> None:
    if column not in ("failed_attempts", "continuations", "throttle_count"):
        raise ValueError(f"not a counter column: {column}")
    con.execute(
        f"INSERT INTO agent_state(agent, state, updated, {column}) VALUES(?,?,?,?) "
        f"ON CONFLICT(agent) DO UPDATE SET {column} = agent_state.{column} + ?, updated = ?",
        (agent, "IDLE", int(time.time()), by, by, int(time.time())),
    )


def reset_counter(con, agent: str, column: str) -> None:
    if column not in ("failed_attempts", "continuations", "throttle_count"):
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


# Handoff steps, in order (section 8.9).
HANDOFF_STEPS = ("requested", "folded", "extracted", "released")


def start_handoff(con, hid: str, agent: str, session_id: str | None, trigger_kind: str,
                  cursor_from: int, deadline: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO handoff(id, agent, session_id, trigger_kind, cursor_from, "
        "cursor_to, step, deadline, state, detail, updated) "
        "VALUES(?,?,?,?,?,NULL,'requested',?,'in_flight',NULL,?)",
        (hid, agent, session_id, trigger_kind, cursor_from, deadline, int(time.time())),
    )


def advance_handoff(con, hid: str, step: str, cursor_to: int | None = None,
                    state: str | None = None, detail: str | None = None) -> None:
    """Commit one step. Each step is committed so a restart can resume from it."""
    con.execute(
        "UPDATE handoff SET step = ?, cursor_to = COALESCE(?, cursor_to), "
        "state = COALESCE(?, state), detail = COALESCE(?, detail), updated = ? WHERE id = ?",
        (step, cursor_to, state, detail, int(time.time()), hid),
    )


def in_flight_handoffs(con):
    return con.execute(
        "SELECT * FROM handoff WHERE state = 'in_flight' ORDER BY updated"
    ).fetchall()


def in_flight_for(con, agent: str):
    return con.execute(
        "SELECT * FROM handoff WHERE agent = ? AND state = 'in_flight' ORDER BY updated LIMIT 1",
        (agent,),
    ).fetchone()
