"""burnrate - what one more agent costs an account, from what the daemon watches.

Whether an account can take one more agent depends on how long it lasts with
that agent on it, so the agent's cost is measured rather than assumed: the
share of an account's 5h window one agent spends per second while it works.

A sample is the account's rise over a stretch in which the same agents were
mid-turn from its first reading to its last, divided by how many they were. One
agent is a clean sample; several are their mean, and are filed under a model or
an effort only when all of them share it, because a rise cannot be split between
kinds. A stretch is dropped when a window rolled over inside it, or when it
held an agent's first turn since a SessionStart or moved_account - that turn's
cold start is `measure.py`'s to price, not a rate to fold into steady work.
Whether it rose is never a reason to drop one: keeping only the busy stretches
would price every agent higher than it is.

Each kind is priced by the median of its last `KEEP` samples once it has
`MIN_SAMPLES`; else its model's pool, else all agents', else the starting value
in `triggers.TYPICAL_AGENT_BURN_PER_S`. The window is fifteen because samples
are noisy (a point of resolution over ten minutes is 0.1%/min): simulated at a
true 0.5%/min, one new sample moves the median of five samples by more than
0.1%/min one time in twenty; with fifteen samples that same jump happens only
four times in a hundred. And it is a median, so one stretch that ran
while an agent sat on a long build does not move it.
"""
from __future__ import annotations

import json
import statistics
import time

from . import agents, db, triggers

KEEP = 15
MIN_SAMPLES = 5
KEEP_TOTAL = 300               # samples kept in all, oldest dropped

# A stretch is measured over at least this long, so one point of resolution in
# the reading is a small part of it, and closed at this long so a run that
# never changes hands still yields samples.
SEG_MIN_S = 600
SEG_MAX_S = 1800

# The estimate stays inside these. An agent that is mid-turn is never free, and
# an estimate that only ever came from a bad afternoon must not price every
# move out of reach; the room check is decision-affecting either way.
FLOOR_PER_S = 0.1 / 60
CEILING_PER_S = 2.0 / 60

_SAMPLES = "burnsamples"

# The weekly window against the 5h one: how many points of the week a point of
# the 5h window costs. Both are percentages of different-sized budgets, so the
# 5h cost of an agent says nothing about the week until this converts it, and
# no plan's ratio is written down anywhere ocp reads - it is measured, from the
# `usage_samples` the daemon keeps: the week's rise over the 5h window's rise,
# across consecutive live readings of one account inside one pair of windows.
# It is a property of the plan, so it is read over days, not hours.
WEEK_RATIO_WINDOW_S = 3 * 86400
# The week moves in whole points, so a ratio is only as good as the rise it
# was divided from: at ten points one point of resolution is a tenth of it.
# An account with less takes the pooled ratio of the accounts on its plan (the
# ratio is the plan's), and where that is under it too there is no ratio and
# the week is judged at its wall alone.
WEEK_RATIO_MIN_POINTS = 10
# The daemon recomputes it this often; a plan's ratio does not move by the tick.
WEEK_RATIO_EVERY_S = 600
_WEEK_RATIO = "weekratio"
POOLED = "*"


def _seg_key(acct: str) -> str:
    return f"burnseg:{acct}"


def kind_of(agent: str) -> tuple[str, str] | None:
    """(model, effort) as configured, or None for an agent that is not there."""
    try:
        cfg = agents.read(agent)
    except Exception:  # noqa: BLE001 - one agent's bad config is not the estimate's problem
        return None
    vendor, model, effort = agents.parts(cfg)
    if vendor != agents.DEFAULT_VENDOR:
        model = f"{vendor}/{model}"
    return model, effort


def _load_samples(con) -> list[dict]:
    try:
        rows = json.loads(db.get_meta(con, _SAMPLES) or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("r"), (int, float))]


