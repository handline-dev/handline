"""The numbers behind the Stats tab: recovery, burn, run-out, insight, token share.

Pure over a connection and a `now`: nothing here reads the clock, the
filesystem or the network, and nothing writes. A screen only draws these.

Usage comes from `usage_samples` (percent of the 5h and 7d windows) and token
counts from `token_calls`. Percent figures are what the vendor reported; anything
ocp derives from tokens and labelled `estimate` is a guess at that share, not a
reading.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import db
from . import replay, status
from ..infra import timefmt

# How far back samples are read: a week, the longest window a sample is judged over.
HISTORY_S = 7 * 86400
BURN_LOOKBACK = 24 * 3600
SHARE_WINDOWS = {"5h": 5 * 3600, "24h": 24 * 3600, "7d": 7 * 86400}
UNATTRIBUTED = "(unattributed)"


def _samples(con, account: str, since: int):
    return db.usage_samples(con, account, since=since)


def _accounts(con, since: int) -> list[str]:
    rows = con.execute("SELECT DISTINCT account FROM usage_samples WHERE ts >= ? ORDER BY account",
                       (since,)).fetchall()
    return [r["account"] for r in rows]


def _status(row, as_of: float):
    """The AccountStatus this sample held, judged as of `as_of` by status's own rules."""
    st = replay.status_from_row(row)
    st.as_of = as_of
    return st


def _measured(row, role: str) -> int | None:
    """The sample's figure when it was a real reading when it was taken: live or
    freshly carried, not stale, lost, unreadable or too old to trust. `role` is
    "short" or "long"."""
    st = _status(row, row["ts"])
    w, src = (st.short, st.short_source) if role == "short" else (st.long, st.long_source)
    return w.pct if src in ("live", "carried") else None


def _points(rows, role: str) -> list[tuple[int, int]]:
    out = []
    for r in rows:
        pct = _measured(r, role)
        if pct is not None:
            out.append((r["ts"], pct))
    return out


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
    """One account's shortest or longest window."""
    label: str                 # its length as a person says it: 5h, 7d
    minutes: int
    pct: int | None            # None: no reading, or its window has ended
    reset_at: int | None       # the next reset, when it is still ahead
    frees: int | None          # percentage points that clear at reset_at
    rate: float | None         # %/hour over the last 24h
    runs_out_at: int | None    # when it reaches 100% at that rate; now if it already has
    before_reset: bool | None  # runs out before reset_at (None: no forecast)
    blocked_since: int | None  # start of the current stretch at 100%


def _window(rows, role: str, now: int) -> Window:
    pts = _points(rows, role)
    last = rows[-1] if rows else None
    held = None
    if last is not None:
        held = status.windows_of(dict(last))[0 if role == "short" else 1]
    reset = held.reset if held else None
    pct = fresh = None
    if last is not None and (reset is None or reset > now):
        st = _status(last, now)
        if role == "short":
            fresh = st.short_source in ("live", "carried")
            pct = st.short_trusted if fresh or st.short_source == "quiet" else None
        else:
            # An old weekly figure is a floor, and a floor still blocks (status.py).
            pct = st.long_trusted
            fresh = st.long_source in ("live", "carried")
    reset_at = reset if pct is not None and reset else None
    rate = _burn([p for p in pts if p[0] >= now - BURN_LOOKBACK]) if fresh else None
    runs_out = before = None
    if pct is not None:
        if pct >= 100:
            runs_out, before = now, (True if reset_at else None)
        elif rate:
            runs_out = now + int((100 - pct) / rate * 3600)
            before = runs_out < reset_at if reset_at else None
    since = None
    if pct is not None and pct >= 100:
        for ts, p in reversed(pts):
            if p < 100:
                break
            since = ts
        since = since or now
    return Window(held.label if held else "?", held.minutes if held else 0, pct, reset_at,
                  pct if reset_at else None, rate, runs_out, before, since)


@dataclass
class AccountStats:
    account: str
    short: Window
    long: Window

    @property
    def blocked(self) -> bool:
        return any(w.blocked_since is not None for w in (self.short, self.long))

    @property
    def recovers_at(self) -> int | None:
        """When a blocked account is usable again: the last of its full windows' resets."""
        ends = [w.reset_at for w in (self.short, self.long) if w.blocked_since is not None]
        return max(ends) if ends and all(ends) else None


def account_stats(con, now: int) -> list[AccountStats]:
    since = now - max(BURN_LOOKBACK, HISTORY_S)
    out = []
    for acct in _accounts(con, since):
        rows = _samples(con, acct, since)
        out.append(AccountStats(acct, _window(rows, "short", now), _window(rows, "long", now)))
    return out


