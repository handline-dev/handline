"""burnrate - what one more agent costs an account, from what the daemon watches.

Whether an account can take one more agent depends on how long it lasts with
that agent on it, so the agent's cost is measured rather than assumed: the
share of an account's 5h window one agent spends per second while it works.

A sample is the account's rise over a stretch in which the same agents were
mid-turn from its first reading to its last, divided by how many they were. One
agent is a clean sample; several are their mean, and are filed under a model or
an effort only when all of them share it, because a rise cannot be split between
kinds. A stretch is dropped when a window rolled over inside it. Whether it
rose is never a reason to drop one: keeping only the busy stretches would price
every agent higher than it is.

Each kind is priced by the median of its last `KEEP` samples once it has
`MIN_SAMPLES`; else its model's pool, else all agents', else the starting value
in `triggers.TYPICAL_AGENT_BURN_PER_S`. The window is fifteen because samples
are noisy (a point of resolution over ten minutes is 0.1%/min): simulated at a
true 0.5%/min, one new sample moves the median of five by more than 0.1%/min one
time in twenty, of fifteen by 0.04. And it is a median, so one stretch that ran
while an agent sat on a long build does not move it.
"""
from __future__ import annotations

import json
import statistics

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
    if span < SEG_MIN_S or seg["p1"] < seg["p0"]:
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


def observe(con, acct: str, active: list[str], st) -> None:
    """One tick's look at an account: `active` are the agents mid-turn on it and
    `st` its reading. Extends the open stretch, or closes it when who is working
    changed or the window rolled over, and files a sample when it was long
    enough."""
    raw = db.get_meta(con, _seg_key(acct))
    try:
        seg = json.loads(raw) if raw else None
    except ValueError:
        seg = None
    who = sorted(active)
    live = st.pct_5h_source == "live" and st.pct_5h is not None and st.rl_at
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
                   "t1": st.rl_at, "p1": st.pct_5h, "reset": st.reset_5h}
        elif st.rl_at > seg["t1"]:
            seg["t1"], seg["p1"] = st.rl_at, st.pct_5h
        if seg["t1"] - seg["t0"] >= SEG_MAX_S:
            _emit(con, acct, seg)
            seg = {**seg, "t0": seg["t1"], "p0": seg["p1"]}
    db.set_meta(con, _seg_key(acct), json.dumps(seg) if seg else None)


class Costs:
    """The estimate, read once from the samples and shared by every account's
    load, so a screen and the daemon price an agent the same."""

    def __init__(self, samples: list[dict]):
        self._samples = samples

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
    return Costs(_load_samples(con))
