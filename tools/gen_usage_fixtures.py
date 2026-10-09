#!/usr/bin/env python3
"""Write go/internal/usage/testdata/status_cases.json: what the Python status module says about a fixed set of status files, so the Go
port is held to it. Run again after the Python changes; the clock and TZ are fixed so the output is the same every time.

    python3 tools/gen_usage_fixtures.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

NOW = 1_800_000_000
os.environ["TZ"] = "UTC"
time.tzset()
time.time = lambda: float(NOW)  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
home = Path(tempfile.mkdtemp(prefix="usagefix-")) / ".orcapipe"
(home / "accounts").mkdir(parents=True)
for _k in [k for k in os.environ if k.startswith("HANDLINE_")]:
    del os.environ[_k]
os.environ["OCP_HOME"] = str(home)
os.environ["OCP_TEST_ROOT"] = str(home.parent)  # a scratch run: paths.home() refuses the real home

from handline.usage import burnrate, load as loadmod, status  # noqa: E402
from handline.infra import paths  # noqa: E402


def win(minutes, pct, reset=None, at=None, carried=False, lost=False, readable=None):
    e = {"minutes": minutes, "used_percent": pct, "resets_at": reset, "observed_at": at, "carried": carried, "lost": lost}
    if readable is not None:
        e["readable"] = readable
    return e


def snap(short=None, long=None, collected=NOW - 10, **extra):
    d = {"windows": [w for w in (short, long) if w is not None]}
    if collected is not None:
        d["collected_at"] = collected
    d.update(extra)
    return d


def write_account(name: str, raw, config: dict | None = None):
    d = home / "accounts" / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "status.json"
    if raw is None:
        f.unlink(missing_ok=True)
    elif isinstance(raw, str):
        f.write_text(raw)
    else:
        f.write_text(json.dumps(raw))


def set_config(cfg: dict):
    (home / "config.yaml").write_text("".join(f"{k}: {json.dumps(v)}\n" for k, v in sorted(cfg.items())))  # a JSON scalar is a YAML one


def clean(v):
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
    return v


def window_out(w):
    return {"minutes": w.minutes, "pct": w.pct, "reset": w.reset, "at": w.at, "carried": w.carried, "lost": w.lost, "readable": w.readable}


def describe(st, ld):
    ok, why = st.assignable(0)
    tw = st.takes_work(1)
    out = {
        "exists": st.exists, "error": bool(st.error), "unmetered": st.unmetered,
        "short": window_out(st.short), "long": window_out(st.long),
        "short_source": st.short_source, "short_trusted": st.short_trusted,
        "long_source": st.long_source, "long_trusted": st.long_trusted, "stale": st.stale,
        "age_s": st.age_s, "short_age_s": st.short_age_s, "live": st.is_live_reading,
        "assignable": [ok, why.split(":")[0] if st.error else why], "assignable_active1": list(st.assignable(1)) if not st.error else None,
        "takes_work": [tw[0], tw[1].split(":")[0] if st.error else tw[1]],
        "rolled_over": list(st.rolled_over()), "idle_floor": list(st.idle_floor(0)), "last_known": st.last_known(),
        "shown_short": list(st.shown_short()), "window_report": st.window_report(),
        "short_headroom": st.short_headroom(), "long_headroom": st.long_headroom(),
        "long_pace": clean(status.long_pace(st)), "unfit": status.unfit(st, ld), "unfit_agent": status.unfit(st, ld, None),
        "trusted_ctx_pct": st.trusted_ctx_pct, "trusted_ctx_used": st.trusted_ctx_used, "ctx_pct": st.ctx_pct, "ctx_used": st.ctx_used,
        "session_id": st.session_id, "model_id": st.model_id, "cost_usd": st.cost_usd,
        "sub_end": st.sub_end, "last_week_first": st.last_week_first,
    }
    for burn in (None, 0.5 / 60):
        r = status.runway(st, burn, NOW, ld)
        out[f"runway_{'none' if burn is None else 'burn'}"] = status.describe_runway(r)
    return out


def mk_load(acct, spec):
    costs = burnrate.Costs([{"r": r} for r in spec.get("samples", [])], spec.get("ratios", {}))
    return loadmod.Load(acct, spec.get("active", 0), spec.get("cap", 2), spec.get("burn"), costs)


rng = random.Random(7)
PCTS = [None, 0, 1, 29, 30, 50, 69, 70, 71, 89.6, 99, 100, 101, "x", True, 28.999999999999996]
RESETS = [None, 0, NOW - 100, NOW - 5, NOW + 60, NOW + 3600, NOW + 86400 * 3, NOW + 9 * 86400]
ATS = [None, NOW - 5, NOW - 400, NOW - 1000, NOW - 5000, NOW + 500]
COLLECTED = [None, NOW - 10, NOW - 899, NOW - 901, NOW - 5000, NOW - 70000, NOW + 30, NOW + 500]


def fuzz_window(minutes):
    return win(minutes, rng.choice(PCTS), rng.choice(RESETS), rng.choice(ATS), rng.random() < .25, rng.random() < .1,
               rng.choice([None, None, None, False]))


cases = []


def add(name, raw, cfg=None, loadspec=None):
    set_config(cfg or {})
    write_account("a", raw)
    st = status.read("a")
    st.as_of = float(NOW)
    ld = mk_load("a", loadspec or {"active": 1, "cap": 2, "burn": 0.2 / 60, "samples": [0.01] * 6, "ratios": {"a": 2.0}})
    cases.append({"name": name, "status": raw, "config": cfg or {}, "load": loadspec or {"active": 1, "cap": 2, "burn": 0.2 / 60, "samples": [0.01] * 6, "ratios": {"a": 2.0}},
                  "expect": describe(st, ld)})


add("missing", None)
add("garbage", "{not json")
add("array", [1, 2])
add("empty", {})
add("legacy", {"pct_5h": 40, "reset_5h": NOW + 3600, "rl_at": NOW - 5, "pct_7d": 20, "reset_7d": NOW + 86400 * 3, "rl7_at": NOW - 5, "collected_at": NOW - 5})
add("quiet", snap(win(300, None, None, None), win(10080, 10, NOW + 86400 * 3, NOW - 5)))
add("live", snap(win(300, 40, NOW + 3600, NOW - 5), win(10080, 20, NOW + 86400 * 3, NOW - 5)))
add("one-window", snap(win(300, 40, NOW + 3600, NOW - 5)))
add("session-fields", snap(win(300, 40, NOW + 3600, NOW - 5), ctx_pct=33.2, ctx_used={"input": 1000, "out": 5, "x": "a", "b": True}, session_id="s1",
                           model_id="m", cost_usd=1.5, transcript_path="/t"))
add("ctx-bare", snap(win(300, 40, NOW + 3600, NOW - 5), ctx_used=1234))
add("week-over", snap(win(300, 40, NOW + 3600, NOW - 5), win(10080, 60, NOW - 10, NOW - 5)))
add("sub-end", snap(win(300, 40, NOW + 3600, NOW - 5), win(10080, 20, NOW + 86400 * 5, NOW - 5)), {"sub_end_a": "2027-01-15", "month_cycle_a": True})
add("sub-end-soon", snap(win(300, 40, NOW + 3600, NOW - 5), win(10080, 20, NOW + 86400 * 5, NOW - 5)), {"sub_end_a": "2027-01-15"})
add("sub-end-off", snap(win(300, 40, NOW + 3600, NOW - 5), win(10080, 20, NOW + 86400 * 5, NOW - 5)), {"sub_end_a": "2027-01-15", "month_cycle_a": False, "cycle_last_week_first": False})
add("no-load", snap(win(300, 40, NOW + 3600, NOW - 5), win(10080, 20, NOW + 86400 * 3, NOW - 5)), None, {"active": 0, "cap": 2, "samples": []})
for i in range(400):
    short = fuzz_window(300) if rng.random() < .9 else None
    long = fuzz_window(10080) if rng.random() < .7 else None
    raw = snap(short, long, rng.choice(COLLECTED))
    if rng.random() < .3:
        raw["ctx_pct"] = rng.choice([None, 10, 91.2, "x"])
        raw["ctx_used"] = rng.choice([None, 5000, {"a": 1, "b": 2}, "x"])
    ls = {"active": rng.choice([0, 0, 1, 2, 3]), "cap": rng.choice([1, 2, 3]), "burn": rng.choice([None, 0.0, 0.1 / 60, 0.5 / 60, 1.5 / 60]),
          "samples": rng.choice([[], [0.005] * 6, [0.02] * 8]), "ratios": rng.choice([{}, {"a": 1.5}, {"a": 8.0}])}
    add(f"fuzz-{i}", raw, rng.choice([{}, {}, {"sub_end_a": "2027-01-15", "month_cycle_a": True}]), ls)

# picks: several accounts, each with a status file, a load and a config
picks = []


def add_pick(name, accounts: dict, **kw):
    config = kw.pop("config", {})
    set_config(config)
    names = list(accounts)
    for n, spec in accounts.items():
        write_account(n, spec["status"])
    loads = {n: mk_load(n, a.get("load", {})) for n, a in accounts.items()}
    removing = kw.pop("removing", [])
    for n in removing:
        (home / "accounts" / n / ".removing").write_text("")
    for n, spec in accounts.items():
        v = spec.get("vendor")
        if v:
            (home / "accounts" / n / "vendor").write_text(v + "\n")

    def reader(n):
        st = status.read(n)
        st.as_of = float(NOW)
        return st
    picked, reasons = status.pick_account(kw.get("candidates", names), bootstrap=kw.get("bootstrap", False), exclude=kw.get("exclude"),
                                          load=loads, prefer=kw.get("prefer"), read_status=reader, vendor=kw.get("vendor"))
    picks.append({"name": name, "accounts": accounts, "candidates": names, "config": config, "removing": removing, "opts": {k: v for k, v in kw.items()},
                  "expect": {"picked": picked or "", "reasons": [list(r) for r in reasons]}})
    for n in removing:
        (home / "accounts" / n / ".removing").unlink()
    for n in accounts:
        (home / "accounts" / n / "vendor").unlink(missing_ok=True)


def acct(short_pct, week_pct, week_reset_in, short_reset_in=3600, collected=NOW - 10, **load):
    return {"status": snap(win(300, short_pct, NOW + short_reset_in, collected), win(10080, week_pct, NOW + week_reset_in, collected), collected=collected),
            "load": load}


add_pick("pace-wins", {"soon": acct(50, 10, 3600), "later": acct(5, 10, 90000)})
add_pick("closer-to-wall", {"close_to_wall": acct(5, 92, 3600), "safe": acct(5, 50, 90000)})
add_pick("both-near-wall", {"wall_a": acct(5, 91, 1000), "wall_b": acct(5, 93, 50)})
add_pick("tie-by-name", {"b": acct(10, 40, 90000), "a": acct(10, 40, 90000)})
add_pick("prefer-keeps-seat", {"a": acct(10, 40, 90000), "b": acct(10, 40.5 if False else 41, 90000)}, prefer="b")
add_pick("at-cap", {"a": acct(10, 40, 90000, active=2, cap=2), "b": acct(20, 40, 90000)})
add_pick("at-wall", {"a": acct(100, 40, 90000), "b": acct(100, 100, 90000)})
add_pick("stale", {"a": {"status": snap(win(300, 20, NOW + 3600, NOW - 2000), win(10080, 30, NOW + 90000, NOW - 2000), collected=NOW - 2000)},
                   "b": acct(10, 10, 90000)})
add_pick("stale-rolled-over", {"a": {"status": snap(win(300, 90, NOW - 100, NOW - 20000), win(10080, 30, NOW + 90000, NOW - 20000), collected=NOW - 20000)}})
add_pick("stale-idle-floor-near-wall", {"a": {"status": snap(win(300, 75, NOW + 3600, NOW - 2000), win(10080, 30, NOW + 90000, NOW - 2000), collected=NOW - 2000)}})
add_pick("bootstrap", {"a": {"status": None}, "b": {"status": None}}, bootstrap=True)
add_pick("no-bootstrap", {"a": {"status": None}})
add_pick("exclude-and-removing", {"a": acct(10, 40, 90000), "b": acct(10, 40, 90000), "c": acct(10, 40, 90000)}, exclude={"b": "just left"}, removing=["c"])
add_pick("other-vendor", {"a": acct(10, 40, 90000), "o": dict(acct(10, 40, 90000), vendor="opencode")})
add_pick("week-room", {"a": acct(10, 99, 200000, samples=[0.01] * 6, ratios={"a": 2.0}), "b": acct(10, 20, 90000)})
add_pick("unknown-week", {"a": {"status": snap(win(300, 20, NOW + 3600, NOW - 5), collected=NOW - 5)}, "b": {"status": snap(win(300, 10, NOW + 3600, NOW - 5), collected=NOW - 5)}})
add_pick("ended", {"a": acct(10, 40, 90000), "b": acct(10, 40, 90000)}, config={"sub_end_a": "2027-01-15", "month_cycle_a": True})
for i in range(60):
    accts = {}
    for n in "abcd"[: rng.randint(2, 4)]:
        s = rng.choice([None, 0, 20, 45, 80, 95, 100])
        w = rng.choice([None, 5, 30, 60, 92, 100])
        coll = rng.choice([NOW - 10, NOW - 10, NOW - 1500, NOW - 30000])
        accts[n] = {"status": snap(win(300, s, NOW + rng.choice([-50, 600, 3600]), coll), win(10080, w, NOW + rng.choice([-50, 5000, 90000, 400000]), coll), collected=coll),
                    "load": {"active": rng.choice([0, 1, 2]), "cap": rng.choice([2, 3]), "burn": rng.choice([None, 0.1 / 60, 0.8 / 60]),
                             "samples": rng.choice([[], [0.01] * 6]), "ratios": rng.choice([{}, {n: 2.0}])}}
    add_pick(f"fuzz-{i}", accts, prefer=rng.choice([None, "a", "b"]), bootstrap=rng.random() < .2)

out = ROOT / "go/internal/usage/testdata/status_cases.json"
out.write_text(json.dumps({"now": NOW, "cases": cases, "picks": picks}, indent=1, sort_keys=True, default=str) + "\n")
print(f"{len(cases)} cases, {len(picks)} picks -> {out}")
