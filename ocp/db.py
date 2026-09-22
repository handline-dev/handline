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
  verdict     TEXT,
  -- When the requester was told this came back. Per row, because answering is
  -- not monotonic in id: a high-water mark loses the answer that arrives late.
  announced_at INTEGER,
  -- When the answer landed. `ts` is when the request was made, and the two can
  -- be two days apart: a screen that sorts answers by `ts` puts yesterday's
  -- reply above this morning's, which reads as an old message nobody answered.
  resolved_at INTEGER,
  -- FIFO position within one target's queue - defaults to id, but `--first`
  -- bisects between two existing orders, so this is REAL, not INTEGER.
  queue_order REAL,
  -- Set once, the first time this request is actually shown to its target
  -- (request #189). NULL is what makes a request still withdrawable/
  -- replaceable by its own requester.
  delivered_at INTEGER,
  -- A report to the human that asks nothing of them: shown, never counted as
  -- waiting on them, and closed once read or superseded by the sender's next.
  fyi INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS requests_target ON requests(target, state);
CREATE INDEX IF NOT EXISTS requests_requester ON requests(requester, ts);
CREATE INDEX IF NOT EXISTS requests_chain ON requests(chain_id, id);

-- Advanced only after the fold output validates.
-- Small named values that are not counters: the orchestration Run this team's
-- work hangs under, and anything else that is one string per installation.
CREATE TABLE IF NOT EXISTS meta (
  name    TEXT PRIMARY KEY,
  value   TEXT,
  updated INTEGER NOT NULL
);

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
  -- Launches that started nothing. Its own column because the two readers of
  -- failed_attempts - the tick's exception handler and the handoff - would
  -- otherwise stall an agent on a streak neither of them saw.
  wake_failures   INTEGER NOT NULL DEFAULT 0,
  -- The Orca terminal this agent runs in, so the daemon can clear its context
  -- and hand it back its own handoff without destroying the agent.
  terminal        TEXT,
  worktree        TEXT,
  -- Which account the session in that terminal is actually on. agent.yaml says
  -- which one it prefers; this says where it went, and they differ whenever
  -- the preferred one had no room when the session was made.
  account         TEXT
);

