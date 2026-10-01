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
from ocp.infra import atomic  # noqa: E402
from ocp.usage import cycle, load  # noqa: E402

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
    for hour in range(3):                       # the text names the hours left, so only the memory keeps it to one
        subscriptions.sweep_endings(con, lambda s: None, now + hour * 3600)
    rows = told()
    check("ending: the founder's board holds one task for the account inside the window, however many hours pass",
          len(rows) == 1 and rows[0]["title"] == f"soon subscription ends {today + dt.timedelta(days=2)}",
          str([r["title"] for r in rows]))
    check("ending: and only one was ever opened - the memory, not a close and reopen each hour",
          con.execute("SELECT COUNT(*) FROM tasks WHERE owner = ?", (db.HUMAN,)).fetchone()[0] == 1)
    body = con.execute("SELECT body FROM requests WHERE target = ? ORDER BY id DESC", (db.HUMAN,)).fetchone()["body"]
    check("ending: the task says what happens after, how to extend, and that it closes itself",
          "not removed" in body and "ocp account cycle soon --end" in body and "closes by itself" in body, body)
    check("ending: it is the founder's own, open, as the home page counts 'for you'",
          rows[0]["owner"] == actions.HUMAN and rows[0]["state"] in actions.DELEGATED_STATES)
    first = rows[0]["id"]

    cfg(1)
    subscriptions.sweep_endings(con, lambda s: None, now)
    now_open = told()
    check("ending: a new end date closes the old notice and opens one for the new date",
          len(now_open) == 1 and now_open[0]["id"] != first
          and now_open[0]["title"] == f"soon subscription ends {today + dt.timedelta(days=1)}",
          str([r["title"] for r in now_open]))
    check("ending: the old one is done, with the daemon's reason",
          "closed by the daemon" in (db.get_task(con, first)["done_evidence"] or ""))

    cfg(-1)
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("ending: once the account has ended its notice is closed, not left open", told() == [])
    subscriptions.sweep_endings(con, lambda s: None, now + 3600)
    check("ending: and an ended account is never told again", told() == [])

    cfg(2)
    subscriptions.sweep_endings(con, lambda s: None, now)
    write_cfg("")
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("ending: turning the cycle off closes the notice too", told() == [])

    # What cannot be read is not "off", not "ended" and not "changed": the notice stays.
    cfg(2)
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("unreadable: setup - one open notice", len(told()) == 1)
    ever = con.execute("SELECT COUNT(*) FROM tasks WHERE owner = ?", (db.HUMAN,)).fetchone()[0]
    log: list[str] = []
    write_cfg(f'month_cycle_soon: true\nsub_end_soon: {today + dt.timedelta(days=2)}\n')    # an unquoted date does not parse
    check("unreadable: setup - the config really cannot be read", load.config_or_none() is None)
    subscriptions.sweep_endings(con, log.append, now)
    check("unreadable: a tick whose config cannot be read leaves the notice open and says so",
          len(told()) == 1 and any("unreadable" in l for l in log), str(log))
    write_cfg("month_cycle_soon: true\nsub_end_soon: not-a-date\n")
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("unreadable: an end date that does not parse leaves it open", len(told()) == 1)
    write_cfg("month_cycle_soon: true\n")
    subscriptions.sweep_endings(con, lambda s: None, now)
    check("unreadable: on the cycle with no end known (no profile) leaves it open", len(told()) == 1)
    write_cfg("month_cycle_soon: true\nsub_end_soon: \"" + str(today + dt.timedelta(days=2)) + "\"\n")
    subscriptions.sweep_endings(con, lambda s: None, now + 3600)
    check("unreadable: and once readable again it is the same one task, not a second",
          len(told()) == 1 and con.execute("SELECT COUNT(*) FROM tasks WHERE owner = ?", (db.HUMAN,)).fetchone()[0] == ever)
    # a told date that has passed closes it even when no end can be known any more
    write_cfg("month_cycle_soon: true\n")
    subscriptions.sweep_endings(con, lambda s: None, now + 4 * DAY)
    check("unreadable: a date already told and now past closes the notice, whatever else is unknown", told() == [])
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
    check("cycle cli: on without a date says so", rc == 0 and "no end date known" in out, out)
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


def write_profile(acct: str, oauth: dict | None = None, raw: str | None = None, **top) -> Path:
    cdir = vendors.config_dir(acct)
    cdir.mkdir(parents=True, exist_ok=True)
    f = cdir / ".claude.json"
    f.write_text(raw if raw is not None else json.dumps({"oauthAccount": oauth or {}, **top}))
    return f


CANARIES = ("SECRET-TOP", "me@example.com", "sk-ant-oat01-SECRETTOKEN", "ACCT-UUID-1234", "ORG-UUID-5678")


