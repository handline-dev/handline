"""The schema, `connect`, and the migrations that bring an older database up to it."""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from ..infra import paths

from .cursors import get_cursor, set_cursor


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
  -- When the human pressed "send now" on this (still undelivered) request: it
  -- is told to its target at once, ahead of a head that is already open.
  send_now INTEGER,
  -- Set once "send now" has told it out of turn, so becoming the head later
  -- (T38) is not a second telling. A request column, not a cursor keyed by
  -- id: a cursor for every request ever sent out of turn sat in `cursors`
  -- forever, with nothing to ever clear it.
  told_out_of_turn INTEGER,
  -- `ocp ask --timebox`: seconds after delivery when the asker is told it is
  -- still open. NULL follows config.yaml's timebox_s. The notice is once per
  -- request, so its time is a column, not a cursor left behind for every row.
  timebox_s INTEGER,
  timebox_noticed_at INTEGER,
  -- A report to the human that asks nothing of them: shown, never counted as
  -- waiting on them, and closed once read or superseded by the sender's next.
  fyi INTEGER NOT NULL DEFAULT 0,
  -- #1349: the id of the request that retired this row as moot - always a
  -- request's own id, never a task's (#1366: the two are separate
  -- AUTOINCREMENT sequences that both start at 1, so mixing them in one
  -- column reads as one namespace when it is two). A record, not a
  -- relation. Read from the row that went moot; nothing in routing, chain
  -- depth, the cycle guard or any limit may read it, the same reason
  -- parent_id does not fit that job. Set once, by the verb that already
  -- knows both ids (a replace, or `ocp resolve --retires`).
  moot_by     INTEGER,
  -- Task #70: a dismissal is a state of the row, not words anyone said. Who
  -- dismissed it and the reason they gave (when is resolved_at); the verdict
  -- stays NULL, so no screen can draw a dismissal as the answerer's own text.
  dismissed_by     TEXT,
  dismissed_reason TEXT
);
CREATE INDEX IF NOT EXISTS requests_target ON requests(target, state);
CREATE INDEX IF NOT EXISTS requests_requester ON requests(requester, ts);
CREATE INDEX IF NOT EXISTS requests_chain ON requests(chain_id, id);

