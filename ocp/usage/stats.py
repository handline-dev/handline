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
from . import prices as pricing, replay, status
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
    """One agent's use of one account over one window, for a screen to draw.

    `share` and `est_pct` are on the cost basis (T45): what the tokens cost at
    list price, not how many there were. Cache reads are most of the tokens and
    a tenth of the price, so a count of them would say who reads most, not who
    spends most. `token_share` is the plain count's share, kept beside it."""
    agent: str
    account: str
    window: str
    tokens: int                  # logged: input, cache write, cache read, output (thinking is inside output)
    classes: dict[str, int] = field(default_factory=dict)   # tokens by kind; thinking is "of which" of out
    cost: float = 0.0            # USD at list price (prices.py)
    costs: dict[str, float] = field(default_factory=dict)   # cost by kind, summing to `cost`
    share: float = 0.0           # of the account's cost in the window
    token_share: float = 0.0     # of the account's tokens in the window
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


@dataclass
class _Tally:
    classes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_KINDS, 0))
    costs: dict[str, float] = field(default_factory=lambda: dict.fromkeys(_KINDS, 0.0))

    @property
    def tokens(self) -> int:
        return sum(v for k, v in self.classes.items() if k != "thinking")

    @property
    def cost(self) -> float:
        return sum(self.costs.values())


_KINDS = ("inp", "cc", "cr", "out", "thinking")


def _by_agent(con, lo: int, hi: int, trees, prices) -> dict[tuple[str, str], _Tally]:
    by: dict[tuple[str, str], _Tally] = {}
    for r in con.execute(
            "SELECT account, cwd, model, inp, cc, cr, out, thinking FROM token_calls "
            "WHERE ts > ? AND ts <= ?", (lo, hi)):
        t = by.setdefault((agent_for_cwd(r["cwd"], trees), r["account"]), _Tally())
        priced = pricing.class_costs(r["model"], r["inp"], r["cc"], r["cr"], r["out"],
                                     r["thinking"], prices)
        for k in _KINDS:
            t.classes[k] += r[k]
            t.costs[k] += priced[k]
    return by


def _shares(by, basis) -> dict[tuple[str, str], float]:
    """Each entry's fraction of its account's total, on `basis` (a _Tally -> number)."""
    totals: dict[str, float] = {}
    for (_, acct), t in by.items():
        totals[acct] = totals.get(acct, 0) + basis(t)
    return {k: (basis(t) / totals[k[1]] if totals[k[1]] else 0.0) for k, t in by.items()}


def _tokens_of(t: _Tally) -> int:
    return t.tokens


def _cost_of(t: _Tally) -> float:
    return t.cost


def token_share(con, now: int, prices: dict[str, float] | None = None) -> list[Share]:
    """Each agent's use per account over 5h, 24h and 7d, largest cost first.

    The agent is the one whose worktree holds the call's cwd. `share` is exact
    arithmetic over what was logged in that sliding span, priced by
    `usage.prices` (`prices` overrides the per-family input prices; see
    `prices.table_from_config`). `est_pct` is an estimate: the agent's cost share
    of the account within the reported window's own span (reset_at - 5h/7d up to
    now) times that window's reported percent, because what the plan charges is
    not tokens. It is None when the reset is unknown or for 24h, which has no
    reported percent."""
    trees = _worktrees(con)
    stats = {a.account: a for a in account_stats(con, now)}
    out: list[Share] = []
    for wname, span in SHARE_WINDOWS.items():
        by = _by_agent(con, now - span, now, trees, prices)
        share = _shares(by, _cost_of)
        by_tokens = _shares(by, _tokens_of)
        own: dict[tuple[str, str], float] = {}
        pcts: dict[str, int] = {}
        for acct, a in stats.items():
            w = next((x for x in (a.short, a.long) if x.minutes * 60 == span), None)
            if w is not None and w.reset_at and w.pct is not None:
                pcts[acct] = w.pct
                mine = _by_agent(con, w.reset_at - span, now, trees, prices)
                own.update(_shares({k: t for k, t in mine.items() if k[1] == acct}, _cost_of))
        for (agent, acct), t in by.items():
            est = own.get((agent, acct), 0.0) * pcts[acct] if acct in pcts else None
            out.append(Share(agent, acct, wname, t.tokens, t.classes, t.cost, t.costs,
                             share[(agent, acct)], by_tokens[(agent, acct)], est))
    return sorted(out, key=lambda s: (list(SHARE_WINDOWS).index(s.window), -s.cost, -s.tokens,
                                      s.agent, s.account))