def test_the_profile_reader_returns_the_three_fields_and_nothing_else(home: Path) -> None:
    use_home(home)
    import contextlib
    import io
    drv = vendors.for_account("a")
    oauth = {"subscriptionCreatedAt": "2026-09-21T12:00:00.000000Z", "userRateLimitTier": "default_claude_max_5x",
             "organizationRateLimitTier": "default_claude_ai", "emailAddress": "me@example.com",
             "accountUuid": "ACCT-UUID-1234", "organizationUuid": "ORG-UUID-5678",
             "accessToken": "sk-ant-oat01-SECRETTOKEN", "billingType": "stripe_subscription"}
    f = write_profile("a", oauth, secret="SECRET-TOP")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        got = drv.profile(vendors.config_dir("a"))
    check("profile: only the start and the tiers come back", set(got) == {"subscription_started", "tiers"}, str(got))
    check("profile: the start is the instant, the tiers are both strings",
          got["subscription_started"] == dt.datetime(2026, 9, 21, 12, tzinfo=dt.timezone.utc).timestamp()
          and got["tiers"] == ["default_claude_max_5x", "default_claude_ai"], str(got))
    check("profile: nothing else from the file is in what it returns, prints or logs",
          not any(c in repr(got) + out.getvalue() + err.getvalue() for c in CANARIES), repr(got))

    # A field that held anything but its own shape is dropped, never passed on.
    f.write_text(json.dumps({"oauthAccount": {
        "subscriptionCreatedAt": "sk-ant-oat01-SECRETTOKEN", "userRateLimitTier": "sk-ant-oat01-SECRETTOKEN",
        "organizationRateLimitTier": "ORG-UUID-5678"}}))
    got = drv.profile(vendors.config_dir("a"))
    check("profile: a value of the wrong shape is dropped, not forwarded",
          got == {"subscription_started": None, "tiers": []}, str(got))
    f.write_text(json.dumps({"oauthAccount": {"userRateLimitTier": "x" * 65, "subscriptionCreatedAt": 12345}}))
    got = drv.profile(vendors.config_dir("a"))
    check("profile: a tier over 64 characters and a non-text date are dropped",
          got == {"subscription_started": None, "tiers": []}, str(got))
    for name, raw in (("not json", "{not json SECRET-TOP"), ("a list", "[]"), ("no account", '{"x": 1}')):
        f.write_text(raw)
        got = drv.profile(vendors.config_dir("a"))
        check(f"profile: {name} reads as no profile, without raising",
              got in ({}, {"subscription_started": None, "tiers": []}) and "SECRET" not in repr(got), str(got))
    f.unlink()
    check("profile: no file is {}", drv.profile(vendors.config_dir("a")) == {})
    f2 = write_profile("a", {"subscriptionCreatedAt": "2026-01-02T00:00:00Z"})
    first = drv.profile(vendors.config_dir("a"))
    f2.write_text(json.dumps({"oauthAccount": {"subscriptionCreatedAt": "2026-03-04T00:00:00Z", "x": "padding"}}))
    check("profile: a changed file is read again",
          drv.profile(vendors.config_dir("a"))["subscription_started"] != first["subscription_started"])
    check("profile: the file is still never linked, copied or created",
          ".claude.json" in drv.never_touch() and ".credentials.json" in drv.never_touch())


def test_the_plan_pools_by_tier_and_an_unknown_tier_is_never_guessed(home: Path) -> None:
    use_home(home)

    def plan(recorded, *tiers) -> str | None:
        d = paths.account_dir("a")
        d.mkdir(parents=True, exist_ok=True)
        if recorded:
            (d / "subscription.json").write_text(json.dumps({"subscriptionType": recorded}))
        elif (d / "subscription.json").exists():
            (d / "subscription.json").unlink()
        write_profile("a", {"userRateLimitTier": tiers[0] if tiers else None,
                            "organizationRateLimitTier": tiers[1] if len(tiers) > 1 else None})
        return accounts.plan_key("a")

    check("plan_key: Pro stays pro whatever the tier", plan("pro", "default_claude_ai") == "pro")
    check("plan_key: Max with a 5x tier is max_5x", plan("max", "default_claude_max_5x") == "max_5x")
    check("plan_key: Max with a 20x tier is max_20x, from either field",
          plan("max", "default_claude_ai", "default_claude_max_20x") == "max_20x")
    check("plan_key: Max with a multiple we do not know stays plain max", plan("max", "default_claude_max_7x") == "max")
    check("plan_key: Max with no tier stays plain max", plan("max") == "max")
    check("plan_key: Max with two different multiples is not guessed", plan("max", "default_claude_max_5x", "default_claude_max_20x") == "max")
    check("plan_key: a max tier on a plan recorded as pro does not change it", plan("pro", "default_claude_max_5x") == "pro")
    check("plan_key: nothing recorded is None", plan(None, "default_claude_max_5x") is None)
    write_profile("a", {"userRateLimitTier": "default_claude_max_20x"})
    (paths.account_dir("a") / "subscription.json").write_text(json.dumps({"subscriptionType": "max"}))
    check("plan_key: a plan changed in the profile shows on the next read", accounts.plan_key("a") == "max_20x")
    write_profile("a", {"userRateLimitTier": "default_claude_max_5x"})
    check("plan_key: and back", accounts.plan_key("a") == "max_5x")