def _emit(con, acct: str, seg: dict) -> None:
    span = seg["t1"] - seg["t0"]
    if span < SEG_MIN_S or seg["p1"] < seg["p0"] or seg.get("cold"):
        return
    kinds = [kind_of(a) for a in seg["agents"]]
    if not kinds or None in kinds:
        return
    models = {k[0] for k in kinds}
    efforts = {k[1] for k in kinds}
    sample = {"t": seg["t1"], "a": acct,
              "r": (seg["p1"] - seg["p0"]) / span / len(kinds),
              "m": next(iter(models)) if len(models) == 1 else None,
              "e": next(iter(efforts)) if len(models) == 1 and len(efforts) == 1 else None}
    db.set_meta(con, _SAMPLES, json.dumps(_load_samples(con)[-(KEEP_TOTAL - 1):] + [sample]))


def observe(con, acct: str, active: list[str], st, firsts: "set[str]" = frozenset()) -> None:
    """One tick's look at an account: `active` are the agents mid-turn on it,
    `st` its reading, and `firsts` those of them in their very first turn since
    a SessionStart or moved_account (`load.first_turn_agents`) - a cold start
    `measure.py` already prices, so it must not also be priced here as steady
    work. Extends the open stretch, or closes it when who is working changed or
    the window rolled over, and files a sample when it was long enough and
    none of it was a cold start."""
    raw = db.get_meta(con, _seg_key(acct))
    try:
        seg = json.loads(raw) if raw else None
    except ValueError:
        seg = None
    who = sorted(active)
    cold = bool(set(active) & firsts)
    live = st.is_live_reading
    if seg and seg["agents"] != who:
        _emit(con, acct, seg)
        seg = None
    elif seg and live and (st.pct_5h < seg["p1"] or st.reset_5h != seg["reset"]):
        # A reading below the last one, or a new reset time, is a rolled window:
        # nothing about the stretch before it is a rate.
        seg = None
    if who and live:
        if seg is None:
            seg = {"agents": who, "t0": st.rl_at, "p0": st.pct_5h,
                   "t1": st.rl_at, "p1": st.pct_5h, "reset": st.reset_5h, "cold": cold}
        else:
            seg["cold"] = seg.get("cold", False) or cold
            if st.rl_at > seg["t1"]:
                seg["t1"], seg["p1"] = st.rl_at, st.pct_5h
        if seg["t1"] - seg["t0"] >= SEG_MAX_S:
            _emit(con, acct, seg)
            seg = {**seg, "t0": seg["t1"], "p0": seg["p1"], "cold": cold}
    db.set_meta(con, _seg_key(acct), json.dumps(seg) if seg else None)


def _pool(plan: str | None) -> str:
    return POOLED + (plan or "")


def week_ratios(rows, plan_of=lambda _a: None) -> dict[str, float]:
    """Points of the week per point of the 5h window, by account and pooled
    per plan under `_pool(plan)`, from `usage_samples` rows in time order. Only a pair of
    consecutive rows of one account that are both live readings of both windows,
    inside the same 5h window and the same week, and in which neither figure
    fell, says anything: a rolled window, a carried figure or a gap does not."""
    totals: dict[str, list[int]] = {}
    last: dict[str, object] = {}

    def live(r) -> bool:
        return (r["pct_5h"] is not None and r["pct_7d"] is not None and r["pct_5h_readable"]
                and not (r["rl_carried"] or r["rl_lost"] or r["rl7_carried"] or r["rl7_lost"]))

    for r in rows:
        a = r["account"]
        prev = last.get(a)
        last[a] = r
        if prev is None or not (live(prev) and live(r)):
            continue
        if r["reset_5h"] != prev["reset_5h"] or r["reset_7d"] != prev["reset_7d"]:
            continue
        d5, d7 = r["pct_5h"] - prev["pct_5h"], r["pct_7d"] - prev["pct_7d"]
        if d5 < 0 or d7 < 0:
            continue
        for key in (a, _pool(plan_of(a))):
            t = totals.setdefault(key, [0, 0])
            t[0] += d5
            t[1] += d7
    return {k: d7 / d5 for k, (d5, d7) in totals.items()
            if d5 > 0 and d7 >= WEEK_RATIO_MIN_POINTS}


