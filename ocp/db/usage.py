"""Usage samples, one row per account per tick."""
from __future__ import annotations

import sqlite3
import time


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