def test_turning_on_a_cycle_whose_default_end_is_past_says_so(home: Path) -> None:
    use_home(home)
    import contextlib
    import io
    write_status(home, "a", pct_5h=5, pct_7d=10, reset_7d=int(time.time()) + DAY)

    def cli(state=None, end=None) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cliaccount.cmd_account_cycle(argparse.Namespace(name="a", state=state, end=end))
        return buf.getvalue()

    write_profile("a", {"subscriptionCreatedAt": "2024-08-31T06:00:00Z"})
    out = cli("on")
    check("cycle cli: a past default end is said plainly, with the way out",
          "default end 2024-09-30 is past - this account will not be picked" in out
          and "--end YYYY-MM-DD" in out, out)
    check("cycle cli: asking again says it again", "is past" in cli())
    out = cli(end=str(dt.date.today() + dt.timedelta(days=9)))
    check("cycle cli: a typed end clears the warning", "is past" not in out and "last day" in out, out)
    write_profile("a", {"subscriptionCreatedAt": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()})
    write_cfg("month_cycle_a: true\n")
    out = cli()
    check("cycle cli: a default end still ahead is not warned about", "is past" not in out and "a month after" in out, out)


def test_two_writers_never_share_a_temp_file(home: Path) -> None:
    use_home(home)
    f = home / "x.txt"
    names: list[str] = []
    real = Path.replace

    def spy(self, target):
        names.append(self.name)
        return real(self, target)

    Path.replace = spy
    try:
        atomic.write_text(f, "one")
        atomic.write_text(f, "two")
        check("atomic: each write has its own temp name", len(names) == 2 and names[0] != names[1], str(names))

        def boom(self, target):
            raise OSError("no")
        Path.replace = boom
        raised = False
        try:
            atomic.write_text(f, "three")
        except OSError:
            raised = True
    finally:
        Path.replace = real
    check("atomic: a failed move raises and leaves no temp file", raised and sorted(p.name for p in home.glob("x.txt*")) == ["x.txt"],
          str(sorted(p.name for p in home.glob("x.txt*"))))
    check("atomic: and the old text is kept", f.read_text() == "two")


def test_config_is_written_in_one_step_and_an_unreadable_one_is_not_empty(home: Path) -> None:
    use_home(home)
    check("config: a missing file reads as empty, and as {} not None", load.config_or_none() == {})
    write_cfg("")
    check("config: an empty file is empty", load.config_or_none() == {})
    load.write_config({"active_cap": 3, "month_cycle_a": True})
    check("config: written and read back", load.config_or_none() == {"active_cap": 3, "month_cycle_a": True})
    check("config: no half-written sibling is left behind",
          not paths.config_file().with_name(paths.config_file().name + ".tmp").exists())
    write_cfg("active_cap: [")
    check("config: text that does not parse is None, where the reader's own _config says {}",
          load.config_or_none() is None and load._config() == {})
    write_cfg("- a\n- b\n")
    check("config: a list is not a mapping", load.config_or_none() is None)
    for name, fn in (("set_cap", lambda: load.set_cap("a", 2)), ("set_cycle", lambda: cycle.set_cycle("a", True))):
        write_cfg("active_cap: 4\n")
        fn()
        check(f"config: {name} keeps the other settings and goes through the one writer",
              load.config_or_none().get("active_cap") in (4, 2), str(load.config_or_none()))


