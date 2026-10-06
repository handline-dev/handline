"""The event stream (contract/protocol.md, `subscribe`, `events.feed`, `event`, `gap`): every change a client
may watch is a row in `stream_log`, whose id is the cursor, so a client that reconnects with its last cursor
gets exactly what it missed. The log is its own table because the events table is read as "anything new
since" by code that must not see these rows.

The daemon is not yet the only writer, so one watcher notices change from any process (sqlite's
`PRAGMA data_version`, every `WATCH_S`) and turns it into rows with the detectors below; a method that
writes later may call `emit` itself. A client that cannot keep up is dropped with a `gap`, never buffered
without limit."""
from __future__ import annotations

import collections
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

from .. import db
from ..actions import prompt_blocks
from . import rpc_agents

WATCH_S = 0.25
RETRY_S = 2.0              # after the watcher has failed, before it tries again
RETENTION_S = 7 * 24 * 3600
KEEP_ROWS = 50000
MAX_QUEUE = 1000            # a subscriber this far behind is dropped with a gap
REPLAY_MAX = 5000           # a resume further behind than this is a gap too
PRUNED = "stream:pruned"    # cursors: the newest id retention has removed

TOPICS = ("status", "accounts", "agents", "inbox", "tasks", "notifications", "operations", "chat")