def refresh_week_ratios(con, now: int | None = None, force: bool = False) -> None:
    """Recompute the ratios from the last WEEK_RATIO_WINDOW_S of samples and keep
    them, at most every WEEK_RATIO_EVERY_S: readers take them from here, so a
    screen redrawing every couple of seconds never scans the samples."""
    now = int(time.time()) if now is None else now
    try:
        kept = json.loads(db.get_meta(con, _WEEK_RATIO) or "{}")
    except ValueError:
        kept = {}
    if not force and isinstance(kept, dict) and now - int(kept.get("at", 0)) < WEEK_RATIO_EVERY_S:
        return
    from . import accounts

    ratios = week_ratios(db.usage_samples(con, since=now - WEEK_RATIO_WINDOW_S),
                         accounts.recorded_subscription_type)
    db.set_meta(con, _WEEK_RATIO, json.dumps({"at": now, "r": ratios}))


def _load_week_ratios(con) -> dict[str, float]:
    try:
        kept = json.loads(db.get_meta(con, _WEEK_RATIO) or "{}")
        return {k: float(v) for k, v in kept.get("r", {}).items()}
    except (ValueError, AttributeError, TypeError):
        return {}


class Costs:
    """The estimate, read once from the samples and shared by every account's
    load, so a screen and the daemon price an agent the same."""

    def __init__(self, samples: list[dict], week_ratios: dict[str, float] | None = None,
                 plan_of=lambda _a: None):
        self._samples = samples
        self._week_ratios = week_ratios or {}
        self._plan_of = plan_of
        self._plans: dict[str, str | None] = {}

    def _plan(self, account: str) -> str | None:
        """The plan, looked up once per account: a redraw asks several times."""
        if account not in self._plans:
            self._plans[account] = self._plan_of(account)
        return self._plans[account]

    def week_per_5h(self, account: str) -> float | None:
        """Points of the week one point of the 5h window costs on this account:
        its own measured ratio, else its plan's pooled one, else None - nothing has
        measured it, and no 5h figure can be turned into the week's."""
        return self._week_ratios.get(account, self._week_ratios.get(_pool(self._plan(account))))

    def _median(self, pick) -> tuple[float, int] | None:
        vals = [s["r"] for s in reversed(self._samples) if pick(s)][:KEEP]
        if len(vals) < MIN_SAMPLES:
            return None
        return statistics.median(vals), len(vals)

    def describe(self, agent: str | None = None) -> tuple[float, str, int]:
        """(percent per second, where it came from, samples behind it)."""
        kind = kind_of(agent) if agent else None
        tiers = []
        if kind:
            tiers.append((f"{kind[0]} {kind[1]}",
                          lambda s: s.get("m") == kind[0] and s.get("e") == kind[1]))
            tiers.append((kind[0], lambda s: s.get("m") == kind[0]))
        tiers.append(("all agents", lambda s: True))
        for name, pick in tiers:
            found = self._median(pick)
            if found:
                return min(CEILING_PER_S, max(FLOOR_PER_S, found[0])), name, found[1]
        return triggers.TYPICAL_AGENT_BURN_PER_S, "starting value", 0

    def per_s(self, agent: str | None = None) -> float:
        return self.describe(agent)[0]

    def by_kind(self, names: list[str]) -> list[str]:
        """One line's worth per kind among `names`: what an agent of it is
        priced at and what that rests on."""
        seen: dict[tuple[str, str], str] = {}
        for n in names:
            k = kind_of(n)
            if k:
                seen.setdefault(k, n)
        out = []
        for (model, effort), n in sorted(seen.items()):
            per_s, source, count = self.describe(n)
            out.append(f"{model} {effort} {per_s * 60:.2f}%/min "
                       f"({f'{count} samples, {source}' if count else source})")
        return out


def costs(con) -> Costs:
    from . import accounts

    return Costs(_load_samples(con), _load_week_ratios(con), accounts.recorded_subscription_type)
