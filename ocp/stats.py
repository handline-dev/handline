"""The numbers behind the Stats tab: recovery, burn, run-out, insight, token share.

Pure over a connection and a `now`: nothing here reads the clock, the
filesystem or the network, and nothing writes. A screen only draws these.

Usage comes from `usage_samples` (percent of the 5h and 7d windows) and token
counts from `token_calls`. Percent figures are what the vendor reported; anything
ocp derives from tokens and labelled `estimate` is a guess at that share, not a
reading.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import db

WINDOWS = {"5h": 5 * 3600, "7d": 7 * 86400}
BURN_LOOKBACK = 24 * 3600
SHARE_WINDOWS = {"5h": 5 * 3600, "24h": 24 * 3600, "7d": 7 * 86400}
UNATTRIBUTED = "(unattributed)"


def _samples(con, account: str, since: int):
    return db.usage_samples(con, account, since=since)


def _accounts(con, since: int) -> list[str]:
    rows = con.execute("SELECT DISTINCT account FROM usage_samples WHERE ts >= ? ORDER BY account",
                       (since,)).fetchall()
    return [r["account"] for r in rows]


def _readable(row, window: str) -> bool:
    suffix = "5h" if window == "5h" else "7d"
    if row[f"pct_{suffix}"] is None or not row["exists_"]:
        return False
    if window == "5h":
        return bool(row["pct_5h_readable"]) and not row["rl_lost"]
    return not row["rl7_lost"]


def _points(rows, window: str) -> list[tuple[int, int]]:
    return [(r["ts"], r[f"pct_{window}"]) for r in rows if _readable(r, window)]


# ------------------------------------------------------------ burn rate

def _burn(points: list[tuple[int, int]]) -> float | None:
    """Percent of the window used per hour across `points`.

    A fall is a reset, not negative use: what the window reads right after
    one is what was spent since it, so that value is counted whole. None when
    there are fewer than two points or no time between them."""
    if len(points) < 2 or points[-1][0] <= points[0][0]:
        return None
    used = 0
    for (_, before), (_, after) in zip(points, points[1:]):
        used += after if after < before else after - before
    return used * 3600 / (points[-1][0] - points[0][0])


@dataclass
class Window:
    """One account's one window (5h or 7d)."""
    pct: int | None            # None: no reading, or its window has ended
    reset_at: int | None       # the next reset, when it is still ahead
    frees: int | None          # percentage points that clear at reset_at
    rate: float | None         # %/hour over the last 24h
    runs_out_at: int | None    # when it reaches 100% at that rate; now if it already has
    before_reset: bool | None  # runs out before reset_at (None: no forecast)
    blocked_since: int | None  # start of the current stretch at 100%


def _window(rows, window: str, now: int) -> Window:
    pts = _points(rows, window)
    last = next((r for r in reversed(rows) if _readable(r, window)), None)
    reset = last[f"reset_{window}"] if last else None
    live = last is not None and (reset is None or reset > now)
    pct = last[f"pct_{window}"] if live else None
    reset_at = reset if live and reset else None
    rate = _burn([p for p in pts if p[0] >= now - BURN_LOOKBACK])
    runs_out = before = None
    if pct is not None:
        if pct >= 100:
            runs_out, before = now, True if reset_at else None
        elif rate:
            runs_out = now + int((100 - pct) / rate * 3600)
            before = runs_out < reset_at if reset_at else None
    since = None
    if pct is not None and pct >= 100:
        for ts, p in reversed(pts):
            if p < 100:
                break
            since = ts
    return Window(pct, reset_at, pct if reset_at else None, rate, runs_out, before, since)


@dataclass
class AccountStats:
    account: str
    w5h: Window
    w7d: Window

    @property
    def blocked(self) -> bool:
        return any(w.blocked_since is not None for w in (self.w5h, self.w7d))

    @property
    def recovers_at(self) -> int | None:
        """When a blocked account is usable again: the last of its full windows' resets."""
        ends = [w.reset_at for w in (self.w5h, self.w7d) if w.blocked_since is not None]
        return max(ends) if ends and all(ends) else None


def account_stats(con, now: int) -> list[AccountStats]:
    since = now - max(BURN_LOOKBACK, WINDOWS["7d"])
    out = []
    for acct in _accounts(con, since):
        rows = _samples(con, acct, since)
        out.append(AccountStats(acct, _window(rows, "5h", now), _window(rows, "7d", now)))
    return out


def recovery_schedule(con, now: int) -> list[dict]:
    """Per account, the next 5h and 7d reset and what clears then, soonest first."""
    rows = []
    for a in account_stats(con, now):
        for name, w in (("5h", a.w5h), ("7d", a.w7d)):
            if w.reset_at:
                rows.append({"account": a.account, "window": name, "reset_at": w.reset_at,
                             "frees_pct": w.frees})
    return sorted(rows, key=lambda r: (r["reset_at"], r["account"], r["window"]))


def burn_rates(con, now: int) -> dict[str, dict[str, float | None]]:
    """%/hour of each window over the last 24h, per account."""
    return {a.account: {"5h": a.w5h.rate, "7d": a.w7d.rate} for a in account_stats(con, now)}


