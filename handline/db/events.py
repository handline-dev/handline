"""The event log."""
from __future__ import annotations

import json
import time


# ---------------------------------------------------------------- events
def add_event(con, agent: str, kind: str, payload: dict | None = None,
              account: str | None = None, dedup_key: str | None = None, ts: int | None = None) -> int | None:
    """Append an event, stamped `ts` or now. Returns the row id, or None if deduped away."""
    # ON CONFLICT names dedup_key specifically. A blanket IntegrityError catch
    # here would also swallow NOT NULL violations, dropping events with no trace
    # and reporting them as if they had been deduped.
    cur = con.execute(
        "INSERT INTO events(ts, agent, account, kind, payload, dedup_key) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(dedup_key) DO NOTHING",
        (int(time.time()) if ts is None else ts, agent, account, kind,
         json.dumps(payload, ensure_ascii=False) if payload else None, dedup_key),
    )
    return cur.lastrowid if cur.rowcount else None


# Fold's own bookkeeping rows. They are kept for audit but excluded from the
# delta a fold reads, so a fold never ends up summarizing its own bookkeeping
# (and so the cursor cannot chase its own tail).
FOLD_META_KINDS = ("fold", "fold_failed")


def _only(clauses: list, params: list, only: tuple[str, ...] | None) -> None:
    """Restrict an events query to these agents; None is everyone."""
    if only is not None:
        clauses.append(f"agent IN ({','.join('?' * len(only))})")
        params.extend(only)


def events_after(con, row_id: int, agent: str | None = None, limit: int = 10000,
                 exclude_kinds: tuple[str, ...] = (), only: tuple[str, ...] | None = None):
    clauses = ["id > ?"]
    params: list = [row_id]
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    _only(clauses, params, only)
    if exclude_kinds:
        clauses.append(f"kind NOT IN ({','.join('?' * len(exclude_kinds))})")
        params.extend(exclude_kinds)
    params.append(limit)
    q = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?"
    return con.execute(q, params).fetchall()


def latest_events(con, agent: str | None = None, limit: int = 50,
                  only: tuple[str, ...] | None = None):
    """The most recent events, returned oldest first so they read downward.

    events_after() is the fold's cursor primitive and starts at the beginning
    on purpose. An operator asking what just happened wants the other end.
    """
    clauses: list = []
    params: list = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    _only(clauses, params, only)
    q = "SELECT * FROM events"
    if clauses:
        q += f" WHERE {' AND '.join(clauses)}"
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return list(reversed(con.execute(q, params).fetchall()))


def count_events(con, agent: str | None = None, after: int = 0,
                 only: tuple[str, ...] | None = None) -> int:
    clauses, params = [], []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    _only(clauses, params, only)
    if after:
        clauses.append("id > ?")
        params.append(int(after))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return int(con.execute(f"SELECT COUNT(*) AS c FROM events{where}", params).fetchone()["c"])


def max_event_id(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
    return int(row["m"])