def recovery_schedule(con, now: int) -> list[dict]:
    """Per account, the next reset of each window and what clears then, soonest first."""
    rows = []
    for a in account_stats(con, now):
        for w in (a.short, a.long):
            if w.reset_at:
                rows.append({"account": a.account, "window": w.label, "reset_at": w.reset_at,
                             "frees_pct": w.frees})
    return sorted(rows, key=lambda r: (r["reset_at"], r["account"], r["window"]))


def burn_rates(con, now: int) -> dict[str, dict[str, float | None]]:
    """%/hour of each window over the last 24h, per account."""
    return {a.account: {a.short.label: a.short.rate, a.long.label: a.long.rate}
            for a in account_stats(con, now)}


def forecasts(con, now: int) -> list[dict]:
    """When each account reaches 100% of each window at its recent rate, and
    whether that comes before the window resets."""
    out = []
    for a in account_stats(con, now):
        for w in (a.short, a.long):
            out.append({"account": a.account, "window": w.label, "pct": w.pct, "rate": w.rate,
                        "runs_out_at": w.runs_out_at, "before_reset": w.before_reset,
                        "reset_at": w.reset_at})
    return out


# ------------------------------------------------------------ insight

def _clock(ts: int) -> str:
    return timefmt.stamp(ts, timefmt.CLOCK)


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
            at = max(w.blocked_since for a in blocked for w in (a.short, a.long)
                     if w.blocked_since is not None)
            return f"all accounts blocked at {clock(at)}{soonest}"
        names = ", ".join(a.account for a in blocked)
        return f"{len(blocked)} of {len(accts)} accounts blocked ({names}){soonest}"
    early = sorted((w.runs_out_at, a.account, w.label) for a in accts
                   for w in (a.short, a.long)
                   if w.runs_out_at and w.before_reset)
    if early:
        at, acct, n = early[0]
        return f"{acct} runs out of its {n} window at {clock(at)}, before it resets"
    return "every account has headroom until its next reset"


# ------------------------------------------------------------ graph series

def short_series(con, now: int) -> dict[str, dict]:
    """Per account: its shortest window's points over the last 24h, and the projected
    line from now at the recent rate, ending at 100%, its reset, or a window out."""
    since = now - BURN_LOOKBACK
    out = {}
    for a in account_stats(con, now):
        pts = _points(_samples(con, a.account, since), "short")
        proj: list[tuple[int, float]] = []
        w = a.short
        if w.pct is not None and w.rate:
            end = min(t for t in (w.runs_out_at, w.reset_at, now + w.minutes * 60) if t)
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


def _by_agent(con, lo: int, hi: int, trees) -> dict[tuple[str, str], dict[str, int]]:
    by: dict[tuple[str, str], dict[str, int]] = {}
    for r in con.execute(
            "SELECT account, cwd, inp, cc, cr, out, thinking FROM token_calls "
            "WHERE ts > ? AND ts <= ?", (lo, hi)):
        c = by.setdefault((agent_for_cwd(r["cwd"], trees), r["account"]),
                          dict.fromkeys(("inp", "cc", "cr", "out", "thinking"), 0))
        for f in c:
            c[f] += r[f]
    return by


def _shares(by) -> dict[tuple[str, str], float]:
    totals: dict[str, int] = {}
    for (_, acct), c in by.items():
        totals[acct] = totals.get(acct, 0) + sum(c.values())
    return {k: (sum(c.values()) / totals[k[1]] if totals[k[1]] else 0.0) for k, c in by.items()}


def token_share(con, now: int) -> list[Share]:
    """Each agent's tokens per account over 5h, 24h and 7d, largest first.

    The agent is the one whose worktree holds the call's cwd. `share` is exact
    arithmetic over what was logged in that sliding span. `est_pct` is an
    estimate: the agent's share of the account's tokens within the reported
    window's own span (reset_at - 5h/7d up to now) times that window's reported
    percent, because tokens are not what the plan charges. It is None when the
    reset is unknown or for 24h, which has no reported percent."""
    trees = _worktrees(con)
    stats = {a.account: a for a in account_stats(con, now)}
    out: list[Share] = []
    for wname, span in SHARE_WINDOWS.items():
        by = _by_agent(con, now - span, now, trees)
        share = _shares(by)
        own: dict[tuple[str, str], float] = {}
        pcts: dict[str, int] = {}
        for acct, a in stats.items():
            w = next((x for x in (a.short, a.long) if x.minutes * 60 == span), None)
            if w is not None and w.reset_at and w.pct is not None:
                pcts[acct] = w.pct
                mine = _by_agent(con, w.reset_at - span, now, trees)
                own.update(_shares({k: c for k, c in mine.items() if k[1] == acct}))
        for (agent, acct), c in by.items():
            est = own.get((agent, acct), 0.0) * pcts[acct] if acct in pcts else None
            out.append(Share(agent, acct, wname, sum(c.values()), c, share[(agent, acct)], est))
    return sorted(out, key=lambda s: (list(SHARE_WINDOWS).index(s.window), -s.tokens,
                                      s.agent, s.account))