def forecasts(con, now: int) -> list[dict]:
    """When each account reaches 100% of each window at its recent rate, and
    whether that comes before the window resets."""
    out = []
    for a in account_stats(con, now):
        for name, w in (("5h", a.w5h), ("7d", a.w7d)):
            out.append({"account": a.account, "window": name, "pct": w.pct, "rate": w.rate,
                        "runs_out_at": w.runs_out_at, "before_reset": w.before_reset,
                        "reset_at": w.reset_at})
    return out


# ------------------------------------------------------------ insight

def _clock(ts: int) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def insight(con, now: int, clock=_clock) -> str:
    """One line for the top of the tab."""
    accts = account_stats(con, now)
    if not accts:
        return "no usage readings yet"
    blocked = [a for a in accts if a.blocked]
    if blocked:
        back = [(a.recovers_at, a.account) for a in blocked if a.recovers_at]
        soonest = f"; earliest recovery {min(back)[1]} {clock(min(back)[0])}" if back else ""
        if len(blocked) == len(accts):
            at = max(w.blocked_since for a in blocked for w in (a.w5h, a.w7d)
                     if w.blocked_since is not None)
            return f"all accounts blocked at {clock(at)}{soonest}"
        names = ", ".join(a.account for a in blocked)
        return f"{len(blocked)} of {len(accts)} accounts blocked ({names}){soonest}"
    early = sorted((w.runs_out_at, a.account, n) for a in accts
                   for n, w in (("5h", a.w5h), ("7d", a.w7d))
                   if w.runs_out_at and w.before_reset)
    if early:
        at, acct, n = early[0]
        return f"{acct} runs out of its {n} window at {clock(at)}, before it resets"
    return "every account has headroom until its next reset"


# ------------------------------------------------------------ graph series

def pct5h_series(con, now: int) -> dict[str, dict]:
    """Per account: its pct_5h points over the last 24h, and the projected line
    from now at the recent rate, ending at 100%, its reset, or 5h out."""
    since = now - BURN_LOOKBACK
    out = {}
    for a in account_stats(con, now):
        pts = _points(_samples(con, a.account, since), "5h")
        proj: list[tuple[int, float]] = []
        w = a.w5h
        if w.pct is not None and w.rate:
            end = min(t for t in (w.runs_out_at, w.reset_at, now + WINDOWS["5h"]) if t)
            end = max(end, now)
            proj = [(now, float(w.pct)),
                    (end, min(100.0, w.pct + w.rate * (end - now) / 3600))]
        out[a.account] = {"points": pts, "projected": proj}
    return out


# ------------------------------------------------------------ token share

@dataclass
class Share:
    agent: str
    account: str
    window: str
    tokens: int
    classes: dict[str, int] = field(default_factory=dict)
    share: float = 0.0          # of the account's tokens in the window
    est_pct: float | None = None  # share x the account's reported pct: an estimate
    estimate: bool = True


def _worktrees(con) -> list[tuple[str, str]]:
    """(directory, agent), longest directory first. The row may hold Orca's
    `id::path` selector, of which only the path is the directory."""
    rows = con.execute("SELECT agent, worktree FROM agent_state WHERE worktree IS NOT NULL "
                       "AND worktree != ''").fetchall()
    dirs = [(r["worktree"].split("::", 1)[-1].rstrip("/"), r["agent"]) for r in rows]
    return sorted(dirs, key=lambda d: -len(d[0]))


def agent_for_cwd(cwd: str, worktrees: list[tuple[str, str]]) -> str:
    cwd = cwd.rstrip("/")
    for d, agent in worktrees:
        if d and (cwd == d or cwd.startswith(d + "/")):
            return agent
    return UNATTRIBUTED


def token_share(con, now: int) -> list[Share]:
    """Each agent's tokens per account over 5h, 24h and 7d, largest first.

    The agent is the one whose worktree holds the call's cwd. `share` is exact
    arithmetic over what was logged; `est_pct` applies it to the account's
    reported 5h/7d percent and is an estimate, because tokens are not what the
    plan charges. The 24h window has no reported percent, so no `est_pct`."""
    trees = _worktrees(con)
    stats = {a.account: a for a in account_stats(con, now)}
    out: list[Share] = []
    for wname, span in SHARE_WINDOWS.items():
        rows = con.execute(
            "SELECT account, cwd, inp, cc, cr, out, thinking FROM token_calls "
            "WHERE ts > ? AND ts <= ?", (now - span, now)).fetchall()
        by: dict[tuple[str, str], dict[str, int]] = {}
        for r in rows:
            k = (agent_for_cwd(r["cwd"], trees), r["account"])
            c = by.setdefault(k, dict.fromkeys(("inp", "cc", "cr", "out", "thinking"), 0))
            for f in c:
                c[f] += r[f]
        totals: dict[str, int] = {}
        for (_, acct), c in by.items():
            totals[acct] = totals.get(acct, 0) + sum(c.values())
        for (agent, acct), c in by.items():
            n = sum(c.values())
            share = n / totals[acct] if totals[acct] else 0.0
            pct = None
            a = stats.get(acct)
            if a and wname in ("5h", "7d"):
                pct = (a.w5h if wname == "5h" else a.w7d).pct
            out.append(Share(agent, acct, wname, n, c, share,
                             None if pct is None else share * pct))
    return sorted(out, key=lambda s: (list(SHARE_WINDOWS).index(s.window), -s.tokens,
                                      s.agent, s.account))
