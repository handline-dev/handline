"""Tests: the Stats tab's data layer (ocp/stats.py), over a real db and a fixed clock.

Run on its own: python3 tests/test_stats.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp import stats  # noqa: E402

NOW = 1_800_000_000
H = 3600


def _sample(con, account, ago_h, p5=None, r5=None, p7=None, r7=None, **kw) -> None:
    con.execute(
        "INSERT INTO usage_samples(account, ts, exists_, pct_5h, reset_5h, pct_7d, reset_7d, "
        "rl_lost) VALUES(?,?,1,?,?,?,?,?)",
        (account, NOW - int(ago_h * H), p5, r5, p7, r7, kw.get("lost", 0)))


def _call(con, account, ago_h, cwd, n, i=[0]) -> None:
    i[0] += 1
    con.execute("INSERT INTO token_calls VALUES(?,?,?,?,?,?,0,?,0,0,0,0)",
                (account, "s", f"r{i[0]}", NOW - int(ago_h * H), "m", cwd, n))


def _fixture(home: Path):
    use_home(home)
    con = db.connect()
    # alpha: 5h at 40 and climbing 10/h, resets in 2h; 7d low.
    for ago, p in ((3, 10), (2, 20), (1, 30), (0, 40)):
        _sample(con, "alpha", ago, p5=p, r5=NOW + 2 * H, p7=5, r7=NOW + 3 * 86400)
    # beta: 5h reached 100 an hour ago and is still there; resets in 30 min.
    for ago, p in ((3, 70), (2, 90), (1, 100), (0, 100)):
        _sample(con, "beta", ago, p5=p, r5=NOW + H // 2, p7=50, r7=NOW + 86400)
    # gamma: a reset in the middle (60 -> 5), 5h resets in 4h.
    for ago, p in ((3, 40), (2, 60), (1, 5), (0, 15)):
        _sample(con, "gamma", ago, p5=p, r5=NOW + 4 * H, p7=20, r7=NOW + 86400)
    con.commit()
    return con


def test_recovery_and_burn(home: Path) -> None:
    con = _fixture(home)
    try:
        rec = stats.recovery_schedule(con, NOW)
        first = rec[0]
        check("stats: recovery is soonest first, with what frees",
              (first["account"], first["window"], first["frees_pct"]) == ("beta", "5h", 100), str(first))
        check("stats: every account and window has its reset",
              len(rec) == 6 and {r["window"] for r in rec} == {"5h", "7d"}, str(rec))
        rates = stats.burn_rates(con, NOW)
        check("stats: a steady climb is its slope per hour",
              abs(rates["alpha"]["5h"] - 10.0) < 1e-9, str(rates["alpha"]))
        # gamma: 40->60 (+20), reset to 5 (counts 5), 5->15 (+10) = 35 over 3h
        check("stats: a drop is a reset, not negative use",
              abs(rates["gamma"]["5h"] - 35 / 3) < 1e-9, str(rates["gamma"]))
        _sample(con, "solo", 0, p5=10, r5=NOW + H)
        con.commit()
        check("stats: one reading is no rate", stats.burn_rates(con, NOW)["solo"]["5h"] is None)
    finally:
        con.close()


def test_forecast_and_insight(home: Path) -> None:
    con = _fixture(home)
    try:
        f = {(r["account"], r["window"]): r for r in stats.forecasts(con, NOW)}
        a = f[("alpha", "5h")]
        check("stats: alpha reaches 100 in 6h at 10/h, after its reset in 2h",
              a["runs_out_at"] == NOW + 6 * H and a["before_reset"] is False, str(a))
        b = f[("beta", "5h")]
        check("stats: a blocked account has run out now", b["runs_out_at"] == NOW, str(b))
        g = f[("gamma", "5h")]
        check("stats: a forecast past the reset is not a threat",
              g["before_reset"] is False, str(g))

        line = stats.insight(con, NOW, clock=lambda t: f"T{(t - NOW) // 60:+d}")
        check("stats: some accounts blocked names them and the earliest recovery",
              line == "1 of 3 accounts blocked (beta); earliest recovery beta T+30", line)

        con.execute("DELETE FROM usage_samples WHERE account != 'beta'")
        line = stats.insight(con, NOW, clock=lambda t: f"T{(t - NOW) // 60:+d}")
        check("stats: all blocked says since when and the earliest recovery",
              line == "all accounts blocked at T-60; earliest recovery beta T+30", line)

        con.execute("DELETE FROM usage_samples")
        check("stats: no readings", stats.insight(con, NOW) == "no usage readings yet")
        for ago, p in ((2, 10), (1, 60), (0, 90)):
            _sample(con, "delta", ago, p5=p, r5=NOW + 4 * H)
        con.commit()
        line = stats.insight(con, NOW, clock=lambda t: f"T{(t - NOW) // 60:+d}")
        check("stats: a run-out before the reset is called out",
              line == "delta runs out of its 5h window at T+15, before it resets", line)
    finally:
        con.close()


def test_an_ended_window_and_unreadable_samples(home: Path) -> None:
    con = _fixture(home)
    try:
        # A window whose reset has passed describes nothing: no pct, no reset.
        _sample(con, "old", 0, p5=80, r5=NOW - 60)
        # An unreadable (lost) newest sample is skipped for the reading.
        _sample(con, "lossy", 1, p5=20, r5=NOW + H)
        _sample(con, "lossy", 0, p5=99, r5=NOW + H, lost=1)
        con.commit()
        st = {a.account: a for a in stats.account_stats(con, NOW)}
        check("stats: an ended window has no pct and no reset",
              st["old"].w5h.pct is None and st["old"].w5h.reset_at is None, str(st["old"].w5h))
        check("stats: a lost sample is not the reading", st["lossy"].w5h.pct == 20)
    finally:
        con.close()


def test_series(home: Path) -> None:
    con = _fixture(home)
    try:
        s = stats.pct5h_series(con, NOW)
        check("stats: series has the last 24h of points",
              [p for _, p in s["alpha"]["points"]] == [10, 20, 30, 40], str(s["alpha"]))
        proj = s["alpha"]["projected"]
        check("stats: the projection runs from now at the rate to the reset",
              proj == [(NOW, 40.0), (NOW + 2 * H, 60.0)], str(proj))
        proj = s["beta"]["projected"]
        check("stats: a blocked account projects flat at 100 to its reset",
              proj[0] == (NOW, 100.0) and proj[-1][1] == 100.0, str(proj))
    finally:
        con.close()


def test_token_share(home: Path) -> None:
    con = _fixture(home)
    try:
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES "
                    "('dev', 'IDLE', 0, 'repo1::/w/dev'), ('rev', 'IDLE', 0, '/w/rev'), "
                    "('dev2', 'IDLE', 0, '/w/dev-two')")
        _call(con, "alpha", 1, "/w/dev", 300)
        _call(con, "alpha", 1, "/w/dev/sub/dir", 100)
        _call(con, "alpha", 1, "/w/rev", 200)
        _call(con, "alpha", 1, "/w/dev-two", 400)      # not under /w/dev
        _call(con, "alpha", 1, "/elsewhere", 100)
        _call(con, "alpha", 10, "/w/rev", 1000)         # outside 5h, inside 24h
        _call(con, "alpha", 100, "/w/rev", 5000)        # inside 7d only
        _call(con, "beta", 1, "/w/dev", 50)
        con.commit()
        rows = stats.token_share(con, NOW)
        by = {(r.window, r.agent, r.account): r for r in rows}
        d = by[("5h", "dev", "alpha")]
        check("stats: a subdirectory of the worktree, and an id:: selector, count for the agent",
              d.tokens == 400, str(d))
        check("stats: a sibling that shares a prefix is another agent",
              by[("5h", "dev2", "alpha")].tokens == 400)
        check("stats: no worktree is unattributed",
              by[("5h", stats.UNATTRIBUTED, "alpha")].tokens == 100)
        check("stats: share is of the account's tokens in the window",
              abs(d.share - 400 / 1100) < 1e-9, str(d.share))
        check("stats: the split is per account", by[("5h", "dev", "beta")].share == 1.0)
        check("stats: the windows differ",
              by[("24h", "rev", "alpha")].tokens == 1200 and by[("7d", "rev", "alpha")].tokens == 6200)
        check("stats: a percent figure is labelled an estimate and is share x reported pct",
              d.estimate and abs(d.est_pct - d.share * 40) < 1e-9, str(d))
        check("stats: 24h has no reported percent to estimate from",
              by[("24h", "dev", "alpha")].est_pct is None)
        check("stats: largest first within a window",
              [r.tokens for r in rows if r.window == "5h"] == sorted(
                  [r.tokens for r in rows if r.window == "5h"], reverse=True))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_recovery_and_burn(Path(td) / 'stats1'))
        run(lambda: test_forecast_and_insight(Path(td) / 'stats2'))
        run(lambda: test_an_ended_window_and_unreadable_samples(Path(td) / 'stats3'))
        run(lambda: test_series(Path(td) / 'stats4'))
        run(lambda: test_token_share(Path(td) / 'stats5'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
