"""The schema, `connect`, and the migrations that bring an older database up to it."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

from ..infra import paths, service

from .cursors import get_cursor, set_cursor


# The task an event names, as the index and every lookup by it spell it.
EVENT_TASK = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END"
# The request an event names, likewise (a dismissal, a close made under its target).
EVENT_REQUEST = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.request_id') END"

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
-- The task pages ask "which events of this kind belong to task N" once per task
-- (`_begun`, `task_handoffs`, the reset and release marks); without this each
-- is a scan of the whole table. The queries spell the expression exactly so
-- (`db.EVENT_TASK`); the json_valid guard is so that a payload that is not JSON
-- is a row the index skips, not an insert or a build that fails.
CREATE INDEX IF NOT EXISTS events_kind_task ON events(kind, CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END);
CREATE INDEX IF NOT EXISTS events_kind_request ON events(kind, CASE WHEN json_valid(payload) THEN json_extract(payload, '$.request_id') END);

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
  -- FIFO position within one target's queue - starts as id, and promoting a
  -- row moves it to a smaller value, so this is REAL, not INTEGER.
  queue_order REAL,
  -- Set once, the first time this request is actually shown to its target
  -- (request #189). NULL is what makes a request still withdrawable/
  -- replaceable by its own requester.
  delivered_at INTEGER,
  -- T418: `delivered_at` is the daemon's own mark that it went to type this, set
  -- before the send. What the receiving session actually got is these: when the
  -- send succeeded and into which session, when its transcript first showed the
  -- line (only then is it delivered), and how many times it has been typed again
  -- because the transcript never showed it.
  typed_at INTEGER,
  typed_session TEXT,
  landed_at INTEGER,
  -- When the human pressed "send now" on this (still undelivered) request: it
  -- is told to its target at once, ahead of a head that is already open.
  send_now INTEGER,
  -- T949: 1 on a notice (`add_request(notice=True)`): only information, carried by the target's next
  -- typed delivery. Not set on the task-board says that came before it.
  notice INTEGER,
  -- T781: when the founder read it. Used only where the target is the founder: a say to him is
  -- born landed and NULL here is "unread" in For you.
  read_at INTEGER,
  -- T797: when the request entered its state (queued: when it was made).
  state_at INTEGER,
  -- #1349: the id of the request that retired this row as moot - always a
  -- request's own id, never a task's (#1366: the two are separate
  -- AUTOINCREMENT sequences that both start at 1, so mixing them in one
  -- column reads as one namespace when it is two). A record, not a
  -- relation. Read from the row that went moot; nothing in routing, chain
  -- depth, the cycle guard or any limit may read it, the same reason
  -- parent_id does not fit that job. Set once, by the verb that already
  -- knows both ids (a replace).
  moot_by     INTEGER,
  -- T202: 1 once the request's edge (relation, response, kind) has been checked
  -- against the sealed routing table by a process that can read the seal. An
  -- agent's own `ask` cannot, so it leaves this NULL and the daemon sets it;
  -- nothing hands a request out (`head_of_queue`) before then.
  edge_ok INTEGER
);
CREATE INDEX IF NOT EXISTS requests_target ON requests(target, state);
CREATE INDEX IF NOT EXISTS requests_requester ON requests(requester, ts);
CREATE INDEX IF NOT EXISTS requests_chain ON requests(chain_id, id);
CREATE INDEX IF NOT EXISTS requests_parent ON requests(parent_id);

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
  -- The builder's evidence that the work is done, and final (T568: the
  -- asker's separate sign-off is gone; a wrong result is reopened): a merge
  -- sha (checked against main), the id of a request where the result was
  -- already reported, or a free-text fact for work that leaves no commit.
  -- NULL is "not done yet"; written by `hl task done`.
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
  -- Who last edited the owner or title, and when - `hl task edit`'s
  -- evidence, the same job closed_by/closed_at do for a close. NULL until
  -- the first edit; overwritten by each later one, so this is the latest
  -- edit only - the full history (old value, new value) is in `events`
  -- under kind 'edit_task', one row per edit.
  edited_by    TEXT,
  edited_at    INTEGER,
  created_at   INTEGER NOT NULL,
  -- Unused since T568 dropped the close step: a done task is final. Kept
  -- because older databases carry them and earlier migrations still read
  -- them; only migrations read them, and nothing writes them.
  closed_by    TEXT,
  closed_at    INTEGER,
  -- A discard (task #85): the asker throwing the task away before it is
  -- done - by whom, why and when. Never signed off as finished work. A task
  -- merged into another is a discard whose reason is 'merged into T<n>'.
  discarded_by  TEXT,
  discard_reason TEXT,
  discarded_at  INTEGER,
  -- Task T68: 1 (P1, high), 2 (P2, the default) or 3 (P3, low). Lists sort
  -- by it, then by id.
  priority     INTEGER NOT NULL DEFAULT 2,
  -- The task this one was merged into (T568: it is also a discard, reason
  -- 'merged into T<n>'); kept so the page can link to it.
  merged_into  INTEGER REFERENCES tasks(id),
  -- Task T82: NULL is a draft (recorded, not approved to run); set, it is
  -- queued - approved, and the manager pulls it to its owner. `doing` is not
  -- stored: it is read live from a request out against the task.
  queued_by    TEXT,
  queued_at    INTEGER,
  -- T286: timeout_s is the length a doing task may run before its opener is told;
  -- timeout_at the next deadline (NULL while it is not doing). See daemon/timebox.py.
  timeout_s    INTEGER,
  timeout_at   INTEGER,
  -- T720: when its owner's session first had the task (the request that delivers it landed; a
  -- founder's card, when it was drawn). Internal - the clock starts here - and never a state a
  -- screen shows. NULL again when the owner changes.
  delivered_at INTEGER,
  -- T737: two different facts, easy to confuse. `sent_at` is "the daemon gave this task to its
  -- owner": set the moment a delivery is out on it, so queued turns doing there (founder #8035) -
  -- never a screen state of its own. `delivered_at` is "the owner's session actually had it", the
  -- clock's start. A task can be sent and not yet delivered, never the reverse.
  sent_at      INTEGER,
  -- who the task answers to - told when it ends, and may reassign it. NULL is its opener
  -- (`task_reporter`): a process on older code opens tasks without it.
  reporter     TEXT
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
-- ends by discard, in the same transaction that discards it and writes any
-- resulting notice (actions.discard_task) - `add_block` also refuses to write
-- a row naming an already-ended task on either end. `task_blockers` still
-- checks the task's own state rather than trusting a row's mere presence,
-- cheap insurance against either guard missing a case, not the only place
-- that state lives.
CREATE TABLE IF NOT EXISTS task_blocks (
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  blocks_on   INTEGER NOT NULL REFERENCES tasks(id),
  created_at  INTEGER NOT NULL,
  dormant     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (task_id, blocks_on)
);
CREATE INDEX IF NOT EXISTS task_blocks_on ON task_blocks(blocks_on);

-- T493: the founder's memos on a task: text kept beside it, added, edited and deleted in
-- place. Not a message - nothing is sent or delivered, and `edited_at` is NULL until changed.
CREATE TABLE IF NOT EXISTS task_memos (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  author      TEXT NOT NULL,
  body        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  edited_at   INTEGER
);
CREATE INDEX IF NOT EXISTS task_memos_task ON task_memos(task_id, id);

-- Advanced only after the fold output validates.
-- Small named values that are not counters: the orchestration Run this team's
-- work hangs under, and anything else that is one string per installation.
CREATE TABLE IF NOT EXISTS meta (
  name    TEXT PRIMARY KEY,
  value   TEXT,
  updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lands (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  sha       TEXT NOT NULL,
  message   TEXT NOT NULL,
  tree      TEXT NOT NULL,
  requester TEXT NOT NULL,
  state     TEXT NOT NULL,
  merged    TEXT,
  output    TEXT,
  ts        INTEGER NOT NULL,
  done_at   INTEGER
);

CREATE TABLE IF NOT EXISTS land_passes (
  sha TEXT PRIMARY KEY,
  by  TEXT NOT NULL,
  ts  INTEGER NOT NULL
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
-- writes it, and the bridge, `hl ps` and `hl doctor` - other processes - read
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
  -- The dialog's own title, raw from term.Blocker (parse_dialog), for a MODAL
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

-- The one prompt an agent's screen shows now (T395): the daemon's screen watch
-- writes it, the bridge reads it and asks the daemon to answer it. At most one
-- row per agent is open (gone_at NULL); a prompt that goes, or turns into a
-- different one, is closed and the new one opens beside it.
CREATE TABLE IF NOT EXISTS prompt_blocks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  agent       TEXT NOT NULL,
  -- 'permission' | 'question' | 'dialog'
  kind        TEXT NOT NULL,
  -- Hash of the panel text: the identity `answer` re-checks against a fresh read.
  fingerprint TEXT NOT NULL,
  title       TEXT NOT NULL,
  -- JSON: the panel's own lines, and what each kind shows on top of them
  -- (options; the hook's tool/command/reason; a question's header and boxes).
  panel       TEXT NOT NULL,
  detail      TEXT NOT NULL,
  -- The permission hook's wait id, the identity of a permission prompt; NULL
  -- for the other kinds, and for a permission prompt the hook never recorded.
  wait_id     INTEGER,
  -- 0 for a prompt only the terminal can answer (a permission prompt with no
  -- hook record to pin its identity to).
  answerable  INTEGER NOT NULL DEFAULT 1,
  first_seen  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL,
  gone_at     INTEGER,
  -- What `answer_block` did: NULL until tried, then 1/0 and its words.
  answered_at INTEGER,
  answer_ok   INTEGER,
  answer_said TEXT
);
CREATE INDEX IF NOT EXISTS prompt_blocks_agent ON prompt_blocks(agent, gone_at);
CREATE UNIQUE INDEX IF NOT EXISTS prompt_blocks_open ON prompt_blocks(agent) WHERE gone_at IS NULL;

-- One row per account per tick that observe_burn already reads a status for -
-- no read this table exists for is ever driven on its own account (#1113).
-- Every usage column is nullable and none is ever coerced to 0: a figure
-- nobody could trust when the sample was taken is NULL here, same as it is
-- on AccountStatus, so a replay reasons about it the same way pick_account
-- does. A gap in `ts` - the daemon down, or a sleeping machine - is a gap:
-- nothing backfills it.
-- The pct_*/reset_*/rl_* columns are what rows written before `windows` hold;
-- they are read (usage.windows) and never written or rewritten.
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
  collected_at     INTEGER,
  windows          TEXT
);
-- (account, ts) serves a read of one account; the prune and a `since` read
-- filter on ts alone, which that index cannot serve (account leads it).
CREATE INDEX IF NOT EXISTS usage_samples_account_ts ON usage_samples(account, ts);
CREATE INDEX IF NOT EXISTS usage_samples_ts ON usage_samples(ts);

-- T351: text still owed to an agent's terminal - a screen_blocker was up, or
-- the session was closed. Only the daemon writes this table, and a row is
-- deleted the instant it is typed: what was typed already lives in the
-- receiver's own session file, so only the unsent survives a restart (#2117).
CREATE TABLE IF NOT EXISTS pending_messages (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  agent   TEXT    NOT NULL,
  text    TEXT    NOT NULL,
  ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS pending_messages_agent ON pending_messages(agent, id);

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

-- Every session start the chat marks: why it began (a restart, a wake, a handoff, a move, a launch by
-- hand), whether it resumed the old conversation, and what it was given - the handoff record in its
-- system prompt and the first prompt, empty where there was none. Written by the launch itself
-- (`launch.exec_agent`) or, for a handoff typed into a session that stays, by the daemon; session_id
-- is NULL for a fresh launch until its SessionStart hook names it.
CREATE TABLE IF NOT EXISTS session_starts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  agent      TEXT    NOT NULL,
  ts         INTEGER NOT NULL,
  session_id TEXT,
  cause      TEXT    NOT NULL,
  resumed    INTEGER NOT NULL,
  text       TEXT    NOT NULL,
  prompt     TEXT    NOT NULL,
  actor      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS session_starts_agent ON session_starts(agent, id);
"""