-- The task board (request #1259). A task is never a message: it is its own
-- row, named and owned by whoever opened it. The columns below are the whole
-- of it - see TASK_FIELDS. Everything else the board shows (a request's body,
-- state, date, verdict) is read live from `requests` through task_requests.
CREATE TABLE IF NOT EXISTS tasks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  owner        TEXT NOT NULL,
  parent_task  INTEGER REFERENCES tasks(id),
  opened_by    TEXT NOT NULL,
  -- Task #71: a single close used to conflate two different calls - the
  -- builder's evidence that the work is done, and the asker's sign-off that
  -- ends it. They are split. done_evidence is that evidence: a merge sha
  -- (checked against main), the id of a request where the result was
  -- already reported, or a free-text fact for work that leaves no commit.
  -- NULL is "not done yet"; written once, by `ocp task done`.
  done_evidence TEXT,
  -- #1366 should-fix 2, carried over: when done_evidence was written,
  -- stamped alongside it - reason_gaps() needs it to age old closes out of a
  -- measurement of current practice, the same job resolved_at does for
  -- requests.
  done_at      INTEGER,
  -- 'sha', 'request' or 'reason': which of mark_task_done's evidence forms
  -- this was. Set from the caller's actual choice, not guessed back from the
  -- text afterwards - a --reason forced past looks_like_sha (the escape
  -- hatch for a reason that happens to look like hex) would otherwise read
  -- as a sha.
  done_via     TEXT,
  -- Who last edited the owner or title, and when - `ocp task edit`'s
  -- evidence, the same job closed_by/closed_at do for a close. NULL until
  -- the first edit; overwritten by each later one, so this is the latest
  -- edit only - the full history (old value, new value) is in `events`
  -- under kind 'edit_task', one row per edit.
  edited_by    TEXT,
  edited_at    INTEGER,
  created_at   INTEGER NOT NULL,
  -- The asker's own sign-off (`ocp close`), final - see actions.task_asker
  -- for who that is. An identity, not evidence: NULL is "still open"
  -- (whether waiting, doing or done); written once, and only once
  -- done_evidence is set - `close_task` refuses a task that is not done yet.
  -- Ordered last here, after every other column, since the #71 migration
  -- can only ADD these two (SQLite has no "insert column here"). A table
  -- migrated through earlier ALTERs (edited_by/edited_at added before #71
  -- ran) still ends up in a different physical order than a fresh CREATE
  -- TABLE - nothing in ocp reads a task's columns by position, so that's
  -- fine; TASK_FIELDS is compared as a set, never as an order.
  closed_by    TEXT,
  closed_at    INTEGER,
  -- A discard (task #85): the asker throwing the task away before it is
  -- done. It also writes closed_by/closed_at, so every open-task query, block
  -- and guard already treats it as gone; these two say it was thrown away, by
  -- whom and why (closed_at is when) - never signed off as finished work.
  discarded_by  TEXT,
  discard_reason TEXT
);
CREATE TABLE IF NOT EXISTS task_requests (
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  request_id  INTEGER NOT NULL,
  PRIMARY KEY (task_id, request_id)
);
CREATE INDEX IF NOT EXISTS task_requests_request ON task_requests(request_id);

-- task #21/#1884: `task_id` cannot be worked until `blocks_on` closes. A row
-- here, not a column on `tasks`, because a task can be blocked by more than
-- one other and `--unblock` removes exactly one edge. Gone once `blocks_on`
-- closes, in the same transaction that closes it and writes any resulting
-- notice (actions.close_task) - `add_block` also refuses to write a row
-- naming an already-closed task on either end. `task_blockers` still checks
-- `tasks.closed_by` itself rather than trusting a row's mere presence,
-- cheap insurance against either guard missing a case, not the only place
-- that state lives.
CREATE TABLE IF NOT EXISTS task_blocks (
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  blocks_on   INTEGER NOT NULL REFERENCES tasks(id),
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (task_id, blocks_on)
);
CREATE INDEX IF NOT EXISTS task_blocks_on ON task_blocks(blocks_on);

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
  -- Which account the session in that terminal is actually on, as chosen when
  -- the session was made.
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
    if request_cols and "send_now" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN send_now INTEGER")
    if request_cols and "moot_by" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN moot_by INTEGER")
        # No backfill: which existing "replaced by #N" row retired which is
        # already named in the verdict text, and re-parsing that text to fill
        # a column meant to replace parsing it is the mistake this exists to
        # end, not repeat.

    task_cols = _cols(con, "tasks")
    if task_cols and "closed_at" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN closed_at INTEGER")
        # No backfill, same reason as resolved_at above: when an old task was
        # actually closed is not recorded anywhere, so a closed row with none
        # is simply excluded from a windowed count rather than dated by guess.
    # done_evidence is task #71's own sign that this table already went
    # through the split below - once it has, closed_by holds an identity,
    # not evidence, and closed_via is gone for good (renamed to done_via).
    # Without this second half of the guard, a table #71 already migrated
    # reads as "never had closed_via" forever (that column is never coming
    # back), and every later connect() re-added it and rebuilt it from
    # closed_by's new, unrelated contents - a real bug, caught by
    # test_the_done_close_migration_upgrades_an_old_tasks_table's second
    # connect(), not simulated.
    if task_cols and "closed_via" not in task_cols and "done_evidence" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN closed_via TEXT")
        # Best-effort backfill only, from the shape closed_by already holds
        # (actions.looks_like_sha, not reimported here to avoid a cycle - db
        # is imported by actions, not the other way). Exact for an
        # installation that only ever ran main, where every existing close
        # predates the --reason escape hatch this guesses wrong for; on this
        # branch's own history a hex-looking --reason (auditor's #1565 NOTE
        # 1: `ocp close 1 deadbeef --reason`) is mislabeled 'sha'. One line
        # in a counting display, nothing else - a real gap, not a claim this
        # is exact everywhere.
        for r in con.execute(
                "SELECT id, closed_by FROM tasks WHERE closed_by IS NOT NULL").fetchall():
            via = "sha" if re.fullmatch(r"[0-9a-fA-F]{4,40}", r["closed_by"]) else "reason"
            con.execute("UPDATE tasks SET closed_via = ? WHERE id = ?", (via, r["id"]))
    if task_cols and "edited_by" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN edited_by TEXT")
        # No backfill: no existing installation has ever edited a task
        # (the verb did not exist), so every row's history here is empty.
    if task_cols and "edited_at" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN edited_at INTEGER")
    if task_cols and "done_evidence" not in task_cols:
        # Task #71: split the old single-stage close into done (the
        # builder's evidence) and closed (the asker's sign-off). The old
        # evidence columns become the done stage under their new names, and
        # closed_by/closed_at start meaning what they now say - an identity
        # and when, never evidence - so every existing closed task reads as
        # done-and-closed by whoever the evidence names, which is the only
        # identity this migration has to offer.
        #
        # Auditor's #2187 SHOULD-FIX 3: five separate autocommit statements,
        # with no lock between them, left this migration strandable - a kill
        # after the first RENAME, or another `connect()` (every ocp command,
        # and the daemon) landing mid-way, would see done_evidence already
        # present and skip the rest for good, leaving done_at/done_via
        # missing and closed_at still holding the old times. BEGIN IMMEDIATE
        # around the whole block makes it one commit or none - SQLite DDL is
        # transactional - and re-reading _cols after the lock is held
        # catches the case this connection's own `task_cols` (read before
        # any lock) went stale: another connection finished the migration in
        # between.
        con.execute("BEGIN IMMEDIATE")
        try:
            if "done_evidence" not in _cols(con, "tasks"):
                con.execute("ALTER TABLE tasks RENAME COLUMN closed_by TO done_evidence")
                con.execute("ALTER TABLE tasks RENAME COLUMN closed_at TO done_at")
                con.execute("ALTER TABLE tasks RENAME COLUMN closed_via TO done_via")
                con.execute("ALTER TABLE tasks ADD COLUMN closed_by TEXT")
                con.execute("ALTER TABLE tasks ADD COLUMN closed_at INTEGER")
                # Best-effort backfill: closed_by should be an identity, and
                # all this migration has for one is the evidence text itself
                # - wrong for a sha close, but the field is otherwise NULL
                # (reading as "still open"), which is worse for every task
                # this rule was written for.
                con.execute(
                    "UPDATE tasks SET closed_by = done_evidence, closed_at = done_at "
                    "WHERE done_evidence IS NOT NULL")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


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
    from ..team import ask
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
    from ..team import ask
    from ..infra import paths

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


def _add_missing_columns(con) -> None:
    request_cols = _cols(con, "requests")
    for col in ("dismissed_by", "dismissed_reason"):
        if col not in request_cols:
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
    for col in ("timebox_s", "timebox_noticed_at"):
        if col not in request_cols:
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} INTEGER")
    if "told_out_of_turn" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN told_out_of_turn INTEGER")
        # Carry over what the old sendnow_told:<id> cursors already knew - a
        # request left NULL here that was in fact already told out of turn
        # would be told a second time once it becomes the head.
        con.execute(
            "UPDATE requests SET told_out_of_turn = 1 WHERE id IN "
            "(SELECT CAST(substr(name, 14) AS INTEGER) FROM cursors "
            "WHERE name LIKE 'sendnow_told:%')")
        con.execute("DELETE FROM cursors WHERE name LIKE 'sendnow_told:%'")
    task_cols = _cols(con, "tasks")
    for col in ("discarded_by", "discard_reason"):
        if col not in task_cols:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
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
