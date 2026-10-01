"""Tests: an account on one-month subscriptions (T743; ocp/usage/cycle.py, the end date in
status.pick_account / long_pace, ocp/daemon/subscriptions.py, `ocp account cycle`).

Run on its own: python3 tests/usage/test_cycle.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.cli import account as cliaccount  # noqa: E402
from ocp.daemon import subscriptions  # noqa: E402
from ocp import actions  # noqa: E402
from ocp.usage import cycle  # noqa: E402

DAY = 86400


def write_cfg(text: str) -> None:
    paths.config_file().write_text(text)


def local_midnight_after(day: str) -> float:
    return dt.datetime.combine(dt.date.fromisoformat(day) + dt.timedelta(days=1), dt.time()).timestamp()


def test_the_cycle_settings_read_and_default_to_off(home: Path) -> None:
    use_home(home)
    write_cfg('month_cycle_a: true\nsub_end_a: "2026-10-22"\nmonth_cycle_b: true\n'
              'sub_end_c: "2026-10-22"\nmonth_cycle_d: true\nsub_end_d: "2026-11-05"\n')
    check("cycle: off unless the account says on", not cycle.enabled("c") and not cycle.enabled("nobody"))
    check("cycle: the end date reads", cycle.end_date("a") == dt.date(2026, 10, 22)
          and cycle.end_date("d") == dt.date(2026, 11, 5))
    check("cycle: on with no date has no end", cycle.end_date("b") is None and cycle.end_of("b") is None)
    check("cycle: a date without the switch is ignored", cycle.end_date("c") is None)
    check("cycle: the account is usable through its last day, and out from the next midnight",
          cycle.end_of("a") == local_midnight_after("2026-10-22"))
    write_cfg("month_cycle_a: true\nsub_end_a: not-a-date\n")
    check("cycle: a date that does not parse is no end, not a crash", cycle.end_of("a") is None)
    check("cycle: the warning window defaults to three days and last week first to on",
          cycle.warn_days() == 3 and cycle.last_week_first())
    write_cfg("cycle_warn_days: 5\ncycle_last_week_first: false\n")
    check("cycle: both are settings", cycle.warn_days() == 5 and not cycle.last_week_first())
    write_cfg("cycle_warn_days: -1\n")
    check("cycle: a nonsense warning window falls back", cycle.warn_days() == 3)


def test_set_cycle_keeps_every_other_setting(home: Path) -> None:
    use_home(home)
    write_cfg("active_cap_a: 2\n")
    cycle.set_cycle("a", True, dt.date(2026, 10, 22))
    cfg = cycle.config()
    check("set_cycle: writes the switch and the date", cfg["month_cycle_a"] is True
          and cycle.end_date("a") == dt.date(2026, 10, 22), str(cfg))
    check("set_cycle: keeps the other settings", cfg["active_cap_a"] == 2)
    cycle.set_cycle("a", False)
    check("set_cycle: off keeps the date for turning it on again",
          not cycle.enabled("a") and cycle.config()["sub_end_a"] == "2026-10-22")
    cycle.set_cycle("a", True)
    check("set_cycle: on again finds the date", cycle.end_date("a") == dt.date(2026, 10, 22))


def test_an_ended_account_is_not_picked_and_not_removed(home: Path) -> None:
    use_home(home)
    now = int(time.time())
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    write_cfg(f"month_cycle_gone: true\nsub_end_gone: \"{yesterday}\"\n"
              f"month_cycle_open: true\nsub_end_open: \"{tomorrow}\"\n"
              "month_cycle_nodate: true\n")
    for a in ("gone", "open", "nodate", "plain"):
        write_status(home, a, pct_5h=5, pct_7d=10, reset_7d=now + 3 * DAY)
    picked, reasons = status.pick_account(["gone", "open", "nodate", "plain"])
    said = dict(reasons)
    check("ended: never picked", picked != "gone", str(picked))
    check("ended: says it ended, on which day, and how to set the next",
          "subscription ended" in said["gone"] and yesterday in said["gone"]
          and "ocp account cycle gone --end" in said["gone"], said["gone"])
    check("ended: the account is still registered", "gone" in paths.list_accounts())
    check("ended: through its last day it is still usable", "ended" not in said["open"], said["open"])
    check("ended: with no date it is not judged ended", "ended" not in said["nodate"], said["nodate"])
    check("ended: an account off the cycle is untouched", "ended" not in said["plain"], said["plain"])
    check("ended: the accounts panel's verdict is the same sentence",
          status.account_verdict("gone", status.read("gone"), None, reasons, True)[0] is False)
    only, _ = status.pick_account(["gone"])
    check("ended: with nothing else the pick refuses", only is None, str(only))


def test_the_short_last_week_is_spent_first(home: Path) -> None:
    use_home(home)
    now = int(time.time())
    end = (dt.date.today() + dt.timedelta(days=1)).isoformat()      # usable until midnight after: ~1-2 days
    write_cfg(f"month_cycle_ending: true\nsub_end_ending: \"{end}\"\n")
    # Same week, same use: 40% with three days to the reset (4 of 7 days elapsed).
    write_status(home, "ending", pct_5h=5, pct_7d=40, reset_7d=now + 3 * DAY)
    write_status(home, "plain", pct_5h=5, pct_7d=40, reset_7d=now + 3 * DAY)
    st = status.read("ending")
    sub_end = cycle.end_of("ending")
    check("last week: read() carries the end date", st.sub_end == sub_end)
    plain = status.long_pace(status.read("plain"))
    ending = status.long_pace(st)
    check("last week: the account off the cycle keeps its pace (40 / 57.1)", abs(plain - 0.70) < 0.01, plain)
    start = now + 3 * DAY - 7 * DAY
    length, remaining = sub_end - start, sub_end - st._now()
    want = 40 / (100 * (length - remaining) / length)
    check("last week: the week is the shorter span from its start to the end date",
          abs(ending - want) < 0.01 and ending < plain, f"{ending} vs {want}")
    picked, _ = status.pick_account(["plain", "ending"])
    check("last week: the ending account goes first at equal use", picked == "ending", str(picked))

    write_cfg(f"month_cycle_ending: true\nsub_end_ending: \"{end}\"\ncycle_last_week_first: false\n")
    off = status.long_pace(status.read("ending"))
    check("last week: the setting off leaves the pace alone", abs(off - plain) < 1e-3, off)

    far = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    write_cfg(f"month_cycle_ending: true\nsub_end_ending: \"{far}\"\n")
    check("last week: an end after the reset changes nothing",
          abs(status.long_pace(status.read("ending")) - plain) < 1e-3)


def test_the_week_stops_mattering_at_the_end_date(home: Path) -> None:
    use_home(home)
    now = int(time.time())
    end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    write_cfg(f"month_cycle_a: true\nsub_end_a: \"{end}\"\n")
    write_status(home, "a", pct_5h=5, pct_7d=40, reset_7d=now + 3 * DAY)
    st = status.read("a")
    left = status.week_ends_in(st)
    check("deadline: the end date, when it comes before the reset",
          left is not None and left < 2 * DAY + 1 and left < 3 * DAY, str(left))
    write_cfg("")
    st = status.read("a")
    check("deadline: otherwise the reset", abs(status.week_ends_in(st) - 3 * DAY) < 5, str(status.week_ends_in(st)))
    write_status(home, "b", pct_5h=5, pct_7d=None, reset_7d=None)
    check("deadline: neither known is None", status.week_ends_in(status.read("b")) is None)


class _Conn:
    pass


def test_the_founder_is_told_once_before_the_end(home: Path) -> None:
    use_home(home)
    con = db.connect()
    now = int(time.time())
    today = dt.date.today()
    for a in ("soon", "far", "off"):
        write_status(home, a, pct_5h=5, pct_7d=10, reset_7d=now + DAY)

    def told() -> list:
        return actions.task_rows(con, owner=actions.HUMAN, state=list(actions.DELEGATED_STATES))

    def cfg(soon_days: int) -> None:
        write_cfg(f'month_cycle_soon: true\nsub_end_soon: "{(today + dt.timedelta(days=soon_days)).isoformat()}"\n'
                  f'month_cycle_far: true\nsub_end_far: "{(today + dt.timedelta(days=20)).isoformat()}"\n'
                  f'sub_end_off: "{(today + dt.timedelta(days=1)).isoformat()}"\n')

    cfg(2)
    for _ in range(3):
        subscriptions.sweep_endings(con, lambda s: None, now)
    rows = told()
    check("ending: the founder's board holds one task for the account inside the window, however many ticks",
          len(rows) == 1 and rows[0]["title"] == f"soon subscription ends {today + dt.timedelta(days=2)}",
          str([r["title"] for r in rows]))
    body = con.execute("SELECT body FROM requests WHERE target = ? ORDER BY id DESC", (db.HUMAN,)).fetchone()["body"]
    check("ending: the task says what happens after and how to extend",
          "not removed" in body and "ocp account cycle soon --end" in body, body)
    check("ending: it is the founder's own, open, as the home page counts 'for you'",
          rows[0]["owner"] == actions.HUMAN and rows[0]["state"] in actions.DELEGATED_STATES)

    cfg(1)
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("ending: a new end date is a new subscription and is told again",
          sorted(r["title"] for r in told())[0].startswith("soon subscription ends") and len(told()) == 2)

    cfg(-1)
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("ending: after the end it is shown ended, not warned again", len(told()) == 2)
    con.close()


def test_a_plan_is_asked_again_each_hour_off_the_tick(home: Path) -> None:
    use_home(home)
    con = db.connect()
    for a in ("a", "b"):
        write_status(home, a, pct_5h=5, pct_7d=10, reset_7d=int(time.time()) + DAY)
    plans = {"a": "pro", "b": "pro"}
    asked: list[str] = []
    real_rec, real_sub = subscriptions.accounts.recorded_subscription_type, subscriptions.accounts.subscription_type
    gate = threading.Event()

    def ask(acct, refresh=False):
        gate.wait(5)
        asked.append(acct)
        plans[acct] = plans_next.get(acct, plans[acct])
        return plans[acct]

    def tick(now):
        subscriptions.sweep_plans(con, lambda s: None, now)

    plans_next: dict[str, str] = {}
    try:
        subscriptions.accounts.recorded_subscription_type = lambda a: plans[a]
        subscriptions.accounts.subscription_type = ask
        now = 1_800_000_000
        tick(now)
        check("plan: the tick returns while the vendor is still being asked", asked == [] and subscriptions._asking.is_alive())
        tick(now + 5)
        gate.set()
        subscriptions.wait_for_plan_ask()
        check("plan: one account is asked, not two, while one is in flight", asked == ["a"], str(asked))
        tick(now + 5)
        subscriptions.wait_for_plan_ask()
        check("plan: the next tick asks the other", asked == ["a", "b"], str(asked))
        tick(now + 10)
        subscriptions.wait_for_plan_ask()
        check("plan: nobody is asked again inside the hour", asked == ["a", "b"], str(asked))
        plans_next["a"] = "max"
        tick(now + 3601)
        subscriptions.wait_for_plan_ask()
        check("plan: asked again after the hour, the oldest first", asked == ["a", "b", "a"], str(asked))
        check("plan: the new plan is not an event until a tick sees it",
              con.execute("SELECT COUNT(*) FROM events WHERE kind='plan_changed'").fetchone()[0] == 0)
        tick(now + 3602)
        rows = con.execute("SELECT * FROM events WHERE kind='plan_changed'").fetchall()
        check("plan: a changed plan is an event, once", len(rows) == 1 and '"max"' in rows[0]["payload"], str(len(rows)))
        tick(now + 3603)
        check("plan: and not again",
              con.execute("SELECT COUNT(*) FROM events WHERE kind='plan_changed'").fetchone()[0] == 1)
    finally:
        gate.set()
        subscriptions.accounts.recorded_subscription_type, subscriptions.accounts.subscription_type = real_rec, real_sub
    con.close()


def test_a_plan_question_that_fails_is_logged_not_swallowed(home: Path) -> None:
    use_home(home)
    logged: list[str] = []
    real = subscriptions.accounts.subscription_type

    def boom(acct, refresh=False):
        raise OSError("no claude")
    try:
        subscriptions.accounts.subscription_type = boom
        subscriptions._ask("a", logged.append)
        subscriptions.accounts.subscription_type = lambda acct, refresh=False: None
        subscriptions._ask("a", logged.append)
    finally:
        subscriptions.accounts.subscription_type = real
    check("plan: a failed question says so", any("failed: OSError" in l for l in logged), str(logged))
    check("plan: so does an account with no plan at all", any("no plan recorded" in l for l in logged), str(logged))


def test_the_end_date_bounds_the_week_in_the_runway_and_in_the_pick(home: Path) -> None:
    use_home(home)
    now = int(time.time())
    write_status(home, "a", pct_5h=5, pct_7d=99, reset_7d=now + 3 * DAY)
    costs = types.SimpleNamespace(long_per_short=lambda a: 1.0, per_s=lambda agent=None: 1e-3)
    ld = types.SimpleNamespace(account="a", burn=None, at_cap=False, costs=costs, active=0, cap=2)
    st = status.read("a")
    lasts = status.long_stretch_s(st, ld)          # headroom 1 point at 1e-3 a second: ~1000s, under the 30 min a start is worth
    check("end bounds: setup - the week alone would refuse a start", lasts is not None and lasts < 1800
          and status._no_long_room(st, ld) is not None and "resets in" in status._no_long_room(st, ld),
          str((lasts, status._no_long_room(st, ld))))
    st.sub_end = now + lasts + 600                 # ends after the stretch would: the refusal stands, in its own words
    why = status._no_long_room(st, ld)
    check("end bounds: an end later than the stretch still refuses, and says the subscription ends",
          why is not None and "ends in" in why and "resets in" not in why, str(why))
    st.sub_end = now + lasts / 2                   # the subscription ends first: nothing is lost by running out
    check("end bounds: an end sooner than the stretch is no reason to refuse", status._no_long_room(st, ld) is None)
    check("end bounds: and the runway column shows no week runway to run out of",
          status.long_runway_s(st, types.SimpleNamespace(account="a", burn=5e-4, at_cap=False, costs=costs)) is None)
    st.sub_end = None
    check("end bounds: without an end the same filling account runs out before the reset",
          status.long_runway_s(st, types.SimpleNamespace(account="a", burn=5e-4, at_cap=False, costs=costs)) is not None)
    write_cfg(f'month_cycle_a: true\nsub_end_a: "{(dt.date.today() + dt.timedelta(days=1)).isoformat()}"\n')
    picked, reasons = status.pick_account(["a"], load={"a": ld})
    check("end bounds: through the pick, the refusal is the subscription's end, in its words",
          picked is None and "ends in" in dict(reasons)["a"], str(reasons))


def test_account_cycle_command(home: Path) -> None:
    use_home(home)
    write_status(home, "a", pct_5h=5, pct_7d=10, reset_7d=int(time.time()) + DAY)

    def run_cli(state=None, end=None) -> tuple[int, str]:
        import io
        import contextlib
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cliaccount.cmd_account_cycle(argparse.Namespace(name="a", state=state, end=end))
        return rc, buf.getvalue() + err.getvalue()

    def run_json() -> tuple[int, str]:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cliaccount.cmd_account_list(argparse.Namespace(json=True))
        return rc, buf.getvalue()

    check("cycle cli: shows off", run_cli() == (0, "a: not on one-month subscriptions\n"))
    rc, out = run_cli("on")
    check("cycle cli: on without a date says so", rc == 0 and "no end date set" in out, out)
    rc, out = run_cli(end="2026-10-22")
    check("cycle cli: a date alone turns it on", rc == 0 and "last day 2026-10-22" in out
          and cycle.enabled("a"), out)
    rc, out = run_cli("off")
    check("cycle cli: off", rc == 0 and "not on" in out and not cycle.enabled("a"), out)
    import json as _json
    rc, out = run_json()
    check("cycle cli: list --json carries the switch and the date as iso or null",
          rc == 0 and _json.loads(out)[0]["month_cycle"] is False and _json.loads(out)[0]["subscription_end"] is None, out)
    run_cli(end="2026-10-22")
    rc, out = run_json()
    check("cycle cli: and the date as iso", _json.loads(out)[0]["subscription_end"] == "2026-10-22", out)
    run_cli("off")
    rc, out = run_cli(end="22/10")
    check("cycle cli: a bad date is refused and nothing is written", rc == 1 and "YYYY-MM-DD" in out
          and not cycle.enabled("a"), out)


def main() -> int:
    def tests(td):
        run(lambda: test_the_cycle_settings_read_and_default_to_off(Path(td) / 'c1'))
        run(lambda: test_set_cycle_keeps_every_other_setting(Path(td) / 'c2'))
        run(lambda: test_an_ended_account_is_not_picked_and_not_removed(Path(td) / 'c3'))
        run(lambda: test_the_short_last_week_is_spent_first(Path(td) / 'c4'))
        run(lambda: test_the_week_stops_mattering_at_the_end_date(Path(td) / 'c5'))
        run(lambda: test_the_founder_is_told_once_before_the_end(Path(td) / 'c6'))
        run(lambda: test_a_plan_is_asked_again_each_hour_off_the_tick(Path(td) / 'c7'))
        run(lambda: test_a_plan_question_that_fails_is_logged_not_swallowed(Path(td) / 'c10'))
        run(lambda: test_the_end_date_bounds_the_week_in_the_runway_and_in_the_pick(Path(td) / 'c9'))
        run(lambda: test_account_cycle_command(Path(td) / 'c8'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
