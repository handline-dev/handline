"""measure - what a cold start costs, from what the daemon watches happen.

Whether an agent at the wall is worth moving is a comparison against the price
of the session that starts on the other side, so the price is measured rather
than assumed: the share of the destination's 5h window that one new session
spends getting from nothing to the end of its first turn.

A measurement is taken only when nothing else was spending that account's
window at either end, because the account's usage is one number and a second
agent's work would be counted as the start's. That discards most starts on a
busy account and keeps the ones that can be trusted; until there are
`MIN_SAMPLES` of them the starting value in `triggers.STAY_WITHIN_S` stands.
"""
from __future__ import annotations

import json
import statistics
import time

from . import db, load, status

KEEP = 10
MIN_SAMPLES = 3


def _key(agent: str) -> str:
    return f"coldstart:{agent}"


def begin(con, agent: str, acct: str) -> None:
    """A session has just been started for `agent` on `acct` from nothing."""
    pct = status.read(acct).trusted_pct_5h
    if pct is None or load.active_agents(con).get(acct, 0):
        db.set_meta(con, _key(agent), None)
        return
    db.set_meta(con, _key(agent), json.dumps({"account": acct, "before": pct,
                                             "at": int(time.time())}))


def settle(con, agent: str) -> None:
    """Close a measurement once the agent's first turn has ended."""
    raw = db.get_meta(con, _key(agent))
    if not raw:
        return
    try:
        m = json.loads(raw)
    except ValueError:
        db.set_meta(con, _key(agent), None)
        return
    done = con.execute("SELECT 1 FROM events WHERE agent = ? AND kind = 'Stop' AND ts >= ? "
                       "LIMIT 1", (agent, m["at"])).fetchone()
    if not done:
        return
    db.set_meta(con, _key(agent), None)
    after = status.read(m["account"]).trusted_pct_5h
    others = load.active_agents(con).get(m["account"], 0)
    if after is None or others or after < m["before"]:
        return
    db.add_event(con, agent, "cold_start", {"account": m["account"], "before": m["before"],
                                            "after": after, "pct": after - m["before"]})


def cold_start_pct(con) -> float | None:
    """The middle of the recent measurements, or None while there are too few."""
    rows = con.execute("SELECT payload FROM events WHERE kind = 'cold_start' "
                       "ORDER BY id DESC LIMIT ?", (KEEP,)).fetchall()
    vals = []
    for r in rows:
        try:
            vals.append(float(json.loads(r["payload"])["pct"]))
        except (ValueError, KeyError, TypeError):
            continue
    return statistics.median(vals) if len(vals) >= MIN_SAMPLES else None