def test_the_default_end_is_a_month_after_the_subscription_started(home: Path) -> None:
    use_home(home)
    check("add_month: the day is kept", cycle.add_month(dt.date(2026, 9, 21)) == dt.date(2026, 10, 21))
    check("add_month: the year turns", cycle.add_month(dt.date(2026, 12, 15)) == dt.date(2027, 1, 15))
    check("add_month: a short month clamps", cycle.add_month(dt.date(2026, 1, 31)) == dt.date(2026, 2, 28)
          and cycle.add_month(dt.date(2028, 1, 31)) == dt.date(2028, 2, 29))
    write_profile("a", {"subscriptionCreatedAt": "2026-09-21T12:00:00Z"})
    start = dt.datetime.fromtimestamp(dt.datetime(2026, 9, 21, 12, tzinfo=dt.timezone.utc).timestamp()).date()
    write_cfg("")
    check("default end: off the cycle there is no end, whatever the profile says", cycle.end_date("a") is None)
    write_cfg("month_cycle_a: true\n")
    check("default end: on the cycle it is the start plus a month", cycle.end_date("a") == cycle.add_month(start)
          and cycle.end_is_default("a"), str(cycle.end_date("a")))
    write_cfg('month_cycle_a: true\nsub_end_a: "2026-12-24"\n')
    check("default end: a typed date wins", cycle.end_date("a") == dt.date(2026, 12, 24) and not cycle.end_is_default("a"))
    write_cfg("month_cycle_a: true\nsub_end_a: not-a-date\n")
    check("default end: a typo is no end, not the default", cycle.end_date("a") is None)
    write_profile("a", raw="not json")
    write_cfg("month_cycle_a: true\n")
    check("default end: a profile that cannot be read is no end", cycle.end_date("a") is None)
    write_profile("a", {"subscriptionCreatedAt": "2024-08-31T06:00:00Z"})
    check("default end: an old start is an ended account until the start changes or an end is typed",
          cycle.end_date("a") == dt.date(2024, 9, 30) and cycle.ended_why("a", time.time()) is not None)
    write_profile("a", {"subscriptionCreatedAt": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat()})
    check("default end: a month gone for ten days is the same", cycle.ended_why("a", time.time()) is not None)
    write_status(home, "a", pct_5h=5, pct_7d=10, reset_7d=int(time.time()) + 3 * DAY)
    write_status(home, "b", pct_5h=5, pct_7d=10, reset_7d=int(time.time()) + 3 * DAY)
    picked, reasons = status.pick_account(["a", "b"])
    check("default end: still not picked long after the lapse, and says why",
          picked == "b" and "subscription ended" in dict(reasons)["a"], str(reasons))
    check("default end: with nothing else the pick refuses rather than use it", status.pick_account(["a"])[0] is None)
    cycle.set_cycle("a", True, dt.date.today() + dt.timedelta(days=9))
    check("default end: a typed end brings it back", status.pick_account(["a"])[0] == "a")
    write_cfg("month_cycle_a: true\n")
    write_profile("a", {"subscriptionCreatedAt": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()})
    check("default end: a start five days ago ends in about a month", cycle.end_date("a") is not None
          and cycle.end_date("a") >= dt.date.today())
    # The default ends at the subscription's own time of day; a typed date is whole-day.
    when = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=25)
    write_profile("a", {"subscriptionCreatedAt": when.astimezone(dt.timezone.utc).isoformat()})
    want = dt.datetime.combine(cycle.add_month(when.date()), when.time())
    write_cfg("month_cycle_a: true\n")
    check("default end: the moment is the start's time of day a month on, not midnight",
          abs(cycle.end_of("a") - want.timestamp()) < 1 and cycle.end_date("a") == want.date(), str(cycle.end_of("a")))
    write_cfg('month_cycle_a: true\nsub_end_a: "' + str(want.date()) + '"\n')
    check("default end: a typed day is usable through all of it",
          cycle.end_of("a") == cycle.day_end(want.date()) and cycle.end_of("a") > want.timestamp())
    target = dt.datetime.now().replace(microsecond=0) - dt.timedelta(hours=2)                  # lapsed two hours ago
    lapsed = next(target - dt.timedelta(days=n) for n in range(27, 33)
                  if cycle.add_month((target - dt.timedelta(days=n)).date()) == target.date())
    write_profile("a", {"subscriptionCreatedAt": lapsed.astimezone(dt.timezone.utc).isoformat()})
    write_cfg("month_cycle_a: true\n")
    check("default end: lapsed a few hours ago it is an ended account that is not picked",
          cycle.end_of("a") is not None and cycle.end_of("a") < time.time()
          and cycle.ended_why("a", time.time()) is not None, str(cycle.end_of("a")))
    write_cfg("")
    check("default end: and not before it is turned on", cycle.ended_why("a", time.time()) is None)


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
        run(lambda: test_the_profile_reader_returns_the_three_fields_and_nothing_else(Path(td) / 'c11'))
        run(lambda: test_the_plan_pools_by_tier_and_an_unknown_tier_is_never_guessed(Path(td) / 'c12'))
        run(lambda: test_the_default_end_is_a_month_after_the_subscription_started(Path(td) / 'c13'))
        run(lambda: test_config_is_written_in_one_step_and_an_unreadable_one_is_not_empty(Path(td) / 'c14'))
        run(lambda: test_turning_on_a_cycle_whose_default_end_is_past_says_so(Path(td) / 'c15'))
        run(lambda: test_two_writers_never_share_a_temp_file(Path(td) / 'c16'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