def _ts(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_of(row) -> dict:
    return {"cursor": row["id"], "topic": row["topic"], "type": row["type"], "at": _ts(row["ts"]),
            "actor": json.loads(row["actor"]) if row["actor"] else None, "agent": row["agent"],
            "title": row["title"], "target": json.loads(row["target"]) if row["target"] else None,
            "data": json.loads(row["data"])}


def append(con, topic: str, type: str, data: dict, agent: str | None = None, actor: dict | None = None,
           title: str | None = None, target: dict | None = None) -> dict:
    cur = con.execute("INSERT INTO stream_log(ts, topic, type, agent, actor, title, target, data) VALUES(?,?,?,?,?,?,?,?)",
                      (int(time.time()), topic, type, agent, json.dumps(actor) if actor else None, title,
                       json.dumps(target) if target else None, json.dumps(data)))
    return event_of(con.execute("SELECT * FROM stream_log WHERE id = ?", (cur.lastrowid,)).fetchone())


def newest(con) -> int:
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM stream_log").fetchone()[0]


def pruned_through(con) -> int:
    return db.get_cursor(con, PRUNED)


def since(con, cursor: int, topics, agent: str | None = None, limit: int = REPLAY_MAX + 1) -> list[dict]:
    q = f"SELECT * FROM stream_log WHERE id > ? AND topic IN ({','.join('?' * len(topics))})"
    args: list = [cursor, *topics]
    if agent:
        q += " AND (agent = ? OR agent IS NULL)"
        args.append(agent)
    return [event_of(r) for r in con.execute(q + " ORDER BY id LIMIT ?", (*args, limit))]


def latest(con, topics, limit: int) -> list[dict]:
    rows = con.execute(f"SELECT * FROM (SELECT * FROM stream_log WHERE topic IN ({','.join('?' * len(topics))}) "
                       "ORDER BY id DESC LIMIT ?) ORDER BY id", (*topics, limit)).fetchall()
    return [event_of(r) for r in rows]


def prune(con, now: int | None = None) -> None:
    """Drop what is past retention, and remember how far, so a client resuming from before it is told."""
    now = int(time.time()) if now is None else now
    old = con.execute("SELECT COALESCE(MAX(id), 0) FROM stream_log WHERE ts < ?", (now - RETENTION_S,)).fetchone()[0]
    over = con.execute("SELECT COALESCE(MIN(id), 0) FROM (SELECT id FROM stream_log ORDER BY id DESC LIMIT ?)",
                       (KEEP_ROWS,)).fetchone()[0] - 1
    through = max(old, over, 0)
    if through > pruned_through(con):
        con.execute("DELETE FROM stream_log WHERE id <= ?", (through,))
        db.set_cursor(con, PRUNED, through)


class Subscription:
    """One connection's stream: a bounded queue a writer thread drains into the connection."""

    def __init__(self, send, close, topics, agent) -> None:
        self.send, self.close, self.topics, self.agent = send, close, set(topics), agent
        self.replay: collections.deque = collections.deque()    # what a resume missed: up to REPLAY_MAX, outside the live bound
        self.queue: collections.deque = collections.deque()     # live messages, bounded by MAX_QUEUE
        self.last = 0                       # the newest cursor queued: nothing at or below it is queued twice
        self.cond = threading.Condition()
        self.dead = False
        self.gap: tuple[str, int] | None = None

    def start(self) -> None:
        """Begin writing; the connection calls this once the reply to `subscribe` is sent, so no notice
        comes before it."""
        threading.Thread(target=self._run, daemon=True, name="ocp-stream-writer").start()

    def wants(self, ev: dict) -> bool:
        return ev["topic"] in self.topics and (not self.agent or ev["agent"] in (None, self.agent))

    def offer(self, ev: dict) -> None:
        with self.cond:
            if self.dead or ev["cursor"] <= self.last:
                return
            self.last = ev["cursor"]
            if not self.wants(ev):
                return
            if len(self.queue) >= MAX_QUEUE:
                self.queue.clear()
                self.gap = ("slow_client", ev["cursor"])
                threading.Timer(2.0, self.close).start()        # a writer stuck on a client that stopped reading
            else:
                self.queue.append({"jsonrpc": "2.0", "method": "event", "params": ev})
            self.cond.notify()

    def preload(self, events: list[dict], notice: tuple[str, dict] | None = None) -> None:
        """Queue what a resume missed, ahead of everything live. It is not held to MAX_QUEUE: a client
        reconnecting 5000 events behind is normal, and its backlog is not it falling behind."""
        with self.cond:
            if notice:
                self.replay.append({"jsonrpc": "2.0", "method": notice[0], "params": notice[1]})
            for ev in events:
                if ev["cursor"] > self.last and self.wants(ev):
                    self.replay.append({"jsonrpc": "2.0", "method": "event", "params": ev})
                self.last = max(self.last, ev["cursor"])
            self.cond.notify()

    def end(self) -> None:
        with self.cond:
            self.dead = True
            self.cond.notify()

    def _run(self) -> None:
        while True:
            with self.cond:
                while not (self.replay or self.queue or self.gap or self.dead):
                    self.cond.wait()
                if self.dead and not self.gap:
                    return
                src = self.replay or self.queue
                gap, msg = self.gap, (src.popleft() if src and not self.gap else None)
            try:
                if gap:
                    self.send({"jsonrpc": "2.0", "method": "gap", "params": {"reason": gap[0], "cursor": gap[1]}})
                    self.close()
                    return
                self.send(msg)
            except OSError:
                self.end()
                return


class Hub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.subs: set[Subscription] = set()

    def subscribe(self, sub: Subscription, con, since_cursor: int | None) -> int:
        """Register `sub`, first queueing what it missed after `since_cursor`. Atomic against `publish`, so
        nothing is missed or sent twice between the replay and the live stream."""
        with self.lock:
            top = newest(con)
            if since_cursor is not None:
                missed = since(con, since_cursor, sub.topics, sub.agent) if since_cursor >= pruned_through(con) else None
                if missed is None or len(missed) > REPLAY_MAX:
                    sub.preload([], ("gap", {"reason": "retention", "cursor": top}))
                else:
                    sub.preload(missed)
            sub.last = max(sub.last, top)
            self.subs.add(sub)
        return top

    def drop(self, sub: Subscription) -> None:
        with self.lock:
            self.subs.discard(sub)
        sub.end()

    def publish(self, ev: dict) -> None:
        with self.lock:
            for sub in list(self.subs):
                sub.offer(ev)


HUB = Hub()
_emit_lock = threading.Lock()


def emit(con, topic: str, type: str, data: dict, **kw) -> dict:
    """Append one event and hand it to the live subscribers, in cursor order."""
    with _emit_lock:
        ev = append(con, topic, type, data, **kw)
        HUB.publish(ev)
    return ev


# ---------------------------------------------------------------- detectors
# One per source of change. Each is called with the watcher's connection and its own state dict, and calls `emit`.
def _agents(con, state: dict) -> None:
    now = {r["agent"]: r["view"] for r in con.execute("SELECT agent, view FROM agent_view")}
    before = state.get("seen")
    state["seen"] = now
    if before is None:                      # the first look is what a client's snapshot already shows
        return
    for name, view in now.items():
        if before.get(name) != view:
            v = json.loads(view)
            emit(con, "agents", "agent.changed", v, agent=name, title=None, target={"screen": "agent", "id": name})
            if v["state"] == "FATAL" and (json.loads(before[name])["state"] if name in before else None) != "FATAL":
                emit(con, "notifications", "stall", {"agent": name}, agent=name, title=f"{name} stalled",
                     target={"screen": "agent", "id": name})
    for name in set(before) - set(now):
        emit(con, "agents", "agent.removed", {"name": name}, agent=name)


_NOTIFY_KINDS = {"mark_task_done": "task_done", "login_expired": "account_out"}


def _cursor_or_prime(con, name: str, newest_sql: str) -> int:
    """Where a detector last looked. A database it has never looked in starts at its newest row: what
    happened before the stream existed is not news."""
    if db.cursor_updated(con, name) is None:
        db.set_cursor(con, name, con.execute(newest_sql).fetchone()[0])
    return db.get_cursor(con, name)


def _notifications(con, state: dict) -> None:
    """What happened since the last look, from the events and prompts the other writers leave. The place it
    reached is a cursor in the database, so what happened while the daemon was down is said when it is back."""
    last = _cursor_or_prime(con, "stream:events", "SELECT COALESCE(MAX(id), 0) FROM events")
    kinds = tuple(_NOTIFY_KINDS)
    for row in con.execute(f"SELECT * FROM events WHERE id > ? AND kind IN ({','.join('?' * len(kinds))}) ORDER BY id",
                           (last, *kinds)).fetchall():
        payload = json.loads(row["payload"]) if row["payload"] and row["payload"][0] == "{" else {}
        if row["kind"] == "mark_task_done":
            tid = payload.get("task_id")
            if payload.get("done", True) and tid is not None:       # the spec's data is {task}: a mark that names no task has nothing to say
                emit(con, "notifications", "task_done", {"task": tid}, agent=row["agent"], title=f"task {tid} done",
                     target={"screen": "task", "id": str(tid)})
        else:
            acct = payload.get("account") or row["account"]
            emit(con, "notifications", "account_out", {"account": acct}, agent=row["agent"],
                 title=f"{acct} needs logging in", target={"screen": "account", "id": str(acct)})
        db.set_cursor(con, "stream:events", row["id"])      # per row: an emit that fails does not repeat the ones before it
    seen = _cursor_or_prime(con, "stream:prompts", "SELECT COALESCE(MAX(id), 0) FROM prompt_blocks")
    for row in con.execute("SELECT * FROM prompt_blocks WHERE id > ? ORDER BY id", (seen,)).fetchall():
        emit(con, "notifications", "prompt_asked", rpc_agents.prompt_of(prompt_blocks.of_row(row)), agent=row["agent"],
             title=f"{row['agent']} asks: {row['title']}", target={"screen": "agent", "id": row["agent"]})
        db.set_cursor(con, "stream:prompts", row["id"])


DETECTORS = [_agents, _notifications]


class Watcher:
    """Looks for change from any writer every WATCH_S, and runs the detectors when there is some."""

    def __init__(self, log) -> None:
        self.log, self.stop = log, threading.Event()
        self.state: list[dict] = [{} for _ in DETECTORS]
        self.thread: threading.Thread | None = None
        self.version = None
        self.last_prune = 0.0

    def scan(self, con) -> None:
        for fn, st in zip(DETECTORS, self.state):
            try:
                fn(con, st)
            except Exception as e:  # noqa: BLE001 - one detector failing is not the others' failure
                self.log(f"  stream: {fn.__name__}: {type(e).__name__}: {e}")
        if time.monotonic() - self.last_prune > 3600:
            self.last_prune = time.monotonic()
            try:
                prune(con)
            except Exception as e:  # noqa: BLE001 - retention is tried again in an hour
                self.log(f"  stream: prune: {type(e).__name__}: {e}")

    def changed(self, con) -> bool:
        v = con.execute("PRAGMA data_version").fetchone()[0]
        moved, self.version = v != self.version, v
        return moved

    def _run(self) -> None:
        con = None
        while not self.stop.is_set():
            try:
                if con is None:
                    con = db.connect()
                    self.version = None         # a new connection's first look is a change
                if self.changed(con):
                    self.scan(con)
                self.stop.wait(WATCH_S)
            except Exception as e:  # noqa: BLE001 - the watcher is every subscriber's only source: it must not end
                self.log(f"  stream: watcher: {type(e).__name__}: {e}")
                if con is not None:
                    try:
                        con.close()
                    except Exception:  # noqa: BLE001 - it is being replaced
                        pass
                    con = None
                self.stop.wait(RETRY_S)
        if con is not None:
            con.close()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="ocp-stream-watch")
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