-- Section 8.9: handoff progress is persisted per step, so a daemon restart
-- resumes from the step it reached instead of starting over.
CREATE TABLE IF NOT EXISTS handoff (
  id          TEXT PRIMARY KEY,
  agent       TEXT NOT NULL,
  session_id  TEXT,
  trigger_kind TEXT,
  step        TEXT NOT NULL,
  deadline    INTEGER,
  state       TEXT NOT NULL,
  detail      TEXT,
  updated     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS handoff_agent ON handoff(agent, state);

-- One row per agent while ocp has work for it and cannot give it: the daemon
-- writes it, and the bridge, `ocp ps` and `ocp doctor` - other processes - read
-- it. Gone the tick the agent can be given its work again.
CREATE TABLE IF NOT EXISTS delivery_blocks (
  agent      TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  detail     TEXT NOT NULL,
  work       TEXT NOT NULL,
  since      INTEGER NOT NULL,
  -- When it goes on the founder's list; the escalation is raised from `since`.
  show_at    INTEGER NOT NULL,
  seen       INTEGER NOT NULL,
  -- The request raised for this episode, so it is raised once and closed once.
  request_id INTEGER,
  -- 'tick' ends with the work; 'close' - ocp's own /exit raised the dialog -
  -- stands for as long as the dialog does.
  origin     TEXT NOT NULL DEFAULT 'tick',
  -- The dialog's own title, raw from orca.Blocker (parse_dialog), for a MODAL
  -- block - never reconstructed from `detail`, which is a rendering a title
  -- with a quote in it or over DETAIL_CHARS long does not round-trip through.
  -- Empty for anything but MODAL.
  title      TEXT NOT NULL DEFAULT ''
);

-- What ocp did about a dialog on an agent's screen, one row per attempt or per
-- decision not to: the record a returning dialog is counted from, and what a
-- request says when it stops pressing. Never rewritten.
CREATE TABLE IF NOT EXISTS dialog_presses (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  agent   TEXT NOT NULL,
  at      INTEGER NOT NULL,
  title   TEXT NOT NULL,
  -- 'cleared' | 'still-open' | 'not-pressed'
  outcome TEXT NOT NULL,
  note    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS dialog_presses_agent ON dialog_presses(agent, at);

-- One row per account per tick that observe_burn already reads a status for -
-- no read this table exists for is ever driven on its own account (#1113).
-- Every usage column is nullable and none is ever coerced to 0: a figure
-- nobody could trust when the sample was taken is NULL here, same as it is
-- on AccountStatus, so a replay reasons about it the same way pick_account
-- does. A gap in `ts` - the daemon down, or a sleeping machine - is a gap:
-- nothing backfills it.
CREATE TABLE IF NOT EXISTS usage_samples (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  account          TEXT    NOT NULL,
  ts               INTEGER NOT NULL,
  exists_          INTEGER NOT NULL,
  error            TEXT,
  pct_5h           INTEGER,
  pct_5h_readable  INTEGER NOT NULL DEFAULT 1,
  reset_5h         INTEGER,
  rl_at            INTEGER,
  rl_carried       INTEGER NOT NULL DEFAULT 0,
  rl_lost          INTEGER NOT NULL DEFAULT 0,
  pct_7d           INTEGER,
  reset_7d         INTEGER,
  rl7_at           INTEGER,
  rl7_carried      INTEGER NOT NULL DEFAULT 0,
  rl7_lost         INTEGER NOT NULL DEFAULT 0,
  collected_at     INTEGER
);
-- (account, ts) serves a read of one account; the prune and a `since` read
-- filter on ts alone, which that index cannot serve (account leads it).
CREATE INDEX IF NOT EXISTS usage_samples_account_ts ON usage_samples(account, ts);
CREATE INDEX IF NOT EXISTS usage_samples_ts ON usage_samples(ts);
-- One row per API call, from the transcripts (tokenlog.py): what the plan
-- charges for, beside what usage_samples says it charged. Not read by any pick.
CREATE TABLE IF NOT EXISTS token_calls (
  account   TEXT    NOT NULL,
  session   TEXT    NOT NULL,
  req       TEXT    NOT NULL,
  ts        INTEGER NOT NULL,
  model     TEXT    NOT NULL,
  cwd       TEXT    NOT NULL,
  side      INTEGER NOT NULL DEFAULT 0,
  inp       INTEGER NOT NULL,
  cc        INTEGER NOT NULL,
  cr        INTEGER NOT NULL,
  out       INTEGER NOT NULL,
  thinking  INTEGER NOT NULL,
  PRIMARY KEY (account, session, req)
);
CREATE INDEX IF NOT EXISTS token_calls_account_ts ON token_calls(account, ts);
CREATE INDEX IF NOT EXISTS token_calls_ts ON token_calls(ts);
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
    _unhide_the_humans_answers(con)
    _rename_orchestrator(con)
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
    if request_cols and "fyi" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN fyi INTEGER NOT NULL DEFAULT 0")
    if request_cols and "announced_at" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN announced_at INTEGER")
        # Everything already resolved was resolved before anything announced
        # answers at all; announcing it now would type a wall of old verdicts
        # at every agent the first time this runs.
        con.execute("UPDATE requests SET announced_at = ? "
                    "WHERE state IN ('done', 'rejected', 'denied')", (int(time.time()),))
    if request_cols and "resolved_at" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN resolved_at INTEGER")
        # No backfill: when an old row was answered is not recorded anywhere,
        # and inventing a time would be a figure with nothing behind it. A row
        # with none falls back to `ts`, which is what it had before.
    if request_cols and "chain_id" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN chain_id TEXT")
        # Existing rows keep their own id as their chain, which is what a
        # parentless request would have had anyway.
        con.execute("UPDATE requests SET chain_id = CAST(id AS TEXT) WHERE chain_id IS NULL")
    if request_cols and "queue_order" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN queue_order REAL")
        # id order is exactly what every existing queue already reads as FIFO,
        # so an old row's place in line does not move the first time this runs.
        con.execute("UPDATE requests SET queue_order = id WHERE queue_order IS NULL")
    if request_cols and "delivered_at" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN delivered_at INTEGER")
        # Only each target's own head (queue_order was just backfilled to id,
        # above, so the head is its smallest id). The old delivery did show a
        # target every queued row, not just this one, so a row past the head
        # may already have been read; marking it delivered anyway would leave
        # --first nothing left to land in front of, which matters more.
        con.execute("""
            UPDATE requests SET delivered_at = ts WHERE state = 'queued' AND id IN (
                SELECT MIN(id) FROM requests WHERE state = 'queued' GROUP BY target
            )
        """)


def _unhide_the_humans_answers(con) -> None:
    """Undo the announced_at backfill for the one reader it was never about.

    That backfill exists so a first run does not type a wall of old verdicts
    at an agent's terminal. The human has no terminal to be typed at - their
    answers are a list they read and dismiss - so on their rows it only marked
    every answer they had ever been sent as already told, before there was
    anywhere to tell them.

    Ledgered in `cursors` rather than keyed to a column that already exists:
    the column arrived two commits before the mailbox that reads it, so there
    is no schema change left to hang this on. It runs once.

    The name is imported here and not held in this module: `ask.HUMAN` is the
    one copy, and a second would go stale the day it changed.
    """
    if get_cursor(con, "migrated:human_answers"):
        return
    from . import ask
    con.execute("UPDATE requests SET announced_at = NULL WHERE requester = ? "
                "AND state IN ('done', 'rejected')", (ask.HUMAN,))
    set_cursor(con, "migrated:human_answers", 1)


# What the founder used to be called. "orchestrator" read as a component of
# this system rather than as the person using it, which is the one distinction
# the routing table exists to make.
OLD_HUMAN = "orchestrator"


def _rename_orchestrator(con) -> None:
    """Rename the founder in every place the old name was written down.

    A rename that only changes the constant leaves the rows, the queue files
    and the routing table addressed to somebody who no longer exists: the
    mailbox empties, every escalation becomes undeliverable, and the reason is
    a word in three files. Once, ledgered like the other migrations.

    team.yaml is edited as text rather than parsed and re-dumped. It is a
    hand-written policy file full of comments explaining why each edge exists,
    and a round-trip through the loader would throw all of that away.
    """
    if get_cursor(con, "migrated:human_name"):
        return
    from . import ask, paths

    new = ask.HUMAN
    if new != OLD_HUMAN:
        con.execute("UPDATE requests SET requester = ? WHERE requester = ?", (new, OLD_HUMAN))
        con.execute("UPDATE requests SET target = ? WHERE target = ?", (new, OLD_HUMAN))
        con.execute("UPDATE cursors SET name = REPLACE(name, ?, ?) WHERE name LIKE ?",
                    (OLD_HUMAN, new, f"%{OLD_HUMAN}%"))
        old_dir = paths.agent_dir(OLD_HUMAN)
        if old_dir.is_dir():
            dest = paths.agent_dir(new)
            if dest.exists():
                for f in (old_dir / "inbox").glob("*.json"):
                    (dest / "inbox").mkdir(parents=True, exist_ok=True)
                    f.replace(dest / "inbox" / f.name)
            else:
                old_dir.rename(dest)
        t = paths.team_file()
        if t.exists() and OLD_HUMAN in t.read_text():
            t.write_text(t.read_text().replace(OLD_HUMAN, new))
    set_cursor(con, "migrated:human_name", 1)


def unannounced_answers(con, agent: str) -> list:
    """What this agent asked, that came back, that it has not been told about.

    Marked per row rather than tracked with a cursor: a cursor works for
    arrival, which happens in id order by construction, and not for answering,
    which does not. Two answers out of order and the older one is behind the
    high-water mark forever - which is the failure this whole path exists to
    prevent, one layer down.
    """
    return con.execute(
        "SELECT id, ts, resolved_at, target, kind, state, verdict, body, parent_id, "
        "chain_id FROM requests WHERE requester = ? "
        "AND state IN ('done', 'rejected') AND announced_at IS NULL ORDER BY id",
        (agent,),
    ).fetchall()


def mark_announced(con, ids: list[int]) -> None:
    now = int(time.time())
    con.executemany("UPDATE requests SET announced_at = ? WHERE id = ?",
                    [(now, int(i)) for i in ids])


def _add_missing_columns(con) -> None:
    state_cols = _cols(con, "agent_state")
    for col in ("failed_attempts", "continuations", "throttle_count", "wake_failures"):
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
    if "task" not in state_cols:
        # The orchestration task this agent's terminal is bound to, so Orca
        # draws it as a worker under its supervisor rather than as one more
        # anonymous tab.
        con.execute("ALTER TABLE agent_state ADD COLUMN task TEXT")
    if "account" not in state_cols:
        # No backfill: an existing row's session was launched on the account
        # agent.yaml named, which is exactly what a null here falls back to.
        con.execute("ALTER TABLE agent_state ADD COLUMN account TEXT")
    if "handoff" not in state_cols:
        # What the agent is handed when it comes back from a park or a move;
        # see handoff.py. NULL once it has been handed over.
        con.execute("ALTER TABLE agent_state ADD COLUMN handoff TEXT")
    if "account_source" not in state_cols:
        # NULL here means "recorded" too, for exactly the rows that predate
        # this column: every one of them was written by `_record_terminal` or
        # a move, both facts, before an "assumed" writer existed at all.
        con.execute("ALTER TABLE agent_state ADD COLUMN account_source TEXT")
    if "launch_fingerprint" not in state_cols:
        # No backfill: an existing row reads as drifted, which is right.
        con.execute("ALTER TABLE agent_state ADD COLUMN launch_fingerprint TEXT")
    if "notified_session" not in state_cols:
        # The session the notify cursor's head was typed into. A delivery
        # belongs to the session that read it, not to the agent.
        con.execute("ALTER TABLE agent_state ADD COLUMN notified_session TEXT")



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
def get_meta(con, name: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else None


def set_meta(con, name: str, value: str | None) -> None:
    con.execute(
        "INSERT INTO meta(name, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (name, value, int(time.time())),
    )


def set_task(con, agent: str, task: str | None) -> None:
    con.execute(
        "INSERT INTO agent_state(agent, state, updated, task) VALUES(?,?,?,?) "
        "ON CONFLICT(agent) DO UPDATE SET task=excluded.task, updated=excluded.updated",
        (agent, "IDLE", int(time.time()), task),
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


# ---------------------------------------------------------------- requests
def add_request(con, requester: str, target: str, body: str, files: str | None,
                relation: str | None, response: str | None, kind: str | None,
                chain_depth: int, parent_id: int | None, state: str = "queued",
                verdict: str | None = None, chain_id: str | None = None,
                first: bool = False, queue_order: float | None = None,
                fyi: bool = False) -> int:
    """Insert a request. `queue_order`, not `id`, decides FIFO position - see
    `queued_for`. `first` and `queue_order` are request #189's --first and
    --replaces.
    """
    cur = con.execute(
        "INSERT INTO requests(ts, requester, target, body, files, relation, response, "
        "kind, chain_depth, parent_id, state, verdict, chain_id, fyi) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), requester, target, body, files, relation, response, kind,
         chain_depth, parent_id, state, verdict, chain_id, int(fyi)),
    )
    rid = int(cur.lastrowid)
    if chain_id is None:
        # A request with no parent starts its own chain, named after itself.
        con.execute("UPDATE requests SET chain_id = ? WHERE id = ?", (str(rid), rid))
    order = rid if queue_order is None else queue_order
    if first and state == "queued":
        # Midpoint between the last delivered row and the first undelivered
        # one; queue_order is REAL so there is always room to bisect into.
        row = con.execute(
            "SELECT MIN(CASE WHEN delivered_at IS NULL THEN queue_order END) AS ceiling, "
            "MAX(CASE WHEN delivered_at IS NOT NULL THEN queue_order END) AS floor_ "
            "FROM requests WHERE target = ? AND state = 'queued'", (target,)).fetchone()
        ceiling, floor_ = row["ceiling"], row["floor_"]
        if ceiling is not None and floor_ is not None:
            order = (float(ceiling) + float(floor_)) / 2
        elif ceiling is not None:
            order = float(ceiling) - 1
        elif floor_ is not None:
            order = float(floor_) + 1
    con.execute("UPDATE requests SET queue_order = ? WHERE id = ?", (order, rid))
    return rid


# States a request can leave 'queued' for without ever having reached its
# target: denied by routing, withdrawn by its own requester, or superseded by
# a replacement. None of the three is evidence of a real exchange, so a cycle
# or ping-pong check counting them would refuse a conversation that, from the
# target's side, never happened.
NEVER_REACHED_TARGET = ("denied", "withdrawn", "replaced")


def chain_members(con, chain_id: str, exclude_id: int | None = None) -> list:
    """`exclude_id` is a row still 'queued' that is about to be replaced or
    withdrawn: the caller already knows it, and a cycle or ping-pong check
    counting it before that write lands would count a conversation that, from
    the target's side, never happened.
    """
    clauses = [f"state NOT IN ({','.join('?' * len(NEVER_REACHED_TARGET))})"]
    params: list = [chain_id, *NEVER_REACHED_TARGET]
    if exclude_id is not None:
        clauses.append("id != ?")
        params.append(exclude_id)
    return con.execute(
        f"SELECT * FROM requests WHERE chain_id = ? AND {' AND '.join(clauses)} ORDER BY id",
        params,
    ).fetchall()


def queued_for(con, target: str):
    """Every request still queued for `target`, ordered by queue_order (its
    delivery position - see `add_request`), not by id."""
    return con.execute(
        "SELECT * FROM requests WHERE target = ? AND state = 'queued' "
        "ORDER BY queue_order, id",
        (target,),
    ).fetchall()


def head_of_queue(con, target: str):
    """The one request `target` is meant to be working on right now, or None.

    Request #189: an agent is given one request at a time, not its whole
    queue - this is where that is decided, once, for every reader (delivery,
    `ocp inbox`, resolve's hand-off to the next one) to agree on.
    """
    rows = queued_for(con, target)
    return rows[0] if rows else None


def queue_depth(con, target: str) -> int:
    return len(queued_for(con, target))


def open_asks_of(con, requester: str) -> int:
    """What this agent asked that nobody has answered yet: what it may be
    waiting on, which is not the same as having forgotten something."""
    return con.execute("SELECT COUNT(*) FROM requests WHERE requester = ? "
                       "AND state = 'queued'", (requester,)).fetchone()[0]


def mark_delivered(con, rid: int) -> bool:
    """Record that this request has been shown to its target. Idempotent.

    False if the row was withdrawn or replaced first - the caller must not
    then act as if delivery happened.
    """
    cur = con.execute(
        "UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) "
        "WHERE id = ? AND state = 'queued'",
        (int(time.time()), rid))
    return cur.rowcount > 0


def retire_queued(con, rid: int, state: str, verdict: str | None = None) -> bool:
    """Take a still-queued request out of the queue, delivered or not.

    For rows nobody can act on once a newer one exists - the human's, where
    "delivered" only means it was drawn on a screen. Announced in the same
    write: the sender retired its own row and is not told it was answered.
    """
    now = int(time.time())
    cur = con.execute(
        "UPDATE requests SET state = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?), announced_at = COALESCE(announced_at, ?) "
        "WHERE id = ? AND state = 'queued'", (state, verdict, now, now, rid))
    return cur.rowcount > 0


def close_if_queued(con, rid: int, state: str, verdict: str, announced: bool = False) -> bool:
    """Close a request that is still open, atomically. True only if this call
    was the one that closed it: an agent resolving it in the same instant wins,
    and its answer is not overwritten. `announced=True` is `resolve`'s
    `notify=False` case: stamped in this same write, for the reason
    `set_request_state` gives."""
    now = int(time.time())
    cur = con.execute(
        "UPDATE requests SET state = ?, verdict = ?, resolved_at = ?, "
        "announced_at = COALESCE(announced_at, ?) WHERE id = ? AND state = 'queued'",
        (state, verdict, now, now if announced else None, rid))
    return cur.rowcount > 0


def retire_if_undelivered(con, rid: int, state: str, verdict: str | None = None) -> bool:
    """Move a still-queued, undelivered request to a terminal state, atomically.

    True only if this call is the one that moved it. A plain read-then-write
    (check delivered_at, then update) races a delivery landing in between;
    this closes that window in one statement.
    """
    cur = con.execute(
        "UPDATE requests SET state = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?) "
        "WHERE id = ? AND state = 'queued' AND delivered_at IS NULL",
        (state, verdict, int(time.time()), rid))
    return cur.rowcount > 0


def get_request(con, rid: int):
    return con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()


def set_request_state(con, rid: int, state: str, verdict: str | None = None,
                      announced: bool = False) -> None:
    # `resolved_at` is stamped on the way out of 'queued' and never rewritten:
    # re-resolving an answered request does not make the answer newer.
    #
    # `announced` stamps announced_at in this same UPDATE rather than in a
    # follow-up call on another connection - a row that is briefly 'done'
    # with announced_at still NULL is exactly what unannounced_answers()
    # looks for, and a sweep landing in that window announced it anyway.
    now = int(time.time())
    con.execute(
        "UPDATE requests SET state = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?), "
        "announced_at = COALESCE(announced_at, ?) WHERE id = ?",
        (state, verdict, now if state != "queued" else None,
         now if announced else None, rid))


def oldest_counted_ask(con, requester: str, since_ts: int) -> int | None:
    """When the ask that has to leave the window before a slot frees was sent.

    The same rule as `asks_since`, so the two cannot disagree about which asks
    are in the window.
    """
    row = con.execute(
        "SELECT ts FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied' "
        "ORDER BY ts LIMIT 1", (requester, since_ts),
    ).fetchone()
    return int(row["ts"]) if row else None


def standing_denial(con, requester: str, target: str, body: str, files: str | None,
                    since_ts: int) -> int | None:
    """The id of an identical ask already refused inside the window, if any.

    Identical includes the attachment: the same words with another file is a
    different message, and reusing the first row would drop the file silently.
    """
    row = con.execute(
        "SELECT id FROM requests WHERE requester = ? AND target = ? AND body = ? "
        "AND files IS ? AND state = 'denied' AND ts >= ? ORDER BY id DESC LIMIT 1",
        (requester, target, body, files, since_ts),
    ).fetchone()
    return int(row["id"]) if row else None


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
# ask/cancel/commit/escalate/report/resolve/withdraw are commands the agent
# itself ran. Of the hook events ocp captures (ocp/events.py CAPTURED),
# UserPromptSubmit, Stop and StopFailure are a live turn happening or ending;
# SubagentStop and PostCompact only ever fire mid a live session, never from
# anything ocp can trigger on its own; PermissionRequest and TeammateIdle both
# mean the agent's own turn ran into something needing a human, same as
# events.py's TURN_BOUNDARIES comment gives for leaving them out of
# QUIET_EVENTS. SessionStart and SessionEnd are the two left out: ocp's own
# resume fires the first and ocp's own `/exit` fires the second, so both can
# happen with the agent having done nothing - the exact reasoning events.py
# gives for leaving SessionStart out of ACTIVE_EVENTS, applied here to the
# clock it never reached before.
AGENT_ACTIVITY_KINDS = frozenset({
    "ask", "cancel", "commit", "escalate", "report", "resolve", "withdraw",
    "UserPromptSubmit", "Stop", "StopFailure", "SubagentStop", "PostCompact",
    "PermissionRequest", "TeammateIdle",
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


def get_terminal(con, agent: str) -> str | None:
    row = con.execute("SELECT terminal FROM agent_state WHERE agent = ?", (agent,)).fetchone()
    return row["terminal"] if row else None


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


# ---------------------------------------------------------------- usage_samples

# At least a week, per #1113, plus enough margin that a slow week - a founder
# looking at "the last week" a few days after asking - still finds it there.
USAGE_SAMPLE_RETENTION_DAYS = 14


def record_usage_sample(con, account: str, st, ts: int | None = None) -> None:
    """One row of what `st` (an `AccountStatus`) actually held, at `ts` (now,
    by default). Every usage figure is written exactly as `st` has it -
    `None` stays `None`, never 0 - so a reader reconstructs the same
    AccountStatus this was taken from, not a guess at what it meant."""
    con.execute(
        "INSERT INTO usage_samples(account, ts, exists_, error, pct_5h, "
        "pct_5h_readable, reset_5h, rl_at, rl_carried, rl_lost, pct_7d, "
        "reset_7d, rl7_at, rl7_carried, rl7_lost, collected_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (account, ts or int(time.time()), int(bool(st.exists)), st.error,
         st.pct_5h, int(bool(st.pct_5h_readable)), st.reset_5h, st.rl_at,
         int(bool(st.rl_carried)), int(bool(st.rl_lost)), st.pct_7d, st.reset_7d,
         st.rl7_at, int(bool(st.rl7_carried)), int(bool(st.rl7_lost)), st.collected_at),
    )


def prune_usage_samples(con, before_ts: int | None = None) -> int:
    """Delete samples older than the retention window (or `before_ts`
    directly, for a test). Returns the number of rows removed. Filters on
    `ts` alone, so it rides `usage_samples_ts`, not the (account, ts) index."""
    cutoff = before_ts if before_ts is not None else (
        int(time.time()) - USAGE_SAMPLE_RETENTION_DAYS * 86400)
    cur = con.execute("DELETE FROM usage_samples WHERE ts < ?", (cutoff,))
    return cur.rowcount


def usage_samples(con, account: str | None = None, since: int | None = None) -> list[sqlite3.Row]:
    """Samples in `ts` order, oldest first - the shape a replay walks
    forward through. `account` and `since` narrow it; neither is required,
    because the replay this exists for is asked of a question nobody
    planned for in advance."""
    q = "SELECT * FROM usage_samples"
    where, args = [], []
    if account is not None:
        where.append("account = ?")
        args.append(account)
    if since is not None:
        where.append("ts >= ?")
        args.append(since)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY ts"
    return con.execute(q, args).fetchall()
