"""Tests: the Stats tab's data layer (ocp/usage/stats.py), over a real db and a fixed clock.

Run on its own: python3 tests/usage/test_stats.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.usage import prices, stats  # noqa: E402

NOW = 1_800_000_000
H = 3600


def _sample(con, account, ago_h, p5=None, r5=None, p7=None, r7=None, **kw) -> None:
    """A live reading taken `ago_h` hours before NOW; `rl_ago_h` makes the figure
    older than the sample, `carried` marks it carried, `lost` marks it lost."""
    ts = NOW - int(ago_h * H)
    seen = ts - int(kw.get("rl_ago_h", 0) * H)
    con.execute(
        "INSERT INTO usage_samples(account, ts, exists_, pct_5h, reset_5h, pct_7d, reset_7d, "
        "rl_lost, rl_at, rl_carried, rl7_at, rl7_carried, collected_at) "
        "VALUES(?,?,1,?,?,?,?,?,?,?,?,?,?)",
        (account, ts, p5, r5, p7, r7, kw.get("lost", 0), seen, kw.get("carried", 0),
         seen, kw.get("carried", 0), ts))


def _call(con, account, ago_h, cwd, n, i=[0]) -> None:
    i[0] += 1
    con.execute("INSERT INTO token_calls VALUES(?,?,?,?,?,?,0,?,0,0,0,0)",
                (account, "s", f"r{i[0]}", NOW - int(ago_h * H), "m", cwd, n))


def _priced_call(con, account, ago_h, cwd, model, inp=0, cc=0, cr=0, out=0, thinking=0, i=[0]) -> None:
    i[0] += 1
    con.execute("INSERT INTO token_calls VALUES(?,?,?,?,?,?,0,?,?,?,?,?)",
                (account, "p", f"p{i[0]}", NOW - int(ago_h * H), model, cwd, inp, cc, cr, out, thinking))


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
        # A lost newest sample is not a reading, whatever an earlier one said.
        _sample(con, "lossy", 1, p5=20, r5=NOW + H)
        _sample(con, "lossy", 0, p5=99, r5=NOW + H, lost=1)
        # The poll stopped 4h ago at 100% with the reset still ahead: not "blocked" now.
        for ago, p in ((5, 60), (4.5, 90), (4, 100)):
            _sample(con, "frozen", ago, p5=p, r5=NOW + H // 2, p7=10, r7=NOW + 86400)
        # Still being sampled, but the figure itself was seen 2h before the
        # newest sample and only carried since: too old to trust.
        _sample(con, "oldcarry", 1, p5=50, r5=NOW + H, carried=1)
        _sample(con, "oldcarry", 0, p5=100, r5=NOW + H, carried=1, rl_ago_h=2)
        # Carried, but recent enough to trust.
        _sample(con, "newcarry", 0, p5=30, r5=NOW + H, carried=1, rl_ago_h=0.1)
        con.commit()
        st = {a.account: a for a in stats.account_stats(con, NOW)}
        check("stats: an ended window has no pct and no reset",
              st["old"].short.pct is None and st["old"].short.reset_at is None, str(st["old"].short))
        check("stats: a lost sample is not the reading", st["lossy"].short.pct is None)
        w = st["frozen"].short
        check("stats: a reading gone stale is unknown: no pct, no forecast, not blocked",
              w.pct is None and w.runs_out_at is None and w.blocked_since is None
              and not st["frozen"].blocked, str(w))
        w = st["oldcarry"].short
        check("stats: a carried figure past the carry bound is unknown too",
              w.pct is None and not st["oldcarry"].blocked, str(w))
        check("stats: a recently carried figure is still a reading",
              st["newcarry"].short.pct == 30)
        check("stats: the insight does not call a frozen account blocked",
              "frozen" not in stats.insight(con, NOW), stats.insight(con, NOW))
    finally:
        con.close()


def test_series(home: Path) -> None:
    con = _fixture(home)
    try:
        s = stats.short_series(con, NOW)
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


def test_estimate_uses_the_windows_own_span(home: Path) -> None:
    con = _fixture(home)
    try:
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES "
                    "('before', 'IDLE', 0, '/w/before'), ('after', 'IDLE', 0, '/w/after')")
        # alpha's 5h window began 3h ago (reset in 2h). 'before' spent only in the
        # previous window, 'after' only in this one; both are inside the sliding 5h.
        _call(con, "alpha", 4, "/w/before", 900)
        _call(con, "alpha", 1, "/w/after", 100)
        con.commit()
        by = {(r.window, r.agent): r for r in stats.token_share(con, NOW) if r.account == "alpha"}
        check("stats: the sliding share still counts both agents",
              abs(by[("5h", "before")].share - 0.9) < 1e-9)
        check("stats: the estimate gives nothing to who spent before the window began",
              by[("5h", "before")].est_pct == 0.0, str(by[("5h", "before")]))
        check("stats: the estimate gives the whole reported percent to who spent in it",
              abs(by[("5h", "after")].est_pct - 40.0) < 1e-9, str(by[("5h", "after")]))
        con.execute("DELETE FROM usage_samples WHERE account = 'alpha'")
        _sample(con, "alpha", 0, p5=40)  # a reading with no reset: window start unknown
        con.commit()
        by = {(r.window, r.agent): r for r in stats.token_share(con, NOW) if r.account == "alpha"}
        check("stats: with no known window start there is no estimate",
              by[("5h", "after")].est_pct is None)
    finally:
        con.close()


def test_prices(home: Path) -> None:
    M = 1_000_000
    models = ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6",
              "claude-haiku-4-5-20251001", "claude-fable-5-1", "something-else", "")
    check("prices: a model is priced by its own entry before its family's",
          [prices.INPUT_PRICE[prices.pattern_of(m)] for m in models]
          == [4.0, 5.0, 5.0, 2.0, 3.0, 1.0, 10.0, 3.0, 3.0],
          str([prices.pattern_of(m) for m in models]))
    check("prices: a fable is not priced as a sonnet", prices.pattern_of("claude-fable-5") == "fable")
    check("prices: a mythos is not priced as a sonnet",
          [prices.pattern_of(m) for m in ("claude-mythos-5-1", "claude-mythos-5")]
          == ["mythos-5-1", "mythos"])

    def each(model, k):
        z = dict(inp=0, cc=0, cr=0, out=0, thinking=0)
        z[k] = M
        return round(prices.cost(model, **z), 6)
    check("prices: sonnet 5 is $2 in, $2.50 to write cache, $0.20 to read it, $10 out",
          [each("claude-sonnet-5", k) for k in ("inp", "cc", "cr", "out")] == [2.0, 2.5, 0.2, 10.0])
    check("prices: opus 5.5 reads its cache at $0.20 of its $4 - a twentieth, not a tenth",
          [each("claude-opus-5-5", k) for k in ("inp", "cc", "cr", "out")] == [4.0, 5.0, 0.2, 20.0])
    check("prices: opus 5 is $5, and its cache read a tenth of that",
          [each("claude-opus-5", k) for k in ("inp", "cr", "out")] == [5.0, 0.5, 25.0])
    check("prices: fable 5.1 reads at $0.25 of its $10",
          [each("claude-fable-5-1", k) for k in ("inp", "cr", "out")] == [10.0, 0.25, 50.0])
    check("prices: mythos 5.1 is priced as fable 5.1",
          [each("claude-mythos-5-1", k) for k in ("inp", "cr", "out")] == [10.0, 0.25, 50.0])
    check("prices: fable 5 and mythos 5 read at a tenth, as the reference gives $0.25 as new with 5.1",
          [each(m, "cr") for m in ("claude-fable-5", "claude-mythos-5")] == [1.0, 1.0])
    check("prices: so opus 5.5 costs twice sonnet 5 per input token",
          each("claude-opus-5-5", "inp") == 2 * each("claude-sonnet-5", "inp"))
    c = prices.class_costs("claude-opus-5-5", 0, 0, 0, 1000, 400)
    check("prices: thinking is part of output, so it is priced once and taken out of it",
          abs(c["thinking"] - 400 * 20 / M) < 1e-12 and abs(c["out"] - 600 * 20 / M) < 1e-12, str(c))
    t = prices.table_from_config({"token_prices": {"opus-5-5": 8, "haiku": "x", "sonnet": -1, "newmodel": 2}})
    check("prices: config.yaml sets a pattern's price in place, a new one goes first, a bad one is ignored",
          t["opus-5-5"] == 8.0 and t["haiku"] == 1.0 and t["sonnet"] == 3.0
          and next(iter(t)) == "newmodel" and prices.pattern_of("a-newmodel-opus", t) == "newmodel", str(t))
    check("prices: the override reaches the cost, cache read ratio kept",
          prices.cost("claude-opus-5-5", 100, 0, 0, 0, 0, t) == 2 * prices.cost("claude-opus-5-5", 100, 0, 0, 0, 0)
          and round(prices.cost("claude-opus-5-5", 0, 0, M, 0, 0, t), 6) == 0.4)


def test_share_is_by_what_tokens_cost(home: Path) -> None:
    con = _fixture(home)
    try:
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES "
                    "('reader', 'IDLE', 0, '/w/reader'), ('writer', 'IDLE', 0, '/w/writer')")
        # The reader has five times the writer's tokens, nearly all cache reads;
        # the writer's are output, which costs fifty times as much per token.
        _priced_call(con, "alpha", 1, "/w/reader", "claude-sonnet-5", cr=1000)
        _priced_call(con, "alpha", 1, "/w/writer", "claude-sonnet-5", out=200, thinking=50)
        con.commit()
        by = {(r.window, r.agent): r for r in stats.token_share(con, NOW) if r.account == "alpha"}
        r, w = by[("5h", "reader")], by[("5h", "writer")]
        check("stats: by count the reader is most of it", abs(r.token_share - 1000 / 1200) < 1e-9, str(r))
        check("stats: by cost the writer is most of it",
              abs(w.share - 1000 / 1100) < 1e-9 and abs(r.share - 100 / 1100) < 1e-9, f"{r.share} {w.share}")
        check("stats: tokens do not count thinking twice", w.tokens == 200 and w.classes["thinking"] == 50,
              str(w))
        check("stats: cost is per kind and the kinds add up",
              abs(sum(w.costs.values()) - w.cost) < 1e-12 and abs(w.cost - 200 * 5 * 2 / 1e6) < 1e-12,
              str(w.costs))
        check("stats: the estimate is on the cost basis",
              abs(w.est_pct - w.share * 40) < 1e-9 and abs(r.est_pct - r.share * 40) < 1e-9, f"{r.est_pct} {w.est_pct}")
        check("stats: the largest cost comes first, not the most tokens",
              [x.agent for x in stats.token_share(con, NOW) if x.window == "5h" and x.account == "alpha"]
              == ["writer", "reader"])
        cheap = {(x.window, x.agent): x for x in
                 stats.token_share(con, NOW, prices.table_from_config({"token_prices": {"sonnet-5": 20}}))
                 if x.account == "alpha"}
        check("stats: prices can be overridden, and shares do not move when every price does",
              abs(cheap[("5h", "writer")].cost - 10 * w.cost) < 1e-12
              and abs(cheap[("5h", "writer")].share - w.share) < 1e-9)
        # Models are priced apart: the same tokens on a dearer model are a bigger share.
        _priced_call(con, "alpha", 1, "/w/reader", "claude-opus-5-5", inp=1000)
        _priced_call(con, "alpha", 1, "/w/writer", "claude-haiku-4-5-20251001", inp=1000)
        con.commit()
        by = {(x.window, x.agent): x for x in stats.token_share(con, NOW) if x.account == "alpha"}
        check("stats: the same input tokens cost four times as much on opus 5.5 as on haiku",
              abs(by[("5h", "reader")].costs["inp"] - 4 * by[("5h", "writer")].costs["inp"]) < 1e-12,
              f"{by[('5h', 'reader')].costs} {by[('5h', 'writer')].costs}")
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_recovery_and_burn(Path(td) / 'stats1'))
        run(lambda: test_forecast_and_insight(Path(td) / 'stats2'))
        run(lambda: test_an_ended_window_and_unreadable_samples(Path(td) / 'stats3'))
        run(lambda: test_series(Path(td) / 'stats4'))
        run(lambda: test_token_share(Path(td) / 'stats5'))
        run(lambda: test_estimate_uses_the_windows_own_span(Path(td) / 'stats6'))
        run(lambda: test_prices(Path(td) / 'stats7'))
        run(lambda: test_share_is_by_what_tokens_cost(Path(td) / 'stats8'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
