"""What each agent used of the accounts over a span, for the Stats tab's table.

Pure over a connection and a span: nothing here reads the clock, the filesystem or the network,
and nothing writes. Token counts come from `token_calls`, priced by `usage.prices`; an agent is
the one whose worktree holds a call's cwd. Anything derived from tokens and labelled `est_pct`
is a guess at a share of the week, not a reading.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import prices as pricing

UNATTRIBUTED = "(unattributed)"


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