@dataclass
class AgentUse:
    """One agent's use over a span, all accounts: what a screen lists under the weekly graph."""
    agent: str
    tokens: int
    cost: float                  # USD at list price
    share: float                 # of everyone's cost in the span
    est_pct: float | None = None  # points of the combined week it used: an estimate, see agent_use
    fracs: dict[str, float] = field(default_factory=dict)   # its cost share of each account in the span


def agent_use(con, lo: int, hi: int, prices: dict[str, float] | None = None,
              week_used: dict[str, float] | None = None) -> list[AgentUse]:
    """Each agent's tokens and list-price cost in (lo, hi], largest cost first.

    `week_used` is the points of each account's week used in the span (read from the
    samples). An agent's `est_pct` is its cost share of each account times that account's
    points, averaged over the accounts: an estimate, because what the plan charges is not
    what the price list says. None without `week_used`."""
    by: dict[str, _Tally] = {}
    per = _by_agent(con, lo, hi, _worktrees(con), prices)
    acct_cost: dict[str, float] = {}
    for (_, acct), t in per.items():
        acct_cost[acct] = acct_cost.get(acct, 0.0) + t.cost
    est: dict[str, float] = {}
    if week_used:
        for (agent, acct), t in per.items():
            if acct_cost[acct] and acct in week_used:
                est[agent] = est.get(agent, 0.0) + t.cost / acct_cost[acct] * week_used[acct] / len(week_used)
    for (agent, _), t in per.items():
        mine = by.setdefault(agent, _Tally())
        for k in _KINDS:
            mine.classes[k] += t.classes[k]
            mine.costs[k] += t.costs[k]
    total = sum(t.cost for t in by.values())
    fracs: dict[str, dict[str, float]] = {}
    for (agent, acct), t in per.items():
        if acct_cost[acct]:
            fracs.setdefault(agent, {})[acct] = t.cost / acct_cost[acct]
    rows = [AgentUse(a, t.tokens, t.cost, t.cost / total if total else 0.0,
                     est.get(a, 0.0) if week_used else None, fracs.get(a, {})) for a, t in by.items()]
    return sorted(rows, key=lambda r: (-r.cost, r.agent))


@dataclass
class UseRow:
    """A line of the agents' table: `key` names what a pick means (an agent, or `grp:<name>`
    for a whole group), `label` is what is drawn, `depth` 1 for a child under its group."""
    key: str
    label: str
    depth: int
    use: AgentUse


def group_uses(uses: list[AgentUse]) -> list[UseRow]:
    """The agents as a tree by the first part of their dotted name: `orcapipe` on top with the
    total of the group, then `self` (the agent `orcapipe` itself) and the rest, `orcapipe.ui`
    as `ui`. An agent whose group has nobody else is one plain row."""
    groups: dict[str, list[AgentUse]] = {}
    for u in uses:
        groups.setdefault(u.agent.split(".", 1)[0], []).append(u)
    rows: list[UseRow] = []
    for name, members in sorted(groups.items(), key=lambda kv: (-sum(u.cost for u in kv[1]), kv[0])):
        if len(members) == 1 and members[0].agent == name:
            rows.append(UseRow(name, name, 0, members[0]))
            continue
        fracs: dict[str, float] = {}
        for u in members:
            for acct, f in u.fracs.items():
                fracs[acct] = fracs.get(acct, 0.0) + f
        est = None if any(u.est_pct is None for u in members) else sum(u.est_pct for u in members)
        total = AgentUse(name, sum(u.tokens for u in members), sum(u.cost for u in members),
                         sum(u.share for u in members), est, fracs)
        rows.append(UseRow(f"grp:{name}", name, 0, total))
        for u in members:               # `uses` is largest cost first, and that order is kept
            rows.append(UseRow(u.agent, "self" if u.agent == name else u.agent[len(name) + 1:], 1, u))
    return rows
