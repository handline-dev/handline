"""What each API call spent, per account, kept beside the percentages.

`usage_samples` holds only how full an account's windows are. What the plan
charges for is tokens, of four classes that are priced differently, and those
are in the Claude transcripts under the account's own `.claude/projects`. This
records them, one row per API call, so a fit of the percentages against the
tokens rests on every interval the daemon has seen instead of a handful.

Nothing in the picking rules reads these rows: they are measurement, not
policy, and stay that way until the fit that uses them is trusted. Only the
conversation logs are read - never anything else under the account directory.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import db, paths, vendors

# Kept longer than the samples they are fitted against (14 days).
RETENTION_DAYS = 60
# A transcript untouched for longer than this is not scanned at all: the first
# run after install backfills at most this far.
BACKFILL_DAYS = 14
# Bytes of transcript read per account per tick. The first run has hundreds of
# megabytes to catch up on; it does so a slice per tick rather than stalling one.
BYTES_PER_TICK = 4 * 1024 * 1024

_OFFSETS = "tokenoffsets:"


def _ts(stamp: str) -> int | None:
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return None


def _call(line: str):
    """(session, request, ts, model, cwd, side, inp, cc, cr, out, thinking) of
    one transcript line that carries a call's usage, else None."""
    if '"usage"' not in line:
        return None
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if not isinstance(o, dict) or o.get("type") != "assistant" or not o.get("requestId"):
        return None
    m = o.get("message") or {}
    u = m.get("usage")
    ts = _ts(o.get("timestamp") or "")
    model = str(m.get("model") or "")
    # A synthetic message (an error the client wrote) carries no usage of a call.
    if not isinstance(u, dict) or ts is None or not model or model.startswith("<"):
        return None
    d = u.get("output_tokens_details") or {}
    return (str(o.get("sessionId") or ""), str(o["requestId"]), ts, model,
            str(o.get("cwd") or ""), int(bool(o.get("isSidechain"))),
            int(u.get("input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
            int(u.get("cache_read_input_tokens") or 0), int(u.get("output_tokens") or 0),
            int((d.get("thinking_tokens") if isinstance(d, dict) else 0) or 0))


def _save(con, acct: str, rows: list) -> None:
    # One request is written as several lines while it streams, the later ones
    # with more output: the row keeps the largest.
    con.executemany(
        "INSERT INTO token_calls(account, session, req, ts, model, cwd, side, inp, cc, cr, out, thinking) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(account, session, req) DO UPDATE SET "
        "out=MAX(out, excluded.out), thinking=MAX(thinking, excluded.thinking), "
        "inp=MAX(inp, excluded.inp), cc=MAX(cc, excluded.cc), cr=MAX(cr, excluded.cr)",
        [(acct,) + r for r in rows])


def ingest(con, acct: str, budget: int = BYTES_PER_TICK, now: int | None = None) -> int:
    """Read what the account's transcripts gained since the last call, up to
    `budget` bytes, and record it. Returns the lines that parsed as a call,
    which is more than the rows written when one streamed over several. A
    file is read from where it stopped, and only up to its last whole line."""
    now = int(time.time()) if now is None else now
    try:
        root = vendors.for_account(acct).memory_root(vendors.config_dir(acct))
    except vendors.UnknownVendor:
        return 0
    if not root.is_dir():
        return 0
    try:
        offsets = json.loads(db.get_meta(con, _OFFSETS + acct) or "{}")
    except ValueError:
        offsets = {}
    if not isinstance(offsets, dict):
        offsets = {}
    fresh = now - BACKFILL_DAYS * 86400
    todo = []
    for f in root.rglob("*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        key = str(f.relative_to(root))
        done = int(offsets.get(key, 0))
        if st.st_size > done and (key in offsets or st.st_mtime >= fresh):
            todo.append((st.st_mtime, key, f, done))
    todo.sort(reverse=True)
    n = 0
    for _, key, f, done in todo:
        if budget <= 0:
            break
        try:
            with open(f, "rb") as fh:
                fh.seek(done)
                chunk = fh.read(budget)
                end = chunk.rfind(b"\n") + 1
                if end == 0 and len(chunk) >= budget:
                    # the record ahead does not fit in what is left of this
                    # tick's budget; read it whole rather than stall this file
                    # forever. The overspend is bounded to that one record.
                    rest = fh.readline()
                    if rest.endswith(b"\n"):
                        chunk += rest
                        end = len(chunk)
        except OSError:
            continue
        if end == 0:
            continue
        budget -= end
        rows = [r for r in map(_call, chunk[:end].decode("utf-8", "replace").split("\n")) if r]
        if rows:
            _save(con, acct, rows)
            n += len(rows)
        offsets[key] = done + end
    db.set_meta(con, _OFFSETS + acct, json.dumps(offsets))
    return n


def prune(con, now: int | None = None) -> int:
    cutoff = (int(time.time()) if now is None else now) - RETENTION_DAYS * 86400
    return con.execute("DELETE FROM token_calls WHERE ts < ?", (cutoff,)).rowcount


def totals(con, account: str, lo: int, hi: int) -> dict[str, int]:
    """Tokens of each class the account spent in (lo, hi]."""
    r = con.execute(
        "SELECT COALESCE(SUM(inp),0) inp, COALESCE(SUM(cc),0) cc, COALESCE(SUM(cr),0) cr, "
        "COALESCE(SUM(out),0) out, COALESCE(SUM(thinking),0) thinking FROM token_calls "
        "WHERE account = ? AND ts > ? AND ts <= ?", (account, lo, hi)).fetchone()
    return {k: r[k] for k in ("inp", "cc", "cr", "out", "thinking")}
