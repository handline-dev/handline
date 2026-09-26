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


def main() -> int:
    def tests(td):
        run(lambda: test_prices(Path(td) / 'stats1'))
        run(lambda: test_agent_use_is_by_what_tokens_cost(Path(td) / 'stats2'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
