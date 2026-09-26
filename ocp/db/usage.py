"""Usage samples, one row per account per tick."""
from __future__ import annotations

import json
import sqlite3
import time

from ..usage import windows


# ---------------------------------------------------------------- usage_samples

# At least a week, per #1113, plus enough margin that a slow week - a founder
# looking at "the last week" a few days after asking - still finds it there.
USAGE_SAMPLE_RETENTION_DAYS = 14


def record_usage_sample(con, account: str, st, ts: int | None = None) -> None:
    """One row of what `st` (an `AccountStatus`) actually held, at `ts` (now,
    by default). Every usage figure is written exactly as `st` has it -
    `None` stays `None`, never 0 - so a reader reconstructs the same
    AccountStatus this was taken from, not a guess at what it meant."""
    got = [w for w in (st.short, st.long) if w.minutes or not w.readable]
    con.execute(
        "INSERT INTO usage_samples(account, ts, exists_, error, collected_at, windows) "
        "VALUES(?,?,?,?,?,?)",
        (account, ts or int(time.time()), int(bool(st.exists)), st.error,
         st.collected_at, json.dumps([_entry(w) for w in got])),
    )


def _entry(w) -> dict:
    e = windows.entry(w.minutes, w.pct, w.reset, w.at, w.carried, w.lost)
    if not w.readable:
        e["readable"] = False
    return e


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