class SchemaError(RuntimeError):
    """The database cannot be used by this code as it stands. `kind` is "newer", "older" or "busy"; `have`
    and `latest` are the database's version and the code's, when it is about versions."""

    def __init__(self, message: str, kind: str = "busy", have: int | None = None, latest: int | None = None) -> None:
        super().__init__(message)
        self.kind, self.have, self.latest = kind, have, latest


def connect(path: Path | None = None, *, migrating: bool = False) -> sqlite3.Connection:
    """Open the database. A step marked additive is one a connect may run itself (it only adds, and the
    baseline is the pass every connect used to run), so deploying one has no window; a database waiting for a
    step not so marked is refused until the daemon (which passes `migrating`) has run it. A newer database is
    refused for good. A file with no tables yet is set up whole."""
    p = path or paths.db_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=10.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    # WAL so the daemon reading cannot block an agent writing.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        have = schema_version(con)
        if have < LATEST_VERSION:
            if migrating or not con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                migrate(con)
            else:
                migrate(con, additive_only=True)
            have = schema_version(con)
        if have > LATEST_VERSION and not _newer_steps_only_add(con):
            raise SchemaError(f"{p} is at schema version {have}, newer than the {LATEST_VERSION} this code "
                              f"knows: update handline before using it", "newer", have, LATEST_VERSION)
        if have < LATEST_VERSION:
            raise SchemaError(f"{p} is at schema version {have}, older than the {LATEST_VERSION} this code "
                              f"needs: run `{RESTART_COMMAND}` to restart the daemon, which brings it up", "older", have, LATEST_VERSION)
    except BaseException:
        con.close()
        raise
    return con


def schema_version(con) -> int:
    """The newest migration this database has had; 0 for one that has had none."""
    try:
        return con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    except sqlite3.OperationalError:        # no such table yet
        return 0


MIGRATE_WAIT_S = 60.0
RESTART_COMMAND = "hl service install"


def migrate(con, additive_only: bool = False) -> None:
    """Apply every migration the database has not had, in order, or with `additive_only` up to the first that
    is not marked additive. Processes
    serialize on a lock file, and each takes the version again once it holds it, so a migration runs once
    however many open at the same time. A step that dies midway is run again, so a step must be safe to
    run again. A lock held past MIGRATE_WAIT_S is an error that names its holder."""
    import fcntl
    file = next(r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main")
    with open(file + ".migrate-lock", "a+") as lock:
        deadline = time.monotonic() + MIGRATE_WAIT_S
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    lock.seek(0)
                    holder = lock.read().strip() or "another process"
                    raise SchemaError(f"{file} has been busy migrating for {MIGRATE_WAIT_S:.0f}s, held by pid "
                                      f"{holder}: wait for it, or end it and try again") from None
                time.sleep(0.05)
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        have = schema_version(con)
        if have > LATEST_VERSION and not _newer_steps_only_add(con):
            raise SchemaError(f"{file} is at schema version {have}, newer than the {LATEST_VERSION} this code "
                              f"knows: update handline before using it", "newer", have, LATEST_VERSION)
        for version, name, step, additive in MIGRATIONS:
            if version <= have:
                continue
            if additive_only and not additive:
                break
            if Path(file).resolve().parent in paths.live_homes() and not service.is_installed_checkout():
                raise SchemaError(f"{file} needs step {version} ({name}), which only the installed main checkout "
                                  f"may apply to the live home: this is {paths.repo_root()}; merge it, or point "
                                  f"HANDLINE_HOME at a scratch folder")
            step(con)
            con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, "
                        "name TEXT NOT NULL, applied_at INTEGER NOT NULL, additive INTEGER NOT NULL DEFAULT 0)")
            _mark_additive_column(con)
            con.execute("INSERT INTO schema_version(version, name, applied_at, additive) VALUES(?, ?, ?, ?)",
                        (version, name, int(time.time()), int(additive)))


def _mark_additive_column(con) -> None:
    """A database stamped before steps said whether they only add: give it the column, filled from the
    steps this code knows."""
    if "additive" in _cols(con, "schema_version"):
        return
    con.execute("ALTER TABLE schema_version ADD COLUMN additive INTEGER NOT NULL DEFAULT 0")
    for version, _name, _step, additive in MIGRATIONS:
        con.execute("UPDATE schema_version SET additive = ? WHERE version = ?", (int(additive), version))


