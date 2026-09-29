"""Tests: the Stats tab's data layer (ocp/usage/stats.py) and the price list, over a real db.

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


def _priced_call(con, account, ago_h, cwd, model, inp=0, cc=0, cr=0, out=0, thinking=0, i=[0]) -> None:
    i[0] += 1
    con.execute("INSERT INTO token_calls VALUES(?,?,?,?,?,?,0,?,?,?,?,?)",
                (account, "p", f"p{i[0]}", NOW - int(ago_h * H), model, cwd, inp, cc, cr, out, thinking))


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


def test_agent_use_is_by_what_tokens_cost(home: Path) -> None:
    use_home(home)
    con = db.connect()
    try:
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES "
                    "('writer', 'IDLE', 0, '/w/writer'), ('reader', 'IDLE', 0, 'x::/w/reader')")
        _priced_call(con, "alpha", 1, "/w/writer/sub", "claude-opus-5-5", inp=1000)
        _priced_call(con, "alpha", 1, "/w/reader", "claude-haiku-4-5-20251001", inp=1000)
        _priced_call(con, "alpha", 1, "/elsewhere", "claude-haiku-4-5-20251001", inp=1000)
        _priced_call(con, "alpha", 30, "/w/writer", "claude-opus-5-5", inp=1000)   # before the span
        con.commit()
        rows = {u.agent: u for u in stats.agent_use(con, NOW - 24 * H, NOW)}
        check("stats: a call counts for the agent whose worktree holds its cwd, else unattributed; "
              "the Orca `id::path` selector is read as the path",
              set(rows) == {"writer", "reader", stats.UNATTRIBUTED}, set(rows))
        check("stats: the same input tokens cost four times as much on opus 5.5 as on haiku",
              abs(rows["writer"].cost - 4 * rows["reader"].cost) < 1e-12, (rows["writer"].cost, rows["reader"].cost))
        check("stats: a call before the span is left out and the shares add up to one",
              rows["writer"].tokens == 1000 and abs(sum(u.share for u in rows.values()) - 1) < 1e-9)
        check("stats: largest cost first", [u.agent for u in stats.agent_use(con, NOW - 24 * H, NOW)][0] == "writer")
        est = {u.agent: u.est_pct for u in stats.agent_use(con, NOW - 24 * H, NOW, None, {"alpha": 12.0})}
        check("stats: an agent's share of the week is its cost share of the account times the account's points",
              abs(sum(est.values()) - 12.0) < 1e-9 and abs(est["reader"] - est[stats.UNATTRIBUTED]) < 1e-9, est)
        check("stats: without the week's points there is no estimate",
              all(u.est_pct is None for u in stats.agent_use(con, NOW - 24 * H, NOW)))
    finally:
        con.close()


def _sample(con, account, ago_h, used, reset_h=48, legacy=False, lost=False) -> None:
    ts = NOW - int(ago_h * H)
    if legacy:      # written before windows were a list: the four names of claude's two
        con.execute("INSERT INTO usage_samples(account, ts, exists_, pct_5h, reset_5h, pct_7d, reset_7d) "
                    "VALUES(?,?,1,?,?,?,?)", (account, ts, 10, ts + 3600, used, NOW + reset_h * H))
        return
    wins = [{"minutes": 300, "used_percent": 10, "resets_at": ts + 3600, "lost": False},
            {"minutes": 10080, "used_percent": used, "resets_at": NOW + reset_h * H, "lost": lost}]
    con.execute("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)",
                (account, ts, json.dumps(wins)))


def test_the_outlook_reads_each_sample_once_and_says_the_same(home: Path) -> None:
    """T439: the stats tab walks the same samples three ways on every refresh; what a
    sample says about its week is parsed once, the rows are read once, and the
    numbers are exactly what reading it every time gives."""
    from ocp.usage import outlook, windows
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        for i in range(60):
            _sample(con, "alpha", 40 - i * 0.6, 20 + i * 0.7)
            _sample(con, "beta", 40 - i * 0.6, 5 + i * 0.3, reset_h=20, legacy=i % 4 == 0)
            if i % 7 == 0:
                _sample(con, "gamma", 40 - i * 0.6, 50, lost=True)      # unreadable: not a reading
        rows = db.usage_samples(con, since=NOW - 41 * H)
        times = [NOW - h * H for h in range(24, -1, -2)]

        def everything():
            return (outlook.weekly_outlook(rows, NOW, 24), outlook.week_consumed(rows, NOW - 24 * H, NOW),
                    outlook.consumed_curve(rows, times))

        outlook._WEEK_CACHE.clear()
        real_max, outlook._WEEK_CACHE_MAX = outlook._WEEK_CACHE_MAX, 0      # every sample read afresh
        try:
            cold = everything()
        finally:
            outlook._WEEK_CACHE_MAX = real_max
        outlook._WEEK_CACHE.clear()
        calls = []
        real_entries = windows.entries
        windows.entries = lambda raw: (calls.append(1), real_entries(raw))[1]
        try:
            first = everything()
            parsed_first = len(calls)
            second = everything()
            parsed_second = len(calls) - parsed_first
        finally:
            windows.entries = real_entries
        check("outlook: the fixture reads (an account, a legacy row and a lost one among them)",
              cold[0].accounts == 2 and cold[0].past and cold[0].future and cold[1] and cold[2], str(cold[0].accounts))
        check("outlook: with the cache the outlook is exactly what reading every sample gives",
              (first[0].past, first[0].future, first[0].accounts) == (cold[0].past, cold[0].future, cold[0].accounts)
              and first[1] == cold[1] and first[2] == cold[2])
        check("outlook: a second look at unchanged samples does not parse one of them again",
              parsed_first == len(rows) and parsed_second == 0, f"first {parsed_first} of {len(rows)}, second {parsed_second}")
        check("outlook: and says the same again", (second[0].past, second[1], second[2]) == (first[0].past, first[1], first[2]))

        # The layer above reads the rows once for all three, and again only for a new sample.
        reads = []
        real_samples = db.usage_samples
        db.usage_samples = lambda *a, **k: (reads.append(1), real_samples(*a, **k))[1]
        try:
            actions.details._SAMPLES["db"] = None
            o1 = actions.weekly_outlook(con, NOW, 24)
            actions.agent_use(con, NOW, 24)
            actions.agent_overlay(con, stats.AgentUse("x", 1, 1.0, 1.0, None, {"alpha": 1.0}), NOW, 24,
                                  [t for t, _ in o1.past])
            once = len(reads)
            _sample(con, "alpha", 0.1, 90)
            actions.weekly_outlook(con, NOW, 24)
            after_new = len(reads)
        finally:
            db.usage_samples = real_samples
        check("outlook: the three asks of one refresh read the samples once", once == 1, str(once))
        check("outlook: a sample written since is read", after_new == 2, str(after_new))

        # A window that has moved past old samples does not keep them: the cache holds
        # only what the current window can ask for, so a bridge left open does not grow.
        outlook._WEEK_CACHE.clear()
        actions.details._SAMPLES["db"] = None
        actions.weekly_outlook(con, NOW, 24)
        before = len(outlook._WEEK_CACHE)
        later = NOW + 30 * H
        _sample(con, "alpha", 0.2, 91)
        actions.weekly_outlook(con, later, 24)
        floor = actions.details._SAMPLES["floor"]
        check("outlook: the cache holds no sample older than the floor of what was read",
              before > 0 and all(k[0] >= floor for k in outlook._WEEK_CACHE)
              and len(outlook._WEEK_CACHE) < before, f"{before} -> {len(outlook._WEEK_CACHE)}, floor {floor}")
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_prices(Path(td) / 'stats1'))
        run(lambda: test_agent_use_is_by_what_tokens_cost(Path(td) / 'stats2'))
        run(lambda: test_the_outlook_reads_each_sample_once_and_says_the_same(Path(td) / 'stats3'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