def _newer_steps_only_add(con) -> bool:
    """True when every step this code does not know was recorded as additive: it only added tables and
    columns, so this code, which names none of them, runs on the database as it is. A daemon started
    before a merge keeps running after another process applies a step the merge brought."""
    if "additive" not in _cols(con, "schema_version"):
        return False
    return not con.execute("SELECT 1 FROM schema_version WHERE version > ? AND additive = 0 LIMIT 1",
                           (LATEST_VERSION,)).fetchone()


def _baseline(con) -> None:
    """Migration 1: everything done before versions existed, as one pass that is safe to run again."""
    # The legacy request columns (`_LEGACY_REQUEST_COLUMNS`): a database that was dropped of them by
    # the doctor, or made without them, never grows them back and runs no migration that names them.
    fresh = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'").fetchone() is None
    legacy = not fresh and not _legacy_columns_dropped(con)
    unmarked = legacy and not set(_LEGACY_REQUEST_COLUMNS) & _cols(con, "requests")
    legacy = legacy and not unmarked       # dropped without the mark: nothing left for a migration to read
    # This runs before the schema: new indexes reference columns an old table
    # may not have yet, and CREATE INDEX would fail before any migration had a
    # chance to act.
    _pre_schema_migrate(con, legacy)
    con.executescript(SCHEMA)
    if fresh or unmarked:
        set_cursor(con, LEGACY_DROPPED, 1)
    _add_missing_columns(con, legacy)
    if legacy:
        _unhide_the_humans_answers(con)
    _rename_orchestrator(con)
    _english_digests(con)
    if legacy:
        _give_answers_their_own_rows(con)
        _asks_reports_and_escalations_to_says(con)
        _answers_are_says(con)
        _drop_daemon_close_notes(con)
    _escalations_are_tasks(con)
    if legacy:
        _one_doing_task_each(con)
    _notes_are_memos(con)
    _handed_to_founder_are_queued(con)
    _task_deliveries_settled(con)
    if legacy:
        _task_sent_at(con)
    _task_sent_at_from_events(con)
    _task_leftovers_settled(con)
    _kinds_are_say(con)
    if legacy:
        _founder_says_landed(con)
        _fyi_are_says(con)
    _requests_one_state(con, legacy)
    if legacy:
        _answers_told_on_the_say(con)
        _closed_rows_leave_the_queue(con)
        _dismissals_are_events(con)
        _reasons_are_events(con)
    _moot_trust_proposals(con)
    _hr_proposals_leave_tasks(con)
    for gone in ("task_sent_on_link", "task_sent_on_unlink", "task_sent_on_request"):
        con.execute(f"DROP TRIGGER IF EXISTS {gone}")     # T751: `db.refresh_sent` keeps sent_at now


# Ordered and numbered; a step is appended, never edited or reordered, and none may drop or reshape a
# column (the doctor drops dead ones). A database that is not at the last number is not current. A step is
# additive when a plain connect may run it under the lock: it is safe to run again and only adds.
def _rpc_replies(con) -> None:
    """The daemon's reply store: a write's reply, kept a day under its request_id (daemon/rpc.py)."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS rpc_replies (
          caller     TEXT    NOT NULL,
          request_id TEXT    NOT NULL,
          method     TEXT    NOT NULL,
          params     TEXT    NOT NULL,
          reply      TEXT    NOT NULL,
          ts         INTEGER NOT NULL,
          PRIMARY KEY (caller, request_id)
        );
        CREATE INDEX IF NOT EXISTS rpc_replies_ts ON rpc_replies(ts);
    """)


def _agent_view(con) -> None:
    """The daemon's stored view of each agent (daemon/agentview.py)."""
    con.execute("CREATE TABLE IF NOT EXISTS agent_view (agent TEXT PRIMARY KEY, view TEXT NOT NULL, updated INTEGER NOT NULL)")


def _stream_log(con) -> None:
    """The event stream's log; its id is the cursor (daemon/stream.py)."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS stream_log (
          id     INTEGER PRIMARY KEY AUTOINCREMENT,
          ts     INTEGER NOT NULL,
          topic  TEXT    NOT NULL,
          type   TEXT    NOT NULL,
          agent  TEXT,
          actor  TEXT,
          title  TEXT,
          target TEXT,
          data   TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS stream_log_topic ON stream_log(topic, id);
    """)


def _hook_applied(con) -> None:
    """The request ids of hook events already applied, kept a day, with whose and when they ran: the newest
    `at` of an agent is what an older event must not move its state back past (session/events.py `apply_hook`)."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS hook_applied (request_id TEXT PRIMARY KEY, ts INTEGER NOT NULL, agent TEXT, at INTEGER);
        CREATE INDEX IF NOT EXISTS hook_applied_agent ON hook_applied(agent, at);
    """)


def _write_effects(con) -> None:
    """What a write did, recorded in the same transaction as the write: a retry of its request_id after a crash
    between the effect and the reply finds this and answers with it, repeating nothing (daemon/rpc.py)."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS write_effects (
          caller      TEXT    NOT NULL,
          request_id  TEXT    NOT NULL,
          method      TEXT    NOT NULL,
          result_json TEXT    NOT NULL,
          created_at  INTEGER NOT NULL,
          PRIMARY KEY (caller, request_id)
        );
        CREATE INDEX IF NOT EXISTS write_effects_created ON write_effects(created_at);
    """)


def _input_holders(con) -> None:
    """Who types at an agent's terminal when it is not the daemon: no row is the daemon. `last_typed` is what the
    idle hold is counted from."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS input_holders (
          agent       TEXT    PRIMARY KEY,
          kind        TEXT    NOT NULL,
          name        TEXT    NOT NULL,
          since       INTEGER NOT NULL,
          last_typed  INTEGER NOT NULL
        );
    """)


def _operations(con) -> None:
    """Long-running operations (`agent.create`): one row each, updated as steps finish and kept for `operation.get`.
    Additive: a process that does not know the table never reads it."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS operations (
          id          TEXT    PRIMARY KEY,
          kind        TEXT    NOT NULL,
          state       TEXT    NOT NULL,
          step        TEXT,
          steps_done  INTEGER NOT NULL DEFAULT 0,
          steps_total INTEGER,
          message     TEXT,
          result_json TEXT,
          agent       TEXT,
          created_at  INTEGER NOT NULL,
          updated_at  INTEGER NOT NULL
        );
    """)


def _devices(con) -> None:
    """Paired devices: one row each, with the sha256 hex of its token. Revoking a device deletes its row.
    Additive: a process that does not know the table never reads it."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
          id         TEXT    PRIMARY KEY,
          name       TEXT    NOT NULL,
          token_hash TEXT    NOT NULL UNIQUE,
          created    INTEGER NOT NULL,
          last_seen  INTEGER
        );
    """)


def _agent_extra_worktrees(con) -> None:
    """The column `agent.yaml extra_worktrees` is kept in: step 1 adds it to a new database only, so one made
    before it had none and every read of it failed. Additive: a process that does not know it never reads it."""
    if "extra_worktrees" not in _cols(con, "agent_state"):
        con.execute("ALTER TABLE agent_state ADD COLUMN extra_worktrees TEXT")


def _transcript_events(con) -> None:
    """A structured session's events, numbered per agent (contract/protocol.md, Transcript stream). Only the Go daemon
    writes them. Additive: a process that does not know the table never reads it."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS transcript_events (
          agent   TEXT    NOT NULL,
          seq     INTEGER NOT NULL,
          session TEXT    NOT NULL,
          turn    TEXT,
          at      INTEGER NOT NULL,
          kind    TEXT    NOT NULL,
          item    INTEGER,
          body    TEXT    NOT NULL,
          PRIMARY KEY (agent, seq)
        );
    """)


def _land_fixes(con) -> None:
    """The merger's housekeeping on a land (`hl land --fix`, `--squash`): the files written over the merge, as a
    JSON object {path: text}, and whether main gets one commit with the merger's message instead of the merge.
    Additive: a process that does not know the table never reads it, and a land with no row lands as before."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS land_fixes (
          land_id INTEGER PRIMARY KEY,
          files   TEXT    NOT NULL,
          squash  INTEGER NOT NULL DEFAULT 0
        );
    """)


def _task_reporter(con) -> None:
    """A task's reporter, apart from its opener. Step 1 adds it to a new database only. Additive: NULL reads
    as the opener, so a process that does not know the column opens tasks that read as before."""
    if "reporter" not in _cols(con, "tasks"):
        con.execute("ALTER TABLE tasks ADD COLUMN reporter TEXT")


def _agent_terminals(con) -> None:
    """Every terminal opened for an agent, kept after a relaunch moves the agent to another, so a terminal it
    left behind can still be found. agent is NULL for a terminal opened with no agent. Additive: a process that
    does not know the table never reads it."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS agent_terminals (
          handle    TEXT    PRIMARY KEY,
          agent     TEXT,
          opened_at INTEGER NOT NULL,
          closed_at INTEGER
        );
    """)


def _drop_lands(con) -> None:
    """The daemon no longer lands branches: merging is plain git. Additive: only the land verbs of code from before
    read these tables, and nothing sends them any more, so that code still runs on the database without them."""
    con.executescript("""
        DROP TABLE IF EXISTS lands;
        DROP TABLE IF EXISTS land_passes;
        DROP TABLE IF EXISTS land_fixes;
    """)


MIGRATIONS = [
    (1, "baseline", _baseline, True),
    (2, "rpc_replies", _rpc_replies, True),
    (3, "agent_view", _agent_view, True),
    (4, "stream_log", _stream_log, True),
    (5, "hook_applied", _hook_applied, True),
    (6, "write_effects", _write_effects, True),
    (7, "input_holders", _input_holders, True),
    (8, "operations", _operations, True),
    (9, "devices", _devices, True),
    (10, "agent_extra_worktrees", _agent_extra_worktrees, True),
    (11, "transcript_events", _transcript_events, True),
    (12, "land_fixes", _land_fixes, True),
    (13, "task_reporter", _task_reporter, True),
    (14, "agent_terminals", _agent_terminals, True),
    (15, "drop_lands", _drop_lands, True),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def _write_if_any(con, table: str, where: str, sql: str, params: tuple = ()) -> None:
    """Run a write only when `where` matches a row. The steps that run at every connect go through
    this: a write that matches nothing still takes the database's one write lock, so a read-only
    caller (the UI polling every 2s) would wait behind whichever process writes, and die on its
    timeout. The read takes no lock."""
    if con.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", params).fetchone():
        con.execute(sql, params)


def _cols(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


# T835: the request columns the answers-to-says work retired. Nothing reads or writes them; the
# answer is a say, a reason or a dismissal an event, the hold `queue_order`, the time `state_at`.
# Like the unread columns (T830) they are dropped only by `ocp doctor --drop-dead-columns`, never
# at connect - a long-lived process on older code still names them - and the doctor marks the
# database (`LEGACY_DROPPED`) so a connect neither re-adds them nor runs the old migrations
# that read them. A database made after this has no such columns and is marked from the start.
_LEGACY_REQUEST_COLUMNS = ("verdict", "resolved_at", "announced_at", "dismissed_by", "dismissed_reason", "fyi")
LEGACY_DROPPED = "legacy_request_columns_dropped"


def _legacy_columns_dropped(con) -> bool:
    try:
        return bool(get_cursor(con, LEGACY_DROPPED))
    except sqlite3.OperationalError:        # no cursors table yet: a database from long before
        return False


def _pre_schema_migrate(con, legacy: bool = True) -> None:
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
    if legacy and request_cols and "fyi" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN fyi INTEGER NOT NULL DEFAULT 0")
    if legacy and request_cols and "announced_at" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN announced_at INTEGER")
        # Everything already resolved was resolved before anything announced
        # answers at all; announcing it now would type a wall of old verdicts
        # at every agent the first time this runs.
        con.execute("UPDATE requests SET announced_at = ? "
                    "WHERE state IN ('done', 'rejected', 'denied')", (int(time.time()),))
    if legacy and request_cols and "resolved_at" not in request_cols:
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
        # nothing to promote ahead of it.
        con.execute("""
            UPDATE requests SET delivered_at = ts WHERE state = 'queued' AND id IN (
                SELECT MIN(id) FROM requests WHERE state = 'queued' GROUP BY target
            )
        """)
    if request_cols and "edge_ok" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN edge_ok INTEGER")
        con.execute("UPDATE requests SET edge_ok = 1")
    if request_cols and "send_now" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN send_now INTEGER")
    if request_cols and "notice" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN notice INTEGER")
        # No backfill: a task-board say from before this column is a "done by" line about work that
        # ended days ago, and carried by a delivery now it would read as news.
    if request_cols and "moot_by" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN moot_by INTEGER")
        # No backfill: which existing "replaced by #N" row retired which is
        # already named in the verdict text, and re-parsing that text to fill
        # a column meant to replace parsing it is the mistake this exists to
        # end, not repeat.

    block_cols = _cols(con, "task_blocks")
    if block_cols and "dormant" not in block_cols:
        # T707: a wait whose two ends one agent holds because an owner change put them there
        con.execute("ALTER TABLE task_blocks ADD COLUMN dormant INTEGER NOT NULL DEFAULT 0")

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
        # 1: `hl close 1 deadbeef --reason`) is mislabeled 'sha'. One line
        # in a counting display, nothing else - a real gap, not a claim this
        # is exact everywhere.
        for r in con.execute(
                "SELECT id, closed_by FROM tasks WHERE closed_by IS NOT NULL").fetchall():
            via = "sha" if re.fullmatch(r"[0-9a-fA-F]{4,40}", r["closed_by"]) else "reason"
            con.execute("UPDATE tasks SET closed_via = ? WHERE id = ?", (via, r["id"]))
    if task_cols and "queued_by" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN queued_by TEXT")
        con.execute("ALTER TABLE tasks ADD COLUMN queued_at INTEGER")
        # No backfill: an existing waiting task was never approved, so it
        # reads as a draft.
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


def _give_answers_their_own_rows(con) -> None:
    """Every answer a request already carries becomes a message of its own, so
    an old verdict has an id to be numbered by and replied to (T421). Only what
    someone said: a dismissal, a retired row and a system reason are no answer.
    Ledgered, so it runs once; the verdict column is left as it was."""
    if get_cursor(con, "migrated:answer_rows"):
        return
    from . import requests as dbreq
    con.execute("BEGIN IMMEDIATE")
    try:
        if get_cursor(con, "migrated:answer_rows"):     # another process got here first
            con.execute("ROLLBACK")
            return
        for r in con.execute(
                "SELECT id, verdict, COALESCE(resolved_at, ts) AS at FROM requests "
                "WHERE state = 'done' AND verdict IS NOT NULL AND verdict != '' "
                "AND dismissed_by IS NULL AND settles IS NULL AND id NOT IN "
                "(SELECT settles FROM requests WHERE settles IS NOT NULL) "
                "ORDER BY id").fetchall():
            dbreq.add_answer(con, r["id"], r["verdict"], at=r["at"])
        set_cursor(con, "migrated:answer_rows", 1)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _asks_reports_and_escalations_to_says(con) -> None:
    """T361 step A: the founder removed ask, report and escalate as kinds of
    message - what is left is a say, and work or a decision is a task.

    A row that is over becomes a say: it is history, and says nothing new. A row
    still queued is not dropped: one that asks for work or an answer opens as a
    task for its target (a decision, when that is the founder) with the row as
    its first link - the card, as `task_new` makes it - unless it has been
    delivered: the agent is answering it as the ask it was shown, and a task
    opened beside that would never end. Those, and a queued report to an agent
    (it has not been read: an unblock notice is one), are left as they are until
    they settle; one to the founder becomes a say. A queued ask already linked to a task is that task's
    delivery, kind `task`. A queued row to the founder is an `escalate` card
    until the readers stop keying on that kind. Answer rows are not touched here.
    Ledgered, so it runs once."""
    if get_cursor(con, "migrated:kinds_to_say"):
        return
    from . import tasks as dbtasks
    from .requests import HUMAN
    from ..infra import textsafe
    con.execute("BEGIN IMMEDIATE")
    try:
        if get_cursor(con, "migrated:kinds_to_say"):     # another process got here first
            con.execute("ROLLBACK")
            return
        old = "(kind IS NULL OR kind IN ('escalate', 'report'))"
        con.execute(f"UPDATE requests SET kind = 'say' WHERE state != 'queued' AND {old}")
        # A report still waiting for an agent is not history: it has not been read, and
        # the daemon delivers it as a request (an unblock notice is one). It stays
        # until the step that types those as says. One to the founder is only shown.
        con.execute(f"UPDATE requests SET kind = 'say' WHERE state = 'queued' AND {old} "
                    "AND (fyi = 1 OR kind = 'report') AND target = ?", (HUMAN,))
        # Still asking for something, and linked to a task already: that task's delivery
        # (or, to the founder, its card).
        con.execute(f"UPDATE requests SET kind = 'task' WHERE state = 'queued' AND {old} "
                    "AND fyi = 0 AND COALESCE(kind, '') != 'report' AND target != ? "
                    "AND id IN (SELECT request_id FROM task_requests)", (HUMAN,))
        con.execute("UPDATE requests SET kind = 'escalate' WHERE state = 'queued' AND kind IS NULL "
                    "AND fyi = 0 AND target = ? AND id IN (SELECT request_id FROM task_requests)", (HUMAN,))
        for r in con.execute(
                f"SELECT id, requester, target, body FROM requests WHERE state = 'queued' AND {old} "
                "AND fyi = 0 AND COALESCE(kind, '') != 'report' AND delivered_at IS NULL "
                "AND id NOT IN (SELECT request_id FROM task_requests) ORDER BY id").fetchall():
            title = textsafe.oneline((r["body"] or "").strip().split("\n", 1)[0])[:80] or "(no text)"
            tid = dbtasks.new_task(con, title, r["target"], r["requester"],
                                   expects="decision" if r["target"] == HUMAN else "report")
            dbtasks.set_task_queued(con, tid, HUMAN)
            dbtasks.link_request(con, tid, r["id"])
            con.execute("UPDATE requests SET kind = ? WHERE id = ?",
                        ("escalate" if r["target"] == HUMAN else "task", r["id"]))
        set_cursor(con, "migrated:kinds_to_say", 1)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _escalations_are_tasks(con) -> None:
    """T361 step B2: the founder's gate is an open decision task, not a kind of row.
    A queued `escalate` row that no task carries opens one (its card, opened by the
    agent it is about); every `escalate` row is then a `task` - the delivery of one,
    or history. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:escalations_are_tasks"):
        return
    from . import tasks as dbtasks
    from .requests import HUMAN
    from ..infra import textsafe
    for r in con.execute("SELECT id, ts, requester, body FROM requests WHERE kind = 'escalate' "
                         "AND state = 'queued' AND target = ? "
                         "AND id NOT IN (SELECT request_id FROM task_requests)", (HUMAN,)).fetchall():
        title = textsafe.oneline((r["body"] or "").strip().split("\n", 1)[0])[:80] or "a decision"
        tid = dbtasks.new_task(con, title, HUMAN, r["requester"], expects="decision")
        con.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (r["ts"], tid))
        dbtasks.set_task_queued(con, tid, HUMAN)
        dbtasks.link_request(con, tid, r["id"])
    con.execute("UPDATE requests SET kind = 'task' WHERE kind = 'escalate'")
    set_cursor(con, "migrated:escalations_are_tasks", 1)


def _handed_to_founder_are_queued(con) -> None:
    """T681: a task handed to the founder read as a draft, which is outside "for you". An open task
    the founder holds by a handoff and that nobody approved is queued by whoever handed it over.
    Ledgered, so it runs once."""
    if get_cursor(con, "migrated:handed_to_founder_queued"):
        return
    from .requests import HUMAN
    for r in con.execute(
            "SELECT t.id, (SELECT json_extract(e.payload, '$.from') FROM events e WHERE e.kind = 'task_handoff' "
            f"AND {EVENT_TASK.replace('payload', 'e.payload')} = t.id AND json_extract(e.payload, '$.to') = ? "
            "ORDER BY e.id DESC LIMIT 1) AS frm FROM tasks t WHERE t.owner = ? AND t.queued_by IS NULL "
            "AND t.discarded_by IS NULL AND t.done_evidence IS NULL", (HUMAN, HUMAN)).fetchall():
        if r["frm"]:
            con.execute("UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ?",
                        (r["frm"], int(time.time()), r["id"]))
    set_cursor(con, "migrated:handed_to_founder_queued", 1)


def _task_deliveries_settled(con) -> None:
    """T720: stamp `tasks.delivered_at` from the deliveries that already landed, and settle the ghosts -
    a delivery still queued on a task that has ended, or one handed on and left with its old holder
    (#7789, #7837). Ledgered, so it runs once; the writers keep it current after that."""
    if get_cursor(con, "migrated:task_deliveries_settled"):
        return
    from .requests import HUMAN, close_if_queued
    con.execute(
        "UPDATE tasks SET delivered_at = (SELECT MIN(CASE WHEN r.target = ? THEN COALESCE(r.landed_at, r.delivered_at) "
        "ELSE r.landed_at END) FROM task_requests l JOIN requests r ON r.id = l.request_id "
        "WHERE l.task_id = tasks.id AND (r.kind = 'task' OR (r.target = ? AND COALESCE(r.kind, '') NOT IN ('say', 'report'))) AND r.target = tasks.owner) "
        "WHERE delivered_at IS NULL", (HUMAN, HUMAN))
    ghosts = con.execute(
        "SELECT DISTINCT r.id FROM requests r JOIN task_requests l ON l.request_id = r.id "
        "WHERE r.state = 'queued' AND r.kind = 'task' AND NOT EXISTS ("
        "SELECT 1 FROM task_requests l2 JOIN tasks t ON t.id = l2.task_id WHERE l2.request_id = r.id "
        "AND t.owner = r.target AND t.done_evidence IS NULL AND t.discarded_by IS NULL)").fetchall()
    for g in ghosts:      # no open task of its target is left for it to deliver: it ended, or was handed on
        close_if_queued(con, g["id"], "done", "its task is no longer this agent's to do", announced=True, settle=False)
    set_cursor(con, "migrated:task_deliveries_settled", 1)


def _kinds_are_say(con) -> None:
    """T766 (T763 design, branch 1): a request is a say or a task. Every other kind left on a row
    that is over - no kind at all (an ask, or a refusal written before the kind was known), answer,
    escalate, report, task_note - is a say: history, and nothing reads the old words any more. A
    queued row is not touched. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:kinds_are_say"):
        return
    con.execute("UPDATE requests SET settles = parent_id WHERE kind = 'answer' AND settles IS NULL "
                "AND parent_id IS NOT NULL")       # as `_answers_are_says` did, for the ones written since
    con.execute("UPDATE requests SET kind = 'say' WHERE state != 'queued' "
                "AND (kind IS NULL OR kind IN ('', 'answer', 'escalate', 'report', 'task_note'))")
    set_cursor(con, "migrated:kinds_are_say", 1)


def _founder_says_landed(con) -> None:
    """T781: a say to the founder is born landed and carries when he read it. What he has already
    dealt with reads as read (when it was closed); a say still queued for him becomes landed and
    unread; a task card he holds stays a task, queued. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:founder_says_landed"):
        return
    con.execute("UPDATE requests SET read_at = COALESCE(resolved_at, ts) WHERE target = 'human' "
                "AND state != 'queued' AND read_at IS NULL")
    from .requests import close_if_queued
    for r in con.execute("SELECT id FROM requests WHERE target = 'human' AND state = 'queued' "
                         "AND kind = 'say'").fetchall():
        if close_if_queued(con, r["id"], "done", None, announced=True, settle=False):
            con.execute("UPDATE requests SET landed_at = COALESCE(landed_at, ts) WHERE id = ?", (r["id"],))
    set_cursor(con, "migrated:founder_says_landed", 1)


def _fyi_are_says(con) -> None:
    """T835 step 1: a report to the founder is a say, so the `fyi` flag says nothing more. Every
    row that carries it becomes a say; one still queued becomes landed and unread, as a say to
    the founder is born, and one already dealt with reads as read. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:fyi_are_says"):
        return
    now = int(time.time())
    con.execute("UPDATE requests SET kind = 'say' WHERE fyi = 1")
    con.execute("UPDATE requests SET read_at = COALESCE(read_at, resolved_at, ts) "
                "WHERE fyi = 1 AND state != 'queued' AND read_at IS NULL")
    con.execute("UPDATE requests SET \"state\" = 'landed', state_at = ?, landed_at = COALESCE(landed_at, ts), "
                "resolved_at = COALESCE(resolved_at, ts), announced_at = COALESCE(announced_at, ts) "
                "WHERE fyi = 1 AND \"state\" = 'queued'", (now,))
    set_cursor(con, "migrated:fyi_are_says", 1)


def _answers_told_on_the_say(con) -> None:
    """T835 step 3: whether the asker was told how a request came back is kept on the answering
    say (`delivered_at`), not on the request (`announced_at`). A closed request still waiting to be
    told that has no answering say gets one (its verdict, or nothing, as the reason it closed);
    every answering say takes the request's telling. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:answers_told_on_the_say"):
        return
    _backfill_answers_told_on_the_say(con)
    set_cursor(con, "migrated:answers_told_on_the_say", 1)


def _backfill_answers_told_on_the_say(con) -> None:
    """What `_answers_told_on_the_say` does, keyed on the rows rather than the ledger: run again before the
    columns are dropped, it catches what a process still on older code closed after the upgrade."""
    from . import requests as dbreq
    for r in con.execute(
            "SELECT id, verdict, COALESCE(resolved_at, ts) AS at FROM requests "
            "WHERE \"state\" IN ('landed', 'withdrawn') AND announced_at IS NULL AND dismissed_by IS NULL "
            "AND COALESCE(kind, '') != 'say' AND settles IS NULL "
            "AND id NOT IN (SELECT settles FROM requests WHERE settles IS NOT NULL) "
            "AND resolved_at IS NOT NULL ORDER BY id").fetchall():
        dbreq.add_answer(con, r["id"], r["verdict"] or "", at=r["at"])
    con.execute("UPDATE requests SET delivered_at = (SELECT q.announced_at FROM requests q "
                "WHERE q.id = requests.settles) WHERE settles IS NOT NULL AND delivered_at IS NULL")


def _closed_rows_leave_the_queue(con) -> None:
    """T835 step 4b: a request that was closed, or whose task hold ended, has no place in its
    target's queue: `queue_order` NULL, which is what `HELD_DELIVERY` reads instead of `resolved_at`.
    Ledgered, so it runs once."""
    if get_cursor(con, "migrated:closed_rows_leave_the_queue"):
        return
    _backfill_closed_rows_leave_the_queue(con)
    set_cursor(con, "migrated:closed_rows_leave_the_queue", 1)


def _backfill_closed_rows_leave_the_queue(con) -> None:
    """What `_closed_rows_leave_the_queue` does, keyed on the rows rather than the ledger: run again before the
    columns are dropped, it catches what a process still on older code closed after the upgrade."""
    con.execute("UPDATE requests SET queue_order = NULL WHERE resolved_at IS NOT NULL AND queue_order IS NOT NULL")


def _dismissals_are_events(con) -> None:
    """T835 step 4c: a dismissal is an event naming the request, who dismissed it and why, not two
    columns of the row. Each dismissed row that has none gets one, at the time it was closed.
    Ledgered, so it runs once."""
    if get_cursor(con, "migrated:dismissals_are_events"):
        return
    _backfill_dismissals_are_events(con)
    set_cursor(con, "migrated:dismissals_are_events", 1)


def _backfill_dismissals_are_events(con) -> None:
    """What `_dismissals_are_events` does, keyed on the rows rather than the ledger: run again before the
    columns are dropped, it catches what a process still on older code closed after the upgrade."""
    for r in con.execute(
            "SELECT id, dismissed_by, dismissed_reason, COALESCE(resolved_at, state_at, ts) AS at FROM requests "
            "WHERE dismissed_by IS NOT NULL AND id NOT IN (SELECT " + EVENT_REQUEST + " FROM events "
            "WHERE kind = 'dismissed' AND " + EVENT_REQUEST + " IS NOT NULL) ORDER BY id").fetchall():
        con.execute("INSERT INTO events(ts, agent, kind, payload) VALUES(?, ?, 'dismissed', ?)",
                    (r["at"], r["dismissed_by"], json.dumps({"request_id": r["id"], "by": r["dismissed_by"],
                                                              "reason": r["dismissed_reason"]})))


def _reasons_are_events(con) -> None:
    """T835 step 4d: why a request closed without an answer (a denial, a withdrawal) is an event
    naming it, not the verdict column. Each closed row that has a verdict, no answering say and no
    dismissal gets one, at the time it closed. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:reasons_are_events"):
        return
    _backfill_reasons_are_events(con)
    set_cursor(con, "migrated:reasons_are_events", 1)


def _backfill_reasons_are_events(con) -> None:
    """What `_reasons_are_events` does, keyed on the rows rather than the ledger: run again before the
    columns are dropped, it catches what a process still on older code closed after the upgrade."""
    for r in con.execute(
            "SELECT id, verdict, COALESCE(state_at, ts) AS at FROM requests WHERE verdict IS NOT NULL AND verdict != '' "
            "AND settles IS NULL AND dismissed_by IS NULL AND id NOT IN "
            "(SELECT settles FROM requests WHERE settles IS NOT NULL) AND id NOT IN "
            "(SELECT " + EVENT_REQUEST + " FROM events WHERE kind = 'request_reason' AND "
            + EVENT_REQUEST + " IS NOT NULL) ORDER BY id").fetchall():
        con.execute("INSERT INTO events(ts, agent, kind, payload) VALUES(?, '', 'request_reason', ?)",
                    (r["at"], json.dumps({"request_id": r["id"], "reason": r["verdict"]})))


def _requests_one_state(con, legacy: bool = True) -> None:
    """T797 (T763 branch 6): a request has one state and one time. done becomes landed; rejected
    becomes landed when it had been delivered and withdrawn when it had not; every row gets the time
    it entered its state. Not ledgered: it runs at every connect, because a daemon still on the old
    code for a minute after a merge writes the old words, and finding nothing is one indexed
    lookup. The closed rows only - a queued one has no state to move to yet, and so cannot strand a
    proposal; the state column is quoted so the chokepoint scan does not read this as a closer.
    A database without the legacy columns (`legacy` False) is mapped without reading them."""
    resolved = "resolved_at, " if legacy else ""
    if legacy and not get_cursor(con, "migrated:requests_one_state"):
        # What ended before it was sent was never an answer to anyone: "withdrawn" joins the states
        # `unannounced_answers` reads (a refusal that was `rejected`), so the old ones are marked told.
        con.execute("UPDATE requests SET announced_at = COALESCE(announced_at, resolved_at, ts) "
                    "WHERE state IN ('withdrawn', 'replaced', 'denied')")
        set_cursor(con, "migrated:requests_one_state", 1)
    # A request that reached its agent is landed, whichever code wrote the landing (T797 6b).
    from .requests import HUMAN
    told = ", announced_at = COALESCE(announced_at, landed_at)" if legacy else ""
    landed = '"state" = \'queued\' AND landed_at IS NOT NULL AND target != ?'
    _write_if_any(con, "requests", landed,
                  'UPDATE requests SET "state" = \'landed\', state_at = landed_at' + told + f' WHERE {landed}',
                  (HUMAN,))
    if con.execute("SELECT 1 FROM requests WHERE state IN ('done', 'rejected') LIMIT 1").fetchone() is None:
        return
    con.execute('UPDATE requests SET "state" = \'landed\', state_at = COALESCE(landed_at, typed_at, '
                f'delivered_at, {resolved}ts) WHERE "state" = \'done\'')
    con.execute('UPDATE requests SET "state" = \'landed\', state_at = COALESCE(landed_at, typed_at, '
                f'delivered_at, {resolved}ts) WHERE "state" = \'rejected\' '
                'AND (delivered_at IS NOT NULL OR landed_at IS NOT NULL)')
    con.execute(f'UPDATE requests SET "state" = \'withdrawn\', state_at = COALESCE({resolved}ts) '
                'WHERE "state" = \'rejected\'')
    con.execute(f"UPDATE requests SET state_at = COALESCE({resolved}ts) WHERE state_at IS NULL "
                "AND state != 'queued'")
    con.execute("UPDATE requests SET state_at = ts WHERE state_at IS NULL")


# Request columns nothing reads or writes any more (T797 branch 7). They are NOT dropped at connect:
# a long-lived process on older code (the bridge, an agent's own CLI) still names them in its
# statements and re-adds them when it connects, so a drop at connect crashes it - or flaps against it
# on every call (T830). Dropped by `ocp doctor --drop-dead-columns`, once everything long-lived has
# been restarted; new code never names them and a fresh database never has them.
_UNREAD_REQUEST_COLUMNS = ("timebox_s", "timebox_noticed_at", "landing_tries", "told_out_of_turn",
                           *_LEGACY_REQUEST_COLUMNS)


def unread_request_columns(con) -> list[str]:
    """The request columns nothing reads that are still there, in the order they would be dropped."""
    present = _cols(con, "requests")
    return [c for c in _UNREAD_REQUEST_COLUMNS if c in present]


def drop_unread_request_columns(con) -> list[str]:
    """Drop what `_UNREAD_REQUEST_COLUMNS` finds still present, in one write transaction with the
    columns read again inside it, so two of these racing cannot both try the same drop; a column
    that vanishes anyway is not an error. The columns dropped."""
    dropped: list[str] = []
    con.execute("BEGIN IMMEDIATE")
    try:
        present = _cols(con, "requests")
        if set(_LEGACY_REQUEST_COLUMNS) <= present:
            # What a process still on older code closed since the upgrade carries its answer, reason
            # or dismissal only in these columns: it is moved to where the readers look first.
            _backfill_answers_told_on_the_say(con)
            _backfill_closed_rows_leave_the_queue(con)
            _backfill_dismissals_are_events(con)
            _backfill_reasons_are_events(con)
        for col in _UNREAD_REQUEST_COLUMNS:
            if col not in present:
                continue
            try:
                con.execute(f"ALTER TABLE requests DROP COLUMN {col}")
                dropped.append(col)
            except sqlite3.OperationalError as e:
                if "no such column" not in str(e):
                    raise
        if not set(_LEGACY_REQUEST_COLUMNS) & _cols(con, "requests"):
            set_cursor(con, LEGACY_DROPPED, 1)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return dropped


def _moot_trust_proposals(con) -> None:
    """T797: trust proposals for a root already declared are settled as moot, once. A store that
    cannot be read here leaves them for the next connect."""
    if get_cursor(con, "migrated:moot_trust_proposals"):
        return
    try:
        from ..team import trust
        trust.settle_moot()
    except Exception:  # noqa: BLE001 - the migration must never stop a connect
        return
    set_cursor(con, "migrated:moot_trust_proposals", 1)


def _hr_proposals_leave_tasks(con) -> None:
    """Roster proposals stop being decision tasks: the open ones leave the founder's task board and
    wait on `hr.decide`. Once; a store that cannot be read here leaves them for the next connect."""
    if get_cursor(con, "migrated:hr_proposals_leave_tasks"):
        return
    try:
        from ..team import hr
        hr.leave_tasks(con)
    except Exception:  # noqa: BLE001 - the migration must never stop a connect
        return
    set_cursor(con, "migrated:hr_proposals_leave_tasks", 1)


def _task_leftovers_settled(con) -> None:
    """T751 D3: the deliveries still queued on a task that has ended, settled once at upgrade. After
    this only `hl doctor --settle-leftovers` does it - never a timer, which would race the writers
    that end a task and then answer its requester."""
    if get_cursor(con, "migrated:task_leftovers_settled"):
        return
    from .requests import settle_leftover_deliveries
    settle_leftover_deliveries(con)
    set_cursor(con, "migrated:task_leftovers_settled", 1)


def _task_sent_at(con) -> None:
    """T737: `tasks.sent_at` from the deliveries out now - set, as the triggers set it, to now for a
    task with an open request that asks something (not an fyi report) of anyone on it. Ledgered, so
    it runs once; the triggers keep it current after that."""
    if get_cursor(con, "migrated:task_sent_at"):
        return
    con.execute(
        "UPDATE tasks SET sent_at = CAST(strftime('%s','now') AS INTEGER) WHERE EXISTS ("
        "SELECT 1 FROM task_requests l JOIN requests r ON r.id = l.request_id "
        "WHERE l.task_id = tasks.id AND r.state = 'queued' AND r.fyi = 0)")
    set_cursor(con, "migrated:task_sent_at", 1)


def _task_sent_at_from_events(con) -> None:
    """T751: the triggers that kept `tasks.sent_at` are dropped (`db.refresh_sent` does it now), and
    the rule gained one clause - an ended task is never sent - so it is worked out once more for every
    task, clearing the ghosts the triggers left."""
    if get_cursor(con, "migrated:task_sent_at_events"):
        return
    from .tasks import refresh_sent
    for r in con.execute("SELECT id FROM tasks").fetchall():
        refresh_sent(con, r[0])
    con.execute("UPDATE tasks SET sent_at = NULL WHERE done_evidence IS NOT NULL OR discarded_by IS NOT NULL")
    set_cursor(con, "migrated:task_sent_at_events", 1)


def _one_doing_task_each(con) -> None:
    """T444: an agent holds one doing task. Where several are being asked of one agent
    and it holds none of them, the newest becomes its current task and the rest read as
    queued - reported once, as an event per agent. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:one_doing_each"):
        return
    from .events import add_event
    from .requests import HUMAN
    rows = con.execute(
        "SELECT t.id, t.owner FROM tasks t WHERE t.discarded_by IS NULL AND t.done_evidence IS NULL "
        "AND t.owner != ? AND (t.id IN (SELECT l.task_id FROM task_requests l JOIN requests r "
        "ON r.id = l.request_id WHERE r.state = 'queued' AND r.fyi = 0) "
        f"OR t.id IN (SELECT {EVENT_TASK} FROM events WHERE kind = 'task_begin')) ORDER BY t.id",
        (HUMAN,)).fetchall()
    by: dict[str, list[int]] = {}
    for r in rows:
        by.setdefault(r["owner"], []).append(r["id"])
    for agent, tids in by.items():
        cur = con.execute("SELECT current_task FROM agent_state WHERE agent = ?", (agent,)).fetchone()
        held = cur["current_task"] if cur else None
        if len(tids) < 2 or held in tids:
            continue
        keep = tids[-1]
        con.execute("INSERT INTO agent_state(agent, state, updated, current_task) VALUES(?,?,?,?) "
                    "ON CONFLICT(agent) DO UPDATE SET current_task = excluded.current_task",
                    (agent, "IDLE", int(time.time()), keep))
        add_event(con, agent, "demoted_doing", {"kept": keep, "queued": [t for t in tids if t != keep]})
    set_cursor(con, "migrated:one_doing_each", 1)


def _notes_are_memos(con) -> None:
    """T521: a task note is a memo. Every note already sent (an event `task_note` with its
    text) becomes a memo of that task, its sender the author, its time the event's. Tasks that
    are gone and empty texts are skipped. Ledgered, so it runs once."""
    if get_cursor(con, "migrated:notes_are_memos"):
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        if get_cursor(con, "migrated:notes_are_memos"):     # another process got here first
            con.execute("ROLLBACK")
            return
        for e in con.execute("SELECT agent, ts, payload FROM events WHERE kind = 'task_note' "
                             "ORDER BY id").fetchall():
            try:
                data = json.loads(e["payload"] or "{}")
            except ValueError:
                continue
            tid, text = data.get("task_id"), (data.get("text") or "").strip()
            if not text or not isinstance(tid, int) \
                    or con.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,)).fetchone() is None:
                continue
            con.execute("INSERT INTO task_memos(task_id, author, body, created_at) VALUES (?,?,?,?)",
                        (tid, e["agent"], text, e["ts"]))
        set_cursor(con, "migrated:notes_are_memos", 1)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _answers_are_says(con) -> None:
    """T361 step B1: an answer row is a say that references the request it settled
    (`parent_id`, and `settles` to tell it from an interim reply). Ledgered, so it
    runs once."""
    if get_cursor(con, "migrated:answers_are_says"):
        return
    con.execute("UPDATE requests SET kind = 'say', settles = parent_id "
                "WHERE kind = 'answer' AND parent_id IS NOT NULL")
    set_cursor(con, "migrated:answers_are_says", 1)


# What the daemon wrote as the verdict of a say it closed: when it typed it, or when it settled an
# old one. Both were drawn in the chat as a reply from the target (T441).
_DAEMON_CLOSE_NOTES = ("typed", "closed: told before says were typed at once")


def _drop_daemon_close_notes(con) -> None:
    """A say the daemon closed had its own note as the verdict, and (T421) as an answer message:
    drawn as a reply nobody wrote (T441). The message goes, and so does the verdict. Once,
    ledgered. The message is found as an answer row, or, once answers are says, by the `settles`
    that marks it; its body is the parent's verdict, and the parent is a say."""
    if get_cursor(con, "migrated:close_notes"):
        return
    marks = ",".join("?" * len(_DAEMON_CLOSE_NOTES))
    answer = "a.kind = 'answer'" + (" OR a.settles IS NOT NULL" if "settles" in _cols(con, "requests") else "")
    con.execute("DELETE FROM requests WHERE id IN (SELECT a.id FROM requests a JOIN requests p "
                f"ON p.id = a.parent_id WHERE ({answer}) AND a.body = p.verdict AND p.kind = 'say' "
                f"AND p.verdict IN ({marks}))", _DAEMON_CLOSE_NOTES)
    con.execute(f"DELETE FROM events WHERE kind = 'request_reason' AND {EVENT_REQUEST} IN (SELECT id FROM requests "
                f"WHERE kind = 'say' AND state = 'done' AND verdict IN ({marks}))", _DAEMON_CLOSE_NOTES)
    con.execute(f"UPDATE requests SET verdict = NULL WHERE kind = 'say' AND state = 'done' "
                f"AND verdict IN ({marks})", _DAEMON_CLOSE_NOTES)
    set_cursor(con, "migrated:close_notes", 1)


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
    from ..infra import paths, sealed

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
            before = t.read_text()
            after = before.replace(OLD_HUMAN, new)
            t.write_text(after)
            sealed.follow(sealed.TEAM, before, after)
    set_cursor(con, "migrated:human_name", 1)


def _english_digests(con) -> None:
    """Every agent's stored daily digests get English section headings in place of the Korean ones they
    were written with. Once, ledgered; a digest written afterwards is English already, and one an older
    process still writes is read by `digest.parse` either way."""
    if get_cursor(con, "migrated:english_digests"):
        return
    from ..infra import atomic, paths
    from ..session import digest
    for agent in paths.list_agents():
        for f in (paths.agent_dir(agent) / "journal").glob("*.md"):
            try:
                before = f.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            after = digest.migrate_text(before)
            if after != before:
                try:
                    atomic.write_text(f, after)
                except OSError:         # a folder it cannot write: the file stays Korean, which parse reads
                    continue
    set_cursor(con, "migrated:english_digests", 1)


def _add_missing_columns(con, legacy: bool = True) -> None:
    # session_starts gained who asked for a start.
    if "actor" not in _cols(con, "session_starts"):
        con.execute("ALTER TABLE session_starts ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
    # usage_samples gained the windows list; the rows before it keep the columns
    # they were written with, and `usage.windows` reads either.
    if "windows" not in _cols(con, "usage_samples"):
        con.execute("ALTER TABLE usage_samples ADD COLUMN windows TEXT")
    request_cols = _cols(con, "requests")
    if "state_at" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN state_at INTEGER")
    if "read_at" not in request_cols:
        con.execute("ALTER TABLE requests ADD COLUMN read_at INTEGER")
    if "settles" not in request_cols:
        # The request a say settled, when it is the answer that closed it (T361).
        con.execute("ALTER TABLE requests ADD COLUMN settles INTEGER")
    # After the column exists, on an old db as on a new one (SCHEMA runs before this):
    # the chat page looks up the answer of every row it draws by this.
    con.execute("CREATE INDEX IF NOT EXISTS requests_settles ON requests(settles)")
    # The chat topic's detector asks for what was touched since a time, by these two (a request: its state_at else its ts; an answer: its ts).
    con.execute("CREATE INDEX IF NOT EXISTS requests_touched ON requests(COALESCE(state_at, ts))")
    con.execute("CREATE INDEX IF NOT EXISTS requests_settles_ts ON requests(settles, ts)")
    for col in ("dismissed_by", "dismissed_reason"):
        if legacy and col not in request_cols:
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
    if "landed_at" not in request_cols:
        for col, kind in (("typed_at", "INTEGER"), ("typed_session", "TEXT"),
                          ("landed_at", "INTEGER")):
            con.execute(f"ALTER TABLE requests ADD COLUMN {col} {kind}")
        # Told before landings were checked: nothing can prove or disprove them now.
        con.execute("UPDATE requests SET landed_at = delivered_at WHERE delivered_at IS NOT NULL")
    _write_if_any(con, "cursors", "name LIKE 'sendnow_told:%'", "DELETE FROM cursors WHERE name LIKE 'sendnow_told:%'")
    task_cols = _cols(con, "tasks")
    for col in ("discarded_by", "discard_reason"):
        if col not in task_cols:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
    if "priority" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 2")
    if "merged_into" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN merged_into INTEGER REFERENCES tasks(id)")
    if "discarded_at" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN discarded_at INTEGER")
        con.execute("UPDATE tasks SET discarded_at = closed_at WHERE discarded_by IS NOT NULL")
    # T568: closed is done and merged is a discard, so a task ends only by
    # done or discard. A closed row already carries its evidence.
    merged = "merged_into IS NOT NULL AND discarded_by IS NULL"
    _write_if_any(con, "tasks", merged,
                  "UPDATE tasks SET discarded_by = closed_by, discarded_at = closed_at, "
                  f"discard_reason = 'merged into T' || merged_into WHERE {merged}")
    closed = "closed_by IS NOT NULL AND discarded_by IS NULL AND done_evidence IS NULL"
    _write_if_any(con, "tasks", closed,
                  "UPDATE tasks SET done_evidence = 'closed by ' || closed_by, done_at = closed_at, "
                  f"done_via = 'reason' WHERE {closed}")
    if "delivered_at" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN delivered_at INTEGER")
    if "sent_at" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN sent_at INTEGER")
    if "timeout_s" not in task_cols:
        con.execute("ALTER TABLE tasks ADD COLUMN timeout_s INTEGER")
        con.execute("ALTER TABLE tasks ADD COLUMN timeout_at INTEGER")
        # Tasks already doing get 4h from now, the rest none (MAX_TASK_TIMEOUT_S
        # as a literal: schema.py imports nothing from actions).
        now = int(time.time())
        con.execute(
            "UPDATE tasks SET timeout_s = 14400, timeout_at = ? "
            "WHERE closed_by IS NULL AND done_evidence IS NULL AND ("
            "  EXISTS (SELECT 1 FROM task_requests l JOIN requests r ON r.id = l.request_id"
            "          WHERE l.task_id = tasks.id AND r.state = 'queued')"
            "  OR EXISTS (SELECT 1 FROM tasks c JOIN task_requests l ON l.task_id = c.id"
            "             JOIN requests r ON r.id = l.request_id"
            "             WHERE c.parent_task = tasks.id AND c.closed_by IS NULL"
            "             AND r.state = 'queued')"
            ")", (now + 14400,))
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
    if "launch_parts" not in state_cols:
        # The settings a session was launched with, one hash each (`launch.fingerprint_parts`): what
        # names the ones that changed. No backfill: a row without it names nothing.
        con.execute("ALTER TABLE agent_state ADD COLUMN launch_parts TEXT")
    if "launch_fingerprint" not in state_cols:
        # No backfill: an existing row reads as drifted, which is right.
        con.execute("ALTER TABLE agent_state ADD COLUMN launch_fingerprint TEXT")
    if "extra_worktrees" not in state_cols:
        # The agent's further worktrees (agent.yaml `extra_worktrees`), as a JSON list of paths: the first one stays in `worktree`.
        # Written once when one is made; a row without it has none.
        con.execute("ALTER TABLE agent_state ADD COLUMN extra_worktrees TEXT")
    if "notified_session" not in state_cols:
        # The session the notify cursor's head was typed into. A delivery
        # belongs to the session that read it, not to the agent.
        con.execute("ALTER TABLE agent_state ADD COLUMN notified_session TEXT")
    if "current_task" not in state_cols:
        # T292: the one task this agent is holding right now, set on
        # delivery of a `task new` or by `task start`, cleared on done or
        # release. REFERENCES tasks(id), never a copy of anything on that
        # row - the board is still the one place a task's own facts live.
        con.execute("ALTER TABLE agent_state ADD COLUMN current_task INTEGER "
                    "REFERENCES tasks(id)")
    if "expects" not in task_cols:
        # T292: what `task done` must be given - sha, verdict, decision, or
        # (NULL) a free reason. Set once at `task new`, on the task itself
        # since it can outlive several requests.
        con.execute("ALTER TABLE tasks ADD COLUMN expects TEXT")
