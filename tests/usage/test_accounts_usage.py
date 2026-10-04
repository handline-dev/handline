"""Tests: account choice, usage readings, tokens, runway and the wall.

Run on its own: python3 tests/usage/test_accounts_usage.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import subscriptions  # noqa: E402




def test_pick_account_orders_by_weekly_pace(home: Path) -> None:
    """#1001, replacing the earliest-weekly-deadline rule (#166/#167/#169/
    #170/#254) this same function used to test: among accounts admitted by
    `takes_work`/`unfit`, the one furthest BEHIND its own weekly pace
    (`status.long_pace`) is picked - not simply the one with the most 5h room
    right now, and not the one whose reset_7d timestamp happens to be soonest
    regardless of what its pct_7d actually is."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())

    # "later" has more 5h headroom than "soon" - the old rule would pick it on
    # that alone - but "soon" is further behind its own pace: 10% used with an
    # hour left (almost the whole week already elapsed) is further behind than
    # 10% used with a day left (much less elapsed).
    write_status(home, "soon", pct_5h=50, pct_7d=10, reset_7d=now + 3600)
    write_status(home, "later", pct_5h=5, pct_7d=10, reset_7d=now + 90000)
    picked, reasons = status.pick_account(["soon", "later"])
    check("pick_account: further behind its own pace wins over more headroom",
          picked == "soon", str(picked))
    said = dict(reasons)
    check("pick_account: the winner's reason says it was picked",
          "picked" in said["soon"], said["soon"])

    # "close_to_wall" resets sooner than "safe" and sits near its own 7d wall,
    # but at 92% with its week almost entirely elapsed it is close to ON pace;
    # "safe" at 50% with most of the week still to run is well behind its own -
    # so "safe" is picked, not the one closer to its wall or to its reset.
    write_status(home, "close_to_wall", pct_5h=5, pct_7d=92, reset_7d=now + 3600)
    write_status(home, "safe", pct_5h=5, pct_7d=50, reset_7d=now + 90000)
    picked, _ = status.pick_account(["close_to_wall", "safe"])
    check("pick_account: further behind its own pace wins even when the "
          "nearer-wall account resets sooner",
          picked == "safe", str(picked))

    # Both near their own 7d wall, but not equally near their own pace:
    # wall_a (91% with 1000s left) is fractionally further behind than
    # wall_b (93% with 50s left, its week essentially fully elapsed).
    write_status(home, "wall_a", pct_5h=5, pct_7d=91, reset_7d=now + 1000)
    write_status(home, "wall_b", pct_5h=5, pct_7d=93, reset_7d=now + 50)
    picked, _ = status.pick_account(["wall_a", "wall_b"])
    check("pick_account: orders by pace even when both are near the wall",
          picked == "wall_a", str(picked))

    # An account whose 7d window has never been observed sorts behind a known
    # pace rather than jumping the queue - a '?' is not low usage.
    write_status(home, "never_seen_7d", pct_5h=5, pct_7d=None)
    picked, _ = status.pick_account(["never_seen_7d", "soon"])
    check("pick_account: a known pace beats an unknown one",
          picked == "soon", str(picked))

    # The old rule's real benefit, not given up: two accounts with the SAME
    # usage but different resets are not a tie under pace, because the one
    # closer to its reset has more of its week already elapsed - same
    # numerator, smaller denominator, lower pace. So "spend the one that
    # resets soonest" still holds in the ordinary case pace was built to
    # improve on, not just the disagreeing case pace was built to fix.
    write_status(home, "resets_soon", pct_5h=5, pct_7d=30, reset_7d=now + 7200)
    write_status(home, "resets_later", pct_5h=5, pct_7d=30, reset_7d=now + 3 * 86400)
    picked, _ = status.pick_account(["resets_soon", "resets_later"])
    check("pick_account: at equal usage, the sooner reset still wins - pace "
          "does not give up the old rule's real case, only its bad one",
          picked == "resets_soon", str(picked))


def test_pace_matches_the_worked_examples_that_were_written_backwards(home: Path) -> None:
    """#1001/#1036: the two worked examples of "ahead" and "behind" its own
    pace came from orcapipe, not from the founder, and orcapipe had the words
    backwards on both - three times, the last of them in the review request
    that asked for this check. Pinned as fixtures so nobody reads the words
    and implements them backwards again.
    Pin the NUMBERS the corrected reading settled on, not the words: 85% with
    one day elapsed (six left) is pace ~5.95 - the worst of the two, because
    burning 85% in a seventh of the week is far ahead of a uniform pace, not
    behind it - and 60% with the week 97.6% elapsed (four hours left) is pace
    ~0.615, comfortably behind, which is what "ahead" was meant to mean."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    write_status(home, "burns_fast", pct_5h=5, pct_7d=85, reset_7d=now + 6 * 86400)
    write_status(home, "comfortable", pct_5h=5, pct_7d=60, reset_7d=now + 4 * 3600)
    fast_pace = status.long_pace(status.read("burns_fast"))
    comfortable_pace = status.long_pace(status.read("comfortable"))
    check("founder's examples: 85% with six days left is far ahead of a "
          "uniform pace (~5.95), not behind it",
          5.9 < fast_pace < 6.0, fast_pace)
    check("founder's examples: 60% with four hours left is comfortably "
          "behind (~0.615), which is the one that reads as 'ahead' in the "
          "sense that matters - it is not going to run out on its own",
          0.6 < comfortable_pace < 0.63, comfortable_pace)
    check("founder's examples: the lower number - comfortable, not fast - is "
          "the one pace spends first", comfortable_pace < fast_pace)


def test_a_near_wall_7d_account_is_not_specially_excluded(home: Path) -> None:
    """Finding, from #1036: an account at 7d 99% with an hour left passes
    `takes_work` and `unfit` exactly like one at 7d 10% with a day left - only
    pace decides between them. There is no weekly analogue of MIN_STRETCH_S;
    decision #254 deliberately dropped the margin that would have been one
    ('spent to its own wall, not held back'), and #256 accepted the fallout by
    name: 'if it runs out mid-session, the agent moves.' So a 99% account is
    not refused for being a hair from its wall - it can still be PICKED, if
    nothing better is on offer - and this pins that as the accepted shape
    rather than an oversight the ranking should close."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    write_status(home, "sliver", pct_5h=9, pct_7d=99, reset_5h=now + 1800, reset_7d=now + 3600)
    st = status.read("sliver")
    ld = loadmod.Load("sliver", 0, 2, 0.0)
    check("near-wall: 99% with an hour left still takes_work",
          st.takes_work(0)[0], st.takes_work(0))
    check("near-wall: and unfit finds nothing wrong with it",
          status.unfit(st, ld) is None)
    # With nothing better available, it is still the pick - #254's spend-to-
    # the-wall guarantee, which pace must not quietly take back.
    picked, _ = status.pick_account(["sliver"])
    check("near-wall: with no alternative, it is still picked, not skipped",
          picked == "sliver", picked)


def test_pick_account_does_not_bounce_between_two_close_accounts(home: Path) -> None:
    """Many consecutive picks, with two accounts close in pace and usage
    rising on both: the choice must cross from one to the other at most once,
    never alternate back and forth as rounding moves the raw pace a hair to
    either side of the tie."""
    os.environ["OCP_HOME"] = str(home)
    base = int(time.time())
    reset_7d = base + 3 * 86400
    clock = {"s": 0}
    real_time = time.time
    time.time = lambda: base + clock["s"]  # noqa: E731
    try:
        pct = {"a": 40.0, "b": 42.0}
        picks = []
        last = None
        for _ in range(400):
            for acct in ("a", "b"):
                write_status(home, acct, pct_5h=10, pct_7d=pct[acct],
                             reset_5h=base + clock["s"] + 7200, reset_7d=reset_7d)
            # `prefer=last`, as a repeated caller (the accounts panel, a
            # tick's own re-check) would pass what it picked last time - that
            # is the memory the dead zone alone does not have.
            picked, _ = status.pick_account(["a", "b"], prefer=last)
            picks.append(picked)
            last = picked
            pct["a"] += 0.02
            pct["b"] += 0.01
            clock["s"] += 300
        switches = sum(1 for x, y in zip(picks, picks[1:]) if x != y)
        check("no-bounce: usage rising on both, the pick crosses at most once",
              switches <= 1, f"{switches} switches: {picks}")
        check("no-bounce: and it does cross, since 'a' starts and ends behind "
              "'b' becomes ahead of it", picks[0] != picks[-1], picks)
    finally:
        time.time = real_time


def test_pick_account_prefer_breaks_an_exact_bucket_tie(home: Path) -> None:
    """The head start `prefer` gives an incumbent can land it exactly on a
    challenger's bucket, not past it - and that tie must still go to the
    incumbent, not to whichever of the two a later tiebreak (headroom,
    confidence, name) happens to favor. Same pct_7d and reset_7d for both, so
    the bucket tie is exact, not a floating-point accident; `y` has more 5h
    headroom, which would win the headroom tiebreak if `prefer` did not
    settle it first."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    reset_7d = now + 3 * 86400
    # x's raw pace bucket, minus prefer's one-step head start, lands exactly
    # on y's raw bucket (44% and 42% of this week, both checked against
    # PACE_HYSTERESIS = 0.02 above).
    write_status(home, "x", pct_5h=50, pct_7d=44, reset_5h=now + 1800, reset_7d=reset_7d)
    write_status(home, "y", pct_5h=10, pct_7d=42, reset_5h=now + 1800, reset_7d=reset_7d)
    picked, reasons = status.pick_account(["x", "y"], prefer="x")
    check("prefer: an exact bucket tie stays with the incumbent, not with "
          "the account that has more headroom", picked == "x", str((picked, reasons)))


def test_pick_account_tonights_shape(home: Path) -> None:
    """#1001's own regression case: alpha 5h49/7d58 at cap, beta 5h19/7d85 at
    cap, delta 5h8/7d30, gamma 5h88/7d89 - delta is picked. alpha and beta are
    excluded outright by their cap, before pace ever runs on them;
    gamma's 12 points of 5h headroom do not last `MIN_STRETCH_S` at the
    typical agent's cost, so it is excluded too - delta is the only candidate
    left, which this still checks end to end rather than assuming."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    for acct, pct5, pct7 in (("alpha", 49, 58), ("beta", 19, 85),
                             ("delta", 8, 30), ("gamma", 88, 89)):
        write_status(home, acct, pct_5h=pct5, pct_7d=pct7,
                     reset_5h=now + 1800, reset_7d=now + 3 * 86400)
    ld = {
        "alpha": loadmod.Load("alpha", 1, 1, 0.0),
        "beta": loadmod.Load("beta", 1, 1, 0.0),
        "delta": loadmod.Load("delta", 0, 2, 0.0),
        "gamma": loadmod.Load("gamma", 0, 2, 0.0),
    }
    picked, reasons = status.pick_account(["alpha", "beta", "delta", "gamma"], load=ld)
    said = dict(reasons)
    check("tonight's shape: delta is picked", picked == "delta", str((picked, reasons)))
    check("tonight's shape: alpha is excluded by its cap", "cap" in said["alpha"], said["alpha"])
    check("tonight's shape: beta is excluded by its cap", "cap" in said["beta"], said["beta"])
    check("tonight's shape: gamma is excluded - not enough of its 5h window "
          "left to be worth a start", "gamma" != picked and said["gamma"] != "", said["gamma"])


def test_pick_account_degrades_when_every_account_would_hit_the_wall(home: Path) -> None:
    """No account qualifies: pick_account returns None and a reason for each,
    not an exception - and a boundary account whose one more agent would still
    last `MIN_STRETCH_S` is not swept into that refusal by mistake."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    write_status(home, "full", pct_5h=100, pct_7d=10, reset_5h=now + 1800)
    write_status(home, "near_wall", pct_5h=99, pct_7d=10, reset_5h=now + 1800)
    picked, reasons = status.pick_account(["full", "near_wall"])
    check("all-wall: nothing qualifies, so the pick refuses rather than "
          "guessing", picked is None, str((picked, reasons)))
    check("all-wall: every refusal says why",
          all(why for _, why in reasons), reasons)

    # 22 points of headroom at the typical cost (0.75%/min) lasts 1760s, just
    # under MIN_STRETCH_S (1800s); 23 lasts 1840s, just over it.
    write_status(home, "just_under", pct_5h=78, pct_7d=10, reset_5h=now + 1800)
    write_status(home, "just_over", pct_5h=77, pct_7d=10, reset_5h=now + 1800)
    check("all-wall: just under the stretch floor still refuses",
          status.pick_account(["just_under"])[0] is None)
    check("all-wall: one more point of headroom crosses it and is picked",
          status.pick_account(["just_over"])[0] == "just_over")


def test_pick_account_stale_and_unknown_pace(home: Path) -> None:
    """A stale or missing figure must never read as low usage: `long_pace`
    returns infinity rather than 0 or a guess."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())

    # Stale but idle - admitted on its floor, but the floor is not knowledge:
    # a missing pct_7d - not merely a stale one, which still carries forward
    # as a floor of its own - keeps pace unknown rather than inferring one.
    write_status(home, "stale_idle", pct_5h=5, pct_7d=None, reset_5h=now + 1800,
                 collected_at=now - 3600)
    st = status.read("stale_idle")
    check("stale/unknown: a missing 7d figure is unknown pace, not 0",
          status.long_pace(st) == float("inf"), status.long_pace(st))

    # A rolled-over 7d window (reset already past) is not a low pace either -
    # trusted_pct_7d is None for it, same as never having read one at all.
    write_status(home, "rolled_over", pct_5h=5, pct_7d=5, reset_5h=now + 1800,
                 reset_7d=now - 10)
    check("stale/unknown: a rolled-over week reads as unknown pace, not 0",
          status.long_pace(status.read("rolled_over")) == float("inf"))

    # Unknown headroom_5h (stale, and past the idle-floor margin) still
    # refuses outright, the same as any other unfit account - pace is never
    # asked about an account that was refused.
    write_status(home, "stale_full", pct_5h=95, reset_5h=now + 1800,
                 collected_at=now - 3600)
    allowed, why = status.read("stale_full").assignable()
    check("stale/unknown: a stale reading too close to the wall still "
          "refuses", not allowed, why)


def test_live_refresh_asks_a_carried_old_candidate_before_refusing_it(home: Path) -> None:
    """founder #1811 part 2, #1816, production shape: an agent walled on
    'here' is looking for room; 'alpha' last answered the endpoint over
    CARRY_MAX_AGE_S ago (usage_api's own writer never sets rl_carried True in
    production, so this is the real shape a stalled poll leaves), and the
    daemon's own background poller has not caught up to it yet - but the
    endpoint itself would say alpha has room right now. Without asking it
    live at the moment of the decision, alpha reads as carried_old, unknown,
    refused - a move given up on while an account could in fact answer.
    Control: the identical setup with live_refresh=False (pick_account's
    default, and the whole of pick_account's behaviour before this) refuses
    it, reproducing the parked-179-minutes symptom."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    accounts.register("alpha")
    # The stale reading's own window has already passed - consistent with why
    # nobody re-asked it: it is old because it describes a window that ended,
    # not one still open, so a fresh, lower reading in a new window is not
    # the "usage only rises" case record() guards.
    write_status(home, "alpha", pct_5h=90, reset_5h=now - 60,
                 rl_carried=False, rl_at=now - status.CARRY_MAX_AGE_S - 300,
                 collected_at=now)
    st = status.read("alpha")
    check("live_refresh setup: alpha is genuinely carried_old before any ask",
          st.short_source == "carried_old", st.short_source)

    real_read, real_login = usage_api.read, accounts.logged_in
    asked: list[str] = []
    try:
        def fake_read(acct, refresh=True):
            asked.append(acct)
            return reading_of(acct, 10, now + 9000, 20, now + 90000, now)
        usage_api.read = fake_read
        accounts.logged_in = lambda acct: True

        picked, ranked = status.pick_account(["alpha"], live_refresh=False)
        check("control: without live_refresh, the carried_old candidate is "
              "still refused - the pre-fix behaviour", picked is None, ranked)
        check("control: and nothing was asked to produce that refusal",
              asked == [], asked)

        picked, ranked = status.pick_account(["alpha"], live_refresh=True)
        check("live_refresh: the same candidate is asked fresh before being "
              "refused", asked == ["alpha"], asked)
        check("live_refresh: and takes the agent, since the fresh answer has "
              "room - the move is not given up on while it could answer",
              picked == "alpha", ranked)
        check("live_refresh: the fresh reading actually landed on disk, not "
              "just in this decision", status.read("alpha").short.pct == 10,
              status.read("alpha").short.pct)
    finally:
        usage_api.read, accounts.logged_in = real_read, real_login


def test_live_refresh_checks_a_stale_idle_floor_before_admitting_it(home: Path) -> None:
    """founder #1811 part 2, #1816, production shape: omega was last seen at
    16%, 3h ago - stale, but inside its window and idle, so idle_floor admits
    it as a floor. The endpoint would now say 100%: the account crossed the
    wall in the 2h45m nothing re-asked it. Admitting it on the old floor
    hands an agent to an account already at the wall. Control: the identical
    setup with live_refresh=False admits it on the stale floor, reproducing
    the bug."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    accounts.register("omega")
    write_status(home, "omega", pct_5h=16, reset_5h=now + 9000,
                 collected_at=now - 3 * 3600)
    st = status.read("omega")
    check("live_refresh setup: omega is stale but idle-floor-admitted before "
          "any ask", st.stale and st.idle_floor(0)[0], (st.stale, st.idle_floor(0)))

    real_read, real_login = usage_api.read, accounts.logged_in
    asked: list[str] = []
    try:
        def fake_read(acct, refresh=True):
            asked.append(acct)
            return reading_of(acct, 100, now + 60, 40, now + 90000, now)
        usage_api.read = fake_read
        accounts.logged_in = lambda acct: True

        picked, ranked = status.pick_account(["omega"], live_refresh=False)
        check("control: without live_refresh, the stale floor admits it - "
              "the pre-fix behaviour", picked == "omega", ranked)
        check("control: and nothing was asked to produce that admission",
              asked == [], asked)

        picked, ranked = status.pick_account(["omega"], live_refresh=True)
        check("live_refresh: the stale floor is asked fresh before being "
              "used", asked == ["omega"], asked)
        check("live_refresh: and refuses now that the fresh answer is at "
              "the wall - not handed to an account already full",
              picked is None, ranked)
        check("live_refresh: the fresh reading actually landed on disk",
              status.read("omega").short.pct == 100, status.read("omega").short.pct)
    finally:
        usage_api.read, accounts.logged_in = real_read, real_login


def test_status_methods_all_honor_a_pinned_as_of() -> None:
    """T48: age_s, rl_age_s and pct_7d_source read as_of, but
    takes_work, rolled_over, idle_floor, shown_short and last_known still
    read time.time() directly - a caller that pins as_of (replay, stats) got
    a mix of the pinned clock and the wall clock.

    Built so the pin and the real wall clock disagree about everything a
    fixed reading depends on (whether the snapshot is stale, whether either
    window has rolled over): a method still reading time.time() would answer
    differently once as_of is set than it does with no as_of and the clock
    mocked to the same value."""
    pinned = 1_000_000.0
    kw = dict(account="a", exists=True, pct_5h=40, pct_7d=50,
              reset_5h=int(pinned + 1800), reset_7d=int(pinned + 100_000),
              collected_at=int(pinned - 1800), rl_at=int(pinned - 1800),
              rl7_at=int(pinned - 1800))
    live = status_of(**kw)             # as_of=None: reads time.time()
    pinned_st = status_of(as_of=pinned, **kw)

    real_time = time.time
    time.time = lambda: pinned
    try:
        want = (live.takes_work(), live.rolled_over(), live.idle_floor(),
                live.shown_short(), live.last_known(), status.long_pace(live),
                status.unfit(live, None), status._no_long_room(live, None),
                status._pick_reason(live, None, None, False))
    finally:
        time.time = real_time
    # The wall clock is back at its real, much later value; only a method
    # that actually reads as_of, not time.time(), can still match `want`.
    got = (pinned_st.takes_work(), pinned_st.rolled_over(), pinned_st.idle_floor(),
           pinned_st.shown_short(), pinned_st.last_known(), status.long_pace(pinned_st),
           status.unfit(pinned_st, None), status._no_long_room(pinned_st, None),
           status._pick_reason(pinned_st, None, None, False))
    for name, w, g in zip(
            ("takes_work", "rolled_over", "idle_floor", "shown_short", "last_known",
             "long_pace", "unfit", "_no_long_room", "_pick_reason"),
            want, got):
        check(f"status: {name} honors a pinned as_of", g == w, str((g, w)))


def test_live_refresh_asks_at_most_one_candidate_per_call(home: Path) -> None:
    """auditor #1821 should-fix: _move_plan/find_room pass every logged-in
    account as candidates, and a fleet-wide outage - several accounts
    carried_old at once, #1811's own scenario - meant an unbounded number of
    sequential usage_api.read calls (each up to HTTP_TIMEOUT_S) inside one
    pick_account call, on the same daemon tick poll_usage's own docstring
    budgets at 30s. Three candidates are carried_old here; only the first in
    list order may be asked live, and the other two fall through to the
    ordinary carried_old refusal, exactly as they would with live_refresh
    off."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    for n in ("alpha", "beta", "gamma"):
        accounts.register(n)
        write_status(home, n, pct_5h=90, reset_5h=now - 60,
                     rl_carried=False, rl_at=now - status.CARRY_MAX_AGE_S - 300,
                     collected_at=now)
    for n in ("alpha", "beta", "gamma"):
        check(f"live_refresh cap setup: {n} is carried_old before any ask",
              status.read(n).short_source == "carried_old", n)

    real_read, real_login = usage_api.read, accounts.logged_in
    asked: list[str] = []
    try:
        def fake_read(acct, refresh=True):
            asked.append(acct)
            return reading_of(acct, 10, now + 9000, 20, now + 90000, now)
        usage_api.read = fake_read
        accounts.logged_in = lambda acct: True

        picked, ranked = status.pick_account(["alpha", "beta", "gamma"], live_refresh=True)
        check("live_refresh cap: only the first candidate was asked live",
              asked == ["alpha"], asked)
        check("live_refresh cap: it took the agent, since the fresh answer "
              "has room", picked == "alpha", ranked)
        check("live_refresh cap: the second candidate was never asked and "
              "was refused, exactly as it would be with live_refresh off",
              "beta" not in asked and any(n == "beta" for n, _ in ranked), ranked)
    finally:
        usage_api.read, accounts.logged_in = real_read, real_login


def test_each_calls_tokens_are_recorded_per_account_from_the_transcripts(home: Path) -> None:
    """The plan charges for tokens of four classes; usage_samples only has the
    percentages. The transcripts' usage is recorded one row per API call, a
    call streamed over several lines counts once, and nothing else in the
    account directory is read."""
    from handline.usage import tokenlog
    _daemon_home(home, 20)
    now = int(time.time())
    proj = vendors.config_dir("alpha") / "projects" / "-w-purser"
    proj.mkdir(parents=True)
    log = proj / "s1.jsonl"
    torn = _call_line("req_torn", 4)
    secret = vendors.config_dir("alpha") / ".credentials.json"
    secret.write_text(_call_line("req_secret", 999))
    # Decoys inside projects/ (a non-.jsonl name) catch the glob widening; a
    # .jsonl outside projects/ catches the root widening.
    (vendors.config_dir("alpha") / "history.jsonl").write_text(_call_line("req_outside", 999))
    (proj / ".credentials.json").write_text(_call_line("req_secret_inproj", 999))
    (proj / "s1.json").write_text(_call_line("req_plainjson", 999))
    log.write_text(_call_line("req_a", 10) + _call_line("req_a", 30) + _call_line("req_b", 5)
                   + json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n"
                   + torn[:40])
    con = db.connect()
    try:
        got = tokenlog.ingest(con, "alpha", now=now)
        rows = {r["req"]: dict(r) for r in con.execute("SELECT * FROM token_calls")}
        check("tokens: a call streamed over two lines is one row, with the larger output",
              set(rows) == {"req_a", "req_b"} and rows["req_a"]["out"] == 30, str(rows))
        check("tokens: every class is kept, thinking included",
              (rows["req_a"]["inp"], rows["req_a"]["cc"], rows["req_a"]["cr"], rows["req_a"]["thinking"])
              == (2, 1000, 50000, 15), str(rows["req_a"]))
        check("tokens: nothing but the conversation logs is read",
              not {"req_secret", "req_secret_inproj", "req_plainjson", "req_outside"} & set(rows), str(rows))
        again = tokenlog.ingest(con, "alpha", now=now)
        check("tokens: a second read of an unchanged file adds nothing", got == 3 and again == 0, str((got, again)))
        with open(log, "a") as fh:
            fh.write(torn[40:] + _call_line("req_c", 7))
        check("tokens: a file is read on from where it stopped, and a torn line once it is whole",
              tokenlog.ingest(con, "alpha", now=now) == 2
              and con.execute("SELECT COUNT(*) FROM token_calls").fetchone()[0] == 4)
        tot = tokenlog.totals(con, "alpha", 0, 2 ** 40)
        check("tokens: totals per class over an interval", tot["cc"] == 4000 and tot["out"] == 30 + 5 + 4 + 7,
              str(tot))
        con.execute("DELETE FROM token_calls")
        db.set_meta(con, "tokenoffsets:alpha", None)
        from handline.daemon import evaluate as dmn_evaluate
        dmn_evaluate.observe_burn(con, lambda _m: None)
        check("tokens: the daemon tick records them beside the samples",
              con.execute("SELECT COUNT(*) FROM token_calls").fetchone()[0] == 4)
        small = tokenlog.ingest(con, "beta", now=now)
        check("tokens: an account with no transcripts records nothing", small == 0)
        old = int(time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, 0)))
        con.execute("UPDATE token_calls SET ts = ?", (old,))
        check("tokens: rows outlive the samples, and are pruned only past their own retention",
              tokenlog.prune(con, now=old + 30 * 86400) == 0
              and tokenlog.RETENTION_DAYS > db.USAGE_SAMPLE_RETENTION_DAYS
              and tokenlog.prune(con, now=old + 61 * 86400) == 4)
    finally:
        con.close()


def test_the_weeks_ratio_to_the_5h_window_is_measured_from_the_samples(home: Path) -> None:
    """The 5h cost of an agent means nothing for the week until the plan's
    ratio converts it, and nothing written down knows the plan: it is the
    week's rise over the 5h window's, over live pairs inside one window and
    one week, and it needs enough rise to be worth dividing."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    now = int(time.time())
    try:
        _seed_week_history(con, "alpha", now, five=(10, 90), seven=(50, 60))
        got = burnrate.long_ratios(db.usage_samples(con))
        check("ratio: 10 points of week over 80 of 5h is about an eighth",
              abs(got.get("alpha", 0) - 10 / 80) < 0.01 and abs(got.get("*", 0) - 10 / 80) < 0.01, str(got))
        _seed_week_history(con, "beta", now + 1, five=(10, 90), seven=(50, 53))
        got = burnrate.long_ratios(db.usage_samples(con))
        check("ratio: an account whose week rose too little to divide takes the pooled ratio",
              "beta" not in got and "*" in got
              and burnrate.Costs([], got).long_per_short("beta") == got["*"]
              and burnrate.Costs([], got).long_per_short("alpha") == got["alpha"], str(got))
        plan = {"alpha": "max", "beta": "pro"}.get
        by_plan = burnrate.long_ratios(db.usage_samples(con), plan)
        check("ratio: a thin account takes its own plan's pool, never another plan's",
              burnrate.Costs([], by_plan, plan).long_per_short("beta") is None
              and burnrate.Costs([], by_plan, plan).long_per_short("alpha") == by_plan["alpha"], str(by_plan))
        burnrate.refresh_long_ratios(con, now, force=True)
        db.set_meta(con, "weekratio", json.dumps({"at": now, "r": {"alpha": 9.0}}))
        burnrate.refresh_long_ratios(con, now + 1)
        check("ratio: a refresh inside the throttle leaves the kept ratios alone",
              burnrate._load_long_ratios(con) == {"alpha": 9.0})
        burnrate.refresh_long_ratios(con, now + burnrate.WEEK_RATIO_EVERY_S + 1)
        check("ratio: and one after it recomputes them",
              abs(burnrate._load_long_ratios(con).get("alpha", 0) - 10 / 80) < 0.01)
        check("ratio: nothing measured is None, not a guess",
              burnrate.Costs([], {}).long_per_short("alpha") is None)
        # A rolled window, a fall and a carried figure are not a rate.
        con.execute("DELETE FROM usage_samples")
        for i, (p5, p7, r5, carried) in enumerate([(10, 50, 1, 0), (90, 60, 1, 1), (95, 62, 1, 0),
                                                   (5, 63, 2, 0), (60, 80, 2, 0)]):
            st = status_of("alpha", exists=True, pct_5h=p5, pct_7d=p7, reset_5h=r5,
                                      reset_7d=99, rl_carried=bool(carried))
            db.record_usage_sample(con, "alpha", st, ts=now - 1000 + i)
        got = burnrate.long_ratios(db.usage_samples(con))
        check("ratio: a carried reading, a new window and a fall add nothing; only the last pair counts",
              abs(got.get("alpha", 0) - 17 / 55) < 0.01, str(got))
    finally:
        con.close()


def test_an_account_the_week_is_about_to_close_is_not_usable_and_not_picked(home: Path) -> None:
    """#1233: gamma at 99% of its week with 1d15h left was drawn usable, with
    'no recent burn', and work kept going to it - minutes of work, then a day
    and a half parked. The week is judged by its runway at the plan's measured
    ratio, on the one function the panel and the pick share."""
    con, loads = _gamma_world(home)
    try:
        st = status.read("gamma")
        picked, reasons = status.pick_account(["gamma", "alpha"], load=loads)
        said = dict(reasons)
        check("week: gamma is not picked", picked == "alpha", str((picked, reasons)))
        check("week: and the reason is the week's runway, not a percent line",
              "7d 99%" in said["gamma"] and "one more agent lasts" in said["gamma"], said["gamma"])
        check("week: unfit says the same", status.unfit(st, loads["gamma"]) == said["gamma"],
              str(status.unfit(st, loads["gamma"])))
        allowed, why = status.account_verdict("gamma", st, loads["gamma"], reasons, True)
        check("week: the panel's tick is off, with the picker's own words",
              not allowed and why == said["gamma"], str((allowed, why)))
        rw = status.describe_runway(status.runway(st, status.read_burn("gamma"), None, loads["gamma"]))
        check("week: the runway column names the week, from the same stretch the pick judged",
              rw == f"~{timefmt.span(status.long_stretch_s(st, loads['gamma']))} to 7d", rw)
        alone, why = status.pick_account(["gamma"], load=loads)
        check("week: gamma alone, #1233's shape, is refused for the week's runway",
              alone is None and "one more agent lasts" in dict(why)["gamma"], str((alone, why)))
        alpha = status.read("alpha")
        filling = types.SimpleNamespace(account="alpha", burn=5e-3, at_cap=False, costs=loads["alpha"].costs)
        ratio = filling.costs.long_per_short("alpha")
        got = status.long_runway_s(alpha := status.read("alpha"), filling)
        check("week: a filling account's figure is its week's headroom at its own fill, not the pick's",
              ratio is not None and got is not None
              and abs(got - (100 - alpha.long_trusted) / (5e-3 * ratio)) < 1e-6, str((got, ratio)))
        check("week: an idle account the week does not close still says no recent burn",
              status.describe_runway(status.runway(alpha, None, None, loads["alpha"])) == "no recent burn")
        check("week: at the week's wall it says so",
              status.describe_runway(status.Runway(50, 0, None, False, False, None, "7d")) == "at the 7d wall")
        five_first = status.Runway(50, 10, 600.0, True, False, long_s=3600.0, long_label="7d")
        week_first = status.Runway(50, 10, 3600.0, True, False, long_s=600.0, long_label="7d")
        check("week: whichever window runs out first is the one shown",
              status.describe_runway(five_first) == "~10m to wall"
              and status.describe_runway(week_first) == "~10m to 7d")
        check("week: an account with room is untouched",
              status.unfit(alpha, loads["alpha"]) is None
              and status.describe_runway(status.runway(alpha, None, None, loads["alpha"])) != "")
    finally:
        con.close()


def test_the_week_check_moves_the_pick_where_pace_alone_would_not(home: Path) -> None:
    """The pick's order is pace, so a test where pace already puts the closed
    account last proves nothing about the week's runway. Here it is the other
    way round: "tight" is the one furthest behind its weekly pace (95% of a
    week with an hour left) and would be picked first on pace, but one more
    agent spends its last 5 points in minutes; "roomy" is worse on pace and has
    the runway. Only the week's runway can choose roomy."""
    _daemon_home(home, 20)
    now = int(time.time())
    write_status(home, "tight", pct_5h=20, pct_7d=95, reset_5h=now + 3 * 3600, reset_7d=now + 3600)
    write_status(home, "roomy", pct_5h=20, pct_7d=70, reset_5h=now + 3 * 3600, reset_7d=now + 3 * 86400)
    con = db.connect()
    try:
        db.set_meta(con, "weekratio", json.dumps({"at": now, "r": {"*": 0.5}}))
        loads = loadmod.read_all(con)
    finally:
        con.close()
    tight, roomy = status.read("tight"), status.read("roomy")
    check("order: tight is further behind its pace, so pace alone would pick it",
          status.long_pace(tight) < status.long_pace(roomy), str((status.long_pace(tight), status.long_pace(roomy))))
    picked, reasons = status.pick_account(["tight", "roomy"], load=loads)
    check("order: with the week's runway, the account it closes is passed over",
          picked == "roomy" and "one more agent lasts" in dict(reasons)["tight"], str((picked, reasons)))
    real = status._no_long_room
    status._no_long_room = lambda *a, **k: None
    try:
        without, _ = status.pick_account(["tight", "roomy"], load=loads)
    finally:
        status._no_long_room = real
    check("order: with the week check removed the pick goes to tight - the control",
          without == "tight", str(without))


def test_a_week_that_rolls_over_first_is_not_closed_by_its_runway(home: Path) -> None:
    """The same 99% with minutes to go is not a wall: the reset arrives before
    one more agent could spend what is left. And with no ratio measured the
    week is judged at its wall alone, as before - no guess about the plan."""
    con, loads = _gamma_world(home, gamma_reset_in=300)
    try:
        st = status.read("gamma")
        check("rollover: 99% with five minutes to the reset is usable",
              status.unfit(st, loads["gamma"]) is None, str(status.unfit(st, loads["gamma"])))
        db.set_meta(con, "weekratio", None)
        con.execute("UPDATE agent_state SET agent = agent")
        bare = loadmod.read_all(con)
        write_status(home, "gamma", pct_5h=20, pct_7d=99, reset_5h=int(time.time()) + 3 * 3600,
                     reset_7d=int(time.time()) + 39 * 3600)
        check("no ratio: nothing is guessed, the week is judged at its wall alone",
              status.unfit(status.read("gamma"), bare["gamma"]) is None)
    finally:
        con.close()


# ---------------------------------------------------------------- usage_samples
def test_usage_samples_round_trip_without_coercing_a_gap_to_zero(home: Path) -> None:
    """#1113: a recorded sample is read back exactly, unknown figures stay
    unknown - never 0 - and pruning removes only what is older than the
    window it names."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        known = status_of("alpha", exists=True, pct_5h=40, pct_7d=60,
                                     reset_5h=1000, reset_7d=2000, rl_at=900, rl7_at=900,
                                     collected_at=900)
        unknown = status_of("beta", exists=True, pct_5h=None, pct_7d=None,
                                       rl_lost=True, collected_at=900)
        db.record_usage_sample(con, "alpha", known, ts=1000)
        db.record_usage_sample(con, "beta", unknown, ts=1000)
        rows = {r["account"]: r for r in db.usage_samples(con)}
        ka, ua = windows.entries(dict(rows["alpha"])), windows.entries(dict(rows["beta"]))
        check("usage_samples: a known figure reads back exactly",
              [(e["minutes"], e["used_percent"]) for e in ka] == [(300, 40), (10080, 60)],
              dict(rows["alpha"]))
        check("usage_samples: an unknown figure is NULL, not 0 - a gap is never a zero",
              ua[0]["used_percent"] is None and ua[1]["used_percent"] is None
              and ua[0]["lost"] is True, dict(rows["beta"]))
        check("usage_samples: a new row leaves the old per-name columns alone",
              rows["alpha"]["pct_5h"] is None and rows["alpha"]["reset_7d"] is None,
              dict(rows["alpha"]))

        st_back = replaymod.status_from_row(rows["alpha"])
        check("usage_samples: the row reconstructs the same AccountStatus "
              "pick_account would have seen",
              st_back.short.pct == 40 and st_back.long.reset == 2000 and st_back.short.at == 900,
              st_back)

        removed = db.prune_usage_samples(con, before_ts=500)
        check("usage_samples: a cutoff earlier than the samples removes nothing",
              removed == 0 and len(db.usage_samples(con)) == 2)
        removed = db.prune_usage_samples(con, before_ts=1500)
        check("usage_samples: a cutoff past them removes exactly the old rows",
              removed == 2 and db.usage_samples(con) == [])
    finally:
        con.close()


def test_the_usage_sample_prune_and_since_read_use_an_index(home: Path) -> None:
    """Both filter on ts alone, which the (account, ts) index cannot serve
    because account leads it: without an index on ts each tick full-scans.
    The statements checked are the ones db actually runs, captured as they
    execute, and the plan must SEARCH - a full scan of an index still scans."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        ran: list[str] = []
        con.set_trace_callback(ran.append)
        db.usage_samples(con, since=1)
        db.prune_usage_samples(con, before_ts=1)
        con.set_trace_callback(None)
        for q in ran:
            plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q))
            check(f"usage_samples: {q!r} is searched by an index, not scanned",
                  "SEARCH usage_samples USING" in plan, plan)
        check("usage_samples: the since read and the prune were both captured",
              len(ran) == 2, ran)
    finally:
        con.close()


def test_a_tick_samples_every_account_and_a_down_daemon_leaves_a_gap(home: Path) -> None:
    """observe_burn logs one row per account per tick, on the same read
    burnrate already makes - no extra call to any account - and a tick that
    never ran (the daemon down) leaves no row, which IS the gap: nothing
    backfills it."""
    _daemon_home(home, 40)
    accounts.register("beta")
    write_status(home, "beta", pct_5h=10, pct_7d=20, reset_5h=int(time.time()) + 1800)
    con = db.connect()
    try:
        before = len(db.usage_samples(con))
        dmn_evaluate.observe_burn(con, lambda _m: None)
        rows = db.usage_samples(con)
        check("samples: one row per account, one tick",
              len(rows) - before == 2, len(rows) - before)
        check("samples: both accounts are in it",
              {r["account"] for r in rows} == {"alpha", "beta"}, {r["account"] for r in rows})
        # Nothing runs between here and the next call - no tick, no row: a
        # daemon that was down leaves an actual gap in `ts`, not a filled one.
        dmn_evaluate.observe_burn(con, lambda _m: None)
        check("samples: a second tick adds exactly one row per account, no more",
              len(db.usage_samples(con)) - before == 4)
    finally:
        con.close()


def test_replay_finds_where_the_new_rule_and_the_old_one_disagree(home: Path) -> None:
    """#1113's own acceptance case: replay the same disagreement
    test_pick_account_orders_by_weekly_pace already proves live - a
    near-wall account resetting soon vs a real-headroom one resetting later -
    from recorded samples instead of from a fixture built for the purpose."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        ts = 500_000_000
        close_to_wall = status_of("close_to_wall", exists=True, pct_5h=5, pct_7d=92,
                                             reset_5h=ts + 1800, reset_7d=ts + 3600,
                                             rl_at=ts, rl7_at=ts, collected_at=ts)
        safe = status_of("safe", exists=True, pct_5h=5, pct_7d=50,
                                    reset_5h=ts + 1800, reset_7d=ts + 90000,
                                    rl_at=ts, rl7_at=ts, collected_at=ts)
        db.record_usage_sample(con, "close_to_wall", close_to_wall, ts=ts)
        db.record_usage_sample(con, "safe", safe, ts=ts)
        diffs = replaymod.replay(con)
        check("replay: finds the recorded tick where the rules disagree",
              len(diffs) == 1 and diffs[0]["ts"] == ts, diffs)
        check("replay: the old rule would have picked the near-wall, "
              "sooner-reset account", diffs[0]["old"] == "close_to_wall", diffs[0])
        check("replay: the new rule picks the one with real headroom instead",
              diffs[0]["new"] == "safe", diffs[0])

        # A single-account tick decides nothing for either rule - not counted
        # as a divergence just because old_pick(...) != None.
        db.record_usage_sample(con, "alone", close_to_wall, ts=ts + 100)
        check("replay: a tick with only one candidate is not a divergence",
              len(replaymod.replay(con)) == 1, replaymod.replay(con))
    finally:
        con.close()


def test_replay_reconstructs_burn_and_lets_the_week_gate_fire(home: Path) -> None:
    """#1400: `load={}` cannot ask `_no_long_room` anything (it needs
    `ld.costs`), so a replay under it agrees with the old deadline rule on
    every tick the week gate would have closed. `tight` (88% of its week, an hour left) is
    first on pace; one more agent alone lasts it 1920s, over the 1800s a start
    is worth, so only the burn (0.5%/min of the 5h window, 1152s with it) closes
    it, and only with the plan's ratio. Dropping either the burn or the ratio
    from the replayed load leaves the pick agreeing with the old rule."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        ts0, ts1 = 500_000_000, 500_000_600
        reset_5h = ts1 + 3 * 3600
        for ts, pct_5h in ((ts0, 20), (ts1, 25)):
            tight = status_of("tight", exists=True, pct_5h=pct_5h, pct_7d=88,
                                         reset_5h=reset_5h, reset_7d=ts1 + 3600,
                                         rl_at=ts, rl7_at=ts, collected_at=ts)
            roomy = status_of("roomy", exists=True, pct_5h=pct_5h, pct_7d=70,
                                         reset_5h=reset_5h, reset_7d=ts1 + 3 * 86400,
                                         rl_at=ts, rl7_at=ts, collected_at=ts)
            db.record_usage_sample(con, "tight", tight, ts=ts)
            db.record_usage_sample(con, "roomy", roomy, ts=ts)
        db.set_meta(con, "weekratio", json.dumps({"at": ts1, "r": {"*": 0.5}}))

        burns = replaymod.reconstruct_burn(db.usage_samples(con))
        check("replay: burn is the rise over the span between the account's own samples",
              abs(burns[("tight", ts1)] - 5 / 600) < 1e-9
              and abs(burns[("roomy", ts1)] - 5 / 600) < 1e-9, burns)
        check("replay: a first sample has no span to measure, so burn 0.0 as live",
              burns[("tight", ts0)] == 0.0 and burns[("roomy", ts0)] == 0.0, burns)

        blind: list = []
        diffs = {d["ts"]: d for d in replaymod.replay(con, unasked=blind)}
        check("replay: a tick where the ratio exists lists nobody as unasked",
              blind == [] and diffs.get(ts1, {}).get("unasked") == [], (blind, diffs.get(ts1)))
        check("replay: the week gate that load={} cannot ask fires here and moves the pick",
              ts1 in diffs, diffs)
        check("replay: the old rule's earliest-deadline still picks the account "
              "closing this week", diffs.get(ts1, {}).get("old") == "tight", diffs.get(ts1))
        check("replay: the new rule, with burn and the week's ratio reconstructed, "
              "passes it over for the one with real runway",
              diffs.get(ts1, {}).get("new") == "roomy", diffs.get(ts1))

        # The control this guards against: under load={} (the pre-#1400
        # replay), the week gate cannot fire at all, so pace alone picks
        # "tight" on both sides and this tick is not a divergence.
        real_time = time.time
        time.time = lambda: ts1
        try:
            bare_pick, _ = status.pick_account(
                ["tight", "roomy"], load={},
                read_status={n: s for n, s in replaymod.by_tick(
                    db.usage_samples(con))[ts1].items()}.get)
        finally:
            time.time = real_time
        check("replay: load={} cannot see the week gate - the control this guards against",
              bare_pick == "tight", bare_pick)

        db.set_meta(con, "weekratio", json.dumps({"at": ts1, "r": {}}))
        none: list = []
        replaymod.replay(con, unasked=none)
        check("replay: with no week ratio the gate could not ask, and says so",
              none == [(ts0, ["roomy", "tight"]), (ts1, ["roomy", "tight"])], none)

        # A plan pooled ratio is chosen by the plan an account had at the tick,
        # which no row kept; today's subscription.json on disk must not stand in.
        sub = paths.account_dir("tight") / "subscription.json"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text(json.dumps({"subscriptionType": "max"}))
        db.set_meta(con, "weekratio", json.dumps({"at": ts1, "r": {"*max": 0.5}}))
        none = []
        replaymod.replay(con, unasked=none)
        check("replay: today's plan on disk does not pick the week ratio of a past tick",
              none == [(ts0, ["roomy", "tight"]), (ts1, ["roomy", "tight"])], none)
    finally:
        con.close()


def test_replay_burn_is_the_daemons_burn(home: Path) -> None:
    """The replayed burn is `triggers.burn_rate_per_s` over the same rolling
    window of live samples the daemon keeps, so a one-step rise across one
    poll is 0.0 (span under the floor), and a row the status calls unreadable,
    carried or lost never enters the window."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        def put(acct, ts, pct, **kw):
            st = status_of(acct, exists=kw.pop("exists", True), pct_5h=pct,
                                      reset_5h=ts + 3600, rl_at=kw.pop("rl_at", ts),
                                      collected_at=ts, **kw)
            db.record_usage_sample(con, acct, st, ts=ts)

        put("a", 1000, 50)
        put("a", 1030, 51)
        put("b", 1000, 50)
        put("b", 1600, 56)
        put("c", 1000, 50)
        put("c", 1600, 56, error="boom")
        put("d", 1000, 50)
        put("d", 1600, 56, rl_carried=True)
        put("e", 1000, 50)
        put("e", 1600, 56, rl_lost=True)
        put("f", 1000, 50)
        put("f", 1600, 40)
        put("g", 1000, 50)
        put("g", 1030, 51)
        put("g", 1600, 52)
        put("h", 1000, 50)
        for i in range(12):
            put("h", 1600 + 10 * i, 56, rl_at=1600)
        put("m", 1000, 50)
        for i in range(1, 12):
            put("m", 1000 + 100 * i, 50 + i * i // 3)
        put("i", 1000, 50, exists=False)
        put("i", 1600, 56, exists=False)
        put("j", 1000, 50)
        put("j", 1600, 56, pct_5h_readable=False)
        st = status_of("k", exists=True, pct_5h=50, reset_5h=9999, rl_at=1000,
                                  collected_at=1000)
        db.record_usage_sample(con, "k", st, ts=1000)
        st = status_of("k", exists=True, pct_5h=56, reset_5h=9999, rl_at=1600,
                                  collected_at=1600 - status.STALE_AFTER_S - 1)
        db.record_usage_sample(con, "k", st, ts=1600)
        burns = replaymod.reconstruct_burn(db.usage_samples(con))
        check("replay burn: one quantized step across one poll is 0.0, as live",
              burns[("a", 1030)] == 0.0, burns[("a", 1030)])
        check("replay burn: a measurable rise is the daemon's rate",
              abs(burns[("b", 1600)] - 6 / 600) < 1e-9, burns[("b", 1600)])
        check("replay burn: the window is end to end over its samples, not the last pair",
              abs(burns[("g", 1600)] - 2 / 600) < 1e-9, burns[("g", 1600)])
        m = {i: (1000 + 100 * i, 50 + i * i // 3) for i in range(1, 12)}
        check("replay burn: the window is the daemon's BURN_WINDOW, not one more or fewer",
              abs(burns[("m", 2100)] - (m[11][1] - m[2][1]) / (m[11][0] - m[2][0])) < 1e-9
              and triggers.BURN_WINDOW == 10, burns[("m", 2100)])
        check("replay burn: a repeated rl_at is one sample, not a slot each",
              abs(burns[("h", 1710)] - 6 / 600) < 1e-9, burns[("h", 1710)])
        for acct, why in (("i", "a row of an account that does not exist"),
                          ("j", "an unreadable row"), ("k", "a row stale at its own tick"),
                          ("c", "an errored row"), ("d", "a carried row"),
                          ("e", "a lost row"), ("f", "a rewound reading")):
            check(f"replay burn: {why} is not a burn", burns[(acct, 1600)] == 0.0,
                  burns[(acct, 1600)])
    finally:
        con.close()


def test_usage_api_reads_a_window_nothing_has_been_spent_in() -> None:
    """The endpoint's answer, and the distinction the whole module turns on.

    A window with `utilization: 0.0` and `resets_at: null` is not an absent
    reading - it is the endpoint saying the window has rolled over and nothing
    has been spent since. Measured on `alpha` (#1369) at the moment its 5h
    window reset: ocp was carrying 100% from the window that had just ended and
    refused the whole fleet, while this said 0%.

    Nothing here touches the network or the Keychain; both are stood in for.
    """
    calls = {"refresh": 0}

    def fake_refresh(acct, timeout=None):
        calls["refresh"] += 1
        return True

    payloads = {
        "rolled": {"five_hour": {"utilization": 0.0, "resets_at": None},
                   "seven_day": {"utilization": 99.0,
                                 "resets_at": "2026-09-26T02:00:00.941775+00:00"}},
        "mid": {"five_hour": {"utilization": 36.4,
                              "resets_at": "2026-09-21T14:50:00.789690Z"},
                "seven_day": {"utilization": 17.0, "resets_at": None}},
        "absent": {"five_hour": None, "seven_day": None},
        "notadict": [1, 2, 3],
    }
    state = {"which": "rolled", "token": "t", "status": 200}

    def fake_fetch(req, timeout=None):
        class R:
            status = state["status"]
            def read(self): return json.dumps(payloads[state["which"]]).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        if state["status"] != 200:
            raise claudemeter.urllib.error.HTTPError(
                claudemeter.USAGE_URL, state["status"], "no", {}, None)
        return R()

    real = (claudemeter.refresh_credential, claudemeter._access_token_detailed,
            claudemeter.urllib.request.urlopen, claudemeter.json.load)
    claudemeter.refresh_credential = fake_refresh
    claudemeter._access_token_detailed = lambda acct: (state["token"], state["token"] is None)
    claudemeter.urllib.request.urlopen = fake_fetch
    claudemeter.json.load = lambda r: json.loads(r.read())
    try:
        r = usage_api.read("alpha")
        check("usage_api: a rolled-over window reads 0%, not None",
              r is not None and r.short.pct == 0, repr(r))
        check("usage_api: a rolled-over window reports no reset",
              r is not None and r.short.reset is None, repr(r))
        check("usage_api: the other window is still read",
              r is not None and r.long.pct == 99 and r.long.reset == 1790388000, repr(r))
        check("usage_api: a refresh is asked for by default", calls["refresh"] == 1,
              str(calls))

        state["which"] = "mid"
        r = usage_api.read("alpha", refresh=False)
        check("usage_api: utilisation rounds to a whole percent", r.short.pct == 36, repr(r))
        check("usage_api: a Z-suffixed reset parses", r.short.reset == 1790002200, repr(r))
        check("usage_api: refresh=False does not renew", calls["refresh"] == 1, str(calls))

        state["which"] = "absent"
        r = usage_api.read("alpha", refresh=False)
        check("usage_api: an absent window is None, not 0",
              r is not None and r.short.pct is None and r.long.pct is None, repr(r))

        state["which"] = "notadict"
        check("usage_api: a payload that is not an object is not a reading",
              usage_api.read("alpha", refresh=False) is None, "expected None")

        state["which"], state["status"] = "rolled", 500
        check("usage_api: a non-200 is 'could not look', not a reading",
              usage_api.read("alpha", refresh=False) is None, "expected None")

        state["status"], state["token"] = 200, None
        check("usage_api: no credential is 'could not look', not 0%",
              usage_api.read("alpha", refresh=False) is None, "expected None")
    finally:
        (claudemeter.refresh_credential, claudemeter._access_token_detailed,
         claudemeter.urllib.request.urlopen, claudemeter.json.load) = real


def test_read_claude_tells_a_dead_credential_apart_from_transient() -> None:
    """`_read_claude`'s own classification (#2186's BLOCKING): drives it
    directly, past `recover`'s stub of `read_detailed`, since that stub is
    the reason #2178's bug (every failure marked needs-relogin) still passed
    the whole suite once before. 401/403 -> auth_failed True; a 429, a 5xx
    and a timeout -> False, because none of those three say the credential
    itself was refused."""
    real_token, real_urlopen = claudemeter._access_token_detailed, claudemeter.urllib.request.urlopen
    claudemeter._access_token_detailed = lambda acct: ("t", False)
    try:
        for code, want in ((401, True), (403, True), (429, False), (503, False)):
            def fake_fetch(req, timeout=None, _code=code):
                raise claudemeter.urllib.error.HTTPError(
                    claudemeter.USAGE_URL, _code, "no", {}, None)
            claudemeter.urllib.request.urlopen = fake_fetch
            reading, auth_failed = claudemeter._read_claude("alpha", refresh=False)
            check(f"_read_claude: HTTPError {code} classifies auth_failed={want}",
                  reading is None and auth_failed is want, (reading, auth_failed))

        def fake_timeout(req, timeout=None):
            raise claudemeter.urllib.error.URLError("timed out")
        claudemeter.urllib.request.urlopen = fake_timeout
        reading, auth_failed = claudemeter._read_claude("alpha", refresh=False)
        check("_read_claude: a timeout is transient, not a dead credential",
              reading is None and auth_failed is False, (reading, auth_failed))

        # A Keychain item that is genuinely gone (#2194's NOTE 2) - never
        # reaches urlopen, and carries the same auth_failed=True the 401/403
        # cases above do.
        claudemeter._access_token_detailed = lambda acct: (None, True)
        reading, auth_failed = claudemeter._read_claude("alpha", refresh=False)
        check("_read_claude: a missing credential is auth_failed=True",
              reading is None and auth_failed is True, (reading, auth_failed))
    finally:
        claudemeter._access_token_detailed, claudemeter.urllib.request.urlopen = real_token, real_urlopen


def test_a_sandboxed_process_cannot_tell_a_missing_login_from_a_hidden_one(home: Path) -> None:
    """A sandboxed agent's `security` lookup answers "not found" for an item
    that exists. That is unknown, not "not logged in": only outside the
    sandbox is an absent item a refusal (T213)."""
    from handline.vendors import claude as claude_mod
    drv = vendors.get("claude")
    cdir = home / "no-credentials"
    real_run, real_env, real_platform = claude_mod.subprocess.run, dict(os.environ), sys.platform

    class R:
        returncode = 44
        stdout = stderr = ""

    try:
        claude_mod.subprocess.run = lambda *a, **k: R()
        sys.platform = "darwin"
        os.environ.pop("SANDBOX_RUNTIME", None)
        check("outside the sandbox an absent keychain item is a plain no",
              drv.credential_present(cdir) is False)
        os.environ["SANDBOX_RUNTIME"] = "1"
        check("inside it the same answer is unknown", drv.credential_present(cdir) is None)
        check("an unknown login does not refuse a launch", accounts.may_start("alpha"))
        check("but it is not reported as a login", not accounts.logged_in("alpha")
              and accounts.login_state("alpha") is None)
        from handline.infra import doctor as doctormod
        check("the listing says unknown, not yes or no",
              accounts.login_word(None) == "unknown (sandboxed)"
              and accounts.login_word(None, short=True) == "?")
        accounts.register("alpha")
        rep = doctormod.Report()
        doctormod._check_accounts(rep)
        check("doctor warns on unknown rather than passing it",
              any(lv == doctormod.WARN and "login unknown" in n for lv, n, _ in rep.rows), rep.rows)
        agents.register("sbx", "alpha")
        try:
            launch.build("sbx")
            refused = ""
        except SystemExit as e:
            refused = str(e)
        check("launch goes past the login check on an unknown login",
              "not logged in" not in refused, refused)
        check("and no_room does not claim nobody is logged in",
              "no account is logged in" not in launch.no_room("sbx"), launch.no_room("sbx"))
        R.returncode = 44
        R.returncode = 0
        check("a present item is still yes in the sandbox", drv.credential_present(cdir) is True)
    finally:
        claude_mod.subprocess.run = real_run
        sys.platform = real_platform
        os.environ.clear()
        os.environ.update(real_env)


def test_access_token_detailed_tells_missing_apart_from_could_not_read() -> None:
    """`_access_token_detailed` (#2186's SHOULD-FIX 1): `security` exit 44 or
    a parsed item with no accessToken is the credential genuinely gone; every
    other shape of failure is 'could not read it', not 'it is not there'."""
    real_run = claudemeter.subprocess.run

    def fake_run(returncode: int, stdout: str):
        class R:
            pass
        r = R()
        r.returncode, r.stdout = returncode, stdout
        return lambda *a, **k: r

    try:
        claudemeter.subprocess.run = fake_run(44, "")
        token, missing = REAL_ACCESS_TOKEN("alpha")
        check("access_token: exit 44 (item not found) is missing=True",
              token is None and missing is True, (token, missing))

        claudemeter.subprocess.run = fake_run(1, "")
        token, missing = REAL_ACCESS_TOKEN("alpha")
        check("access_token: any other nonzero exit is missing=False (could not read)",
              token is None and missing is False, (token, missing))

        claudemeter.subprocess.run = fake_run(
            0, '{"claudeAiOauth": {"accessToken": ""}}')
        token, missing = REAL_ACCESS_TOKEN("alpha")
        check("access_token: a parsed item with an empty token is missing=True",
              token is None and missing is True, (token, missing))

        claudemeter.subprocess.run = fake_run(0, "not json")
        token, missing = REAL_ACCESS_TOKEN("alpha")
        check("access_token: malformed stdout is missing=False (could not read)",
              token is None and missing is False, (token, missing))

        def raise_timeout(*a, **k):
            raise claudemeter.subprocess.TimeoutExpired(cmd="security", timeout=15)
        claudemeter.subprocess.run = raise_timeout
        token, missing = REAL_ACCESS_TOKEN("alpha")
        check("access_token: a timed-out security call is missing=False",
              token is None and missing is False, (token, missing))

        claudemeter.subprocess.run = fake_run(
            0, '{"claudeAiOauth": {"accessToken": "tok"}}')
        token, missing = REAL_ACCESS_TOKEN("alpha")
        check("access_token: a real token is returned, missing=False",
              token == "tok" and missing is False, (token, missing))
    finally:
        claudemeter.subprocess.run = real_run


def test_the_zai_meter_reads_the_plan_and_hides_its_key(home: Path) -> None:
    """#42 step 2: the opencode driver's meter path reads the account's
    auth.json in memory for its one request - AGENTS.md rule 1 as amended
    (#2010) - and turns the coding plan's two credit windows into the
    percentages every other metered account carries. The rule's other half is
    pinned with the same sentinel: the key reaches no agent - not the launch
    env, not what `ocp usage` prints, not the status file the reading lands
    in. Nothing here touches the network; the endpoint stands in for itself."""
    use_home(home)
    accounts.register("zai", vendor="opencode")
    agents.register("glm", model="zai-coding-plan/glm-5.3-flash")
    _with_vendor("glm", "opencode")
    cdir = vendors.config_dir("zai")
    (cdir / "opencode").mkdir(parents=True, exist_ok=True)
    (cdir / "opencode" / "auth.json").write_text(
        '{"zai-coding-plan": {"type": "api", "key": "SECRET-ZAI-KEY"}}')
    drv = vendors.get("opencode")
    drv.binary = lambda: "/x/bin/opencode"
    drv.credential_present = lambda config_dir: True
    asked: list[str] = []
    now_ms = time.time() * 1000
    reset5_ms, reset7_ms = now_ms + 3_600_000, now_ms + 6 * 24 * 3600 * 1000
    body = {"raw": json.dumps(zai_quota(now_ms)).encode()}

    def fake_open(req, timeout=None):
        asked.append(req.headers.get("Authorization"))
        payload = body["raw"]

        class R:
            status = 200
            def read(self):
                if isinstance(payload, Exception):
                    raise payload
                return payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    real_open = ocmod._OPENER.open
    ocmod._OPENER.open = fake_open
    try:
        check("zai meter: the driver reports the vendor as metered",
              drv.meter() is not None and status.metered("zai"))

        r = usage_api.read("zai", refresh=False)
        check("zai meter: the 5-hour window reads pct and reset",
              r is not None and r.short.pct == 13
              and r.short.reset == reset5_ms // 1000, repr(r))
        check("zai meter: the weekly window reads pct and reset",
              r is not None and r.long.pct == 2
              and r.long.reset == reset7_ms // 1000, repr(r))
        check("zai meter: the endpoint was asked with the credential, once",
              asked == ["Bearer SECRET-ZAI-KEY"], str(asked))

        usage_api.record(r)
        snap = paths.status_file("zai").read_text()
        check("zai meter: the reading lands in the status file without the key",
              "SECRET-ZAI-KEY" not in snap and windows.entries(json.loads(snap))[0]["used_percent"] == 13,
              snap)

        # A 200 is not an answer until it carries a figure: each of these
        # shapes is could-not-look, because recorded it would read as a fresh
        # null and a fresh null reads as quiet - 0% spent (#2025 blocker).
        body["raw"] = json.dumps({"code": 200, "success": True, "data": {"limits": []}}).encode()
        check("zai meter: a 200 with no limit rows is could-not-look, not 0%",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = json.dumps({"code": 200, "success": False, "data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "percentage": 87}]}
        }).encode()
        check("zai meter: success naming a failure is could-not-look, whatever it carries",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = json.dumps({"code": 200, "success": True, "data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 6, "percentage": 9}]}
        }).encode()
        check("zai meter: no 5-hour figure is could-not-look, not a weekly-only reading",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = json.dumps({"code": 200, "success": True, "data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "percentage": 87.5,
             "nextResetTime": reset5_ms},
            {"type": "CREDIT_LIMIT", "unit": 6, "percentage": 2.9}]}
        }).encode()
        r = usage_api.read("zai", refresh=False)
        check("zai meter: a float percentage is taken and rounded down, not dropped",
              r is not None and r.short.pct == 87 and r.long.pct == 2, repr(r))
        body["raw"] = json.dumps({"code": 500, "success": True, "data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "percentage": 87}]}
        }).encode()
        check("zai meter: a code naming a failure is could-not-look",
              usage_api.read("zai", refresh=False) is None)
        # json accepts NaN, Infinity and 1e400; none is a figure, and each
        # raised outside any try before the isfinite gate (#2037 blocker).
        body["raw"] = b'{"code": 200, "success": true, "data": {"limits": [' \
                      b'{"type": "CREDIT_LIMIT", "unit": 3, "percentage": NaN}]}}'
        check("zai meter: a NaN percentage is could-not-look, not a crash",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = b'{"code": 200, "success": true, "data": {"limits": [' \
                      b'{"type": "CREDIT_LIMIT", "unit": 3, "percentage": 87, ' \
                      b'"nextResetTime": NaN}]}}'
        check("zai meter: a NaN reset is could-not-look, not a crash",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = b'{"code": 200, "success": true, "data": "limits"}'
        check("zai meter: a data field that is not a dict is could-not-look, not a crash",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = b'{"code": 200, "success": true, "data": {"limits": [' \
                      b'{"type": "CREDIT_LIMIT", "unit": 3, "percentage": 87, ' \
                      b'"nextResetTime": 9999999999999999999999999999999999999}]}}'
        check("zai meter: a reset outside any sane window is could-not-look, not a time bomb",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = json.dumps({"code": 200, "success": True, "data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "percentage": 87,
             "nextResetTime": now_ms - 90_000}]}}
        ).encode()
        r = usage_api.read("zai", refresh=False)
        check("zai meter: a reset already past reads the rolled window quiet - 0/None, "
              "never the ended window's 87",
              r is not None and r.short.pct == 0 and r.short.reset is None, repr(r))
        body["raw"] = http.client.IncompleteRead(b"x", 10)
        check("zai meter: a body cut off mid-read is could-not-look, not a crash",
              usage_api.read("zai", refresh=False) is None)
        body["raw"] = json.dumps(zai_quota(now_ms)).encode()

        out = repr(usage_api.read("zai", refresh=False))
        check("zai meter: the reading holds the figures, never the key",
              "SECRET-ZAI-KEY" not in out and "13" in out and "2" in out, out)

        argv, env = launch.build("glm")
        leaked = [k for k, v in env.items() if "SECRET-ZAI-KEY" in str(v)]
        check("zai meter: no launch env value carries the key", not leaked, str(leaked))

        (cdir / "opencode" / "auth.json").write_text("{}")
        check("zai meter: a credential that is not the coding plan is could-not-look",
              usage_api.read("zai", refresh=False) is None)
        (cdir / "opencode" / "auth.json").unlink()
        check("zai meter: no credential at all is could-not-look, not 0%",
              usage_api.read("zai", refresh=False) is None)
    finally:
        del drv.binary, drv.credential_present
        ocmod._OPENER.open = real_open


def test_a_reading_is_asked_of_the_accounts_own_drivers_meter(home: Path) -> None:
    """`usage_api.read_detailed` names no vendor: it asks `Driver.meter()`. Claude's
    meter is the one a turn renews, opencode's reads its own endpoint, and a
    vendor with none - or none at all - is "could not look", never a reading."""
    use_home(home)
    check("meter: claude's is renewed by a turn", vendors.get("claude").meter().renews_by_turn)
    oc = vendors.get("opencode")
    check("meter: opencode's is not", oc.meter().renews_by_turn is False)
    accounts.register("zai", vendor="opencode")
    real = oc.usage_reading
    try:
        oc.usage_reading = lambda cdir: (10, 111, 20, 222)
        r, failed = usage_api.read_detailed("zai", refresh=False)
        check("meter: opencode's reading carries its account and figures",
              (r.account, r.short.pct, r.short.reset, r.long.pct, r.long.reset) == ("zai", 10, 111, 20, 222)
              and failed is False, repr(r))
        oc.usage_reading = lambda cdir: None
        check("meter: could not look is (None, False)",
              usage_api.read_detailed("zai", refresh=False) == (None, False))
    finally:
        oc.usage_reading = real
    with second_vendor():
        accounts.register("other", vendor="zai")
        check("meter: a vendor with no meter is not asked",
              usage_api.read_detailed("other") == (None, False))
    check("meter: an account whose vendor has no driver is not asked",
          usage_api.read_detailed("other") == (None, False))


def test_usage_api_record_stands_in_for_a_probe(home: Path) -> None:
    """What the endpoint leaves behind, and the one rule it must not break.

    `record` leaves a snapshot the next tick reads like any other -
    so it has to merge rather than overwrite, and it has to keep the
    collector's rule that usage never falls inside one window while still
    letting a rollover through. The rollover case is the one that matters: it
    is what made `alpha` unassignable eighteen seconds after its 5h window
    reset (#1369).
    """
    restore_world()
    use_home(home)
    p_ = home / "status.json"
    now = int(time.time())

    # A live session's own fields. These are the collector's and must survive.
    p_.write_text(json.dumps({
        "account": "alpha", "session_id": "sess-1", "transcript_path": "/t/x.jsonl",
        "ctx_pct": 42, "model_id": "claude-opus-5", "cost_usd": 1.5,
        "pct_5h": 80, "reset_5h": now + 600, "rl_at": now - 300, "rl_carried": True,
        "pct_7d": 50, "reset_7d": now + 99999, "collected_at": now - 4000,
    }))

    ok = usage_api.record(reading_of(
        account="alpha", pct_5h=90, reset_5h=now + 600,
        pct_7d=51, reset_7d=now + 99999, at=now), path=p_)
    got = json.loads(p_.read_text())
    check("record: it writes", ok, "record returned False")
    check("record: the session's own fields survive",
          got.get("session_id") == "sess-1" and got.get("transcript_path") == "/t/x.jsonl"
          and got.get("ctx_pct") == 42 and got.get("cost_usd") == 1.5, json.dumps(got))
    check("record: a higher figure in the same window is taken", _win(got)["used_percent"] == 90,
          json.dumps(got))
    check("record: the reading is marked observed, not carried",
          _win(got)["observed_at"] == now and _win(got)["carried"] is False
          and _win(got)["lost"] is False,
          json.dumps(got))
    check("record: collected_at moves, so staleness does not fail the account closed",
          got["collected_at"] == now, json.dumps(got))
    check("record: the source is named", got.get("rl_source") == "api", json.dumps(got))

    # Lower, same window: the collector's rule says usage only rises.
    usage_api.record(reading_of(
        account="alpha", pct_5h=70, reset_5h=now + 600,
        pct_7d=51, reset_7d=now + 99999, at=now), path=p_)
    check("record: a lower figure inside the same window is refused",
          _win(json.loads(p_.read_text()))["used_percent"] == 90, p_.read_text())

    # Lower, new window: alpha's case. The high-water must not survive it.
    usage_api.record(reading_of(
        account="alpha", pct_5h=0, reset_5h=None,
        pct_7d=51, reset_7d=now + 99999, at=now), path=p_)
    rolled = json.loads(p_.read_text())
    check("record: a rollover replaces the high-water outright",
          _win(rolled)["used_percent"] == 0 and _win(rolled)["resets_at"] is None,
          json.dumps(rolled))
    check("record: and the account reads assignable again",
          status.read("alpha", path=p_).assignable()[0],
          str(status.read("alpha", path=p_).assignable()))


def _win(record: dict, minutes: int = 300) -> dict:
    """The record's window of that length."""
    return next(e for e in windows.entries(record) if e["minutes"] == minutes)


def test_usage_recover_is_a_per_account_switch(home: Path) -> None:
    """load.py's own idiom (`cap_of`/`set_cap`), for #1885: on by default,
    off only where config.yaml names the account, and every other account and
    setting untouched by flipping one."""
    use_home(home)
    check("usage recover: on by default, nothing written yet",
          loadmod.usage_recover_enabled("omega"))
    loadmod.set_cap("omega", 3)
    loadmod.set_usage_recover("omega", False)
    check("usage recover: off once turned off", not loadmod.usage_recover_enabled("omega"))
    check("usage recover: a different account is untouched",
          loadmod.usage_recover_enabled("sigma"))
    check("usage recover: turning it off does not disturb another setting",
          loadmod.cap_of("omega") == 3)
    loadmod.set_usage_recover("omega", True)
    check("usage recover: turning it back on removes the key rather than storing true",
          "usage_recover_omega" not in yamlcompat.load(paths.config_file().read_text()),
          paths.config_file().read_text())
    check("usage recover: back on", loadmod.usage_recover_enabled("omega"))


def test_recover_marks_and_clears_needs_relogin(home: Path) -> None:
    """The one file this writes, and the one thing that reads it: `recover`
    itself, not fail-closed (status.py never looks at this mark - an account
    stays refused there by the ordinary staleness rule either way)."""
    use_home(home)
    check("recover: not marked to start", not recovermod.needs_relogin("omega"))
    recovermod.mark_relogin("omega", "still 401 after a real turn")
    check("recover: marked", recovermod.needs_relogin("omega"))
    check("recover: the reason is on disk",
          "401" in (paths.account_dir("omega") / "needs_relogin").read_text())
    recovermod.clear_relogin("omega")
    check("recover: cleared", not recovermod.needs_relogin("omega"))
    # Clearing twice is not an error - the CLI's --clear-relogin has no way to
    # know in advance whether the mark is even there.
    recovermod.clear_relogin("omega")


def test_recover_spends_one_turn_and_rereads(home: Path) -> None:
    """#1885's mechanism end to end, standing in for the network, the
    Keychain and the CLI the same way `test_usage_api_reads_a_window...`
    already does - `recover` is a caller of `fold.run_model` and
    `usage_api.read_detailed`, never the network itself.
    """
    use_home(home)
    (paths.account_dir("omega") / ".claude").mkdir(parents=True, exist_ok=True)

    real_run_model, real_read_detailed, real_record = (
        foldmod.run_model, usage_api.read_detailed, usage_api.record)
    real_logged_in, real_vendor = accounts.logged_in, vendors.account_vendor
    accounts.logged_in = lambda acct: True
    vendors.account_vendor = lambda acct: "claude"
    calls = {"run_model": [], "recorded": []}
    try:
        # --- happy path: the turn runs, the re-read succeeds -------------
        foldmod.run_model = lambda prompt, acct, model: (
            calls["run_model"].append((prompt, acct, model)) or (True, "hi yourself"))
        usage_api.read_detailed = lambda acct, refresh=True: (reading_of(
            account=acct, pct_5h=1, reset_5h=int(time.time()) + 18000,
            pct_7d=10, reset_7d=None, at=int(time.time())), False)
        usage_api.record = lambda reading: calls["recorded"].append(reading) or True
        ok, msg = recovermod.recover("omega")
        check("recover: a working turn and a clean re-read succeed", ok, msg)
        check("recover: it is haiku, one line, on the account asked for",
              calls["run_model"] == [(recovermod.RECOVER_PROMPT, "omega", recovermod.RECOVER_MODEL)],
              str(calls["run_model"]))
        check("recover: the fresh reading is recorded",
              len(calls["recorded"]) == 1 and calls["recorded"][0].short.pct == 1,
              str(calls["recorded"]))
        check("recover: no needs-relogin mark is left behind",
              not recovermod.needs_relogin("omega"))

        # --- the turn runs but the credential is still refused (401/403) ---
        calls["run_model"].clear()
        calls["recorded"].clear()
        usage_api.read_detailed = lambda acct, refresh=True: (None, True)
        ok, msg = recovermod.recover("omega")
        check("recover: still refused after a real turn is a failure", not ok, msg)
        check("recover: marks needs re-login so the next tick does not retry forever",
              recovermod.needs_relogin("omega"))
        check("recover: nothing is recorded from a reading that never came",
              calls["recorded"] == [])
        recovermod.clear_relogin("omega")

        # --- the turn runs but the read fails transiently (timeout/429/5xx) -
        calls["run_model"].clear()
        calls["recorded"].clear()
        usage_api.read_detailed = lambda acct, refresh=True: (None, False)
        ok, msg = recovermod.recover("omega")
        check("recover: a transient failure after a real turn is still a failure",
              not ok, msg)
        check("recover: a transient failure leaves no needs-relogin mark - "
              "nothing here said the credential itself was refused",
              not recovermod.needs_relogin("omega"))
        check("recover: nothing is recorded from a reading that never came",
              calls["recorded"] == [])

        # --- marked: refuses before spending anything ----------------------
        recovermod.mark_relogin("omega", "still 401 after a real turn")
        calls["run_model"].clear()
        ok, msg = recovermod.recover("omega")
        check("recover: a marked account is refused without a turn",
              not ok and calls["run_model"] == [], msg)
        recovermod.clear_relogin("omega")

        # --- turned off: refuses before spending anything -------------------
        loadmod.set_usage_recover("omega", False)
        ok, msg = recovermod.recover("omega")
        check("recover: turned off refuses without a turn",
              not ok and calls["run_model"] == [], msg)
        loadmod.set_usage_recover("omega", True)

        # --- not a claude account: refuses ----------------------------------
        vendors.account_vendor = lambda acct: "opencode"
        ok, msg = recovermod.recover("omega")
        check("recover: a non-claude account is refused, not asked to auth", not ok, msg)
        vendors.account_vendor = lambda acct: "claude"

        # --- not logged in: refuses ------------------------------------------
        accounts.logged_in = lambda acct: False
        ok, msg = recovermod.recover("omega")
        check("recover: not logged in is refused before spending a turn",
              not ok and calls["run_model"] == [], msg)
        accounts.logged_in = lambda acct: True

        # --- the run itself fails: no re-read, no record ----------------------
        foldmod.run_model = lambda prompt, acct, model: (False, "boom")
        usage_api.record = lambda reading: calls["recorded"].append(reading) or True
        ok, msg = recovermod.recover("omega")
        check("recover: a failed turn is reported and nothing is recorded",
              not ok and "boom" in msg and calls["recorded"] == [], msg)

        # --- at the 7d wall: refuses before spending anything ------------------
        real_record(reading_of(
            account="omega", pct_5h=1, reset_5h=int(time.time()) + 18000,
            pct_7d=status.LONG_BLOCK, reset_7d=int(time.time()) + 500000,
            at=int(time.time())))
        calls["run_model"].clear()
        foldmod.run_model = lambda prompt, acct, model: (
            calls["run_model"].append((prompt, acct, model)) or (True, "hi yourself"))
        ok, msg = recovermod.recover("omega")
        check("recover: a 7d account at the wall is not spent on",
              not ok and calls["run_model"] == [], msg)
    finally:
        foldmod.run_model, usage_api.read_detailed, usage_api.record = (
            real_run_model, real_read_detailed, real_record)
        accounts.logged_in, vendors.account_vendor = real_logged_in, real_vendor
        recovermod.clear_relogin("omega")


def test_the_pull_reads_the_context_the_statusline_used_to_push(home: Path) -> None:
    """One transcript, and the figure the collector would have pushed for it.

    The live check is in the report for #1402: on every agent that had a figure,
    this arithmetic reproduced the statusLine's `current_usage` to the token.
    Here it is pinned, along with the two records that must not be mistaken for
    the last turn - a synthetic message and a cost-state.
    """
    use_home(home)
    accounts.register("alpha")
    cwd = home / "wt-backend"
    cwd.mkdir(parents=True, exist_ok=True)
    write_transcript(
        home, "alpha", cwd, "d548338d-0000-4000-8000-000000000001",
        turns=[("claude-opus-5", {"input_tokens": 1, "output_tokens": 1,
                                  "cache_read_input_tokens": 10_000}),
               # The real last turn, with the nested fields the transcript
               # carries and the statusLine's payload did not.
               ("claude-opus-5", dict(LIVE_USAGE, service_tier="standard",
                                      iterations=[{"input_tokens": 2}],
                                      cache_creation={"ephemeral_1h_input_tokens": 924}))],
        extra=[{"type": "assistant",
                "message": {"model": "<synthetic>",
                            "usage": {"input_tokens": 0, "output_tokens": 0}}},
               {"type": "cost-state", "totalCostUSD": 0.078,
                "totalLinesAdded": 3, "totalLinesRemoved": 1}])

    snap = collect.snapshot("backend", cwd)
    check("pull: finds the session", snap is not None)
    check("pull: the file name is the session id",
          snap["session_id"] == "d548338d-0000-4000-8000-000000000001", snap["session_id"])
    check("pull: the account is read off the path the session writes to",
          snap["account"] == "alpha", snap["account"])
    check("pull: tokens are the last turn's four counts, summed",
          collect.tokens(snap["ctx_used"]) == 86085, snap["ctx_used"])
    check("pull: and the nested fields beside them are not counted",
          set(snap["ctx_used"]) == set(LIVE_USAGE), snap["ctx_used"])
    check("pull: ctx_pct is what the statusLine reported for the same tokens",
          snap["ctx_pct"] == 9, snap["ctx_pct"])
    check("pull: the model comes from the same turn",
          snap["model_id"] == "claude-opus-5", snap["model_id"])
    check("pull: a synthetic message is not the last turn",
          snap["ctx_pct"] == 9 and snap["ctx_used"]["cache_read_input_tokens"] == 84777)
    check("pull: cost comes from the session's own cost-state",
          snap["cost_usd"] == 0.078 and snap["lines_added"] == 3, snap["cost_usd"])
    check("pull: collected_at is when the session wrote, not when we looked",
          snap["collected_at"] == int(Path(snap["transcript_path"]).stat().st_mtime),
          snap["collected_at"])

    # And it lands where every reader already looks, so nothing downstream has
    # to learn that the mechanism changed.
    check("pull: writes the agent's snapshot", collect.collect("backend", cwd) is not None)
    st = status.read("alpha", paths.agent_status_file("backend"))
    check("pull: status.read sees the context", st.ctx_pct == 9, st.ctx_pct)
    check("pull: status.read sees the session",
          st.session_id == "d548338d-0000-4000-8000-000000000001", st.session_id)


def test_a_cleared_session_reads_zero_and_an_unsearchable_one_reads_unknown(home: Path) -> None:
    """The two ways a transcript has no turn, which have opposite answers.

    A `/clear` starts a new transcript file. Measured on `orcapipe.chat`
    (#1402): eleven lines, no assistant turn, while the statusLine was still
    reporting the 20% the session had before the clear. Its context is 0 - that
    is what the clear was for - and reading it as unknown throws away the fact.

    A file we could not search to the end is the other case and must stay
    unknown: reading it as 0 would tell the daemon an agent near its window's
    end had an empty context, and nothing would ever park it.
    """
    use_home(home)
    accounts.register("alpha")
    cwd = home / "wt-chat"
    cwd.mkdir(parents=True, exist_ok=True)
    write_transcript(home, "alpha", cwd, "b4be3c1a-0000-4000-8000-000000000002",
                     turns=[("claude-sonnet-5", {"cache_read_input_tokens": 203_226})],
                     at=int(time.time()) - 120)
    cleared = [{"type": "user", "message": {"role": "user", "content":
                                            "<command-name>/clear</command-name>"}},
               {"type": "cost-state", "totalCostUSD": 0}]
    write_transcript(home, "alpha", cwd, "caaaab5a-0000-4000-8000-000000000003",
                     extra=cleared, at=int(time.time()))

    snap = collect.snapshot("chat", cwd)
    check("cleared: the new transcript is the session",
          snap["session_id"].startswith("caaaab5a"), snap["session_id"])
    check("cleared: its context is 0, not the 20% of the session before it",
          snap["ctx_pct"] == 0, snap["ctx_pct"])

    # The same file, with the search cut short before it can see the whole of
    # it. Nothing about the session changed; what changed is what we can know.
    tail, ceiling = claude_collect.TAIL_BYTES, claude_collect.TAIL_MAX_BYTES
    try:
        claude_collect.TAIL_BYTES = claude_collect.TAIL_MAX_BYTES = 8
        snap = collect.snapshot("chat", cwd)
        check("unsearchable: a file we could not read to the end is unknown",
              snap["ctx_pct"] is None, snap["ctx_pct"])
        # And a turn further back than the first tail is still found, by
        # doubling - the case a fixed tail would have missed.
        claude_collect.TAIL_BYTES, claude_collect.TAIL_MAX_BYTES = 8, 1 << 20
        snap = collect.snapshot("chat", cwd)
        check("doubling: the turn behind a short tail is still found",
              snap["ctx_pct"] == 0, snap["ctx_pct"])
    finally:
        claude_collect.TAIL_BYTES, claude_collect.TAIL_MAX_BYTES = tail, ceiling


def test_the_newest_transcript_across_accounts_is_the_session(home: Path) -> None:
    """Which account an agent is on, read off the file the session is writing.

    The statusLine took it from `CLAUDE_CONFIG_DIR`, first-hand. The pull takes
    it from the path of the transcript, which is the same fact - and it follows
    an `hl agent move` for nothing, because the file left behind under the old
    account is simply older. Measured live: `orcapipe.chat` moved alpha -> sigma
    and the pull reported sigma on the next read.
    """
    use_home(home)
    accounts.register("alpha")
    accounts.register("sigma")
    cwd = home / "wt-chat"
    cwd.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    write_transcript(home, "alpha", cwd, "aaaaaaaa-0000-4000-8000-000000000004",
                     turns=[("claude-opus-5", {"cache_read_input_tokens": 100_000})],
                     at=now - 600)
    check("move: before the move, the older account is the only one",
          collect.snapshot("chat", cwd)["account"] == "alpha")
    write_transcript(home, "sigma", cwd, "bbbbbbbb-0000-4000-8000-000000000005",
                     turns=[("claude-opus-5", {"cache_read_input_tokens": 20_000})],
                     at=now)
    snap = collect.snapshot("chat", cwd)
    check("move: the newest transcript wins, whichever account holds it",
          snap["account"] == "sigma" and snap["session_id"].startswith("bbbbbbbb"),
          f"{snap['account']} {snap['session_id']}")
    check("move: and the context is the new session's, not the old one's",
          snap["ctx_pct"] == 2, snap["ctx_pct"])


def test_an_unknown_model_gets_no_context_figure(home: Path) -> None:
    """A window size is a table, so an unfamiliar model has no percentage.

    Guessing small parks every agent on the context trigger at a fifth of its
    real window; guessing large is the silence we already live with. The figure
    goes unknown and the session's identity still comes back, because that part
    was never a guess.
    """
    use_home(home)
    accounts.register("alpha")
    cwd = home / "wt-next"
    cwd.mkdir(parents=True, exist_ok=True)
    write_transcript(home, "alpha", cwd, "cccccccc-0000-4000-8000-000000000006",
                     turns=[("some-model-nobody-has-measured",
                             {"cache_read_input_tokens": 500_000})])
    snap = collect.snapshot("next", cwd)
    check("unknown model: no context percentage", snap["ctx_pct"] is None, snap["ctx_pct"])
    check("unknown model: no invented window size", snap["ctx_size"] is None, snap["ctx_size"])
    check("unknown model: the session is still identified",
          snap["session_id"].startswith("cccccccc") and snap["account"] == "alpha")
    check("unknown model: and the tokens are still reported",
          collect.tokens(snap["ctx_used"]) == 500_000)


def test_the_daemon_pulls_only_for_an_open_session(home: Path) -> None:
    """The one thing a pull cannot reproduce is a heartbeat.

    `collected_at` fresh means "a session is alive here" to every reader in
    `status.py`, so collecting for an agent whose session the daemon has closed
    would report a dead session as live for ever. A closed agent's snapshot is
    left to age into stale, exactly as it does under the statusLine.
    """
    _daemon_home(home, 40)
    cwd = home / "wt-backend"
    cwd.mkdir(parents=True, exist_ok=True)
    write_transcript(home, "alpha", cwd, "dddddddd-0000-4000-8000-000000000007",
                     turns=[("claude-opus-5", {"cache_read_input_tokens": 300_000})])
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(cwd))
        dmn_usage_poll.collect_snapshots(con, lambda _m: None)
        st = status.read("alpha", paths.agent_status_file("backend"))
        check("tick: an open session's context is pulled every tick",
              st.ctx_pct == 30, st.ctx_pct)

        paths.agent_status_file("backend").unlink()
        db.set_closed(con, "backend", "dddddddd-0000-4000-8000-000000000007")
        dmn_usage_poll.collect_snapshots(con, lambda _m: None)
        check("tick: a closed agent's snapshot is not refreshed",
              not paths.agent_status_file("backend").exists())
    finally:
        con.close()


def test_a_turn_ending_refreshes_the_snapshot_through_the_hook(home: Path) -> None:
    """The statusLine's per-turn push, done by a hook that was already there.

    `Stop` fires when the agent has finished a turn, already runs `hl hook`,
    and already carries the `cwd` the transcript lives under - so the snapshot
    is refreshed at the moment the status line used to render one, with no
    status line installed. A `Stop` arriving is also the heartbeat: the session
    is alive and has just finished a turn.

    An event that is not a turn boundary must not do it. `PermissionRequest`
    fires while the agent waits on a human, and treating that as a turn would
    keep rewriting the snapshot of a session that is doing nothing.
    """
    use_home(home)
    accounts.register("alpha")
    agents.register("backend")
    cwd = home / "wt-backend"
    cwd.mkdir(parents=True, exist_ok=True)
    write_transcript(home, "alpha", cwd, "eeeeeeee-0000-4000-8000-000000000008",
                     turns=[("claude-opus-5", {"cache_read_input_tokens": 450_000})])
    real_agent = os.environ.get("OCP_AGENT")

    def hook(event: str, **fields) -> None:
        real_in = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"hook_event_name": event, "session_id": "s",
                                            "cwd": str(cwd), **fields}))
        try:
            evmod.record_from_stdin([event])
        finally:
            sys.stdin = real_in

    try:
        os.environ["OCP_AGENT"], os.environ["OCP_ACCOUNT"] = "backend", "alpha"
        hook("Stop")
        st = status.read("alpha", paths.agent_status_file("backend"))
        check("hook: a turn ending refreshes this session's snapshot",
              st.ctx_pct == 45, st.ctx_pct)
        check("hook: with the session it was written by",
              st.session_id == "eeeeeeee-0000-4000-8000-000000000008", st.session_id)

        paths.agent_status_file("backend").unlink()
        hook("PermissionRequest", tool_name="Bash")
        check("hook: an event that is not a turn boundary does not collect",
              not paths.agent_status_file("backend").exists())
    finally:
        if real_agent is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = real_agent


def test_the_usage_poll_asks_one_account_per_tick_and_renews_only_on_failure(home: Path) -> None:
    """Rate limits are the half no transcript carries, so they are polled.

    One account per tick, because five HTTPS GETs in the path of a 30s loop is
    five timeouts. And `refresh=True` starts the Claude CLI to renew the
    account's token, so it is only tried when the request actually failed, and
    then no more often than its own cooldown - otherwise a logged-out account
    pays a CLI start every two minutes for ever.
    """
    _daemon_home(home, 40)
    accounts.register("beta")
    asked: list[tuple[str, bool]] = []
    answers: list[bool] = [True]

    def fake_read(acct, refresh=True):
        asked.append((acct, refresh))
        if not answers[0]:
            return None
        return reading_of(acct, 7, None, 11, None, int(time.time()))

    real_read, real_login, real_recover = usage_api.read, accounts.logged_in, recovermod.recover
    try:
        accounts.logged_in = lambda acct: True
        usage_api.read = fake_read
        # #1885/#76 is driven separately below; a plain read failure here
        # must fall into the ordinary backoff, not spend a real turn.
        recovermod.recover = lambda acct: (False, "stub: not under test here")
        con = db.connect()
        try:
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: one account per tick", len(asked) == 1, asked)
            check("poll: and no renewal on the common path", asked[0][1] is False, asked)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: the next tick takes the next account",
                  [a for a, _ in asked] == ["alpha", "beta"], asked)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: an account just read is not read again this minute",
                  len(asked) == 2, asked)
            check("poll: the reading lands where every reader looks",
                  status.read("alpha").short.pct == 7, status.read("alpha").short.pct)

            # Nothing has happened on either account since those readings, so
            # there is nothing to ask about - the cursors being clear is not a
            # reason on its own.
            asked.clear()
            db.set_cursor(con, "usage-poll:alpha", 0)
            db.set_cursor(con, "usage-poll:beta", 0)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: an idle account is not asked at all", asked == [], asked)

            # A turn is what moves the figures, and our own hooks record one.
            db.add_event(con, "backend", "Stop", {}, account="alpha")
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: a turn since the last reading makes it due",
                  [a for a, _ in asked] == ["alpha"], asked)

            # And the spending nothing records - a fold's `claude -p`, a human
            # at a terminal - is why quiet is not trusted for ever. Beta's
            # reading is aged past the cadence with no event of any kind.
            asked.clear()
            write_status(home, "beta", pct_5h=3, pct_7d=4,
                         rl_at=int(time.time()) - dmn_states.USAGE_MAX_AGE_S - 1)
            db.set_cursor(con, "usage-poll:beta", 0)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: a reading older than the cadence is asked about anyway",
                  [a for a, _ in asked] == ["beta"], asked)

            # Now the endpoint cannot be reached: the token is the usual reason,
            # and renewing it is one CLI start, once per cooldown.
            asked.clear()
            answers[0] = False
            db.add_event(con, "backend", "Stop", {}, account="alpha")
            db.set_cursor(con, "usage-poll:alpha", 0)
            db.set_cursor(con, "usage-poll:beta", 0)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: a failure is retried once, with a renewal",
                  [r for _a, r in asked] == [False, True], asked)
            asked.clear()
            db.add_event(con, "backend", "Stop", {}, account="alpha")
            db.set_cursor(con, "usage-poll:alpha", 0)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll: and the renewal is not tried again inside its cooldown",
                  [r for _a, r in asked] == [False], asked)
        finally:
            con.close()
    finally:
        usage_api.read, accounts.logged_in, recovermod.recover = real_read, real_login, real_recover


def test_a_failing_account_never_blocks_the_ones_behind_it(home: Path) -> None:
    """founder #1811, #1799 part 2: the production shape. `list_accounts` is
    sorted, so alpha/beta/delta/gamma sit ahead of omega/sigma/theta - and
    with all four failing on every try (the endpoint 429s an account at its
    own wall, or a token stays dead), the old `return` on the first failure
    each tick meant the loop never reached the rest: omega answered fine and
    went three hours unpolled because it came alphabetically last. Driven
    over several ticks - the account that succeeds is not the point, that it
    is reached at all despite four always-failing accounts ahead of it is."""
    _daemon_home(home, 40)
    for a in ("alpha", "beta", "delta", "gamma", "omega"):
        accounts.register(a)
    asked: list[str] = []

    def fake_read(acct, refresh=True):
        asked.append(acct)
        if acct == "omega":
            return reading_of(acct, 5, None, 6, None, int(time.time()))
        return None

    real_read, real_login, real_recover = usage_api.read, accounts.logged_in, recovermod.recover
    try:
        accounts.logged_in = lambda acct: True
        usage_api.read = fake_read
        recovermod.recover = lambda acct: (False, "stub: not under test here")
        con = db.connect()
        try:
            for _ in range(3):
                dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("starve: omega is actually asked, despite four accounts "
                  "ahead of it in list_accounts order failing every time",
                  "omega" in asked, asked)
            check("starve: and its reading actually lands",
                  status.read("omega").short.pct == 5, status.read("omega").short.pct)
            check("starve: the four failing accounts were each tried too - "
                  "none of them silently dropped to make room",
                  {"alpha", "beta", "delta", "gamma"} <= set(asked), asked)
        finally:
            con.close()
    finally:
        usage_api.read, accounts.logged_in, recovermod.recover = real_read, real_login, real_recover


def test_one_accounts_usage_read_raising_is_contained(home: Path) -> None:
    """Task #51: an exception out of one account's reading (a dropped HTTPS body,
    a recover() that blows up) used to leave `poll_usage`, then the tick, and
    launchd restarted the daemon into the same raise. It is now that account's
    failure: logged by name, backed off, and the accounts behind it still read."""
    import http.client
    _daemon_home(home, 40)
    accounts.register("beta")
    asked: list[str] = []

    def fake_read(acct, refresh=True):
        asked.append(acct)
        if acct == "alpha":
            raise http.client.IncompleteRead(b"par")
        return reading_of(acct, 7, None, 11, None, int(time.time()))

    real_read, real_login, real_recover = usage_api.read, accounts.logged_in, recovermod.recover
    real_poll = dmn_usage_poll.poll_usage
    try:
        accounts.logged_in = lambda acct: True
        usage_api.read = fake_read
        recovermod.recover = lambda acct: (False, "stub")
        con = db.connect()
        try:
            logs: list[str] = []
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll raise: it does not leave poll_usage, and names the account",
                  any("alpha" in m and "IncompleteRead" in m for m in logs), str(logs))
            check("poll raise: the account behind it is read in the same tick",
                  asked == ["alpha", "beta"] and status.read("beta").short.pct == 7, str(asked))
            check("poll raise: the failing account falls into its backoff",
                  db.get_meta(con, "usage-fail:alpha") == "1"
                  and db.get_cursor(con, "usage-poll:alpha") > int(time.time()), str(logs))
            asked.clear()
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll raise: and is not asked again inside it", "alpha" not in asked, str(asked))

            # recover() raising on a dead token is the same containment.
            asked.clear()
            db.set_cursor(con, "usage-poll:beta", 0)
            db.add_event(con, "backend", "Stop", {}, account="beta")
            usage_api.read = lambda acct, refresh=True: None
            def boom(acct):
                raise RuntimeError("recover blew up")
            recovermod.recover = boom
            logs.clear()
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll raise: a recover() that raises is contained too",
                  any("beta" in m and "recover blew up" in m for m in logs), str(logs))

            # A failure outside any one account must not end the tick either.
            def no_poll(_con, _log):
                raise OSError("accounts directory unreadable")
            dmn_usage_poll.poll_usage = no_poll
            logs.clear()
            dmn_evaluate.tick(con, logs.append)
            check("poll raise: a failure outside any account does not crash the tick",
                  any("poll failed" in m and "unreadable" in m for m in logs), str(logs))
        finally:
            con.close()
    finally:
        usage_api.read, accounts.logged_in, recovermod.recover = real_read, real_login, real_recover
        dmn_usage_poll.poll_usage = real_poll


def test_poll_usage_calls_recover_on_a_dead_token_and_at_the_5h_boundary(home: Path) -> None:
    """Task #76: `poll_usage` is the one place that notices a reading is worth
    a real turn - `recover.recover(acct)` is the mechanism (#1885), this is
    the trigger. Two call sites, per purser #2176's spec:
    - the plain read still reads None after the refresh=True retry (a dead
      token) - recover() is tried before the fail-streak/backoff path.
    - `usage_due`'s 'its 5h window has ended' case, even though the plain read
      just succeeded - a stale reading can report success right up to the
      boundary, and nothing else starts the new window.
    A refusal (recover() itself is cheap - off, marked, at the wall) falls
    into the ordinary path unchanged either way, so this costs nothing on the
    common case recover() is not needed for."""
    _daemon_home(home, 40)
    real_read, real_login, real_recover, real_record = (
        usage_api.read, accounts.logged_in, recovermod.recover, usage_api.record)
    recovered: list[str] = []
    recorded: list = []
    try:
        accounts.logged_in = lambda acct: True
        usage_api.record = lambda reading: recorded.append(reading) or real_record(reading)

        # --- a dead token: recover() succeeds, treated like a normal read ---
        usage_api.read = lambda acct, refresh=True: None
        recovermod.recover = lambda acct: (
            recovered.append(acct) or (True, f"{acct}: recovered - 5h 3% 7d 4%"))
        con = db.connect()
        try:
            logs: list[str] = []
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll+recover: a dead token calls recover()", recovered == ["alpha"], recovered)
            check("poll+recover: recover's own message is logged, not a 'could not ask'",
                  any("recovered - 5h 3% 7d 4%" in m for m in logs)
                  and not any("could not ask" in m for m in logs), logs)
            check("poll+recover: the fail streak is reset, exactly like a plain success",
                  db.get_meta(con, "usage-fail:alpha") is None,
                  db.get_meta(con, "usage-fail:alpha"))
            check("poll+recover: the poll cursor moved on to the ordinary cadence",
                  db.get_cursor(con, "usage-poll:alpha") >= int(time.time()) + dmn_states.USAGE_POLL_S - 1,
                  db.get_cursor(con, "usage-poll:alpha"))
            check("poll+recover: recover() records itself - daemon does not double-record",
                  recorded == [], recorded)

            # --- a dead token: recover() refuses too - the old path, unchanged
            recovered.clear()
            db.set_cursor(con, "usage-poll:alpha", 0)
            db.set_meta(con, "usage-fail:alpha", None)
            recovermod.recover = lambda acct: (
                recovered.append(acct) or (False, "stub: refuses"))
            logs.clear()
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll+recover: refusal still tries recover() first",
                  recovered == ["alpha"], recovered)
            check("poll+recover: and falls into the unchanged backoff path",
                  db.get_meta(con, "usage-fail:alpha") == "1"
                  and any("could not ask" in m for m in logs), logs)
        finally:
            con.close()

        # --- the 5h window ended, but the plain read still succeeded --------
        recovered.clear()
        recorded.clear()
        now = int(time.time())
        write_status(home, "alpha", pct_5h=90, pct_7d=10,
                     reset_5h=now - 60, rl_at=now - 60)
        usage_api.read = lambda acct, refresh=True: reading_of(
            acct, 91, now + 18000, 11, None, now)

        def _recover_and_record(acct):
            recovered.append(acct)
            real_record(reading_of(acct, 0, int(time.time()) + 18000, 11, None,
                                          int(time.time())))
            return True, f"{acct}: recovered - 5h 0% 7d 11%"
        recovermod.recover = _recover_and_record
        con = db.connect()
        try:
            db.set_cursor(con, "usage-poll:alpha", 0)
            check("poll+recover setup: the 5h window has in fact ended",
                  dmn_usage_poll.usage_due(con, "alpha", status.read("alpha"), now)
                  == "its 5h window has ended")
            logs = []
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll+recover: the 5h boundary calls recover() too, "
                  "even though the plain read just succeeded",
                  recovered == ["alpha"], recovered)
            check("poll+recover: recover's own reading wins, not the stale plain one",
                  status.read("alpha").short.pct == 0, status.read("alpha").short.pct)

            # --- recover() refuses at the boundary: the plain reading still stands
            recovered.clear()
            db.set_cursor(con, "usage-poll:alpha", 0)
            # A reset of its own: the one above already had its turn.
            write_status(home, "alpha", pct_5h=90, pct_7d=10,
                         reset_5h=now - 120, rl_at=now - 120)
            recovermod.recover = lambda acct: (
                recovered.append(acct) or (False, "stub: refuses"))
            logs = []
            dmn_usage_poll.poll_usage(con, logs.append)
            check("poll+recover: a refusal at the boundary still tried recover()",
                  recovered == ["alpha"], recovered)
            check("poll+recover: and falls back to the plain reading that already succeeded",
                  status.read("alpha").short.pct == 91
                  and any("its 5h window has ended - 5h 91%" in m for m in logs), logs)
        finally:
            con.close()

        # --- the common path never spends a turn it does not need -----------
        recovered.clear()
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        usage_api.read = lambda acct, refresh=True: reading_of(
            acct, 6, int(time.time()) + 1800, 11, None, int(time.time()))
        con = db.connect()
        try:
            db.add_event(con, "backend", "Stop", {}, account="alpha")
            db.set_cursor(con, "usage-poll:alpha", 0)
            dmn_usage_poll.poll_usage(con, lambda _m: None)
            check("poll+recover: an ordinary due-for-a-reason poll never calls recover()",
                  recovered == [], recovered)
        finally:
            con.close()
    finally:
        (usage_api.read, accounts.logged_in,
         recovermod.recover, usage_api.record) = real_read, real_login, real_recover, real_record


def test_a_reset_window_gets_one_turn_per_reset_and_never_while_exhausted(home: Path) -> None:
    """T18: an account whose 5h or 7d window has reset is given one real turn
    so it is live again. The 5h case was already wired (#76); the 7d one was
    not - `poll_usage` matched the reason text of the 5h window only."""
    _daemon_home(home, 40)
    real = (usage_api.read, accounts.logged_in, recovermod.recover)
    turns: list[str] = []
    now = int(time.time())
    reading = {}

    def fake_recover(acct):
        turns.append(acct)
        return True, f"{acct}: recovered"

    def poll(con):
        db.set_cursor(con, "usage-poll:alpha", 0)
        dmn_usage_poll.poll_usage(con, lambda _m: None)

    try:
        accounts.logged_in = lambda acct: True
        recovermod.recover = fake_recover
        usage_api.read = lambda acct, refresh=True: reading_of(acct, *reading["v"], now)
        con = db.connect()
        try:
            # 7d ended, 5h fine: the week is what reset.
            write_status(home, "alpha", pct_5h=2, pct_7d=100, reset_5h=now + 3600,
                         reset_7d=now - 60, rl_at=now - 60)
            reading["v"] = (2, now + 3600, 0, now + 7 * 86400)
            poll(con)
            check("reset turn: a 7d window that has reset gets a turn", turns == ["alpha"], turns)

            # The server keeps naming the same reset: no second turn for it.
            write_status(home, "alpha", pct_5h=2, pct_7d=100, reset_5h=now + 3600,
                         reset_7d=now - 60, rl_at=now - 60)
            poll(con)
            check("reset turn: at most once per reset", turns == ["alpha"], turns)

            # A later reset is a new occasion.
            write_status(home, "alpha", pct_5h=2, pct_7d=100, reset_5h=now + 3600,
                         reset_7d=now - 30, rl_at=now - 30)
            poll(con)
            check("reset turn: the next reset gets its own", turns == ["alpha", "alpha"], turns)

            # Ended by the held figure, but the endpoint still says exhausted.
            turns.clear()
            write_status(home, "alpha", pct_5h=100, pct_7d=3, reset_5h=now - 60,
                         reset_7d=now + 86400, rl_at=now - 60)
            reading["v"] = (100, now + 1800, 3, now + 86400)
            poll(con)
            check("reset turn: never while the endpoint still reports the window exhausted",
                  turns == [], turns)

            # Nothing has reset: nothing to spend.
            write_status(home, "alpha", pct_5h=100, pct_7d=3, reset_5h=now + 1800,
                         reset_7d=now + 86400, rl_at=now - 60)
            db.add_event(con, "backend", "Stop", {}, account="alpha")
            poll(con)
            check("reset turn: a window still running gets none", turns == [], turns)

            # The switch (`hl account recover <name> on|off`, config.yaml
            # `usage_recover_<name>: false`): off, no turn is spent; on, one is.
            recovermod.recover = real[2]
            spent: list[str] = []
            real_model, real_detail = foldmod.run_model, usage_api.read_detailed
            foldmod.run_model = lambda prompt, acct, model: (spent.append(model) or (True, ""), )[-1]
            usage_api.read_detailed = lambda acct, refresh=False: (
                reading_of(acct, 0, now + 3600, 0, now + 7 * 86400, now), False)
            try:
                loadmod.set_usage_recover("alpha", False)
                write_status(home, "alpha", pct_5h=2, pct_7d=3, reset_5h=now + 3600,
                             reset_7d=now - 10, rl_at=now - 10)
                poll(con)
                check("reset turn: switched off, no turn is spent", spent == [], spent)
                loadmod.set_usage_recover("alpha", True)
                write_status(home, "alpha", pct_5h=2, pct_7d=3, reset_5h=now + 3600,
                             reset_7d=now - 10, rl_at=now - 10)
                poll(con)
                check("reset turn: on (the default), one Haiku turn is spent",
                      spent == [recovermod.RECOVER_MODEL], spent)
            finally:
                foldmod.run_model, usage_api.read_detailed = real_model, real_detail
        finally:
            loadmod.set_usage_recover("alpha", True)
            con.close()
    finally:
        usage_api.read, accounts.logged_in, recovermod.recover = real


def test_a_reading_past_the_carry_bound_is_carried_old_even_though_the_flag_is_false(
        home: Path) -> None:
    """#1799 part 2 (orcapipe): reverting status.py's rl_age_s check to main
    and re-running the full suite stayed green at 3486 passed, 0 failed -
    nothing pinned it directly. usage_api never sets rl_carried True in
    production (it always clears it to False on a successful poll, see its
    own record()), so this is the actual on-disk shape a stalled poll leaves:
    rl_carried False, rl_at old, collected_at fresh (something else, unrelated
    to rate limits, kept it that way). Must read carried_old and must not be
    assignable on that shape alone - not on a flag nobody sets."""
    use_home(home)
    write_status(home, "alpha", pct_5h=55, reset_5h=int(time.time()) + 9000,
                 rl_carried=False, rl_lost=False,
                 rl_at=int(time.time()) - status.CARRY_MAX_AGE_S - 60,
                 collected_at=int(time.time()))
    st = status.read("alpha")
    check("carried_old: a reading past the carry bound reads carried_old even "
          "with rl_carried False - the shape usage_api actually writes",
          st.short_source == "carried_old", st.short_source)
    check("carried_old: and it is not assignable",
          not st.assignable()[0], st.assignable()[1])


def test_a_carried_reading_with_no_age_at_all_reads_carried_old_not_live(
        home: Path) -> None:
    """auditor #1805 note 3: the rl_age_s reorder above, read the other
    direction, quietly loosened one case - rl_carried True with rl_at None
    used to read carried_old (nothing to bound the carry by), and after the
    reorder read carried, one step fresher than it earned. Unreached in
    production (usage_api's only writer always sets rl_at alongside
    rl_carried), but a payload that omits it must still read as too old to
    trust, not as newly live - the same test for pct_7d_source/rl7_at."""
    use_home(home)
    now = int(time.time())
    write_status(home, "alpha", pct_5h=40, reset_5h=now + 9000,
                 rl_carried=True, rl_at=None, collected_at=now,
                 pct_7d=30, reset_7d=now + 90000, rl7_carried=True, rl7_at=None)
    st = status.read("alpha")
    check("no-age carry: rl_carried True with rl_at None reads carried_old, "
          "not carried or live", st.short_source == "carried_old", st.short_source)
    check("no-age carry: the same holds for the weekly figure",
          st.long_source == "carried_old", st.long_source)


def test_context_tokens_override_falls_back_on_garbage() -> None:
    """#1669 proof: absent and garbage must both read as the default, and
    garbage must never read as 0 - that would fire the trigger on every tick,
    which is worse than ignoring a bad config entirely."""
    check("context_tokens: absent falls back to the default",
          agents.context_tokens({}) == triggers.CONTEXT_TOKENS)
    check("context_tokens: an explicit null falls back too",
          agents.context_tokens({"context_tokens": None}) == triggers.CONTEXT_TOKENS)
    check("context_tokens: zero is garbage here, not itself",
          agents.context_tokens({"context_tokens": 0}) == triggers.CONTEXT_TOKENS)
    check("context_tokens: negative falls back too",
          agents.context_tokens({"context_tokens": -5}) == triggers.CONTEXT_TOKENS)
    check("context_tokens: a string is garbage, not a figure to coerce",
          agents.context_tokens({"context_tokens": "lots"}) == triggers.CONTEXT_TOKENS)
    check("context_tokens: a bool is garbage too, not 0 or 1",
          agents.context_tokens({"context_tokens": True}) == triggers.CONTEXT_TOKENS)
    check("context_tokens: a real configured figure is used as-is",
          agents.context_tokens({"context_tokens": 300_000}) == 300_000)


def test_the_clamp_holds_a_small_window_agent_down() -> None:
    """#1669 proof: a model collect.ctx_size recognizes clamps the effective
    limit to CONTEXT_CLAMP_FRACTION of its own window, so a figure configured
    (or defaulted) for a 1M-token model cannot leave a 200k one with a check
    that never fires."""
    haiku_clamp = int(200_000 * triggers.CONTEXT_CLAMP_FRACTION)
    check("clamp: a figure sized for a 1M model is held down on haiku's window",
          status.effective_context_limit({"context_tokens": 500_000},
                                        "claude-haiku-4-5-20251001") == haiku_clamp,
          status.effective_context_limit({"context_tokens": 500_000},
                                        "claude-haiku-4-5-20251001"))
    check("clamp: the flat default is held down to haiku's clamp - 120,000, "
          "60% of haiku's 200,000 window, so there the clamp is the working limit",
          status.effective_context_limit({}, "claude-haiku-4-5-20251001") == haiku_clamp)
    check("clamp: a sane figure on a 1M-window model is not touched",
          status.effective_context_limit({"context_tokens": 300_000}, "claude-sonnet-5")
          == 300_000)
    check("clamp: an unrecognized model has no window to take a fraction of - "
          "the flat figure stands alone",
          status.effective_context_limit({"context_tokens": 900_000}, "some-future-model")
          == 900_000)


def test_an_unknown_model_now_hands_off_on_tokens(home: Path) -> None:
    """#1669 proof: today, collect.ctx_size(model_id) returns None for a model
    this box has never priced, so _pct(used, None) is None, ctx_pct is None,
    and the context trigger never fires - silently, forever, no refusal, no
    log line. ctx_used needs no window at all, so the same agent now hands
    off exactly like any other."""
    use_home(home)
    model = "some-future-model-nobody-has-priced"
    check("unknown model: collect genuinely does not know its window",
          collect.ctx_size(model) is None)
    limit = status.effective_context_limit({}, model)
    check("unknown model: no clamp applies - the flat default stands alone",
          limit == triggers.CONTEXT_TOKENS)
    t = triggers.evaluate(10, 200_000, ctx_limit=limit)
    check("unknown model: the trigger fires on the raw count anyway",
          t.fired and t.kind == "context", t.reason)
    quiet = triggers.evaluate(10, 50_000, ctx_limit=limit)
    check("unknown model: and stays quiet under its own default, same as any model",
          not quiet.fired, quiet.reason)


def test_a_stale_ctx_used_does_not_fire(home: Path) -> None:
    """#1669 proof: trusted_ctx_used gets the same staleness treatment
    trusted_ctx_pct already has (status.py's stale property) - a snapshot
    nobody has refreshed must not fire the trigger just because the number it
    once wrote was large."""
    use_home(home)
    agents.register("keeper")
    accounts.register("alpha")
    write_status(home, "alpha", pct_5h=10, pct_7d=10, reset_5h=int(time.time()) + 1800)
    write_agent_status("keeper", "alpha",
                       ctx_used={"cache_read_input_tokens": 900_000},
                       model_id="claude-sonnet-5",
                       collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
    st = status.read_for_agent("keeper", "alpha")
    check("stale ctx_used: trusted_ctx_used is None while the snapshot is stale",
          st.trusted_ctx_used is None, str(st.trusted_ctx_used))
    limit = status.effective_context_limit(agents.read("keeper"), st.model_id)
    t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used, ctx_limit=limit)
    check("stale ctx_used: the trigger does not fire on an untrusted figure",
          not t.fired, t.reason)

    # #1762 should-fix 2: read_for_agent's own "own.error or own.stale" branch
    # already nulls ctx_used for every agent read before trusted_ctx_used is
    # ever consulted, so the check above holds whether or not that property's
    # own guard exists - it is not what it was named to prove. Pinned here
    # through status.read() directly, on the agent's own file, which has no
    # such branch: only trusted_ctx_used's own `None if self.stale` stands
    # between a 900,000-token reading and a fired trigger.
    own = status.read("", paths.agent_status_file("keeper"))
    check("stale ctx_used: read() also carries the raw figure through unmasked",
          own.ctx_used == 900_000, own.ctx_used)
    check("stale ctx_used: it is trusted_ctx_used's own guard, pinned directly, "
          "that withholds it - not a masking branch upstream",
          own.trusted_ctx_used is None, own.trusted_ctx_used)


def test_status_read_sums_a_raw_ctx_used_dict_the_way_collect_does(home: Path) -> None:
    """#1762 should-fix 4: the line connecting every on-disk snapshot to the
    context trigger - status.read's `collect.tokens(raw_ctx_used) if
    isinstance(raw_ctx_used, dict) else _as_int(raw_ctx_used)` - was covered
    only incidentally, inside test_shared_account_context_isolation, a test
    named for account isolation that happens to assert a context trigger
    fires. Pinned directly here, against the same four-key shape
    collect.snapshot actually writes."""
    use_home(home)
    usage = {"input_tokens": 100, "output_tokens": 50,
             "cache_creation_input_tokens": 2_000, "cache_read_input_tokens": 300_000}
    write_status(home, "alpha", pct_5h=10, ctx_used=usage)
    st = status.read("alpha")
    check("read(): a dict ctx_used is summed the same way collect.tokens does",
          st.ctx_used == collect.tokens(usage), (st.ctx_used, collect.tokens(usage)))
    check("read(): and it is the real sum, not a passthrough of the dict itself",
          st.ctx_used == 302_150, st.ctx_used)


def test_a_new_account_gets_its_first_reading_without_anyone_asking(home: Path) -> None:
    """An account that exists, is logged in and has no status.json is asked
    about once by the daemon, with nobody waiting; asked once, it is not asked
    again on that ground, and one that is not logged in is not asked at all.

    The cadence is what this is about, and it outlived the mechanism: it used to
    start `probe.py` - a session and a real turn - and now it is one HTTPS GET
    (#1447). A never-logged-in account must still not cost a keychain
    subprocess every tick.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real_login, real_read = accounts.logged_in, usage_api.read
    asked: list[str] = []
    try:
        (home / "accounts" / "alpha" / "status.json").unlink()
        accounts.logged_in = lambda a: True  # noqa: E731
        usage_api.read = lambda acct, refresh=True: (  # noqa: E731
            asked.append(acct)
            or reading_of(acct, 3, int(time.time()) + 1800, 4, None, int(time.time())))
        st = status.read("alpha")
        check("new account: the state under test - it exists and has no reading",
              "alpha" in paths.list_accounts() and not st.exists)
        check("new account: and that is a reason to ask",
              dmn_usage_poll.usage_due(con, "alpha", st, int(time.time())) == "no reading yet")
        dmn_usage_poll.poll_usage(con, lambda _m: None)
        check("new account: the daemon asks it, unprompted", asked == ["alpha"], str(asked))
        asked.clear()
        dmn_usage_poll.poll_usage(con, lambda _m: None)
        check("new account: and asks once, not every tick", asked == [], str(asked))
        check("new account: because it has a reading now",
              dmn_usage_poll.usage_due(con, "alpha", status.read("alpha"), int(time.time())) is None)

        # An account nobody is logged in to costs a `security` subprocess per
        # check on macOS, so a check that came up empty is not repeated at once.
        checked: list[str] = []
        accounts.logged_in = lambda a: checked.append(a) or False  # noqa: E731
        (home / "accounts" / "alpha" / "status.json").unlink()
        db.set_cursor(con, "usage-poll:alpha", 0)
        dmn_usage_poll.poll_usage(con, lambda _m: None)
        check("new account: not logged in, nothing to ask", asked == [], str(asked))
        dmn_usage_poll.poll_usage(con, lambda _m: None)
        check("new account: and a login check that came up empty is not repeated next tick",
              checked == ["alpha"], str(checked))

        # A removed name added again is a new account, and is asked again.
        accounts.register("epsilon")
        accounts.logged_in = lambda a: True  # noqa: E731
        db.set_cursor(con, "usage-poll:alpha", int(time.time()) + 600)  # one per pass
        dmn_usage_poll.poll_usage(con, lambda _m: None)
        check("new account: epsilon was asked about", asked == ["epsilon"], str(asked))
        accounts.logged_in = lambda a: False  # noqa: E731 - logout took
        fresh_backup()                      # an account is not removed without a backup from the last minutes
        ok, msg = accounts.remove_account("epsilon", lambda: True, lambda a, e: 0, con)
        check("new account: removing it forgets its poll cooldown",
              ok and db.get_cursor(con, "usage-poll:epsilon") == 0, msg)
        accounts.register("epsilon")
        accounts.logged_in = lambda a: True  # noqa: E731
        asked.clear()
        dmn_usage_poll.poll_usage(con, lambda _m: None)
        check("new account: the same name added again is asked again",
              asked == ["epsilon"], str(asked))
    finally:
        accounts.logged_in, usage_api.read = real_login, real_read
        con.close()


# ---------------------------------------------------------------- regressions
def test_status_edge_cases(home: Path) -> None:
    use_home(home)

    # json.loads accepts bare NaN/Infinity; these must read as unknown, not
    # crash the daemon mid-tick.
    d = home / "accounts" / "nanny"
    d.mkdir(parents=True, exist_ok=True)
    for literal, label in [("NaN", "NaN"), ("Infinity", "Infinity"), ("-Infinity", "-Infinity")]:
        (d / "status.json").write_text(
            '{"session_id":"s","collected_at":%d,"pct_5h":%s}' % (int(time.time()), literal))
        st = status.read("nanny")
        check(f"status: {label} reads as unknown, not a crash", st.short.pct is None,
              str(st.short.pct))
        check(f"status: {label} then refuses assignment", not st.assignable()[0])

    (d / "status.json").write_text(
        '{"session_id":"s","collected_at":%d,"pct_5h":"85"}' % int(time.time()))
    check("status: a string percentage reads as unknown", status.read("nanny").short.pct is None)

    # A millisecond timestamp is a plausible collector bug. Clamping it to
    # age 0 made it look permanently fresh.
    write_status(home, "future", pct_5h=5, collected_at=int(time.time()) * 1000)
    st = status.read("future")
    check("status: a future timestamp is unknown age, not fresh", st.age_s is None,
          str(st.age_s))
    check("status: a future timestamp is stale", st.stale)
    check("status: a future timestamp refuses assignment", not st.assignable()[0])


def test_carry_bounds_and_losses(home: Path) -> None:
    """What `status.read` must still refuse, whoever wrote the snapshot.

    These were found through the statusLine collector, which is gone (D11), and
    they are reading rules rather than writing ones: a figure we cannot place
    in a window is ignorance and must not read as an idle window; a lost weekly
    figure is the absence of a refusal, which costs the rest of the week; and a
    figure goes old while collected_at keeps refreshing.

    The writing rules they were found with - usage never falls inside one
    window, a rollover replaces the high-water - are `usage_api.record`'s now
    and are checked in `test_usage_api_record_stands_in_for_a_probe`. What no
    longer exists at all is the carry: the endpoint states both windows or
    answers nothing, so there is no payload that quietly omits one.
    """
    use_home(home)
    now = int(time.time())
    path = home / "accounts" / "carry" / "status.json"
    read = lambda: status.read("carry", path)  # noqa: E731

    # 1. A figure with no window: real, and unplaceable. Reading it as a quiet
    #    window is a 95% account reading as 0%.
    write_status(home, "carry", pct_5h=95, reset_5h=None, rl_at=now, rl_lost=True,
                 pct_7d=10, reset_7d=now + 99999, rl7_at=now)
    st = read()
    check("carry: a lost figure reads as unknown, not as a quiet window",
          st.short_source == "unknown", st.short_source)
    check("carry: so it refuses assignment", not st.assignable()[0], st.assignable()[1])

    # And a first-hand reading is what takes the account out of that state.
    usage_api.record(reading_of("carry", 7, now + 1800, 10, now + 99999, now),
                     path=path)
    st = read()
    check("carry: a first-hand reading clears the loss",
          st.short.lost is False and st.short.pct == 7, f"{st.short.lost} {st.short.pct}")

    # 2. The weekly gate is written as "block if the figure is high", so a lost
    #    7d is the absence of a refusal rather than one.
    write_status(home, "carry", pct_5h=5, reset_5h=now + 1800, rl_at=now,
                 pct_7d=None, reset_7d=None, rl7_at=None, rl7_lost=True)
    st = read()
    check("carry: a lost 7d still refuses, rather than vanishing",
          not st.assignable()[0], st.assignable()[1])
    check("carry: the refusal names the weekly gate", "7d" in st.assignable()[1])

    # 3. A figure goes old even while collected_at stays fresh - the ordinary
    #    shape of an account whose poll has stopped answering while a session
    #    on it goes on writing its own transcript.
    write_status(home, "carry", pct_5h=55, reset_5h=now + 9000, rl_carried=True,
                 rl_lost=False, rl7_lost=False, pct_7d=10, reset_7d=now + 99999,
                 rl7_at=now, rl_at=now - 4 * 3600, collected_at=now)
    st = read()
    check("carry: a fresh collected_at does not make an old figure fresh",
          st.short_source == "carried_old", st.short_source)
    check("carry: it refuses new work", not st.assignable()[0], st.assignable()[1])
    check("carry: the refusal says how old it is", "ago" in st.assignable()[1])
    check("carry: but the trigger still gets the floor, not a null",
          st.short_trusted == 55, str(st.short_trusted))
    t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used)
    check("carry: so an idle agent is not handed off for usage_unknown",
          t.kind != "usage_unknown", t.reason)


def test_an_account_change_and_a_new_account_leave_project_memory_alone(home: Path) -> None:
    """Memory is per agent now (`hl memory`), so neither a launch that lands on
    another account nor `register` moves, links or logs anything of the old
    repo-keyed project memory."""
    os.environ["OCP_HOME"] = str(home)
    agents.register("mover")
    con = db.connect()
    try:
        db.set_account(con, "mover", "alpha")
        mem = home / "accounts" / "alpha" / ".claude" / "projects" / "-x" / "memory"
        mem.mkdir(parents=True)
        (mem / "note.md").write_text("learned on alpha\n")

        launch._record_terminal("mover", "beta")
        check("record_terminal: still records the new account",
              agents.current_account("mover", con) == "beta",
              agents.current_account("mover", con))
        check("record_terminal: carries no memory to the landing account",
              not (home / "accounts" / "beta" / ".claude" / "projects" / "-x").exists())
        check("record_terminal: logs no memory_migrated event",
              not con.execute("SELECT 1 FROM events WHERE kind = 'memory_migrated'").fetchone())
        check("record_terminal: the old account's memory is untouched",
              mem.is_dir() and not mem.is_symlink() and (mem / "note.md").exists())
    finally:
        con.close()
    accounts.register("gamma")
    check("register: links no project memory to shared/",
          not (home / "shared" / "memory").exists()
          and not (home / "accounts" / "gamma" / ".claude" / "projects").exists())


def test_unobserved_spend_stays_off_unreadable_accounts(home: Path) -> None:
    """A fold runs `claude -p`, which is headless and has no status line, so it
    is the one spender no collector ever sees. Choosing by the raw figure sent
    it to the accounts whose usage is already unobserved, because None passed
    the "not at the hard stop" test."""
    use_home(home)
    real_login = accounts.logged_in
    try:
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        now = int(time.time())
        write_status(home, "blind", pct_5h=None, rl_lost=True)
        write_status(home, "hot", pct_5h=100, reset_5h=now + 1800)
        check("fold: an account whose reading was lost is not a fallback",
              foldmod._cheapest_account() is None, str(foldmod._cheapest_account()))
        write_status(home, "usable", pct_5h=20, reset_5h=now + 1800)
        check("fold: a readable account is", foldmod._cheapest_account() == "usable",
              str(foldmod._cheapest_account()))

        # A carried figure is a floor, and a floor below the stop still has
        # room. This one is deliberate: the fold is small, and refusing here
        # would leave the day unrecorded on a quiet machine.
        for acct in ("usable", "hot"):
            (home / "accounts" / acct / "status.json").unlink()
        write_status(home, "old", pct_5h=40, reset_5h=now + 9000,
                     rl_carried=True, rl_at=now - 4 * 3600)
        check("fold: a carried floor with room is still usable",
              foldmod._cheapest_account() == "old", str(foldmod._cheapest_account()))
        st = status.read("old")
        check("fold: even though it takes no new agents",
              not st.assignable()[0] and st.short_source == "carried_old")

        # A stale reading is still an account this daemon cannot currently
        # read, and the fold must not prefer it over one with a genuinely
        # current reading. Neither account here has an actual pct_7d, so pace
        # ties at "unknown" for both and headroom decides - already in favor
        # of the readable one, before the fold's own exclusion even applies.
        write_status(home, "fresh", pct_5h=50, reset_5h=now + 1800,
                     reset_7d=now + 86400 * 2)
        write_status(home, "stale_idle", pct_5h=5, reset_5h=now + 1800,
                     reset_7d=now + 1800, collected_at=now - 3600)
        check("fold: pick_account already prefers the readable account "
              "on headroom, with no pct_7d behind either reset",
              status.pick_account(["fresh", "stale_idle"])[0] == "fresh")
        check("fold: and the fold's own exclusion agrees",
              foldmod._cheapest_account() == "fresh",
              str(foldmod._cheapest_account()))
    finally:
        accounts.logged_in = real_login
        tui._LOGIN.clear()


def test_subscription_type_is_recorded_and_cached(home: Path) -> None:
    """Request #167/#170: subscriptionType comes from `claude auth status`,
    claude's own report of what it is signed in as - never a credential file
    (AGENTS.md rule 1) - and is cached so the panel does not shell out to
    `claude` on every refresh."""
    use_home(home)
    accounts.register("gamma")
    real_login, real_run = accounts.logged_in, subprocess.run
    calls: list = []

    class _Result:
        stdout = json.dumps({"subscriptionType": "pro"})

    try:
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        subprocess.run = lambda cmd, **kw: (calls.append(cmd), _Result())[1]
        got = accounts.subscription_type("gamma")
        check("subscription_type: reads the plan from claude's own report",
              got == "pro", got)
        check("subscription_type: exactly one call was made", len(calls) == 1, str(calls))

        got2 = accounts.subscription_type("gamma")
        check("subscription_type: cached, not asked again", len(calls) == 1, str(calls))
        check("subscription_type: cached answer matches", got2 == "pro", got2)

        accounts.subscription_type("gamma", refresh=True)
        check("subscription_type: refresh=True asks again", len(calls) == 2, str(calls))

        real_login2 = accounts.logged_in
        accounts.logged_in = lambda a: False  # noqa: E731
        got3 = accounts.subscription_type("gamma", refresh=True)
        accounts.logged_in = real_login2
        check("subscription_type: cache survives even when asked again with no login",
              got3 == "pro", got3)
    finally:
        accounts.logged_in, subprocess.run = real_login, real_run


def test_burn_rate_ignores_carried_samples(home: Path) -> None:
    """A carried figure pairs an unchanging percentage with a collected_at that
    moves every tick. Sampling that flattens the measured rate to zero, and an
    account that was climbing then reads as one with nothing on it."""
    os.environ["OCP_HOME"] = str(home)
    rising = [(1000, 70), (1150, 74), (1300, 78)]
    live_burn = triggers.burn_rate_per_s(rising)
    check("burn: a rising account has a measurable rate", live_burn > 0, str(live_burn))

    # What the old sampling produced: ten ticks of the same carried figure,
    # each with a fresh timestamp, evicting the real samples.
    flattened = [(1450 + i * 30, 78) for i in range(10)]
    check("burn: flat carried samples measure as no burn at all",
          triggers.burn_rate_per_s(flattened) == 0.0)
    check("burn: the real rate is what shortens what an account has left for one more agent",
          triggers.stretch_s(20, live_burn) < triggers.stretch_s(20, 0.0))


def test_a_session_figure_is_not_shown_as_an_accounts(home: Path) -> None:
    """cost and context belong to one session, and the account file is written
    by whichever session rendered last. Reading them from there made an
    account's cost flip every second and gave an agent that has never run
    somebody else's context - and the trigger then judged it on that."""
    use_home(home)
    agents.register("never_ran")
    agents.register("has_session")
    # #1762 should-fix 3: with no ctx_used key on either fixture there was
    # nothing for never_ran to borrow, so "nor a raw token count either"
    # passed whether or not the borrow-guard existed. Both now carry one, the
    # shape collect.snapshot actually writes - a dict of counts - so the
    # account's 770,000 is a real, sizeable figure a bug would actually leak.
    write_status(home, "alpha", pct_5h=20, ctx_pct=77,
                 ctx_used={"input_tokens": 770_000}, cost_usd=9.99,
                 model_id="claude-haiku-4-5-20251001")
    (home / "agents" / "has_session").mkdir(parents=True, exist_ok=True)
    (home / "agents" / "has_session" / "status.json").write_text(json.dumps({
        "account": "alpha", "collected_at": int(time.time()), "session_id": "s1",
        "pct_5h": 20, "ctx_pct": 12, "ctx_used": {"input_tokens": 12_000},
        "cost_usd": 4.0}))

    st = status.read_for_agent("never_ran", "alpha")
    check("an agent with no session has no context, not someone else's",
          st.ctx_pct is None, str(st.ctx_pct))
    check("nor a raw token count either", st.ctx_used is None, str(st.ctx_used))
    check("and no session id to point at either", st.session_id is None)
    # auditor #1813 note 4: without its own snapshot, model_id must not be
    # borrowed either - a haiku account's last session would otherwise clamp
    # a never-run agent's (unset) context to haiku's window, not the default.
    check("nor the account's last-seen model either", st.model_id is None,
          str(st.model_id))
    check("but the account's rate limits still apply to it", st.short.pct == 20)
    check("so the trigger cannot fire on a context it does not have",
          triggers.evaluate(st.short_trusted, st.trusted_ctx_used).kind != "context")
    own = status.read_for_agent("has_session", "alpha")
    check("an agent with a session is judged on its own context", own.ctx_pct == 12,
          str(own.ctx_pct))
    check("on its own token count too, not the account's 770,000",
          own.ctx_used == 12_000, own.ctx_used)

    check("account cost is the live sessions summed, not the last writer's",
          status.live_cost("alpha") == 4.0, str(status.live_cost("alpha")))
    stale = home / "agents" / "has_session" / "status.json"
    d = json.loads(stale.read_text())
    d["collected_at"] = int(time.time()) - 100000
    stale.write_text(json.dumps(d))
    check("a session too old to trust is not counted in it",
          status.live_cost("alpha") is None, str(status.live_cost("alpha")))


def test_a_woken_agent_is_not_judged_on_the_snapshot_it_left(home: Path) -> None:
    """The reading was taken before the wake, and a closed agent's snapshot is
    stale by construction - nothing refreshes a status line while its session is
    gone. Evaluating it after the wake hands off the session just restored, and
    leaves the work that caused the wake still unread."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True     # the probe must not be what saves us
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-7", "pct_5h": 20}))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))

        # The account was this agent's only session, so its snapshot ages out too.
        write_status(home, "alpha", pct_5h=20, pct_7d=10,
                     collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        st = status.read_for_agent("backend", "alpha")
        check("wake: the reading it left behind is unknown, not a wall",
              not triggers.evaluate(st.short_trusted, st.trusted_ctx_used).fired)

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: it comes back", any("--resume sess-7" in t for t in sent), str(sent))
        check("wake: and is not handed off on the reading it left behind",
              row["state"] != dmn_states.PARKED, str(dict(row)))
        check("wake: nothing was folded for it",
              row["continuations"] == 0 and not db.in_flight_handoffs(con), str(dict(row)))
        check("wake: and it was not cleared", not any("/clear" in t for t in sent), str(sent))
        check("wake: the work that caused the wake is still queued",
              len(db.queued_for(con, "backend")) == 1)
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_find_transcript_walks_accounts_and_takes_only_a_plain_id(home: Path) -> None:
    _daemon_home(home, 20)
    for acct, proj, sid in (("alpha", "-wt", "sess-7"), ("beta", "-other", "sess-9")):
        f = vendors.config_dir(acct) / "projects" / proj / f"{sid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
    found = handoffmod.find_transcript("sess-9", "alpha")
    check("transcript: found under another account when not under its own",
          found is not None and "beta" in str(found), str(found))
    for bad in ("*", "sess-?", "[a-z]ess-7", "../x", ".hidden", "a/b", ""):
        check(f"transcript: {bad!r} names nothing", handoffmod.find_transcript(bad, "alpha") is None)


def test_a_restart_finds_the_previous_transcript_without_a_status_path(home: Path) -> None:
    """T169: a restart is told where the previous conversation is even when the
    status snapshot names no transcript - by the session id it does have, or by
    the agent's own hook events - and says so plainly when nothing finds it."""
    _daemon_home(home, 20)
    f = vendors.config_dir("alpha") / "projects" / "-wt" / "sess-old.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{}\n")
    con = db.connect()
    try:
        write_agent_status("backend", "alpha", session_id="sess-old")
        text, missing = handoffmod.capture(con, "backend")
        check("restart: a status with an id but no path finds the transcript by name",
              f"previous session: sess-old, recorded at {f}" in text and "the session's transcript path" not in missing, text)

        paths.agent_status_file("backend").unlink()
        db.add_event(con, "backend", "UserPromptSubmit", {"session_id": "sess-old"})
        db.add_event(con, "backend", "SessionStart", {"session_id": "sess-new"})
        text, missing = handoffmod.capture(con, "backend")
        check("restart: with no snapshot at all the last used session's events name it, "
              "and a fresh session's SessionStart is not taken for it",
              f"previous session: sess-old, recorded at {f}" in text and "the session's transcript path" not in missing, text)

        told = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("restart: a fresh start with no handoff still carries the path and the request",
              str(f) in told and "request: none in progress" in told, told)

        db.add_event(con, "backend", "UserPromptSubmit", {"session_id": "sess-gone"})
        told = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("restart: a transcript nothing finds is said, not dropped",
              "could not be found" in told and "sess-gone" not in told, told)
    finally:
        con.close()


def test_only_the_agents_own_events_move_its_activity_clock(home: Path) -> None:
    """Every kind ocp can write to events is either the agent acting or an
    event ocp itself caused, and last_activity's allow-list (db.AGENT_ACTIVITY_KINDS)
    must classify it as one or the other. An allow-list, not the deny-list this
    replaced: an unsorted kind there defaulted to activity, which is the harm
    #1527, #1533, and SessionEnd (ocp's own `/exit`, pinning a closed agent's
    clock to when it was closed) all share. Every classification is proven
    individually, not just asserted in bulk."""
    daemon_kinds = {"account_mismatch", "answered", "close", "close_failed", "close_task",
                    "mark_task_done", "reopen_task", "discard_task", "merge_task",
                    "cold_start", "dialog_declined", "edit_task", "release_task", "background_work_ended",
                    "block_task", "unblock_task", "dormant_block", "released_at_done", "task_unblocked", "child_result_told", "task_end_told", "bypass_accepted", "notice_told", "notices_digested", "task_deliver_refused", "closed_under", "dismissed", "request_reason",
                    "demoted_doing", "queue_task", "draft_task",
                    "fold", "fold_failed",
                    "forced_move", "fresh_close", "handoff", "moved_account", "nudged",
                    "notified", "park", "session_gone", "started_for_work", "terminal_closed", "terminal_gone",
                    "unstuck", "login_expired", "trust_confirmed", "wake", "wake_failed", "SessionStart",
                    "SessionEnd", "timebox_exceeded", "task_timeout_exceeded",
                    "unasked_running", "stale_background",
                    "stale_background_stopped", "dialog_accepted",
                    "dialog_accept_failed", "stop_wait_stuck", "forgotten_resolve_capped",
                    "landed", "retyped", "landing_lost", "shell_cleared",
                    "background_prompted", "background_abandoned", "background_started", "background_checked",
                    "schedule_fired",           # T605: a scheduled message the daemon typed
                    "settle_decision_task",     # the founder's task following its request out of the queue
                    "task_question",            # the founder's question on a task, recorded so the chat can tell it
                    "plan_changed"}             # T743: an account's plan, asked again, is not the one recorded
    # login_expired is an account's login found expired on an agent's screen, recorded
    # under the agent that showed it, not anything that agent did.
    # close_task, mark_task_done and reopen_task (#71) are here, not in
    # AGENT_ACTIVITY_KINDS, because none of the three is necessarily the
    # agent whose clock a careless future caller might attribute it to: a
    # close is the asker's sign-off (often the founder or a different
    # agent than the task's owner), a done-mark and a reopen are attributed
    # to whoever the CLI's own identity check names, but the allow-list is
    # what decides what any future caller - one that does not refuse for
    # want of an identity - can do to a clock, not just what today's callers
    # happen to send (#1565 SF3).
    # edit_task is here for the same reason: editing the board is not the
    # edited agent doing work, and it is often the founder or a manager
    # editing someone else's task entirely. block_task and unblock_task are
    # the same shape as edit_task (task #21/#1884); task_unblocked is
    # close_task's own aftermath, recorded under whoever closed the
    # blocker, not the blocked task's owner - never that owner's activity.
    # task_deliver_refused (T325) is recorded under the owner a queued
    # task's delivery was attempted for, not anything that owner did.

    # Literal add_event(agent, "kind", ...) call sites: every one sorted, and
    # the scan proven to have read every site, not just the ones its regex
    # can parse - a call it cannot read left an unsorted kind passing at
    # 11/11 until this count was added.
    found, calls = [], 0
    for f in Path(dmn_states.__file__).parent.parent.rglob("*.py"):
        text = f.read_text()
        found += re.findall(r'add_event\s*\([^,()]+,[^,()]+,\s*"([a-zA-Z_]+)"', text)
        calls += len(re.findall(r'add_event\s*\(', text))
    check("activity: every literal kind ocp writes is sorted here",
          set(found) <= db.AGENT_ACTIVITY_KINDS | daemon_kinds,
          str(sorted(set(found) - db.AGENT_ACTIVITY_KINDS - daemon_kinds)))
    check("activity: the scan reads every call site, not just the ones its regex can parse",
          len(found) == calls - 2, f"{len(found)} literal kinds found, {calls} add_event( sites "
          "(expected 2 unmatched: the def in db/events.py, the kind-as-variable forwarder in events.py)")

    # events.py's forwarder passes `event`, a variable ranging over CAPTURED -
    # invisible to the regex above at any count, so CAPTURED is sorted here
    # directly. This is the site #1536's arithmetic exempted without checking
    # what flowed through it: every hook kind an agent's own live session can
    # send.
    check("activity: every hook kind ocp captures is sorted here",
          evmod.CAPTURED <= db.AGENT_ACTIVITY_KINDS | daemon_kinds,
          str(sorted(evmod.CAPTURED - db.AGENT_ACTIVITY_KINDS - daemon_kinds)))

    _daemon_home(home, 20)
    con = db.connect()
    try:
        for kind in sorted(daemon_kinds):
            con.execute("DELETE FROM events WHERE agent = 'backend'")
            db.add_event(con, "backend", kind, {})
            check(f"activity: {kind} is an event ocp caused, not activity",
                  db.last_activity(con, "backend") is None)
        for kind in sorted(db.AGENT_ACTIVITY_KINDS):
            con.execute("DELETE FROM events WHERE agent = 'backend'")
            db.add_event(con, "backend", kind, {})
            check(f"activity: {kind} is the agent's own and moves the clock",
                  db.last_activity(con, "backend") is not None)
    finally:
        con.close()


def test_a_dead_sessions_id_is_kept_only_from_a_matching_snapshot(home: Path) -> None:
    """A move by hand changes agent.yaml (so `current_account` answers at
    once) without touching the status file a session on the OLD account
    already wrote - that file is the new account's until a session actually
    runs there. The sweep used to trust its session id anyway once the
    process died, on the theory that "its own id is not in doubt" - which
    reproduced in production as a --resume recorded for a session that lives
    under a config directory this agent is no longer even pointed at, and
    that launch failed every time it was retried.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.live_terminals, obsmod.live_sessions, orcamod.available)
    f = home / "agents" / "backend" / "status.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: {}     # the process is gone

        # Stale by account, not only by age: this is beta's snapshot, and
        # `current_account` already says alpha.
        f.write_text(json.dumps({"account": "beta", "collected_at": int(time.time()),
                                 "session_id": "beta-sess"}))
        dmn_reconciler.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("sweep: a session id from a snapshot on the wrong account is dropped",
              row["closed_at"] is not None and row["resume_session"] is None,
              str(dict(row)))

        # Reopen it and let its own account's snapshot be the one on file.
        db.clear_closed(con, "backend")
        f.write_text(json.dumps({"account": "alpha", "collected_at": int(time.time()),
                                 "session_id": "alpha-sess"}))
        dmn_reconciler.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("sweep: and kept once the snapshot is the account it is on now",
              row["closed_at"] is not None and row["resume_session"] == "alpha-sess",
              str(dict(row)))
    finally:
        orcamod.live_terminals, obsmod.live_sessions, orcamod.available = real
        con.close()


def test_a_moved_walled_wake_names_the_new_account(home: Path) -> None:
    """The one branch where `choose_account` can name an account other than
    the agent's own: no `resume_session` means this is the move's wake, not a
    resume, so it is free to land wherever there is room, not tied to the
    account its last session lived under. Before this, only the resuming
    branch (where the account it checks is always the agent's own) was ever
    exercised.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real_login = accounts.logged_in
    try:
        db.set_account(con, "backend", "alpha")
        db.set_closed(con, "backend", None)   # no resume id: the move's wake
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        write_status(home, "beta", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        row = db.get_agent_state(con, "backend")
        check("moved: this row has no resume id - the move's wake",
              row["resume_session"] is None, dict(row))
        check("moved: current_account is still the old one - nothing has "
              "started under the new one yet",
              agents.current_account("backend", con) == "alpha", row)
        check("moved: but the wait is checked against the account it would "
              "actually land on, which has room",
              launch.choose_account("backend", resuming=False, con=con) == "beta")
        check("moved: so it is not walled, even though the agent's own "
              "account is",
              launch.wake_wait("backend", row, con) is None)
    finally:
        accounts.logged_in = real_login
        con.close()


def test_a_resume_is_not_woken_onto_an_account_at_its_weekly_wall(home: Path) -> None:
    """The resume branch is pinned to the account its session is tied to and
    cannot follow an account change - nothing upstream refuses that account
    on 7d the way `find_room` does for a move, so `wake_wait` is the only
    thing standing between a resume and waking straight into the weekly
    wall. Reverting the 7d branch alone (leaving the 5h one) makes this
    fail while test_a_moved_walled_wake_names_the_new_account still passes,
    since that one only exercises the move branch."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_account(con, "backend", "alpha")
        db.set_closed(con, "backend", "sess-old")   # a resume id: the resume branch
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=10, pct_7d=100, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800, reset_7d=int(time.time()) + 86400)
        row = db.get_agent_state(con, "backend")
        check("resumed: this row has a resume id", row["resume_session"] is not None, dict(row))
        w = launch.wake_wait("backend", row, con)
        check("resumed: 5h has room, but 7d is at the wall - still walled, and says which window",
              w is not None and w.window == "7d" and w.pct == 100, w)
        reason = launch.wake_wait_reason("backend", row, con) or ""
        check("resumed: the sentence names the weekly window, not the 5h one",
              "7d window" in reason, reason)
        check("resumed: the rollover is dated, not a bare clock that would "
              "claim a four-day-out wall rolls over today",
              time.strftime("%m-%d", time.localtime(int(time.time()) + 86400)) in reason,
              reason)
        write_status(home, "alpha", pct_5h=10, pct_7d=99, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800, reset_7d=int(time.time()) + 86400)
        check("resumed: one point under the wall is not walled",
              launch.wake_wait("backend", row, con) is None)
    finally:
        con.close()


def test_a_start_with_no_room_says_why_and_is_not_a_failed_attempt(home: Path) -> None:
    """Every account at its wall: a closed agent's fresh wake and a spawn for
    waiting work both return no_room's text, name the account that frees up
    first, and leave wake_failures alone; a resume with no recorded account
    refuses in build; wake_wait says which account it waits on."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    real = (orcamod.available, orcamod.alive, spawnmod.spawn)
    try:
        orca_seen()
        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        db.set_closed(con, "backend", None)   # no resume id: a fresh start
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        con.commit()
        check("no room: find_room has nothing to offer", launch.find_room("backend") is None)

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("no room: a walled fresh wake waits and names the account",
              not ok and "alpha" in why and row["wake_failures"] == 0, why)

        w = launch.wake_wait("backend", row, con)
        check("no room: wake_wait, with no resume id, names the account that frees first",
              w is not None and w.account == "alpha" and w.window == "5h", w)

        # Nothing walled, nothing logged in: wake_wait has no wall to name, so
        # the guard in _wake_agent is what refuses, with no_room's own text.
        (home / "accounts" / "alpha" / ".claude" / ".credentials.json").unlink()
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("no room: a fresh wake with no account to go to returns no_room's text",
              not ok and why == launch.no_room("backend") and "logged in" in why, why)
        check("no room: and it is not a failed attempt",
              db.get_agent_state(con, "backend")["wake_failures"] == 0)

        spawned: list[str] = []
        spawnmod.spawn = lambda *a, **k: (spawned.append("x"), (False, "no"))[1]
        agents.register("marina", repo=str(home / "checkout"))
        msg = dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        check("no room: a spawn for waiting work is not attempted, and says why",
              not spawned and launch.no_room("marina") in msg, msg)
        check("no room: and it is not a failed attempt either",
              (db.get_agent_state(con, "marina") or {"wake_failures": 0})["wake_failures"] == 0)

        db.clear_closed(con, "backend")
        con.execute("UPDATE agent_state SET account = NULL WHERE agent = 'backend'")
        con.commit()
        try:
            launch.build("backend", ["--resume", "x"])
            check("no room: a resume with no recorded account is refused", False, "built")
        except SystemExit as e:
            check("no room: a resume with no recorded account is refused",
                  "no recorded session to resume" in str(e), str(e))
    finally:
        orcamod.available, orcamod.alive, spawnmod.spawn = real
        con.close()


def test_a_move_has_nowhere_to_go_when_every_account_is_walled(home: Path) -> None:
    """With every account at its own wall, find_room returns None and a fresh
    launch has no account: there is no preferred one to fall back to."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_account(con, "backend", "alpha")
        db.set_closed(con, "backend", None)   # no resume id: the move's wake
        write_status(home, "alpha", pct_5h=10, pct_7d=100, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800, reset_7d=int(time.time()) + 86400)
        check("move, nowhere else: find_room has nothing to offer - alpha is walled and alone",
              launch.find_room("backend") is None)
        check("move, nowhere else: choose_account has no account either",
              launch.choose_account("backend", resuming=False, con=con) is None)
    finally:
        con.close()


def test_a_usage_park_is_woken_when_the_reading_clears(home: Path) -> None:
    """Parking is a level trigger - a usage reading over the line - and
    un-parking was only an edge: an open session slept to `wake_at` even
    after that same reading, evaluated the same way, would no longer fire.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage")
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: an open session wakes the moment the reading clears, "
              "not at the deadline it was given",
              row["state"] == dmn_states.QUEUED and row["wake_at"] is None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_a_usage_park_stays_parked_while_the_reading_is_at_the_wall(home: Path) -> None:
    """The un-park question and the trigger are the same `evaluate()`, so a
    reading that parked an agent cannot also wake it, at any number of ticks:
    it stays parked until the reading is off the wall."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
            dmn_delivery.session_present)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        # A re-fold, if the flap caused one, must be able to run to its end.
        orcamod.idle_now = lambda h, **kw: True
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        dmn_delivery.session_present = lambda a: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage")
        # Past MIN_HANDOFF_INTERVAL_S, so that guard cannot be what keeps a
        # re-fold from happening in the ticks below.
        con.execute("UPDATE handoff SET updated = ? WHERE agent = 'backend'",
                    (int(time.time()) - 2 * dmn_states.MIN_HANDOFF_INTERVAL_S,))
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        # Still at the wall, with work waiting and the account's reset inside T:
        # not cleared, and not worth a cold start to leave.
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        before = db.get_agent_state(con, "backend")
        continuations_before = int(before["continuations"])

        for _ in range(5):
            dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
            row = db.get_agent_state(con, "backend")
            check("park: still parked - the reading is still at the wall",
                  row["state"] == dmn_states.PARKED, str(dict(row)))
        # A state that flapped back to PARKED every time would still look fine
        # here if this only checked the label; a handoff per round would not.
        check("park: no handoff was ever paid for across the flap",
              int(row["continuations"]) == continuations_before,
              dict(before=continuations_before, after=row["continuations"]))
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
         dmn_delivery.session_present) = real
        con.close()


def test_a_weekly_park_stays_parked_while_the_week_is_full(home: Path) -> None:
    """A weekly park is un-parked on the weekly reading too: 5h at 10% with 7d at
    100% is still the condition that parked it."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
            dmn_delivery.session_present)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        # A re-fold, if the flap caused one, must be able to run to its end.
        orcamod.idle_now = lambda h, **kw: True
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        dmn_delivery.session_present = lambda a: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage_7d")
        # Past MIN_HANDOFF_INTERVAL_S, so that guard cannot be what keeps a
        # re-fold from happening in the ticks below.
        con.execute("UPDATE handoff SET updated = ? WHERE agent = 'backend'",
                    (int(time.time()) - 2 * dmn_states.MIN_HANDOFF_INTERVAL_S,))
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=10, pct_7d=100, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800, reset_7d=int(time.time()) + 86400)
        before = db.get_agent_state(con, "backend")
        continuations_before = int(before["continuations"])

        for _ in range(5):
            dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
            row = db.get_agent_state(con, "backend")
            check("park: still parked - the reading is still at the wall",
                  row["state"] == dmn_states.PARKED, str(dict(row)))
        # A state that flapped back to PARKED every time would still look fine
        # here if this only checked the label; a handoff per round would not.
        check("park: no handoff was ever paid for across the flap",
              int(row["continuations"]) == continuations_before,
              dict(before=continuations_before, after=row["continuations"]))
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
         dmn_delivery.session_present) = real
        con.close()


def test_a_context_park_does_not_auto_clear_on_a_usage_reading(home: Path) -> None:
    """A context park is not a usage park either, even though PARKED alone
    does not say so - only the handoff that caused it does. A clear usage
    reading is not the condition that parked this agent, so it stays parked
    on its own deadline, the same as any other cause this scope excludes.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "context")
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) + 3600, detail="context; wakes at 23:00")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: a context park is not a usage park - it keeps its own deadline",
              row["state"] == dmn_states.PARKED and row["wake_at"] is not None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_an_assumed_account_does_not_auto_clear_a_park(home: Path) -> None:
    """Case A: the db account column is null - `current_account` fell back
    to agent.yaml's preference rather than reporting a fact - and the
    reading that preference points at looks clear. That is the exact
    divergence that parked keeper for 'usage' against the wrong account's
    reading on 2026-09-18 while its real account sat at 3%; un-parking on
    that same assumption would wake it into whatever its real, unknown
    account is actually doing. It stays parked - no better, no worse than
    before this fix - until something records which account it is on.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        # The column is null, as it is from `started_for_work` until the
        # launched process records itself.
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        con.commit()
        _park_via_handoff(con, "backend", "usage")
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        row = db.get_agent_state(con, "backend")
        check("park: the account column really is null here",
              row["account"] is None, dict(row))

        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: an agent with no known account never un-parks",
              row["state"] == dmn_states.PARKED and row["wake_at"] is not None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_account_reading_prefers_recorded_then_reported_then_assumed(home: Path) -> None:
    """Three sources, and only the first two are facts. A recorded column
    wins outright; failing that, a fresh report from the agent's own running
    session beats a bare config preference - and beats even a recorded
    column when that column itself only holds an earlier guess, never
    confirmed. Only when neither exists at all does agent.yaml's preference
    stand in, and it is never mistaken for either fact.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_account(con, "backend", "alpha")
        r = agents.account_reading("backend", con)
        check("account: a recorded column wins outright",
              r.account == "alpha" and r.source == "recorded", r)

        db.set_account(con, "backend", None)
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        write_agent_status("backend", "beta", pct_5h=10)
        r = agents.account_reading("backend", con)
        check("account: null column falls to a fresh report",
              r.account == "beta" and r.source == "reported", r)

        write_agent_status("backend", "beta", pct_5h=10,
                           collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        r = agents.account_reading("backend", con)
        check("account: a stale report is not evidence - nothing says",
              r is None, r)

        db.set_account(con, "backend", "alpha", source="assumed")
        write_agent_status("backend", "beta", pct_5h=10)
        r = agents.account_reading("backend", con)
        check("account: a fresh report outranks a column that only holds a "
              "guess of its own",
              r.account == "beta" and r.source == "reported", r)
    finally:
        con.close()


def test_supervise_reads_the_reported_account_not_an_assumed_preference(home: Path) -> None:
    """The incident itself: a null recorded column used to mean reading
    whatever agent.yaml prefers as if it were this agent's own usage. Backend
    prefers alpha, which is pinned at the wall; its real, running session -
    visible only through its own fresh report - is on beta, nowhere near it.
    Before this fix `supervise` read alpha's 100% and parked an agent that
    was not spending it; now it reads the account the session actually
    reports and leaves it alone.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        write_status(home, "beta", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        write_agent_status("backend", "beta", pct_5h=5, ctx_pct=5)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        con.commit()

        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("account: a park against the wrong, assumed account no longer happens",
              row["state"] != dmn_states.PARKED, dict(row))
    finally:
        con.close()


def test_a_park_against_an_assumed_account_gets_a_short_recheck(home: Path) -> None:
    """A park's ceiling is `reset_5h` off whatever account it judged the
    trigger against - and against an assumed one, that number belongs to
    agent.yaml's preference, not to any account this agent is shown to be
    on. It fails closed to a short re-look instead of sleeping the hours
    that account's own window happens to have left.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        # No recorded column, no fresh report: nothing says which account,
        # and alpha is hours from rolling over.
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        con.commit()
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 4 * 3600)
        t = triggers.evaluate(95, 10)
        before = int(time.time())
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("account: an assumed park gets a short re-look, not alpha's own "
              "4h-out rollover",
              row["state"] == dmn_states.PARKED
              and row["wake_at"] is not None
              and row["wake_at"] < before + dmn_states.ASSUMED_ACCOUNT_RECHECK_S + 5,
              dict(row))
    finally:
        con.close()


def test_a_resumed_handoff_also_fails_closed_on_an_assumed_account(home: Path) -> None:
    """The same ceiling rule, through `resume_handoff`'s own path: a restart
    recovering an in-flight handoff must not sleep against agent.yaml's
    preference either.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 4 * 3600)
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        con.commit()
        db.start_handoff(con, "hoff-restart2", "backend", None, "usage",
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")
        before = int(time.time())
        dmn_handoff.resume_handoff(con, row, lambda _m: None)
        fresh = db.get_agent_state(con, "backend")
        check("account: a resumed handoff's park also gets a short re-look, "
              "not alpha's own 4h-out rollover",
              fresh["state"] == dmn_states.PARKED
              and fresh["wake_at"] is not None
              and fresh["wake_at"] < before + dmn_states.ASSUMED_ACCOUNT_RECHECK_S + 5,
              dict(fresh))
    finally:
        con.close()


def test_a_spawned_agent_gets_an_assumed_account_until_launch_confirms_it(home: Path) -> None:
    """The window between a spawn and that spawn's own launch reporting in
    is exactly where a null column used to sit undiagnosable. It now carries
    a guess instead, marked as one - `account_reading` still prefers a fresh
    report over it the moment there is one, and prefers it over nothing
    (the previous behaviour) until then.
    """
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    real_spawn = spawnmod.spawn
    try:
        def fake_spawn(agent, repo, base="main", prompt=None):
            db.set_terminal(con, agent, "term_new", None)
            return True, "spawned"
        spawnmod.spawn = fake_spawn
        agents.register("marina", repo=str(home / "checkout"))
        db.add_request(con, "reviewer", "marina", "look at this", None,
                       "peer", "async", None, 0, None)

        dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        row = db.get_agent_state(con, "marina")
        check("account: a fresh spawn has no account until its launch records one",
              row["account"] is None, dict(row))
    finally:
        spawnmod.spawn = real_spawn
        con.close()


def test_starts_for_waiting_work_that_keep_failing_stall_the_agent(home: Path) -> None:
    """T483: legacy sat IDLE for three days with work waiting - three failed starts
    latched the streak with nothing to show for it, and `ocp unstall` takes only
    STALLED. The third failure stalls it, with the reason, and unstall lifts it."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    real_spawn = spawnmod.spawn
    try:
        spawnmod.spawn = lambda *a, **k: (False, "worktree create failed: invalid reference: main")
        agents.register("marina", repo=str(home / "checkout"))
        db.add_request(con, "reviewer", "marina", "look at this", None, "peer", "async", None, 0, None)

        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS - 1):
            dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        row = db.get_agent_state(con, "marina")
        check("start stall: short of the limit it is still tried again",
              row["state"] != dmn_states.STALLED and row["wake_failures"] == dmn_states.MAX_FAILED_ATTEMPTS - 1, dict(row))

        dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        row = db.get_agent_state(con, "marina")
        check("start stall: the last failure stalls it and keeps the reason",
              row["state"] == dmn_states.STALLED and "invalid reference: main" in row["detail"], dict(row))

        actions.unstall(con, "marina")
        row = db.get_agent_state(con, "marina")
        check("start stall: unstall clears the streak so the next tick tries again",
              row["wake_failures"] == 0 and row["state"] != dmn_states.STALLED, dict(row))
    finally:
        spawnmod.spawn = real_spawn
        con.close()


def test_an_agent_latched_idle_before_the_stall_existed_stalls_with_its_reason(home: Path) -> None:
    """T502: legacy and personal were IDLE at wake_failures=3 from before T483, so the
    stall (which fires on a fresh failure) never reached them. One more try either
    starts them or stalls them with the reason."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    real_spawn = spawnmod.spawn
    try:
        calls: list[int] = []
        spawnmod.spawn = lambda *a, **k: (calls.append(1), (False, "worktree create failed: invalid reference: main"))[1]
        agents.register("marina", repo=str(home / "checkout"))
        db.add_request(con, "reviewer", "marina", "look at this", None, "peer", "async", None, 0, None)
        db.bump_counter(con, "marina", "wake_failures", dmn_states.MAX_FAILED_ATTEMPTS)

        dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        row = db.get_agent_state(con, "marina")
        check("latched idle: it is tried once more and stalls with the reason",
              len(calls) == 1 and row["state"] == dmn_states.STALLED
              and "invalid reference: main" in row["detail"], f"{calls} {dict(row)}")

        dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        check("latched idle: once stalled it is left alone, not tried every tick", len(calls) == 1, str(calls))

        # An old latch whose cause is gone starts instead of stalling.
        agents.register("second", repo=str(home / "checkout"))
        db.add_request(con, "reviewer", "second", "look", None, "peer", "async", None, 0, None)
        db.bump_counter(con, "second", "wake_failures", dmn_states.MAX_FAILED_ATTEMPTS)
        spawnmod.spawn = lambda agent, *a, **k: (db.set_terminal(con, agent, "term_new", None), (True, "spawned"))[1]
        dmn_lifecycle._start_for_work(con, "second", lambda _m: None)
        row = db.get_agent_state(con, "second")
        check("latched idle: a cause that is gone lets it start and clears the streak",
              row["wake_failures"] == 0 and row["state"] != dmn_states.STALLED, dict(row))
    finally:
        spawnmod.spawn = real_spawn
        con.close()


def test_a_near_wall_7d_account_is_still_a_candidate_but_not_guaranteed_the_pick(
        home: Path) -> None:
    """Founder's decision #254: an account at 5h 9% / 7d 99% is spendable to
    the weekly wall, not held back short of it - it must still be a candidate,
    and an agent working on it moves only once 7d actually reaches its own
    wall. Which of two candidates the *pick* prefers is now pace (#1001), not
    #254's concern; alpha at 99% with an hour left is close to on
    pace (elapsed is almost the whole week already), while beta at 10% with a
    day left is well behind its own - so beta is picked here, and alpha's
    place in this test is to prove it is not refused for being this close to
    its wall, not that it wins the pick."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")

    # alpha's weekly headroom expires soon and is spendable to the wall itself.
    now = int(time.time())
    def alpha(pct5, pct7):
        write_status(home, "alpha", pct_5h=pct5, pct_7d=pct7,
                     reset_7d=now + 3600, reset_5h=now + 1800)
    write_status(home, "beta", pct_5h=9, pct_7d=10,
                 reset_7d=now + 90000, reset_5h=now + 1800)
    alpha(9, 99)
    allowed, why = status.read("alpha").assignable()
    check("7d wall: 99% is still assignable, not held back short of the wall",
          allowed, why)
    picked, reasons = status.pick_account(["alpha", "beta"], load={})
    check("7d wall: at 99%, pace picks the account further behind its own "
          "weekly pace instead (beta, 10% with a day left)",
          picked == "beta", str((picked, reasons)))
    alpha(9, 100)
    check("7d wall: at the wall it is not picked",
          status.pick_account(["alpha", "beta"], load={})[0] == "beta")
    # The 5h side is a stretch, not a line: an idle account is picked while one
    # more agent would last the least worth starting, and not once it would not.
    alpha(70, 10)
    check("5h: 70% with nothing on it lasts long enough and is picked",
          status.pick_account(["alpha", "beta"], load={})[0] == "alpha")
    alpha(85, 10)
    picked, reasons = status.pick_account(["alpha", "beta"], load={})
    check("5h: 85% would last 20 minutes and is not picked",
          picked == "beta" and "under the 30m" in dict(reasons)["alpha"], str(reasons))
    alpha(100, 10)
    check("5h: at the wall it is not picked",
          status.pick_account(["alpha", "beta"], load={})[0] == "beta")
    alpha(9, 100)

    con = db.connect()
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        sessions_are("keeper")
        db.set_account(con, "keeper", "alpha")

        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        t = triggers.evaluate(9, 10, long_pct=100)
        check("7d wall: 5h 9% / 7d 100% fires a move trigger on the 7d figure",
              t.fired and t.kind == "usage_7d" and "longest window" in t.reason, t.reason)

        real = (orcamod.available, orcamod.alive, orcamod.close_session,
               orcamod.relaunch, orcamod.idle_now, dmn_delivery.wait_for_session)
        try:
            orca_idle()
            orcamod.close_session = exit_ends_session("keeper")
            orcamod.relaunch = lambda h, cmd, **kw: (True, "relaunched")
            dmn_delivery.wait_for_session = lambda agent, timeout=0: True
            keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
            check("7d wall: the agent moves off an account that hit its 7d wall",
                  dmn_handoff._move_to_room(con, "keeper", t, lambda _m: None)[0])
            check("7d wall: it landed on the account with weekly room",
                  agents.current_account("keeper", con) == "beta",
                  agents.current_account("keeper", con))
        finally:
            (orcamod.available, orcamod.alive, orcamod.close_session,
             orcamod.relaunch, orcamod.idle_now, dmn_delivery.wait_for_session) = real
    finally:
        con.close()


def test_an_agent_is_judged_on_the_account_its_process_is_on(home: Path) -> None:
    """keeper #500: the column said gamma, the process was still on beta, and
    the unpark read gamma's room six times into beta's rate limit."""
    _daemon_home(home, 20)
    _with_accounts("beta", "gamma")
    con = db.connect()
    real = (orcamod.available, orcamod.alive, obsmod.env_of)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "gamma")
        _park_via_handoff(con, "backend", "usage")
        now = int(time.time())
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=now + 3600, detail="usage; wakes at 23:00")
        write_status(home, "beta", pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=now + 1800)
        write_status(home, "gamma", pct_5h=5, pct_7d=10, ctx_pct=5, reset_5h=now + 1800)
        dmn_delivery._seen["backend"] = obsmod.Observed(session_seen=True, session_pid=4242)

        _process_on("beta")
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        row = db.get_agent_state(con, "backend")
        check("process: on beta at the wall, it is not unparked on gamma's room",
              row["state"] == dmn_states.PARKED, str(dict(row)))
        check("process: and the column is set to where the process is",
              row["account"] == "beta", str(dict(row)))
        check("process: the disagreement is on the record",
              any(e["kind"] == "account_mismatch"
                  for e in db.events_after(con, 0, agent="backend")))

        # The same world with the process where the column says: gamma has room.
        db.set_account(con, "backend", "gamma")
        _process_on("gamma")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("process: on gamma, gamma's room does unpark it",
              db.get_agent_state(con, "backend")["state"] == dmn_states.QUEUED,
              str(dict(db.get_agent_state(con, "backend"))))
    finally:
        (orcamod.available, orcamod.alive, obsmod.env_of) = real
        dmn_delivery._seen.pop("backend", None)
        con.close()


def test_a_move_is_not_recorded_while_the_process_is_still_on_the_old_account(
        home: Path) -> None:
    """A move that was asked for is not one that happened until the process
    that is running is on the new account."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=9, pct_7d=100, reset_7d=now + 3600, reset_5h=now + 1800)
    write_status(home, "beta", pct_5h=9, pct_7d=10, reset_7d=now + 90000, reset_5h=now + 1800)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.idle_now, dmn_delivery.wait_for_session, obsmod.env_of)
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        table = sessions_are("keeper")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        t = triggers.evaluate(9, 10, long_pct=100)
        orca_idle()
        orcamod.close_session = exit_ends_session("keeper")
        orcamod.relaunch = lambda h, cmd, **kw: (table.__setitem__("keeper", 4200), (True, "ok"))[1]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")

        _process_on("alpha")
        check("move: a process that came back on the old account is not a move",
              not dmn_handoff._move_to_room(con, "keeper", t, lambda _m: None)[0])
        check("move: and the column still names the account it is on",
              agents.current_account("keeper", con) == "alpha",
              agents.current_account("keeper", con))
        check("move: no moved_account event was written",
              not any(e["kind"] == "moved_account"
                      for e in db.events_after(con, 0, agent="keeper")))

        # Back to a live session, then a relaunch that does land on beta.
        con.execute("UPDATE agent_state SET closed_at = NULL, state = 'IDLE' "
                    "WHERE agent = 'keeper'")
        table["keeper"] = 4200
        _process_on("beta")
        con.execute("DELETE FROM events WHERE agent = 'keeper' AND kind = 'moved_account'")
        check("move: on the new account it is one",
              dmn_handoff._move_to_room(con, "keeper", t, lambda _m: None)[0]
              and agents.current_account("keeper", con) == "beta")
        check("move: and the move is on the record",
              sum(e["kind"] == "moved_account"
                  for e in db.events_after(con, 0, agent="keeper")) == 1)
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.idle_now, dmn_delivery.wait_for_session, obsmod.env_of) = real
        con.close()


def test_a_forced_move_goes_through_the_verified_move_or_not_at_all(home: Path) -> None:
    """A person names the destination; it still has to be one the pick would
    send an agent to, the session ends only if it does, and a move that did not
    land leaves the agent live where it was."""
    _daemon_home(home, 20)
    accounts.register("beta")
    accounts.register("gamma")
    agents.register("keeper")
    credentialed(home, "alpha", "beta", "gamma")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=9, pct_7d=10, reset_7d=now + 90000, reset_5h=now + 1800)
    write_status(home, "beta", pct_5h=9, pct_7d=10, reset_7d=now + 90000, reset_5h=now + 1800)
    write_status(home, "gamma", pct_5h=100, pct_7d=10, reset_7d=now + 90000, reset_5h=now + 1800)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.idle_now, orcamod.typeable_now, dmn_delivery.wait_for_session, obsmod.env_of,
            accounts.logged_in)
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        table = sessions_are("keeper")
        db.set_account(con, "keeper", "alpha")
        orca_idle()
        orcamod.typeable_now = lambda h: True
        orcamod.close_session = exit_ends_session("keeper")
        cmds: list[str] = []
        orcamod.relaunch = lambda h, cmd, **kw: (cmds.append(cmd),
                                                 table.__setitem__("keeper", 4200), (True, "ok"))[2]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        accounts.logged_in = lambda a: True
        keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
        log: list[str] = []

        check("forced: a live session is offered the move", actions.can(con, "keeper", "move")[0])
        check("forced: an agent with no terminal is not",
              not actions.can(con, "ghost", "move")[0])
        ok, msg = dmn_handoff.force_move(con, "keeper", "gamma", "test", log.append)
        check("forced: an account with no room is refused before anything ends",
              not ok and "keeper" in table and db.get_agent_state(con, "keeper")["account"] == "alpha", msg)
        ok, msg = dmn_handoff.force_move(con, "keeper", "alpha", "test", log.append)
        check("forced: the account it is already on is refused", not ok and "already" in msg, msg)

        # The process that comes up is still on alpha: not a move, and nothing
        # is parked or cleared - the session is live.
        # The move stops before it ends anything: the handoff it took must not
        # stay on a session that never ended.
        db.set_handoff(con, "keeper", None)
        real_record = handoffmod.record
        handoffmod.record = lambda c, a: (db.set_handoff(c, a, "# handoff · keeper\n"), [])[1]
        orcamod.alive = lambda h: h != "term_x"
        try:
            ok, msg = dmn_handoff.force_move(con, "keeper", "beta", "test", log.append)
        finally:
            handoffmod.record = real_record
            orcamod.alive = lambda h: True
        check("forced: a move that stops before ending anything is not a move", not ok, msg)
        check("forced: and the handoff it took is not left on a session that never ended",
              db.get_handoff(con, "keeper") is None, str(db.get_handoff(con, "keeper")))
        # A record already on the row stays exactly as it was.
        db.set_handoff(con, "keeper", "# handoff · keeper\n- an earlier record\n")
        handoffmod.record = lambda c, a: (db.set_handoff(c, a, "# handoff · keeper\n- new\n"), [])[1]
        orcamod.alive = lambda h: h != "term_x"
        try:
            dmn_handoff.force_move(con, "keeper", "beta", "test", log.append)
        finally:
            handoffmod.record = real_record
            orcamod.alive = lambda h: True
        check("forced: a record that was already there is put back, not replaced",
              db.get_handoff(con, "keeper") == "# handoff · keeper\n- an earlier record\n",
              str(db.get_handoff(con, "keeper")))
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")

        _process_on("alpha")
        ok, msg = dmn_handoff.force_move(con, "keeper", "beta", "test", log.append)
        row = db.get_agent_state(con, "keeper")
        check("forced: a process that stays on the old account is not a move", not ok, msg)
        check("forced: and it is left live, not parked",
              row["state"] == dmn_states.IDLE and row["account"] == "alpha", str(dict(row)))

        con.execute("UPDATE agent_state SET closed_at = NULL WHERE agent = 'keeper'")
        table["keeper"] = 4200
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
        _process_on("beta")
        ok, msg = dmn_handoff.force_move(con, "keeper", "beta", "the founder", log.append)
        check("forced: onto an account with room, with the process there, it moves", ok, msg)
        check("forced: the relaunch was told the destination",
              bool(cmds) and "--account beta" in cmds[-1], str(cmds))
        check("forced: the record names it",
              db.get_agent_state(con, "keeper")["account"] == "beta")
        check("forced: who ordered it is on the record",
              any(e["kind"] == "forced_move" and "the founder" in str(e["payload"])
                  for e in db.events_after(con, 0, agent="keeper")))

        # The session ends and the launch on the new account fails: it is not a
        # wall, so the agent comes back on the next look, not at a 5h reset.
        table["keeper"] = 4200
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
        orcamod.relaunch = lambda h, cmd, **kw: (False, "refused")
        # The move above typed a real /exit and started the single writer's
        # 15s settle window (#2064); this next move is real here, not later.
        db.clear_cursor(con, "typed_at:keeper")
        ok, msg = dmn_handoff.force_move(con, "keeper", "alpha", "test", log.append)
        row = db.get_agent_state(con, "keeper")
        check("forced: a launch that fails on the destination is not a move", not ok, msg)
        check("forced: and it is parked for a short look, not until a 5h reset",
              row["wake_at"] is not None
              and row["wake_at"] <= int(time.time()) + dmn_states.ASSUMED_ACCOUNT_RECHECK_S + 5,
              str(dict(row)))
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.idle_now, orcamod.typeable_now, dmn_delivery.wait_for_session, obsmod.env_of,
         accounts.logged_in) = real
        con.close()


def test_an_agent_cannot_force_a_move(home: Path) -> None:
    """A forced move ends a session; it is a person's, not an agent's."""
    _daemon_home(home, 20)
    agents.register("keeper")
    real = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "keeper"
    try:
        code = []
        _capture(lambda: code.append(climod.main(["agent", "move", "keeper", "beta", "--yes"])))
        check("forced: `hl agent move` from an agent's session is refused",
              code == [1], str(code))
        check("forced: and nothing was changed",
              db.get_agent_state(db.connect(), "keeper") is None)
    finally:
        if real is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = real


def test_an_agent_at_the_wall_moves_to_an_account_with_room(home: Path) -> None:
    """Total throughput is accounts x window, and moving a session does not change
    it. What it changes is whether an agent sits out its own five hours while
    another account idles beside it - worth a cold start only for an agent that
    is at the wall with work waiting and a reset too far off to wait for. The
    session being moved is already over: it hit the wall, and its context is
    gone either way."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper")
    write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    credentialed(home, "alpha", "beta")
    keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
    db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
    far = int(time.time()) + 3 * 3600
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
            dmn_delivery.wait_for_session)
    launched: list[str] = []
    try:
        orca_idle()
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        orcamod.close_session = exit_ends_session("keeper")
        orcamod.resume_with = lambda h, text: (True, "handed back")
        orcamod.relaunch = lambda h, cmd, **kw: (
            launched.append(cmd), _SESSION_TABLE["t"].update({"keeper": 4300}), (True, "relaunched"))[2]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        sessions_are("keeper")
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_account(con, "keeper", "alpha")
        con.commit()

        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        check("move: an account at the wall gives way to room",
              launch.choose_account("keeper") == "beta", launch.choose_account("keeper"))
        check("move: but a resume goes back where its session lives",
              launch.choose_account("keeper", resuming=True) == "alpha",
              launch.choose_account("keeper", resuming=True))

        t = triggers.evaluate(100, 10)
        check("move: the wall is a usage trigger", t.fired and t.kind == "usage", t.kind)
        dmn_handoff.perform_handoff(con, "keeper", t, lambda _m: None)

        row = db.get_agent_state(con, "keeper")
        # A state nothing else in the daemon will act on is where a moved agent
        # went to die: HANDOFF is protected from its own hooks, skipped by the
        # sweep, refused by close, and delivered nothing. Every other exit from
        # a handoff writes a state, so this one has to as well.
        check("move: it comes out of the handoff into a state that is dispatched",
              row["state"] == dmn_states.QUEUED and not row["wake_at"] and not row["closed_at"],
              f"{row['state']} wake={row['wake_at']} closed={row['closed_at']}")
        db.add_request(con, "marina", "keeper", "after the move", None,
                       "peer", "async", "task", 0, None)
        told: list[str] = []
        real_send = orcamod.send
        try:
            orcamod.send = lambda h, t, enter=True: (told.append(t), (True, ""))[1]
            dmn_delivery.deliver_queued(con, "keeper", lambda _m: None)
        finally:
            orcamod.send = real_send
        check("move: and work reaches it on the account it moved to", told, str(told))
        check("move: a fresh session is started, not a resume of the old one",
              launched and "--resume" not in launched[-1], str(launched))
        # What that relaunch will exec with, and what it writes down. The
        # launch is the single writer of the account - it is the process that
        # sets CLAUDE_CONFIG_DIR - so the record cannot disagree with the
        # environment the session is actually running in.
        argv, env = launch.build("keeper")
        check("launch: an agent cannot make subagents or message another "
              "agent's session directly - both go through `ocp ask`, where "
              "the bridge can see it",
              "--disallowedTools" in argv
              and argv[argv.index("--disallowedTools") + 1] == launch.DISALLOWED_TOOLS
              and {"Agent", "SendMessage"} <= set(launch.DISALLOWED_TOOLS.split(",")),
              str(argv))
        check("launch: an agent cannot schedule its own wakeup or cron",
              {"ScheduleWakeup", "CronCreate"} <= set(launch.DISALLOWED_TOOLS.split(",")),
              launch.DISALLOWED_TOOLS)
        check("move: the launch it typed goes to the new account",
              env["OCP_ACCOUNT"] == "beta"
              and env["CLAUDE_CONFIG_DIR"].endswith("accounts/beta/.claude"),
              f"{env['OCP_ACCOUNT']} {env['CLAUDE_CONFIG_DIR']}")
        launch._record_terminal("keeper", env["OCP_ACCOUNT"])
        check("move: and starting there is what records it",
              agents.current_account("keeper", con) == "beta",
              agents.current_account("keeper", con))
        moved = [e for e in db.events_after(con, 0, agent="keeper")
                 if e["kind"] == "moved_account"]
        check("move: the move is on the record, with both ends",
              moved and '"from": "alpha"' in moved[-1]["payload"]
              and '"to": "beta"' in moved[-1]["payload"], str(moved))

        # Below the wall a working agent is left alone, but an account that
        # would not last one more start is not somewhere to start a new one.
        db.set_account(con, "keeper", "alpha")
        write_status(home, "alpha", pct_5h=87, pct_7d=20, ctx_pct=5, reset_5h=far)
        t87 = triggers.evaluate(87, 10)
        check("move: 87% is not a trigger - it runs to the wall", not t87.fired, t87.reason)
        check("move: but an account that would last 20 minutes gives way to one that lasts",
              launch.choose_account("keeper") == "beta", launch.choose_account("keeper"))

        # A session that ended during the handoff must not be typed at: `/exit`
        # at a login shell exits the shell, and Orca's terminal goes with it.
        db.set_account(con, "keeper", "alpha")
        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        db.clear_closed(con, "keeper")
        typed: list[str] = []
        real_close = orcamod.close_session
        try:
            orcamod.close_session = lambda h, **kw: (typed.append("/exit"), (True, "ended"))[1]
            sessions_are()                      # it went away while we folded
            check("move: a session that is already gone is not moved",
                  not dmn_handoff._move_to_room(con, "keeper", t, lambda _m: None)[0])
            check("move: and nothing is typed at the shell it left behind", not typed,
                  str(typed))
        finally:
            orcamod.close_session = real_close
            sessions_are("keeper")

        # Nowhere to go is the old behaviour, unchanged: fold, park, wait for
        # the window. Both accounts at the wall is that world.
        db.set_account(con, "keeper", "alpha")
        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        write_status(home, "beta", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        db.clear_closed(con, "keeper")
        con.execute("DELETE FROM handoff")     # MIN_HANDOFF_INTERVAL_S, not a real one
        db.clear_cursor(con, "typed_at:keeper")  # #1973's settle window, not a real one either
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lambda _m: None)
        row = db.get_agent_state(con, "keeper")
        check("move: with no room anywhere it parks and waits, as before",
              row["state"] == dmn_states.PARKED and row["wake_at"], f"{row['state']} {row['wake_at']}")
        parked = db.events_after(con, 0, agent="keeper", exclude_kinds=())
        check("move: the park itself is a recorded event, like close and wake",
              any(e["kind"] == "park" for e in parked), [e["kind"] for e in parked])

        # An account we cannot read is not an account that is full. Moving on it
        # would send agents off whichever account went dark.
        write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        unknown = triggers.evaluate(None, 10)
        check("move: an unreadable account is not a trigger", not unknown.fired, unknown.kind)
        check("move: and nothing moves on it",
              not dmn_handoff._move_to_room(con, "keeper", unknown, lambda _m: None)[0])

        # What it is told when it lands. A move is not a close: its handoff was
        # taken a moment ago and `launch.context_for` put it in the new
        # session's system prompt, so telling it there is none would send it
        # hunting for what it is already holding.
        moved_text = dmn_lifecycle.wake_prompt("keeper", resumed=False, was_closed=True,
                                     moved_to="beta", con=con)
        check("move: the agent is told it was moved, and where",
              "beta" in moved_text and "5h limit" in moved_text, moved_text[:160])
        check("move: and not that there is no handoff",
              "There is no handoff" not in moved_text, moved_text[:160])
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
         dmn_delivery.wait_for_session) = real
        con.close()


def test_a_move_blocked_by_a_dialog_retries_soon_not_at_the_old_accounts_wall(home: Path) -> None:
    """#1013, from the real log: chat hit its wall with beta and delta free,
    could not close because a modal (ocp's own earlier /exit) was open, and
    the fallback parked it 2h21m against the walled account's own reset - the
    worst of both, since a destination existed the whole time and the modal
    is exactly what #996 presses.

    The property: a move that fails because a destination was found but the
    close could not happen (a dialog, a session that would not
    end) parks for a short recheck, the same shape as an assumed account -
    never against the account being LEFT, which has nothing to do with why
    the move failed."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper")
    db.set_account(con, "keeper", "alpha")      # a recorded account, not merely assumed
    write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5,
                 reset_5h=int(time.time()) + 3 * 3600)
    credentialed(home, "alpha", "beta")
    keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
    db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
    # The turn ended on the usage limit and has long since settled, so
    # perform_handoff does not gate on typeable_now at all - it is
    # `screen_blocker`'s job below to say a dialog is what stops the close,
    # not this earlier probe.
    db.add_event(con, "keeper", "StopFailure", {"limit": "5h"})
    con.execute("UPDATE events SET ts = ? WHERE agent = 'keeper' AND kind = 'StopFailure'",
                (int(time.time()) - dmn_states.LIMIT_SETTLE_S - 60,))
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.clear_context,
            orcamod.idle_now, orcamod.screen_blocker)
    try:
        orca_idle()
        orcamod.clear_context = lambda h, **kw: (False, "a modal is open on its screen; not typing /clear at it")
        orcamod.close_session = lambda h, **kw: (False, "a modal is open on its screen; not typing /exit at it")
        orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, '"Stay in this session?"')
        sessions_are("keeper")
        db.set_terminal(con, "keeper", "term_x", "wt")

        t = triggers.evaluate(100, 10)
        before = int(time.time())
        dmn_handoff.perform_handoff(con, "keeper", t, lambda _m: None)

        row = db.get_agent_state(con, "keeper")
        check("dialog move: it parks - the close never happened",
              row["state"] == dmn_states.PARKED and row["wake_at"], f"{row['state']} {row['wake_at']}")
        check("dialog move: not moved - the account is still the one it started on",
              agents.current_account("keeper", con) == "alpha", agents.current_account("keeper", con))
        check("dialog move: but the recheck is short, not the walled account's own 3h reset",
              row["wake_at"] < before + dmn_states.ASSUMED_ACCOUNT_RECHECK_S + 30,
              f"wake_at={row['wake_at']} before={before}")
        check("dialog move: the dialog itself is tracked, for #996 to press",
              db.block_get(con, "keeper") is not None and
              db.block_get(con, "keeper")["kind"] == orcamod.MODAL)

        # Contrast: no destination at all still parks against the wall it hit.
        db.block_clear(con, "keeper")
        write_status(home, "beta", pct_5h=100, pct_7d=20, ctx_pct=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        db.clear_closed(con, "keeper")
        con.execute("DELETE FROM handoff")
        before2 = int(time.time())
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lambda _m: None)
        row2 = db.get_agent_state(con, "keeper")
        check("dialog move: with nowhere to go, still the long wait - the two are not confused",
              row2["wake_at"] > before2 + dmn_states.ASSUMED_ACCOUNT_RECHECK_S + 30,
              f"wake_at={row2['wake_at']} before={before2}")

        # Contrast again: a destination exists, but Orca itself is down - that
        # is nobody's terminal in particular, so it is the long wait too, not
        # the short recheck the dialog got.
        write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        db.clear_closed(con, "keeper")
        con.execute("DELETE FROM handoff")
        orcamod.available = lambda: False
        before3 = int(time.time())
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lambda _m: None)
        row3 = db.get_agent_state(con, "keeper")
        check("dialog move: orca itself down is not 'about this terminal' either",
              row3["wake_at"] > before3 + dmn_states.ASSUMED_ACCOUNT_RECHECK_S + 30,
              f"wake_at={row3['wake_at']} before={before3}")
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.clear_context,
         orcamod.idle_now, orcamod.screen_blocker) = real
        con.close()


def test_the_usage_limit_dialog_is_declined_with_the_key_that_was_measured(home: Path) -> None:
    """No option of it only declines, so the inert-option rule cannot admit
    it; Esc is named for it because Esc was pressed on a live one and closed
    it with the session left at its prompt. Anything that only resembles it
    is not pressed."""
    restore_world()
    for name, tail, want in (
        ("the measured dialog", LIMIT_DIALOG, "\x1b"),
        ("another reset time", [ln.replace("3:30pm", "11:05am") for ln in LIMIT_DIALOG], "\x1b"),
        ("without the option that waits here", [ln for ln in LIMIT_DIALOG if "Wait here" not in ln], None),
        ("without the option that stops", [ln for ln in LIMIT_DIALOG if "Stop and wait" not in ln], None),
        ("the same title over other options",
         [ln.replace("Stop and wait for limit to reset", "Delete everything") for ln in LIMIT_DIALOG], None),
    ):
        footer = orcamod._footer_row(tail)
        dialog = orcamod.parse_dialog(tail, footer)
        key = orcamod.declining_key(dialog)[0] if dialog else None
        check(f"limit dialog: {name}", key == want, str((key, want)))


def test_an_agent_parks_through_the_usage_limit_dialog(home: Path) -> None:
    """The stuck shape from the night of 2026-09-21: the turn ended on the
    limit, Claude Code put up its dialog, and every attempt to park met it -
    /clear refused at a modal, so the agent never parked and never moved.
    The property: it is parked, having been sent Esc and then /clear, in that order."""
    con, term = _park_behind(home, LIMIT_DIALOG)
    orcamod.clear_context = REAL_ORCA["clear_context"]
    orcamod.close_session = REAL_ORCA["close_session"]
    try:
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lambda _m: None)
        row = db.get_agent_state(con, "keeper")
        check("park through: it ended parked", row["state"] == dmn_states.PARKED, row["state"])
        check("park through: Esc first, then /clear, nothing else",
              term.sent[:1] == [("\x1b", False)] and [t for t, _ in term.sent[1:]] == ["/clear"], str(term.sent))
        ev = con.execute("SELECT payload FROM events WHERE agent = 'keeper' AND kind = 'dialog_declined'").fetchall()
        check("park through: what was pressed is on the record", len(ev) == 1, str([dict(e) for e in ev]))
    finally:
        con.close()


def test_a_parked_agent_gets_past_a_usage_limit_dialog_before_its_wake(home: Path) -> None:
    """Founder #2087: a turn still running when a context handoff typed its
    /clear can hit the usage wall on its own afterward, well after the park
    already began - and wake_at, set for the reason the handoff recorded, can
    be hours off from when that turn actually failed. Nothing else looks at a
    parked agent's screen before its own wake_at comes due, so the dialog
    would otherwise sit up for however long that is. Esc'd on the very next
    tick instead, whatever wake_at says - and the park itself is untouched."""
    con, term = _park_behind(home, LIMIT_DIALOG, ends_at_limit=False)
    con.execute("DELETE FROM requests WHERE target = 'keeper'")
    try:
        wake = int(time.time()) + 3600
        db.set_agent_state(con, "keeper", dmn_states.PARKED, wake_at=wake, detail="context; parked")
        dmn_evaluate.supervise(con, "keeper", lambda _m: None, int(time.time()), {}, [])
        check("parked: the dialog is pressed past well before wake_at",
              term.sent == [("\x1b", False)], str(term.sent))
        row = db.get_agent_state(con, "keeper")
        check("parked: the park itself is untouched - still due at its own wake_at",
              row["state"] == dmn_states.PARKED and row["wake_at"] == wake, str(dict(row)))
        ev = con.execute(
            "SELECT payload FROM events WHERE agent = 'keeper' AND kind = 'dialog_declined'").fetchall()
        check("parked: what was pressed is on the record", len(ev) == 1, str([dict(e) for e in ev]))
    finally:
        con.close()


def test_a_pinned_closed_agent_moves_instead_of_waiting_out_its_window(home: Path) -> None:
    """Request #156/#165 item 1's unfinished piece, #223: a closed agent's
    resume id lives under one account's config directory, so the ordinary
    wake only ever looked at that one account - an agent could sit closed
    and waiting for its own window while another account had clear room the
    whole time. keeper's #173 (readiness) was meant to fold this case in,
    but it was withdrawn (#253), so it is fixed directly in the wake."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")
    con = db.connect()
    real = (orcamod.available, orcamod.relaunch, orcamod.resume_with, dmn_delivery.wait_for_session,
           obsmod.live_sessions)
    launched: list[str] = []
    try:
        obsmod.live_sessions = lambda: {}
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        # What the close took while the session was live; a snapshot read at
        # wake time is stale by construction.
        keep_handoff("keeper", "# handoff · keeper\nprevious session: sess-old")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        write_status(home, "beta", pct_5h=10, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        orcamod.available = lambda: True
        orcamod.relaunch = lambda h, cmd, **kw: (launched.append(cmd), (True, "relaunched"))[1]
        orcamod.resume_with = lambda h, text: (True, "handed back")
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        ok, why = dmn_lifecycle._wake_agent(con, "keeper", lambda _m: None)
        check("pinning: the wake succeeds instead of waiting out alpha's window",
              ok, why)
        check("pinning: the relaunch is a fresh start, not --resume on a full account",
              launched and "--resume" not in launched[-1], str(launched))
        row = db.get_agent_state(con, "keeper")
        check("pinning: resume_session is dropped - it belonged to alpha",
              row["resume_session"] is None, str(dict(row)))
        moved = [e for e in db.events_after(con, 0, agent="keeper")
                 if e["kind"] == "moved_account"]
        check("pinning: the move is on the record",
              moved and '"from": "alpha"' in moved[-1]["payload"]
              and '"to": "beta"' in moved[-1]["payload"], str(moved))
        pinned = con.execute("SELECT state FROM handoff WHERE agent = 'keeper' "
                             "AND trigger_kind = 'pinned'").fetchall()
        check("pinning: the move is a finished handoff, not a degraded one",
              [r["state"] for r in pinned] == ["done"], str([dict(r) for r in pinned]))
    finally:
        (orcamod.available, orcamod.relaunch, orcamod.resume_with, dmn_delivery.wait_for_session,
        obsmod.live_sessions) = real
        con.close()


def test_a_stale_account_whose_window_rolled_over_can_take_work(home: Path) -> None:
    """A snapshot only refreshes while a session runs on the account, so an
    unused account stays stale for good unless a passed window counts."""
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=10, pct_7d=98, reset_5h=now + 1800,
                 reset_7d=now + 86400 * 3)
    write_status(home, "beta", pct_5h=40, pct_7d=34, reset_5h=now - 9 * 3600 + 18000,
                 reset_7d=now + 86400 * 2, collected_at=now - int(9.8 * 3600))
    picked, reasons = status.pick_account(["alpha", "beta"])
    check("stale: a rolled-over account with 7d room is picked ahead of alpha at 98",
          picked == "beta", str(reasons))
    check("stale: the reason says why",
          any(n == "beta" and "window rolled over" in r and "34%" in r for n, r in reasons),
          str(reasons))
    check("stale: the panel and account list agree it takes work",
          status.read("beta").takes_work()[0] and not status.read("beta").assignable()[0])
    write_status(home, "beta", pct_5h=40, pct_7d=100, reset_5h=now - 3600,
                 reset_7d=now + 86400 * 2, collected_at=now - int(9.8 * 3600))
    check("stale: a 7d reading at the wall still refuses",
          status.pick_account(["beta"])[0] is None)
    write_status(home, "beta", pct_5h=40, pct_7d=34, reset_5h=now + 3600,
                 reset_7d=now + 86400 * 2, collected_at=now - int(2 * 3600))
    write_status(home, "alpha", pct_5h=87, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400)
    check("takes_work: 87% is assignable, but the pick would not start an agent there",
          status.read("alpha").assignable()[0]
          and status.pick_account(["alpha"], load={})[0] is None
          and status.unfit(status.read("alpha"), None) is not None)
    check("stale: an open window with room to spare is used, on the last figure seen",
          status.pick_account(["beta"])[0] == "beta")
    write_status(home, "beta", pct_5h=100, pct_7d=34, reset_5h=now + 3600,
                 reset_7d=now + 86400 * 2, collected_at=now - int(2 * 3600))
    check("stale: an open window at the wall still refuses - the figure only rises",
          status.pick_account(["beta"])[0] is None)


def test_room_is_filtered_before_the_ranking_not_after(home: Path) -> None:
    """A destination too full to last is dropped before the deadline order, so
    a good account behind it is still found; a weekly trigger parks until the
    week resets; a rescue whose handoff is missing a fact keeps the resume id."""
    _daemon_home(home, 20)
    accounts.register("nearwall")
    accounts.register("empty")
    agents.register("keeper")
    now = int(time.time())
    credentialed(home, "alpha", "nearwall", "empty")
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400 * 3)
    write_status(home, "nearwall", pct_5h=80, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 23 * 3600)
    write_status(home, "empty", pct_5h=0, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400 * 4)
    write_status(home, "nearwall", pct_5h=100, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 23 * 3600)
    check("room: the pick skips the account at the wall for the empty one behind it",
          status.pick_account(["nearwall", "empty"], load={})[0] == "empty")
    write_status(home, "nearwall", pct_5h=88, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 23 * 3600)
    check("room: a launch does not land on an account that would not last a start",
          launch.choose_account("keeper") == "empty", launch.choose_account("keeper"))

    check("room: a weekly trigger parks until the week resets",
          triggers.wake_after("usage_7d", now + 1800, now + 86400 * 2, now) == now + 86400 * 2)
    check("room: a 5h trigger parks until the 5h window resets",
          triggers.wake_after("usage", now + 1800, now + 86400 * 2, now) == now + 1800)

    write_status(home, "alpha", pct_5h=100, reset_5h=None, pct_7d=10,
                 collected_at=now - 6 * 3600)
    write_status(home, "nearwall", pct_5h=100, pct_7d=10, reset_5h=now + 1800)
    write_status(home, "empty", pct_5h=100, pct_7d=10, reset_5h=now + 1800)
    check("room: a reading older than 5h with no reset is not still a floor",
          launch.find_room("keeper") == "alpha", str(launch.find_room("keeper")))

    write_status(home, "alpha", pct_5h=100, pct_7d=5, reset_5h=now + 3 * 3600)
    write_status(home, "spare", pct_5h=10, pct_7d=5, reset_5h=now + 1800)
    credentialed(home, "alpha", "spare")
    con = db.connect()
    real = orcamod.available
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        db.set_account(con, "keeper", "alpha")      # recorded: an assumed one never moves
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        orcamod.available = lambda: True
        ok, why = dmn_lifecycle._wake_agent(con, "keeper", lambda _m: None)
        row = db.get_agent_state(con, "keeper")
        check("rescue: a handoff missing a fact keeps the resume id and waits",
              not ok and row["resume_session"] == "sess-old", f"{why} {dict(row)}")
        tried = con.execute("SELECT COUNT(*) AS n FROM handoff WHERE agent = 'keeper'"
                            ).fetchone()["n"]
        dmn_lifecycle._wake_agent(con, "keeper", lambda _m: None)
        dmn_lifecycle._wake_agent(con, "keeper", lambda _m: None)
        check("rescue: a degraded one is not retried on the next tick",
              con.execute("SELECT COUNT(*) AS n FROM handoff WHERE agent = 'keeper'"
                          ).fetchone()["n"] == tried == 1, str(tried))
    finally:
        orcamod.available = real
        con.close()


def test_the_runway_is_an_estimate_from_the_daemons_burn(home: Path) -> None:
    """Remaining headroom and time to the wall come from the burn the daemon
    measured, and are blank rather than guessed on a stale reading."""
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    per_s = 30 / 3600      # 30 points an hour
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600)
    status.write_burn("alpha", per_s, now)
    r = status.runway(status.read("alpha"), status.read_burn("alpha"), now)
    check("runway: points left are counted to the wall",
          r and r.left_short == 45 and r.left_long == 90, str(r))
    check("runway: time to the wall follows the measured burn",
          status.describe_runway(r) == "~1h30m to wall", status.describe_runway(r))
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 1800)
    check("runway: a reset before the wall reads 'outlasts reset'",
          status.describe_runway(status.runway(status.read("alpha"),
                                                status.read_burn("alpha"), now))
          == "outlasts reset")
    status.write_burn("alpha", 0.0, now)
    check("runway: no burn is said, not projected",
          status.describe_runway(status.runway(status.read("alpha"), 0.0, now))
          == "no recent burn")
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 6 * 3600)
    check("runway: at the wall it says so",
          status.describe_runway(status.runway(status.read("alpha"), per_s, now))
          == "at the wall")
    check("runway: an old burn reading is not used",
          status.read_burn("alpha", now + status.BURN_MAX_AGE_S + 60) is None)
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600,
                 collected_at=now - 3 * 3600)
    check("runway: a stale reading gives no estimate at all",
          status.runway(status.read("alpha"), per_s, now) is None
          and status.describe_runway(None) == "")

    # The team line.
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600)
    write_status(home, "beta", pct_5h=65, pct_7d=10, reset_5h=now + 7 * 3600)
    status.write_burn("alpha", per_s, now)
    status.write_burn("beta", per_s, now)
    line = status.team_outlook(["alpha", "beta"], now, gone=lambda n: False)
    check("team: at the combined pace the accounts hit the wall before the reset",
          "hit the wall around" in line and "next reset" in line and line.startswith("estimate"),
          line)
    status.write_burn("alpha", per_s / 100, now)
    status.write_burn("beta", per_s / 100, now)
    check("team: a slow pace lasts past the next reset",
          "last past the next reset" in status.team_outlook(["alpha", "beta"], now, gone=lambda n: False))
    write_status(home, "alpha", pct_5h=55, pct_7d=10, collected_at=now - 3 * 3600)
    write_status(home, "beta", pct_5h=65, pct_7d=10, collected_at=now - 3 * 3600)
    check("team: blank when no reading is current", status.team_outlook(["alpha", "beta"], now, gone=lambda n: False) == "")

    # An account that ends before the reset takes the headroom it has not spent with it.
    write_status(home, "alpha", pct_5h=10, pct_7d=10, reset_5h=now + 3 * 3600)      # 90 left, burns slowly
    write_status(home, "beta", pct_5h=90, pct_7d=10, reset_5h=now + 3 * 3600)       # 10 left, burns faster
    status.write_burn("alpha", 0.001, now)
    status.write_burn("beta", 0.002, now)
    check("team ends: with no end the pooled headroom lasts past the reset",
          "last past the next reset" in status.team_outlook(["alpha", "beta"], now, gone=lambda n: False))
    real_end = status.cycle.end_of
    status.cycle.end_of = lambda a, cfg=None, at=None: now + 1000 if a == "alpha" else None
    try:
        line = status.team_outlook(["alpha", "beta"], now, gone=lambda n: False)
        check("team ends: alpha ending in 1000 s takes its unspent 89 points with it, so the wall comes first",
              "hit the wall around" in line, line)
        status.cycle.end_of = lambda a, cfg=None, at=None: now + 9 * 3600 if a == "alpha" else None
        check("team ends: an end after the reset changes nothing",
              "last past the next reset" in status.team_outlook(["alpha", "beta"], now, gone=lambda n: False))
    finally:
        status.cycle.end_of = real_end
    check("team ends: the walk of the pool by itself",
          status._wall_with_ends(0, 100, 0.003, [(1000, 90, 0.001)]) == 5000
          and status._wall_with_ends(0, 100, 0.003, []) == 100 / 0.003)

    # The daemon records what it measured.
    write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=now + 1800)
    (home / "accounts" / "alpha" / "burn.json").unlink()
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now,
                      {"alpha": [(now - 200, 20), (now, 21)]}, [])
    finally:
        con.close()
    check("burn: the daemon writes the rate the pick and the screens use",
          status.read_burn("alpha") is not None)

    # Samples stop, supervise keeps running for a parked agent on the account:
    # the pace must age out, not be re-stamped every tick.
    old = now - 2 * status.BURN_MAX_AGE_S
    samples = {"alpha": [(old - 600, 20), (old, 30)]}
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=now + 3600, detail="parked")
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, samples, [])
    finally:
        con.close()
    check("burn: a pace whose samples stopped goes stale though supervise keeps running",
          status.read_burn("alpha", now) is None)


def test_the_active_cap_decides_where_the_next_agent_may_start(home: Path) -> None:
    """An account at its cap of agents mid-turn drops out of the pick, so waking
    work spills to the next account; running agents never move because of it."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    for a in ("alpha", "beta"):
        write_status(home, a, pct_5h=10, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
    con = db.connect()
    try:
        paths.config_file().write_text("version: 1\n")
        check("cap: two agents at once is the default", loadmod.cap_of("alpha") == 2)
        loadmod.set_cap("alpha", 3)
        check("cap: set per account, the others keep the default",
              loadmod.cap_of("alpha") == 3 and loadmod.cap_of("beta") == 2)
        check("cap: setting it keeps the rest of the config",
              "version: 1" in paths.config_file().read_text())
        try:
            loadmod.set_cap("alpha", 0)
            check("cap: less than one is refused", False)
        except ValueError:
            check("cap: less than one is refused", True)
        loadmod.set_cap("alpha", 2)

        _running_on(con, "alpha", "w1", "w2")
        ld = loadmod.read_all(con)
        check("cap: mid-turn agents are counted on the account they run on",
              ld["alpha"].active == 2 and ld["beta"].active == 0, str(ld))
        picked, reasons = status.pick_account(["alpha", "beta"], load=ld)
        check("cap: the account at its cap drops out and work spills to the next",
              picked == "beta" and "at its cap: 2 of 2" in dict(reasons)["alpha"],
              str(reasons))
        check("cap: running agents were not moved by it",
              all(agents.current_account(n, con) == "alpha" for n in ("w1", "w2")))
        check("cap: a launch's own account gives way too",
              launch.choose_account("backend") == "beta", launch.choose_account("backend"))
        loadmod.set_cap("alpha", 3)
        check("cap: raising it puts the account back",
              "at its cap" not in dict(status.pick_account(
                  ["alpha", "beta"], load=loadmod.read_all(con))[1])["alpha"])
        loadmod.set_cap("alpha", 2)

        _running_on(con, "beta", "w3", "w4")
        picked, reasons = status.pick_account(["alpha", "beta"], load=loadmod.read_all(con))
        check("cap: every account capped means nothing is picked and work waits",
              picked is None and all("at its cap" in r for _, r in reasons), str(reasons))
    finally:
        con.close()


def test_the_pick_weighs_how_fast_an_account_is_filling(home: Path) -> None:
    """Headroom alone does not say an account has room: one at 40% with three
    agents filling it lasts less than an idle one at 60%."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=40, pct_7d=10, reset_5h=now + 3600,
                 reset_7d=now + 3600)
    write_status(home, "beta", pct_5h=60, pct_7d=10, reset_5h=now + 3600,
                 reset_7d=now + 86400)
    status.write_burn("alpha", 3.0 / 60, now)      # 3%/min: three agents' worth
    picked, reasons = status.pick_account(["alpha", "beta"], load=loadmod.read_all())
    why = dict(reasons)
    check("fill: the emptier account that is filling fast loses to the fuller idle one",
          picked == "beta", str(reasons))
    check("fill: and says why - the fill and what one more agent would last",
          "fill 3.0%/min" in why["alpha"] and "under the 30m" in why["alpha"], why["alpha"])
    check("fill: the pick shows its own reason too",
          "one more agent (0.75%/min) lasts" in why["beta"] and "<- picked" in why["beta"], why["beta"])
    status.write_burn("alpha", 0.0, now)
    check("fill: with the fill gone the earlier weekly deadline wins again",
          status.pick_account(["alpha", "beta"], load=loadmod.read_all())[0] == "alpha")


def test_at_the_wall_it_is_a_cost_comparison_not_a_line(home: Path) -> None:
    """Stay parked when nothing is waiting or the account resets within T;
    otherwise move once, never back to the account just left before it resets."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    con = db.connect()
    try:
        agents.register("keeper")
        db.set_account(con, "keeper", "alpha")

        def world(alpha_reset, beta_pct=10):
            write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=alpha_reset,
                         reset_7d=now + 86400)
            write_status(home, "beta", pct_5h=beta_pct, pct_7d=10, reset_5h=now + 3600,
                         reset_7d=now + 86400)

        world(now + 3 * 3600)
        dest, why = dmn_handoff._move_plan(con, "keeper", "usage")
        check("stay: an idle agent never moves, however far the reset",
              dest is None and "idle agent" in why, why)
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        dest, why = dmn_handoff._move_plan(con, "keeper", "usage")
        check("move: work is waiting and the reset is far - to the pick",
              dest == "beta" and "<- picked" in why, f"{dest} {why}")
        world(now + 10 * 60)
        dest, why = dmn_handoff._move_plan(con, "keeper", "usage")
        check("stay: its account resets within T - cheaper to wait",
              dest is None and "cheaper to wait" in why, why)

        # One wall is one move: not back to the account it just left, until that
        # account has reset.
        world(now + 3 * 3600)
        db.add_event(con, "keeper", "moved_account", {"from": "beta", "to": "alpha",
                                                      "trigger": "usage"})
        dest, why = dmn_handoff._move_plan(con, "keeper", "usage")
        check("once: it does not go back to the account it just left before it resets",
              dest is None, f"{dest} {why}")
        write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=now - 60,
                     reset_7d=now + 86400)
        dest, why = dmn_handoff._move_plan(con, "keeper", "usage")
        check("once: after it has reset it is an ordinary destination again",
              dest == "beta", f"{dest} {why}")

        # Every account walled or capped: the work waits in the queue.
        world(now + 3 * 3600, beta_pct=10)
        _running_on(con, "beta", "w1", "w2")
        agents.register("other")
        db.set_account(con, "other", "alpha")
        db.add_request(con, "marina", "other", "carry on", None, "peer", "async", None, 0, None)
        dest, why = dmn_handoff._move_plan(con, "other", "usage")
        check("wait: with every other account capped or walled the work stays queued",
              dest is None and "no account can take one more agent" in why
              and "at its cap" in why, why)
    finally:
        con.close()


def test_the_cold_start_is_measured_and_sets_how_long_to_wait(home: Path) -> None:
    """T is what a cold start spends of the destination's window, measured on
    starts nothing else was spending on - and 39 minutes until there are three."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    try:
        def start(before, after, others=0):
            write_status(home, "alpha", pct_5h=before, pct_7d=10,
                         reset_5h=int(time.time()) + 3600)
            if others:
                _running_on(con, "alpha", "busy")
            measure.begin(con, "backend", "alpha")
            if others:
                return
            db.add_event(con, "backend", "Stop", {})
            write_status(home, "alpha", pct_5h=after, pct_7d=10,
                         reset_5h=int(time.time()) + 3600)
            measure.settle(con, "backend")

        start(20, 40)
        check("measure: one start is not enough to go on",
              measure.cold_start_pct(con) is None)
        start(10, 30)
        start(5, 25)
        check("measure: the middle of three starts is the cost",
              measure.cold_start_pct(con) == 20.0, str(measure.cold_start_pct(con)))
        check("measure: T is that share of the shortest window",
              triggers.stay_within_s(20.0, 5 * 3600) == 60 * 60
              and triggers.stay_within_s(None, 5 * 3600) == 39 * 60
              and triggers.stay_within_s(20.0) == 39 * 60)

        # A start with another agent spending on the account is not measured.
        start(30, 0, others=1)
        check("measure: a start that something else was spending beside is discarded",
              db.get_meta(con, "coldstart:backend") is None)

        # And T is what the decision uses: an account resetting in 50 minutes is
        # worth waiting for at 20% (60 minutes) but not at the 39-minute default.
        db.set_agent_state(con, "busy", dmn_states.IDLE)
        agents.register("keeper")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        now = int(time.time())
        write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 50 * 60,
                     reset_7d=now + 86400)
        write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
        dest, why = dmn_handoff._move_plan(con, "keeper", "usage")
        check("measure: the measured T keeps it waiting", dest is None and "60m" in why, why)
    finally:
        con.close()


def test_a_stale_snapshot_shows_its_last_figures_and_still_is_offered(home: Path) -> None:
    """The last known 5h and 7d figures are shown with their age wherever an
    account is, and a reset that has passed is said. An idle account whose
    window has not passed keeps its floor: nothing has spent on it since the
    last reading, so it is still offered on that figure, not refused merely
    for being old."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=5, pct_7d=10, reset_5h=now + 3600,
                 reset_7d=now + 86400)
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    st = status.read("beta")
    ok, why = st.takes_work()
    check("stale: an idle account inside its window keeps its floor, and is offered",
          ok and "5h 41% (60m old), 7d 20%" in why and "stale but idle" in why, why)
    picked, ranked = status.pick_account(["beta"], load={})
    check("stale: the pick uses it, on the last figure seen",
          picked == "beta" and "5h 41%" in dict(ranked)["beta"], str(ranked))
    check("stale: nothing that decides reads the number directly",
          st.short_trusted is None)
    out = _capture(lambda: climod.main(["account", "list"]))
    check("stale: `hl account list` shows it", "5h 41% (60m old)" in out, out)
    panel = tui._ANSI.sub("", _snap(200, 40))
    con = db.connect()
    try:
        facts = actions.account_facts(con, "beta")
    finally:
        con.close()
    check("stale: the account's own page draws the last figure, marked, and how old the snapshot is",
          facts["status"].shown_short() == (41, status.IDLE_MARK)
          and timefmt.ago(facts["status"].age_s) == "60m", str(facts["status"].shown_short()))
    check("stale: the panel keeps the sentence off the row", "5h 41% (60m old)" not in panel,
          panel[:400])

    cells = tui.account_cells("beta", status.read("beta"))
    check("stale: the bridge cell shows the last figure, filled to it and dimmed",
          "41%?" in tui._ANSI.sub("", cells["five"]) and "█" in cells["five"]
          and tui.GRN not in cells["five"] and tui.DIM in cells["five"], repr(cells["five"]))
    # At 100 the last-seen fill must not be the live one: same fill, different
    # colour, and the "?".
    write_status(home, "beta", pct_5h=100, pct_7d=10, reset_5h=int(time.time()) + 1800,
                 reset_7d=int(time.time()) + 86400, collected_at=int(time.time()) - 3600)
    old = tui.account_cells("beta", status.read("beta"))["five"]
    check("stale: and carries the ?", "100%?" in tui._ANSI.sub("", old), repr(old))
    write_status(home, "beta", pct_5h=None, pct_7d=None, reset_5h=None, reset_7d=None,
                 collected_at=now - 3600)
    check("stale: with no figure on file the cell is still ?",
          "?%" in tui.account_cells("beta", status.read("beta"))["five"])
    write_status(home, "beta", pct_5h=90, pct_7d=20, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    why = status.read("beta").takes_work()[1]
    check("stale: within the margin of the wall when last seen still refuses",
          not status.read("beta").takes_work()[0] and "too close to" in why, why)
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now - 600,
                 reset_7d=now + 86400, collected_at=now - 3600)
    why = status.read("beta").takes_work()[1]
    check("stale: a passed reset is marked, the number kept",
          "5h 41% (60m old, reset since)" in why, why)
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now + 1800,
                 reset_7d=now - 60, collected_at=now - 3600)
    why = status.read("beta").takes_work()[1]
    check("stale: and a passed weekly reset", "7d 20% (reset since)" in why, why)


def test_pick_account_judges_an_idle_account_on_its_floor_not_on_zero(home: Path) -> None:
    """A stale-but-idle account is admitted on its last figure, and its
    stretch (whether it lasts long enough to be worth starting on) must be
    computed from that floor - not from an assumed-empty window, which is
    indistinguishable from real headroom once something is actually burning
    on the account. The margin (30 points) alone cannot catch this: it only
    guarantees enough headroom against zero burn, not against a real one."""
    _daemon_home(home, 20)
    _with_accounts("beta")
    now = int(time.time())
    write_status(home, "beta", pct_5h=65, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    check("pick: the stale reading clears idle_floor's margin (below 70%)",
          status.read("beta").idle_floor()[0])
    # Real floor 65%, headroom 35 - too little to last 30m at this burn.
    # At a fill of 0 the same burn reads as headroom 100 and would last
    # comfortably, which is exactly the bug: the account we cannot currently
    # see would be preferred over one refused on real numbers just like it.
    ld = {"beta": loadmod.Load("beta", 0, triggers.DEFAULT_ACTIVE_CAP, 0.03)}
    check("pick: judged on its real floor, the stretch is too short to start",
          status.pick_account(["beta"], load=ld)[0] is None)


def test_idle_floor_refuses_an_account_with_an_agent_mid_turn_on_it(home: Path) -> None:
    """A stale reading with an agent of ours actually mid-turn on the account
    is not idle - it is exactly the spend we cannot account for, and must fail
    closed regardless of the margin."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "beta", pct_5h=40, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    check("idle_floor: with nothing running on it, the floor holds",
          status.read("beta").idle_floor()[0])
    check("idle_floor: with an agent mid-turn on it, it does not",
          not status.read("beta").idle_floor(active=1)[0])
    con = db.connect()
    try:
        _running_on(con, "beta", "w1")
        ld = loadmod.read_all(con)
        check("idle_floor: load.active reflects the running agent", ld["beta"].active == 1)
        ok, why = status.read("beta").takes_work(ld["beta"].active)
        check("takes_work: an account with an agent mid-turn on it fails closed",
              not ok and "stale" in why, why)
        picked, reasons = status.pick_account(["beta"], load=ld)
        check("pick: it is not picked while an agent is mid-turn on it",
              picked is None, str(reasons))
    finally:
        con.close()


def test_idle_floor_refuses_a_snapshot_older_than_its_own_window(home: Path) -> None:
    """reset_5h ahead of us while the snapshot itself is already older than a
    5h window is wide cannot be well-formed data - a rewound clock or a
    hand-edited file, not an idle account - and must not read as a floor."""
    _daemon_home(home, 20)
    _with_accounts("beta")
    now = int(time.time())
    write_status(home, "beta", pct_5h=40, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - (26 * 3600))
    st = status.read("beta")
    check("idle_floor: an age past a whole window with reset_5h still ahead refuses",
          not st.idle_floor()[0])
    check("assignable: falls through to the ordinary stale refusal, not a floor",
          not st.assignable()[0] and "fail-closed" in st.assignable()[1], st.assignable()[1])


def test_idle_floor_margin_and_its_refusal_line(home: Path) -> None:
    """A stale floor is admitted only below 70% (30 below the wall), and when
    the stretch refuses one, the line prints the floor it judged, not None."""
    _daemon_home(home, 20)
    _with_accounts("beta")
    now = int(time.time())
    for pct, admitted in ((69, True), (75, False)):
        write_status(home, "beta", pct_5h=pct, pct_7d=10, reset_5h=now + 1800,
                     reset_7d=now + 86400, collected_at=now - 3600)
        check(f"idle_floor: a stale {pct}% floor is {'admitted' if admitted else 'refused'}",
              status.read("beta").idle_floor()[0] is admitted)
    write_status(home, "beta", pct_5h=65, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    ld = {"beta": loadmod.Load("beta", 0, triggers.DEFAULT_ACTIVE_CAP, 0.03)}
    picked, reasons = status.pick_account(["beta"], load=ld)
    line = " ".join(r for _a, r in reasons)
    check("pick: the stale-floor refusal line prints the floor, not None",
          picked is None and "5h 65%" in line and "None" not in line, line)


def test_a_sweep_after_a_move_does_not_keep_the_old_accounts_session(home: Path) -> None:
    """keeper 09-19 15:45: moved gamma -> alpha, and eight seconds later the
    sweep recorded it closed with the gamma session id, which alpha cannot resume."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "beta")
        write_agent_status("backend", "alpha", session_id="sess-old",
                           collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: {}
        dmn_reconciler.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("sweep: it is recorded as closed", row["closed_at"] is not None)
        check("sweep: but not with the session of the account it left",
              row["resume_session"] is None, str(row["resume_session"]))
    finally:
        orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real
        con.close()


def test_replaying_0919_makes_fewer_moves_than_the_old_rules(home: Path) -> None:
    """Today's walls, 12:36 to 14:45, driven through the real decision functions.

    Fixed by the log: alpha at 85% with four agents on it at 12:36 and its
    window resetting at 14:00; beta at 20% resetting at 16:50; gamma unavailable
    until marina's session gave it a reading at 14:30. The old rules moved four
    agents alpha -> beta at 12:37-12:53 (at 85-89%, none at the wall) and three
    beta -> gamma at 14:40: seven cold starts. Modelled, and the same for both:
    an agent working costs its account 0.35%/min (alpha's own rise over the
    same half hour, divided by the agents on it), and a cold start costs the
    destination 13%."""
    restore_world()
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    orcamod.available = lambda: False
    obsmod.live_sessions = lambda: None
    base = int(time.time())
    clock = {"m": 0}
    real_time = time.time
    time.time = lambda: base + clock["m"] * 60
    STEADY, COLD = 0.35, 13.0
    try:
        _with_accounts("alpha", "beta")
        names = ("mgr", "aud", "pur", "hm")
        for n in names + ("rev",):
            agents.register(n)
        con = db.connect()
        for n in names:
            db.set_account(con, n, "alpha")
            db.set_agent_state(con, n, dmn_states.RUNNING)
            db.add_request(con, "marina", n, "carry on", None, "peer", "async", None, 0, None)
        db.add_request(con, "marina", "rev", "look", None, "peer", "async", None, 0, None)

        pct = {"alpha": 85.0, "beta": 20.0}
        reset = {"alpha": base + 84 * 60, "beta": base + 254 * 60}
        mode = {n: "run" for n in names}
        moves, cold_starts, waiting_min = [], 0, 0

        def publish():
            for a, p in pct.items():
                n = sum(1 for x, m in mode.items() if m == "run"
                        and agents.current_account(x, con) == a)
                write_status(home, a, pct_5h=int(round(min(100.0, p))), pct_7d=20, ctx_pct=5,
                             reset_5h=reset[a], reset_7d=base + 86400 * 3,
                             collected_at=base + clock["m"] * 60)
                status.write_burn(a, n * STEADY / 60, base + clock["m"] * 60)

        def hold(n, acct):
            db.set_account(con, n, acct)
            db.set_agent_state(con, n, dmn_states.RUNNING if mode[n] == "run" else dmn_states.PARKED)

        publish()
        for m in range(0, 130):
            clock["m"] = m
            if m == 84:                                   # alpha's window rolls over
                pct["alpha"], reset["alpha"] = 0.0, base + (84 + 300) * 60
            if m == 114:                                  # marina's session on gamma
                _with_accounts("gamma")
                agents.register("marina")
                db.set_account(con, "marina", "gamma")
                db.set_agent_state(con, "marina", dmn_states.RUNNING)
                mode["marina"] = "run"
                pct["gamma"], reset["gamma"] = 1.0, base + (114 + 300) * 60
            if m == 14:                                   # reviewer's work arrives
                a = launch.find_room("rev")
                if a:
                    mode["rev"] = "run"
                    pct[a] += COLD
                    cold_starts += 1
                    hold("rev", a)
                    moves.append((m, "rev", "start", a))
                else:
                    mode["rev"] = "wait"
                    hold("rev", "alpha")
            for a in list(pct):
                n = sum(1 for x, md in mode.items() if md == "run"
                        and agents.current_account(x, con) == a)
                if a != "gamma" or m >= 114:
                    pct[a] = min(100.0, pct[a] + n * STEADY)
            publish()
            for n in [x for x in mode if x != "marina"]:
                here = agents.current_account(n, con)
                if mode[n] == "run" and pct[here] >= 100:
                    mode[n] = "wait"
                    hold(n, here)
                if mode[n] == "wait":
                    if pct[here] < 100:
                        mode[n] = "run"
                        hold(n, here)          # its own window rolled over: no cold start
                        continue
                    waiting_min += 1
                    dest, _ = dmn_handoff._move_plan(con, n, "usage")
                    if dest:
                        db.add_event(con, n, "moved_account",
                                     {"from": here, "to": dest, "trigger": "usage"})
                        mode[n] = "run"
                        pct[dest] += COLD
                        cold_starts += 1
                        hold(n, dest)
                        moves.append((m, n, here, dest))
                        publish()
        con.close()
    finally:
        time.time = real_time
    old_moves = 7
    hhmm = lambda m: time.strftime("%H:%M", time.localtime(base + m * 60 - 0))  # noqa: E731
    starts = [(m, n, f"{s}->{d}") for m, n, s, d in moves if s != "start"]
    check("replay: the new rules move fewer agents than the seven the old ones did",
          len(starts) < old_moves, f"{len(starts)} moves: {starts}")
    check("replay: and nothing was moved before the wall",
          all(m >= 10 for m, *_ in starts), str(starts))
    print(f"    replay 09-19 12:36-14:45: old rules 7 moves (7 cold starts); new rules "
          f"{len(starts)} moves, {cold_starts} cold starts incl. reviewer's start, "
          f"{waiting_min} agent-minutes waiting at a wall; moves at minute offsets {starts}")


def test_a_turn_that_ends_on_the_usage_limit_is_at_the_wall(home: Path) -> None:
    """StopFailure with the limit message on a live agent: shown as stopped, not
    RUNNING, and handed off and moved though nothing reads full and the terminal
    probe still says mid-turn."""
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    transcript = home / "t.jsonl"
    transcript.write_text('{"text":"5h usage 87% >= 85%, at its usage limit, hit your session limit"}\n')
    os.environ["OCP_AGENT"], os.environ["OCP_ACCOUNT"] = "backend", "alpha"
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="active")
        def stop_failure(**fields):
            real_in = sys.stdin
            sys.stdin = io.StringIO(json.dumps(dict(
                {"hook_event_name": "StopFailure", "session_id": "s",
                 "transcript_path": str(transcript)}, **fields)))
            try:
                evmod.record_from_stdin(["StopFailure"])
            finally:
                sys.stdin = real_in

        # A StopFailure for another reason, whose transcript talks about limits.
        stop_failure(error="server_error")
        check("limit: another error type is not a limit, whatever the transcript says",
              evmod.at_limit(con, "backend") is None
              and db.get_agent_state(con, "backend")["state"] == dmn_states.RUNNING)
        stop_failure(error="rate_limit", error_details="You've hit your session limit")
        row = db.get_agent_state(con, "backend")
        lim = evmod.at_limit(con, "backend")
        check("limit: the structured error type marks it, and says where",
              lim and "session limit" in lim["limit"] and lim["limit_source"] == "payload.error",
              str(lim))
        check("limit: the agent no longer reads RUNNING", row["state"] == dmn_states.IDLE, str(dict(row)))
        db.add_event(con, "backend", "UserPromptSubmit", {})
        check("limit: a prompt after the failure clears it", evmod.at_limit(con, "backend") is None)
        stop_failure(error="rate_limit", error_details="You've hit your session limit")
        # Just after the failure a person may be typing: the probe still decides.
        real_idle = orcamod.idle_now
        orcamod.idle_now = lambda h, **kw: False
        real_av = orcamod.available
        orcamod.available = lambda: True
        try:
            check("limit: fresh, the mid-turn probe still defers the handoff",
                  dmn_handoff.perform_handoff(con, "backend", triggers.Trigger(True, "usage", "x"),
                                      lambda _m: None) is True
                  and db.in_flight_for(con, "backend") is None)
        finally:
            orcamod.idle_now, orcamod.available = real_idle, real_av
        con.execute("UPDATE events SET ts = ts - 120 WHERE agent = 'backend' AND kind = 'StopFailure'")
        con.commit()

        write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=now + 1800)
        write_status(home, "beta", pct_5h=5, pct_7d=10, reset_5h=now + 1800)
        seen = []
        typed = []
        db.add_request(con, "reviewer", "backend", "work for later", None,
                       "peer", "async", None, 0, None)
        real_send = orcamod.send
        orcamod.send = lambda h, t, enter=True: (typed.append(t), (True, ""))[1]
        real = (orcamod.available, orcamod.alive, orcamod.idle_now, dmn_handoff._move_to_room,
                dmn_delivery.session_present, orcamod.clear_context)
        try:
            orcamod.available = lambda: True
            orcamod.alive = lambda h: True
            orcamod.idle_now = lambda h, **kw: False   # the probe would say mid-turn
            orcamod.clear_context = lambda h, **kw: (True, "cleared")
            dmn_delivery.session_present = lambda a: True
            dmn_handoff._move_to_room = lambda con_, a, t, log, dest=None: (seen.append(t), (True, False))[1]
            # Idle, so delivery would go through if nothing held it back.
            orcamod.idle_now = lambda h, **kw: True
            dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
            check("limit: a queued request is not typed into an agent at its limit",
                  not typed, str(typed))
            seen.clear()
            con.execute("UPDATE handoff SET updated = updated - 1000 WHERE agent = 'backend'")
            # The first pass already moved backend (the stub above stands in
            # for a real /exit) and started the single writer's 15s settle
            # window (#2064); real here, not 1000s ago like the interval just
            # backdated, so it is cleared too or this pass reads as too soon.
            db.clear_cursor(con, "typed_at:backend")
            db.set_agent_state(con, "backend", dmn_states.IDLE, detail="at its usage limit")
            orcamod.idle_now = lambda h, **kw: False
            dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        finally:
            (orcamod.available, orcamod.alive, orcamod.idle_now, dmn_handoff._move_to_room,
             dmn_delivery.session_present, orcamod.clear_context) = real
        orcamod.send = real_send
        check("limit: once settled, handed off and moved though the probe says mid-turn",
              seen and seen[0].kind == "usage" and "payload.error" in seen[0].reason, str(seen))
    finally:
        os.environ.pop("OCP_AGENT", None); os.environ.pop("OCP_ACCOUNT", None)
        con.close()


def test_an_unknown_reset_does_not_make_the_runway_absurd(home: Path) -> None:
    """auditor #364: an unknown reset_5h left the eta line unbounded - a slow
    enough burn read as tens of thousands of hours to the wall, which is not
    just too wide for its column, it is wrong: a 5h window resets within 5h
    whether or not the collector told us when."""
    _daemon_home(home, 20)
    now = int(time.time())
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=None)
    status.write_burn("alpha", 5e-7, now)  # a burn slow enough to imply years
    r = status.runway(status.read("alpha"), status.read_burn("alpha"), now)
    check("runway: an eta past the longest a 5h window can run is past_reset",
          r.past_reset, r)
    check("runway: so the line says it outlasts reset, not an absurd duration",
          status.describe_runway(r) == "outlasts reset", status.describe_runway(r))
    # A reset within the window's own bound still reads as an eta, known or not.
    status.write_burn("alpha", 30 / 3600, now)
    r2 = status.runway(status.read("alpha"), status.read_burn("alpha"), now)
    check("runway: an eta safely inside the window is unaffected",
          not r2.past_reset and status.describe_runway(r2) == "~1h30m to wall",
          status.describe_runway(r2))


def test_what_one_agent_costs_is_measured_from_stretches_of_work(home: Path) -> None:
    """A sample is the account's rise over a stretch in which the same agents
    were mid-turn, divided by their number; a stretch is dropped when it is too
    short or a window rolled, and kept when nothing rose."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("solo", model="sonnet", effort="medium")
    agents.register("duo-a", model="opus", effort="high")
    agents.register("duo-b", model="opus", effort="high")
    agents.register("duo-c", model="opus", effort="low")
    agents.register("mixed", model="sonnet", effort="high")
    t0 = 1_000_000

    def stretch(who, readings, reset=9_999_999_999):
        for dt, pct in readings:
            burnrate.observe(con, "alpha", who, _Reading(pct, t0 + dt, reset))
        burnrate.observe(con, "alpha", [], _Reading(readings[-1][1], t0 + readings[-1][0] + 30))

    stretch(["solo"], [(0, 10), (300, 13), (700, 18)])
    got = _samples_of(con)
    check("burn: one agent's stretch is a sample of its own rise", len(got) == 1
          and abs(got[0]["r"] - 8 / 700) < 1e-9 and got[0]["m"] == "sonnet"
          and got[0]["e"] == "medium", str(got))
    stretch(["solo"], [(1000, 20), (1300, 23)])
    check("burn: a stretch too short to measure is not one", len(_samples_of(con)) == 1)
    stretch(["solo"], [(2000, 30), (2700, 30)])
    got = _samples_of(con)
    check("burn: a stretch in which nothing rose is kept - dropping it would price agents high",
          len(got) == 2 and got[1]["r"] == 0.0, str(got))
    before = len(_samples_of(con))
    stretch(["solo"], [(3000, 90), (3400, 5), (3900, 8), (4100, 9)])
    got = _samples_of(con)[before:]
    check("burn: a window that rolled over is not a rate; what comes after it starts afresh",
          len(got) == 1 and abs(got[0]["r"] - 4 / 700) < 1e-9, str(got))
    before = len(_samples_of(con))
    for at, pct, reset in ((5000, 40, 100), (5400, 43, 100), (5800, 46, 200), (6000, 47, 200),
                           (6300, 48, 200)):
        burnrate.observe(con, "alpha", ["solo"], _Reading(pct, t0 + at, reset))
    burnrate.observe(con, "alpha", [], _Reading(48, t0 + 6400))
    check("burn: a new reset time is a new window as well: 40 -> 46 is not counted, "
          "46 -> 48 is too short", len(_samples_of(con)) == before, str(_samples_of(con)[before:]))
    stretch(["duo-a", "duo-b"], [(7000, 10), (7600, 22)])
    got = _samples_of(con)[-1]
    check("burn: two agents of one kind are their mean, filed under that kind",
          abs(got["r"] - 6 / 600) < 1e-9 and (got["m"], got["e"]) == ("opus", "high"), str(got))
    stretch(["duo-a", "duo-c"], [(8000, 10), (8600, 16)])
    got = _samples_of(con)[-1]
    check("burn: one model at two efforts is filed under the model only",
          (got["m"], got["e"]) == ("opus", None), str(got))
    stretch(["duo-a", "mixed"], [(9000, 10), (9600, 16)])
    got = _samples_of(con)[-1]
    check("burn: a rise that cannot be split between models is filed under neither",
          (got["m"], got["e"]) == (None, None), str(got))
    before = len(_samples_of(con))
    burnrate.observe(con, "alpha", ["solo"], _Reading(10, t0 + 20000))
    burnrate.observe(con, "alpha", ["solo"], _Reading(14, t0 + 20700))
    burnrate.observe(con, "alpha", ["duo-a"], _Reading(30, t0 + 20730))
    burnrate.observe(con, "alpha", [], _Reading(60, t0 + 21400))
    got = _samples_of(con)[before:]
    check("burn: a change of who is working closes the stretch before it, not across it",
          len(got) == 1 and abs(got[0]["r"] - 4 / 700) < 1e-9, str(got))
    # A stretch that runs on is cut where it is long enough and goes on from there.
    before = len(_samples_of(con))
    for i in range(0, 4000, 300):
        burnrate.observe(con, "alpha", ["solo"], _Reading(10 + i // 100, t0 + 30000 + i))
    check("burn: a run that never changes hands still yields samples",
          len(_samples_of(con)) > before + 1, str(len(_samples_of(con))))
    con.close()


def test_an_agent_is_priced_by_the_nearest_kind_with_samples(home: Path) -> None:
    """Its own kind at MIN_SAMPLES, then its model, then everyone, then the
    starting value; a median of the last KEEP, held inside its bounds."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("op", model="opus", effort="high")
    agents.register("op-low", model="opus", effort="low")
    agents.register("so", model="sonnet", effort="medium")
    agents.register("newmodel", model="haiku", effort="medium")
    per_min = lambda a: round(burnrate.costs(con).per_s(a) * 60, 3)  # noqa: E731
    start = round(triggers.TYPICAL_AGENT_BURN_PER_S * 60, 3)
    check("cost: with no samples every agent is at the starting value",
          per_min("op") == per_min("so") == per_min(None) == start, str(per_min("op")))
    n = burnrate.MIN_SAMPLES
    _give_samples(con, [(0.9, "opus", "high")] * (n - 1))
    check("cost: one short of the floor is still the starting value", per_min("op") == start)
    _give_samples(con, [(0.9, "opus", "high")] * n)
    check("cost: at the floor an agent is priced by its own kind", per_min("op") == 0.9)
    check("cost: another effort of the same model falls back to the model's - "
          "that pool is the same samples", per_min("op-low") == 0.9)
    check("cost: a model nobody has measured falls back to the all-agent median",
          per_min("so") == per_min("newmodel") == per_min(None) == 0.9)
    _give_samples(con, [(0.9, "opus", "high")] * n + [(0.3, "sonnet", "medium")] * n)
    check("cost: each kind has its own number", (per_min("op"), per_min("so")) == (0.9, 0.3))
    _give_samples(con, [(0.5, None, None)] * n)
    check("cost: mixed-kind samples reach only the all-agent pool",
          per_min("op") == per_min(None) == 0.5)
    _give_samples(con, [(5.0, "opus", "high")] * n + [(0.0, "sonnet", "medium")] * n)
    check("cost: the estimate is held inside its bounds",
          (per_min("op"), per_min("so")) == (2.0, 0.1), f"{per_min('op')} {per_min('so')}")
    _give_samples(con, [(2.0, "opus", "high")] * 40 + [(0.4, "opus", "high")] * burnrate.KEEP)
    check("cost: only the last KEEP samples count", per_min("op") == 0.4)
    _give_samples(con, [(0.4, "opus", "high")] * 8 + [(9.0, "opus", "high")] * 4)
    check("cost: a median, so a few wild stretches do not move it", per_min("op") == 0.4)
    db.set_meta(con, burnrate._SAMPLES, "not json")
    check("cost: unreadable samples are the starting value", per_min("op") == start)
    line = burnrate.costs(con).by_kind(["op", "so"])
    check("cost: each kind is priced, and says what it rests on",
          len(line) == 2 and "starting value" in line[0], str(line))
    con.close()


def test_the_price_of_an_agent_holds_still_across_ticks(home: Path) -> None:
    """A move is judged every tick, so the estimate must not turn one agent away
    and admit it again minutes later: at the fallback, at the floor, with no
    samples, and as noisy stretches keep arriving around the line."""
    import random
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    agents.register("mover", model="sonnet", effort="medium")
    now = int(time.time())
    # 40% headroom, filling at 0.5%/min: one more agent lasts 40 / (0.5 + a), and
    # 30 minutes is the line, so a = 0.83%/min is exactly on it.
    write_status(home, "beta", pct_5h=60, pct_7d=10, reset_5h=now + 3600, reset_7d=now + 86400)
    status.write_burn("beta", 0.5 / 60, now)

    def admitted() -> bool:
        picked, _ = status.pick_account(["beta"], load=loadmod.read_all(con), agent="mover")
        return picked == "beta"

    def flips(trace: list[bool]) -> int:
        return sum(1 for a, b in zip(trace, trace[1:]) if a != b)

    check("ticks: with no samples the account is judged at the starting value, every tick",
          {admitted() for _ in range(5)} == {True})
    rng = random.Random(7)
    trace, all_rates = [], []
    for _ in range(80):
        # Noisy stretches of an agent whose true cost, 0.65%/min, is under the
        # 0.83 line: a point of resolution and how hard each stretch worked.
        rate = max(0.0, 0.65 + rng.uniform(-0.4, 0.4))
        all_rates.append(rate)
        cur = json.loads(db.get_meta(con, burnrate._SAMPLES) or "[]")
        cur.append({"t": len(cur), "a": "alpha", "r": rate / 60, "m": "sonnet", "e": "medium"})
        db.set_meta(con, burnrate._SAMPLES, json.dumps(cur))
        trace.append(admitted())
    trace_live = []
    for i in range(len(all_rates)):
        recent = all_rates[max(0, i - 2):i + 1]
        trace_live.append(40 / (0.5 + sum(recent) / len(recent)) >= 30)
    check("ticks: through the floor and beyond, the same agent is never turned away",
          flips(trace) == 0 and all(trace), str(trace))
    check("ticks: a live average of the last three would have flipped it repeatedly",
          flips(trace_live) >= 3, str(flips(trace_live)))
    check("ticks: the same account is the same answer to the same agent, tick after tick",
          len({admitted() for _ in range(20)}) == 1)
    _give_samples(con, [(0.3, "sonnet", "medium")] * burnrate.MIN_SAMPLES)
    check("ticks: at the floor the answer holds across ticks too",
          len({admitted() for _ in range(20)}) == 1 and admitted())
    con.close()


def test_a_cheap_agent_is_admitted_where_a_dear_one_is_not(home: Path) -> None:
    """The reason for measuring: the same account, the same fill, and each agent
    priced by its own kind - sole and shared alike."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    agents.register("dear", model="opus", effort="high")
    agents.register("cheap", model="sonnet", effort="medium")
    now = int(time.time())
    write_status(home, "beta", pct_5h=60, pct_7d=10, reset_5h=now + 3600, reset_7d=now + 86400)
    status.write_burn("beta", 0.5 / 60, now)
    _give_samples(con, [(0.9, "opus", "high")] * 6 + [(0.3, "sonnet", "medium")] * 6)

    def verdict(agent):
        picked, reasons = status.pick_account(["beta"], load=loadmod.read_all(con), agent=agent)
        return picked, dict(reasons)["beta"]

    dear, cheap = verdict("dear"), verdict("cheap")
    check("cost: the dear agent is refused (40 / (0.5 + 0.9) = 28m)",
          dear[0] is None and "(0.90%/min)" in dear[1] and "~28m" in dear[1], str(dear))
    check("cost: the cheap one is admitted on the same account (40 / (0.5 + 0.3) = 50m)",
          cheap[0] == "beta" and "(0.30%/min)" in cheap[1], str(cheap))
    check("cost: a screen that names no agent prices a typical one, and says so",
          "(0.90%/min)" not in str(status.pick_account(["beta"], load=loadmod.read_all(con))[1]),
          str(status.pick_account(["beta"], load=loadmod.read_all(con))[1]))
    # Shared: the fill is already several agents' worth; the newcomer's price is
    # still its own.
    status.write_burn("beta", 1.0 / 60, now)
    check("cost: on an account already filling at 1.0%/min the split holds: "
          "40 / 1.9 = 21m refused, 40 / 1.3 = 30m admitted",
          verdict("dear")[0] is None and verdict("cheap")[0] == "beta",
          str((verdict("dear"), verdict("cheap"))))
    con.close()


def test_every_real_caller_prices_the_agent_it_is_placing(home: Path) -> None:
    """`status.pick_account` being agent-aware is not enough on its own: each of
    `_move_plan` and `launch.find_room`'s two calls has to pass
    `agent` through, or the newcomer is priced as a typical one and this test
    catches it - dropping `agent=agent` from any of them turns the dear
    agent's refusal into an admission."""
    _daemon_home(home, 20)
    accounts.register("beta")
    credentialed(home, "alpha", "beta")
    agents.register("dear", model="opus", effort="high")
    agents.register("cheap", model="sonnet", effort="medium")
    agents.register("dear-pref", model="opus", effort="high")
    agents.register("cheap-pref", model="sonnet", effort="medium")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 10 * 3600,
                reset_7d=now + 86400)
    write_status(home, "beta", pct_5h=60, pct_7d=10, reset_5h=now + 3600, reset_7d=now + 86400)
    con = db.connect()
    status.write_burn("beta", 0.5 / 60, now)
    n = burnrate.MIN_SAMPLES
    db.set_meta(con, burnrate._SAMPLES, json.dumps(
        [{"t": i, "a": "x", "r": 0.9 / 60, "m": "opus", "e": "high"} for i in range(n)] +
        [{"t": i, "a": "x", "r": 0.3 / 60, "m": "sonnet", "e": "medium"} for i in range(n)]))
    db.set_account(con, "dear", "alpha")
    db.set_account(con, "cheap", "alpha")
    db.add_request(con, "marina", "dear", "carry on", None, "peer", "async", None, 0, None)
    db.add_request(con, "marina", "cheap", "carry on", None, "peer", "async", None, 0, None)

    dest, why = dmn_handoff._move_plan(con, "dear", "usage")
    check("callers: _move_plan refuses the dear agent (40 / (0.5 + 0.9) = 28m)",
          dest is None and "0.90%/min" in why, why)
    dest, why = dmn_handoff._move_plan(con, "cheap", "usage")
    check("callers: _move_plan admits the cheap one on the same account",
          dest == "beta" and "0.30%/min" in why, why)

    check("callers: find_room refuses the dear agent (walled preferred, refused fallback)",
          launch.find_room("dear") is None, str(launch.find_room("dear")))
    check("callers: find_room admits the cheap one via the fallback pick",
          launch.find_room("cheap") == "beta", str(launch.find_room("cheap")))
    check("callers: find_room refuses the dear agent on its own preferred account",
          launch.find_room("dear-pref") is None, str(launch.find_room("dear-pref")))
    check("callers: find_room admits the cheap one there directly, no fallback needed",
          launch.find_room("cheap-pref") == "beta", str(launch.find_room("cheap-pref")))
    con.close()


def test_the_daemon_feeds_the_estimate_each_tick(home: Path) -> None:
    """Ticks with one agent mid-turn on an account, its reading rising, end in a
    sample under that agent's kind; and with none mid-turn, in none."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("worker", model="opus", effort="high")
    db.set_account(con, "worker", "alpha")
    db.set_agent_state(con, "worker", dmn_states.RUNNING)
    now = int(time.time())
    base = now - 800
    logged: list[str] = []
    for i, pct in enumerate((10, 12, 15, 17, 19)):
        write_status(home, "alpha", pct_5h=pct, pct_7d=10, reset_5h=base + 18000,
                     rl_at=base + i * 200, collected_at=now)
        dmn_evaluate.observe_burn(con, logged.append)
    check("burn: a running agent's stretch is open, not yet a sample", not _samples_of(con))
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    dmn_evaluate.observe_burn(con, logged.append)
    got = _samples_of(con)
    check("burn: the tick that finds it idle files the sample under its kind",
          len(got) == 1 and (got[0]["m"], got[0]["e"]) == ("opus", "high")
          and abs(got[0]["r"] - 9 / 800) < 1e-9, str(got))
    dmn_evaluate.observe_burn(con, logged.append)
    write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=base + 18000,
                 rl_at=now, collected_at=now)
    db.set_agent_state(con, "backend", dmn_states.RUNNING)
    db.set_account(con, "backend", "alpha")
    # The tick's own lines go to a list of their own: it also starts the hourly plan question on a
    # thread, which logs when it finds no plan and does so whenever the machine lets it - before or
    # after the checks below. Joined, so none lands later, and kept apart, so it is not read as burn.
    ticked: list[str] = []
    dmn_evaluate.tick(con, ticked.append)
    subscriptions.wait_for_plan_ask()
    check("burn: the daemon's own tick feeds it", db.get_meta(con, "burnseg:alpha") is not None,
          str(ticked))
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    dmn_evaluate.observe_burn(con, logged.append)
    check("burn: and an idle account adds none", len(_samples_of(con)) == 1 and not logged,
          str(logged))
    con.close()


def test_the_plan_question_a_tick_starts_logs_to_the_tick_and_not_to_the_burn_check(home: Path) -> None:
    """The cause of a flaky burn check: a tick starts the hourly plan question on a thread, and with no
    plan to read it logs a line, whenever the machine schedules it. Slowed here so it always lands after
    the tick returns, which is the order a loaded machine produces: the line is the tick's, it arrives
    once the thread is joined, and a log the test keeps for the burn estimate never holds it."""
    _daemon_home(home, 20)
    con = db.connect()
    real = subscriptions.accounts.subscription_type
    try:
        def slow_none(acct, refresh=False):
            time.sleep(0.2)
            return None
        subscriptions.accounts.subscription_type = slow_none
        subscriptions._asking = None
        db.set_cursor(con, "plan_checked:alpha", 0)
        ticked: list[str] = []
        dmn_evaluate.tick(con, ticked.append)
        burn_log: list[str] = []
        dmn_evaluate.observe_burn(con, burn_log.append)
        check("plan thread: the tick returns before the question is answered", not any("no plan recorded" in m for m in ticked), str(ticked))
        subscriptions.wait_for_plan_ask()
        check("plan thread: its line is the tick's, once the thread is joined",
              any("alpha: no plan recorded" in m for m in ticked), str(ticked))
        check("plan thread: and the burn estimate's own log never holds it", not burn_log, str(burn_log))
    finally:
        subscriptions.wait_for_plan_ask()
        subscriptions.accounts.subscription_type = real
        con.close()


def test_a_cold_start_is_not_priced_as_steady_work(home: Path) -> None:
    """`measure.py` already prices the first turn since a SessionStart or
    moved_account as a cold start; a burn sample must not price it again as
    steady work, or an agent's cost is inflated by every launch."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("worker", model="opus", effort="high")
    db.set_account(con, "worker", "alpha")
    now = int(time.time())
    base = now - 4000
    db.add_event(con, "worker", "SessionStart")
    db.set_agent_state(con, "worker", dmn_states.RUNNING)
    check("cold: fresh since SessionStart, no Stop yet - it is a first turn",
          "worker" in loadmod.first_turn_agents(con))
    for i, pct in enumerate((10, 12, 15, 17, 19)):
        write_status(home, "alpha", pct_5h=pct, pct_7d=10, reset_5h=base + 18000,
                     rl_at=base + i * 200, collected_at=now)
        dmn_evaluate.observe_burn(con, lambda _m: None)
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    dmn_evaluate.observe_burn(con, lambda _m: None)
    check("cold: a stretch that held a first turn files no sample", not _samples_of(con))

    db.add_event(con, "worker", "Stop")
    check("cold: a completed turn is no longer a first one",
          "worker" not in loadmod.first_turn_agents(con))
    db.set_agent_state(con, "worker", dmn_states.RUNNING)
    base2 = now
    for i, pct in enumerate((20, 23, 27, 30, 34)):
        write_status(home, "alpha", pct_5h=pct, pct_7d=10, reset_5h=base + 18000,
                     rl_at=base2 + i * 200, collected_at=now)
        dmn_evaluate.observe_burn(con, lambda _m: None)
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    dmn_evaluate.observe_burn(con, lambda _m: None)
    got = _samples_of(con)
    check("cold: its second turn, after a Stop, files normally",
          len(got) == 1 and (got[0]["m"], got[0]["e"]) == ("opus", "high"), str(got))
    con.close()


def test_account_list_is_the_present_state_and_status_is_the_pick(home: Path) -> None:
    """`account list` is how the accounts stand now: the windows with their
    marks, the roster in the bridge's own words, no record of earlier turns.
    `status` is why the pick chose what it chose, and it carries the price of
    an agent that pick rests on."""
    from handline.daemon import states as dmn_states
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    agents.register("frontend", model="opus", effort="high")
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
        db.set_agent_state(con, "frontend", dmn_states.IDLE)
        db.set_account(con, "frontend", "alpha")
        con.commit()
        found, unplaced = loadmod.roster(con)
        cell = lambda a: textsafe.oneline(tui.agents_cell(found.get(a, (0, 0)), unplaced))  # noqa: E731
    finally:
        con.close()
    out = _capture(lambda: climod.main(["account", "list"]))
    head, alpha = out.splitlines()[0], next(l for l in out.splitlines() if l.startswith("alpha"))
    check("account list: the roster is a column, in the bridge's own words",
          "AGENTS ▶" in head and cell("alpha") == "2 ▶1" and f" {cell('alpha')} " in alpha,
          alpha)
    check("account list: an account with no agents reads 0",
          cell("beta") == "0" and " 0 " in next(l for l in out.splitlines() if l.startswith("beta")))
    check("account list: no record of what earlier turns cost",
          "one more agent costs" not in out and "samples" not in out, out)
    check("account list: the windows, their marks and the legend stay",
          "7d resets" in head and "AGE" in head and status.SOURCE_LEGEND in out, head)
    rows = json.loads(_capture(lambda: climod.main(["account", "list", "--json"])))
    by = {r["account"]: r for r in rows}
    check("account list --json: the same roster",
          (by["alpha"]["agents_assigned"], by["alpha"]["agents_running"]) == (2, 1)
          and by["beta"]["agents_assigned"] == 0, by["alpha"])
    text = _capture(lambda: climod.main(["status"]))
    check("status: the pick's reasons, the price they rest on, and the verdict",
          "one more agent costs" in text and "\npicked:" in text
          and text.index("one more agent costs") < text.index("\npicked:"), text)
    check("status --json: carries the same price",
          json.loads(_capture(lambda: climod.main(["status", "--json"])))["agent_costs"]
          == loadmod.costs().by_kind(paths.list_agents()))
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            climod.main(["status", "--table"])
        refused = False
    except SystemExit:
        refused = True
    check("status: it has one job, so the dashboard flag is gone (`hl bridge --once`)",
          refused)


def test_choose_account_does_not_bounce_back_to_a_stale_full_account(home: Path) -> None:
    """Founder's priority #241: harbor-manager moved alpha -> gamma at 85%,
    then gamma -> alpha 12 minutes later - back to the account it had just
    left, whose 5h window (5 hours) plainly had not reset. `choose_account`'s
    preferred-account check read a stale snapshot as "not full", because a
    reading that stops refreshing the moment nobody is spending on it any
    more is silence, not room - the raw figure is still a floor until the
    window it describes has actually rolled over."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")
    write_status(home, "beta", pct_5h=10, pct_7d=5,
                 reset_5h=int(time.time()) + 1800)

    # alpha was last seen at the wall and nothing has run on it since, so its
    # snapshot is now stale. Its window is still open.
    write_status(home, "alpha", pct_5h=100, pct_7d=5,
                 reset_5h=int(time.time()) + 1800,
                 collected_at=int(time.time()) - 1000)
    check("choose_account: a stale-but-still-full account is not "
          "read as room",
          launch.choose_account("keeper") == "beta", launch.choose_account("keeper"))

    # Its window has genuinely rolled over since the reading was taken - the
    # figure is stale, but it no longer describes the window that is open
    # now, so it is not a floor on anything.
    write_status(home, "alpha", pct_5h=100, pct_7d=5,
                 reset_5h=int(time.time()) - 60,
                 collected_at=int(time.time()) - 1000)
    check("choose_account: once its own window has actually reset, the "
          "account is trusted again",
          status.pick_account(["alpha"], load={})[0] == "alpha",
          str(status.pick_account(["alpha"], load={})))

    # A stale account that was never full is not held back by this at all -
    # only a figure that was itself at the wall becomes a floor.
    write_status(home, "alpha", pct_5h=20, pct_7d=5,
                 reset_5h=int(time.time()) + 1800,
                 collected_at=int(time.time()) - 1000)
    check("choose_account: a stale reading that was never full is unaffected",
          status.pick_account(["alpha"], load={})[0] == "alpha",
          str(status.pick_account(["alpha"], load={})))


def test_move_requires_clear_headroom_not_bare_assignability(home: Path) -> None:
    """A move only goes to an account that would last: one that is assignable
    but would run out within minutes of a cold start is a second move waiting to
    happen, and each one spends a start."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")
    con = db.connect()
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        sessions_are("keeper")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        # beta is assignable (80% < 100%) but at that pace one more agent would
        # last 27 minutes, under the least stretch a start is worth.
        write_status(home, "beta", pct_5h=80, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        allowed, _ = status.read("beta").assignable()
        check("move headroom: the destination is bare-assignable to begin with",
              allowed)
        t = triggers.evaluate(100, 10)
        logged: list[str] = []
        check("move headroom: a marginal destination is refused, agent stays put",
              not dmn_handoff._move_to_room(con, "keeper", t, logged.append)[0])
        check("move headroom: the account on record has not changed",
              agents.current_account("keeper", con) == "alpha",
              agents.current_account("keeper", con))
        check("move headroom: the refusal is logged",
              any("staying on alpha" in m for m in logged), str(logged))

        # Clear headroom - the same move now succeeds.
        write_status(home, "beta", pct_5h=30, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        real = (orcamod.available, orcamod.alive, orcamod.close_session,
               orcamod.relaunch, orcamod.idle_now, dmn_delivery.wait_for_session)
        try:
            orca_idle()
            orcamod.close_session = exit_ends_session("keeper")
            orcamod.relaunch = lambda h, cmd, **kw: (True, "relaunched")
            dmn_delivery.wait_for_session = lambda agent, timeout=0: True
            keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
            logged.clear()
            check("move headroom: clear headroom lets the move through",
                  dmn_handoff._move_to_room(con, "keeper", t, logged.append)[0])
            check("move headroom: it actually landed on beta",
                  agents.current_account("keeper", con) == "beta",
                  agents.current_account("keeper", con))
            check("move headroom: the successful move says why it picked beta "
                  "(decision d)",
                  any("moved alpha -> beta" in m and "5h" in m for m in logged), str(logged))
        finally:
            (orcamod.available, orcamod.alive, orcamod.close_session,
             orcamod.relaunch, orcamod.idle_now, dmn_delivery.wait_for_session) = real
    finally:
        con.close()


def test_move_to_room_reaches_a_bootstrap_account(home: Path) -> None:
    """Review of buddyron/bootstrap-and-hr-move: choose_account(bootstrap=True)
    could pick a never-run account for the move, but _move_to_room's own
    re-check called plain assignable(), which refuses "no status.json" - the
    same reason bootstrap exists to lift. A bootstrapped pick was reachable
    from an ordinary launch and never from the one place an agent at the wall
    needs a second account most."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper")
    write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5,
                 reset_5h=int(time.time()) + 3 * 3600)
    accounts.register("gamma")
    (home / "accounts" / "gamma" / ".claude" / ".credentials.json").write_text("{}")
    keep_handoff("keeper", "# handoff · keeper\n## Next\n- carry on\n")
    db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
            dmn_delivery.wait_for_session)
    try:
        orca_idle()
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        orcamod.close_session = exit_ends_session("keeper")
        orcamod.resume_with = lambda h, text: (True, "handed back")
        orcamod.relaunch = lambda h, cmd, **kw: (True, "relaunched")
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        sessions_are("keeper")
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_account(con, "keeper", "alpha")

        check("move (bootstrap): choose_account reaches the never-run account",
              launch.choose_account("keeper") == "gamma")
        t = triggers.evaluate(100, 10)
        check("move (bootstrap): the move actually completes",
              dmn_handoff._move_to_room(con, "keeper", t, lambda _m: None)[0])
        check("move (bootstrap): and lands on the bootstrapped account",
              agents.current_account("keeper", con) == "gamma",
              agents.current_account("keeper", con))
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
         dmn_delivery.wait_for_session) = real
        con.close()


def test_a_fold_with_no_account_says_which_and_why(home: Path) -> None:
    """"No logged-in account" sent the reader to the one thing that was not
    wrong. An account is passed over here for being at the wall, or for a usage
    figure nothing can read, far more often than for credentials."""
    use_home(home)
    for name in ("full", "unreadable", "out"):
        accounts.register(name)
    (vendors.config_dir("full") / ".credentials.json").write_text("{}")
    (vendors.config_dir("unreadable") / ".credentials.json").write_text("{}")
    write_status(home, "full", pct_5h=100, pct_7d=10, ctx_pct=5)
    # Stale, not quiet: a fresh snapshot with no figure is read as 0%, which is
    # a reading. What cannot be read at all is one nothing has refreshed.
    write_status(home, "unreadable", pct_5h=None, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - status.STALE_AFTER_S - 60)

    said = foldmod._account_reasons()
    check("fold: the account at the wall is named with its figure",
          "full at 100%" in said, said)
    check("fold: the one nothing can read is named as that, not as logged out",
          "unreadable usage unreadable" in said, said)
    check("fold: and the one actually logged out is named as that",
          "out not logged in" in said, said)


def test_a_moved_handoff_with_a_good_record_clears_the_streak(home: Path) -> None:
    """The moved-path reset in `_perform_handoff` (an agent that moves to
    another account never runs its own clear, so a good record is the whole
    of what it can vouch for) - covered on its own, since a handoff that ends
    in a move never reaches the record+clear reset below it."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper")
    db.set_account(con, "keeper", "alpha")
    con.commit()
    write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    credentialed(home, "alpha", "beta")
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
            dmn_delivery.wait_for_session)
    try:
        orca_idle()
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        orcamod.close_session = exit_ends_session("keeper")
        orcamod.resume_with = lambda h, text: (True, "handed back")
        orcamod.relaunch = lambda h, cmd, **kw: (
            _SESSION_TABLE["t"].update({"keeper": 4300}), (True, "relaunched"))[1]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        readable_agent(con, home, "keeper")
        sessions_are("keeper")
        # A move only happens for work waiting - an idle agent at the wall
        # just parks (see _move_to_room), which would exercise the other
        # reset site instead of the one this test is for.
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        for _ in range(3):
            db.bump_counter(con, "keeper", "throttle_count")

        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        t = triggers.evaluate(100, 10)
        dmn_handoff.perform_handoff(con, "keeper", t, lambda _m: None)

        row = db.get_agent_state(con, "keeper")
        check("moved handoff: it really moved (state dispatched, not parked)",
              row["state"] == dmn_states.QUEUED, str(dict(row)))
        check("moved handoff: a good record clears the streak on its own",
              row["throttle_count"] == 0, str(dict(row)))
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
         dmn_delivery.wait_for_session) = real
        con.close()


def test_a_moved_handoff_with_a_failed_record_still_marks_typed(home: Path) -> None:
    """The move path's `typed=True` (#2064's (ok, typed) split) is unconditional
    on `ok`: a move always types /exit, whether or not the record landed. This
    matters because the move calls the unlocked `_wake_agent`, which does not
    mark `typed_at` itself - the mark this test is for is the only thing
    covering the wake prompt /exit-then-resume just typed into the new
    session (#1973's settle window). No `readable_agent` here, so the record
    comes out degraded (`ok=False`) while the move still succeeds - the case
    should-fix 1 in #2108 asked for."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper")
    db.set_account(con, "keeper", "alpha")
    con.commit()
    write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    credentialed(home, "alpha", "beta")
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
            dmn_delivery.wait_for_session)
    try:
        orca_idle()
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        orcamod.close_session = exit_ends_session("keeper")
        orcamod.resume_with = lambda h, text: (True, "handed back")
        orcamod.relaunch = lambda h, cmd, **kw: (
            _SESSION_TABLE["t"].update({"keeper": 4300}), (True, "relaunched"))[1]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        sessions_are("keeper")
        # A terminal handle, so the move itself can go ahead (`_move_to_room`
        # needs one) - but no `write_agent_status`, so the record still comes
        # out degraded for the session facts it cannot read.
        db.set_terminal(con, "keeper", "term_x", None)
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)

        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        db.clear_cursor(con, "typed_at:keeper")
        t = triggers.evaluate(100, 10)
        dmn_handoff.perform_handoff(con, "keeper", t, lambda _m: None)

        row = db.get_agent_state(con, "keeper")
        check("moved-degraded: it really moved (state dispatched, not parked)",
              row["state"] == dmn_states.QUEUED, str(dict(row)))
        check("moved-degraded: the record itself is degraded",
              row["throttle_count"] > 0, str(dict(row)))
        check("moved-degraded: the move's own /exit still marks typed_at",
              db.get_cursor(con, "typed_at:keeper") > 0, str(dict(row)))
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
         dmn_delivery.wait_for_session) = real
        con.close()


def test_a_usage_5h_handoff_still_parks_until_reset(home: Path) -> None:
    """The one trigger #1903 keeps waiting for: a usage wall does not clear
    until its own window rolls over, whatever a context handoff now does.
    """
    _daemon_home(home, 100)
    con = db.connect()
    real_clear, real_alive, real_idle = orcamod.clear_context, orcamod.alive, orcamod.idle_now
    try:
        db.set_account(con, "backend", "alpha")
        reset_at = int(time.time()) + 9000
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=reset_at)
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        sessions_are("backend")

        t = triggers.evaluate(100, 5)
        check("usage_5h fixture: fires the usage trigger", t.kind == "usage", t.kind)
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("usage_5h handoff: still parks", row["state"] == dmn_states.PARKED, str(dict(row)))
        check("usage_5h handoff: wakes at the account's own reset",
              row["wake_at"] is not None and abs(row["wake_at"] - reset_at) < 5,
              str(dict(row)))
    finally:
        orcamod.clear_context, orcamod.alive = real_clear, real_alive
        orcamod.idle_now = real_idle
        con.close()


def test_a_hand_park_against_an_assumed_account_stays_forceable(home: Path) -> None:
    """`actions.park` (the screen's `p`) is #1620/#1625's one park a person may
    force early - but an agent with no recorded or reported account reads as
    'assumed' (agents.account_reading's ordinary fallback, not an edge case),
    which also asks for the short account-move recheck window. That ceiling
    is not a reason to lose the by-hand mark: `w` must still force it.

    Built through actions.park() itself, not a hand-written detail string -
    the fixture production actually makes, per the auditor's #1744 should-fix."""
    _daemon_home(home, 50)
    con = db.connect()
    real_alive, real_idle = orcamod.alive, orcamod.idle_now
    try:
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="cleared")
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        con.commit()
        check("park: no account is recorded, so there is no reading",
              agents.account_reading("backend", con) is None)

        actions.park(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("park: it parked", row["state"] == dmn_states.PARKED, str(dict(row)))
        check("park: the by-hand mark survives the assumed-account short retry",
              row["detail"].startswith(dmn_states.BY_HAND_MARK), row["detail"])
        ok, why = actions.can(con, "backend", "wake")
        check("park: and w may still force it", ok, why)
    finally:
        orcamod.alive, orcamod.idle_now = real_alive, real_idle
        con.close()


def test_a_park_with_every_account_walled_is_a_clean_handoff(home: Path) -> None:
    """The fold used to fail here - no account could pay for the call - and every
    walled park counted as degraded. The handoff needs no account."""
    _daemon_home(home, 100)
    wt = _worktree_with_a_dirty_file(home / "wt-backend")
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(wt))
        write_agent_status("backend", "alpha", session_id="sess-1",
                           transcript_path="/tmp/sess-1.jsonl")
        logs: list[str] = []
        dmn_evaluate.tick(con, logs.append)
        row = db.get_agent_state(con, "backend")
        check("park: a walled agent is parked", row["state"] == dmn_states.PARKED, str(dict(row)))
        check("park: the handoff is not degraded, so nothing accrues to the streak",
              row["throttle_count"] == 0
              and not any("degraded" in m for m in logs), f"{dict(row)} {logs}")
        check("park: the record is waiting for its wake",
              "sess-1" in (db.get_handoff(con, "backend") or ""))
        check("park: no fold failure is logged",
              not con.execute("SELECT 1 FROM events WHERE kind = 'fold_failed'").fetchall())
    finally:
        con.close()


def test_env_of_is_not_fooled_by_an_env_looking_token_in_argv(home: Path) -> None:
    """`ps eww` prints argv before the real environment, so an agent launched
    with an argument that itself contains `NAME=value` text - a system prompt
    passed with `--append-system-prompt`, say - puts that text ahead of the
    real environment in the same line. Scanning the whole line for the first
    `OCP_AGENT=` token, as `env_of` used to, reads the argv one and misnames
    the agent; the daemon then sees the real one as gone and types its launch
    command into the live session it is still running in. `env_of` must read
    only what is actually the environment."""
    real_run = obsmod.subprocess.run

    class FakeResult:
        def __init__(self, rc: int, out: str) -> None:
            self.returncode, self.stdout = rc, out

    cmd_line = "/bin/claude --append-system-prompt 'ship it, OCP_AGENT=orcapipe please'\n"
    env_line = cmd_line.rstrip("\n") + " OCP_AGENT=keeper OCP_HOME=/x\n"

    def ps(call, **k):
        return FakeResult(0, env_line if call[1] == "eww" else cmd_line)

    try:
        obsmod.subprocess.run = ps
        check("env_of: reads the real environment's OCP_AGENT, not argv's",
              obsmod.env_of(4200, "OCP_AGENT") == "keeper",
              str(obsmod.process_env(4200)))
    finally:
        obsmod.subprocess.run = real_run


def test_a_logged_out_account_is_gone_from_the_forecasts_until_it_logs_in_again(home: Path) -> None:
    use_home(home)
    now = int(time.time())
    for a in ("alpha", "beta"):
        write_status(home, a, pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600)
        status.write_burn(a, 1e-3, now)
    real = accounts.login_state
    try:
        accounts.login_state = lambda a: True
        both = status.team_outlook(["alpha", "beta"], now)
        check("logged out: both logged in are counted", "2 accounts" in both, both)
        accounts.login_state = lambda a: a != "beta"
        check("logged out: an account with no credential is not counted",
              "1 account " in status.team_outlook(["alpha", "beta"], now) + " ", status.team_outlook(["alpha", "beta"], now))
        check("logged out: and a caller's own test says who is", "2 accounts" in status.team_outlook(
            ["alpha", "beta"], now, gone=lambda n: False))
        accounts.login_state = lambda a: None
        check("logged out: an account whose login cannot be seen is still counted",
              "2 accounts" in status.team_outlook(["alpha", "beta"], now))
        accounts.login_state = lambda a: True
        accounts.mark_login_expired("beta", "Login expired")
        check("logged out: a marked expired login is not counted, whatever its credential says",
              "1 account " in status.team_outlook(["alpha", "beta"], now) + " ")
        marked = accounts.login_expired("beta")["at"]
        check("logged out: it is gone from the moment it was found, not from now",
              accounts.gone_since("beta", now + 999) == marked and accounts.gone_since("alpha", now) is None)
        accounts.clear_login_expired("beta")
        check("logged out: counted again once the mark goes (a login)",
              "2 accounts" in status.team_outlook(["alpha", "beta"], now))
        accounts.login_state = lambda a: a != "beta"
        picked, ranked = status.pick_account()
        check("logged out: the default pick leaves it out", all(n != "beta" for n, _ in ranked), str(ranked))
    finally:
        accounts.login_state = real


def test_the_graph_stops_counting_a_logged_out_account_from_when_it_was_found(home: Path) -> None:
    use_home(home)
    now = 1_800_000_000
    con = db.connect()
    try:
        for a, used in (("alpha", 20), ("beta", 60)):
            for h in range(24, -1, -1):
                ts = now - h * 3600
                con.execute("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)",
                            (a, ts, json.dumps([{"minutes": 10080, "used_percent": used,
                                                 "resets_at": now + 3 * 86400, "observed_at": ts}])))
        con.commit()
        real = accounts.login_state
        accounts.login_state = lambda a: True
        try:
            both = actions.weekly_outlook(con, now)
            accounts.mark_login_expired("beta", "Login expired")
            accounts.login_state = lambda a: a != "beta"
            gone = actions.weekly_outlook(con, now)
        finally:
            accounts.login_state = real
    finally:
        con.close()
    check("graph: both counted while logged in", both.future[-1][1] == 60, str(both.future[-1]))
    check("graph: the logged-out one is left out of what is still to come (alpha 80% left alone)",
          gone.future[-1][1] == 80, str(gone.future[-1]))


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_a_logged_out_account_is_gone_from_the_forecasts_until_it_logs_in_again(Path(td) / 'gone1'))
        run(lambda: test_the_graph_stops_counting_a_logged_out_account_from_when_it_was_found(Path(td) / 'gone2'))
        run(lambda: test_pick_account_orders_by_weekly_pace(Path(td) / 'deadline'))
        run(lambda: test_pace_matches_the_worked_examples_that_were_written_backwards(Path(td) / 'foundersex'))
        run(lambda: test_a_near_wall_7d_account_is_not_specially_excluded(
            Path(td) / 'nearwall'))
        run(lambda: test_pick_account_does_not_bounce_between_two_close_accounts(
            Path(td) / 'nobounce'))
        run(lambda: test_pick_account_prefer_breaks_an_exact_bucket_tie(Path(td) / 'prefertie'))
        run(lambda: test_pick_account_tonights_shape(Path(td) / 'tonight'))
        run(lambda: test_pick_account_degrades_when_every_account_would_hit_the_wall(
            Path(td) / 'allwall'))
        run(lambda: test_pick_account_stale_and_unknown_pace(Path(td) / 'staleknown'))
        run(lambda: test_live_refresh_asks_a_carried_old_candidate_before_refusing_it(
            Path(td) / 'liverefreshcarried'))
        run(lambda: test_live_refresh_checks_a_stale_idle_floor_before_admitting_it(
            Path(td) / 'liverefreshfloor'))
        run(lambda: test_live_refresh_asks_at_most_one_candidate_per_call(
            Path(td) / 'liverefreshcap'))
        run(lambda: test_status_methods_all_honor_a_pinned_as_of())
        run(lambda: test_each_calls_tokens_are_recorded_per_account_from_the_transcripts(
            Path(td) / 'tokencalls'))
        run(lambda: test_the_weeks_ratio_to_the_5h_window_is_measured_from_the_samples(Path(td) / 'weekratio'))
        run(lambda: test_an_account_the_week_is_about_to_close_is_not_usable_and_not_picked(
            Path(td) / 'weekwall'))
        run(lambda: test_the_week_check_moves_the_pick_where_pace_alone_would_not(
            Path(td) / 'weekorder'))
        run(lambda: test_a_week_that_rolls_over_first_is_not_closed_by_its_runway(Path(td) / 'weekroll'))
        run(lambda: test_usage_samples_round_trip_without_coercing_a_gap_to_zero(
            Path(td) / 'usagesamples'))
        run(lambda: test_the_usage_sample_prune_and_since_read_use_an_index(
            Path(td) / 'sampleindex'))
        run(lambda: test_a_tick_samples_every_account_and_a_down_daemon_leaves_a_gap(
            Path(td) / 'tickgap'))
        run(lambda: test_replay_finds_where_the_new_rule_and_the_old_one_disagree(
            Path(td) / 'replaydiff'))
        run(lambda: test_replay_reconstructs_burn_and_lets_the_week_gate_fire(
            Path(td) / 'replayweek'))
        run(lambda: test_replay_burn_is_the_daemons_burn(Path(td) / 'replayburn'))
        run(lambda: test_usage_api_reads_a_window_nothing_has_been_spent_in())
        run(lambda: test_read_claude_tells_a_dead_credential_apart_from_transient())
        run(lambda: test_access_token_detailed_tells_missing_apart_from_could_not_read())
        run(lambda: test_a_sandboxed_process_cannot_tell_a_missing_login_from_a_hidden_one(Path(td) / 'sbxlogin'))
        run(lambda: test_the_zai_meter_reads_the_plan_and_hides_its_key(Path(td) / 'zaimeter'))
        run(lambda: test_a_reading_is_asked_of_the_accounts_own_drivers_meter(Path(td) / 'meterdrv'))
        run(lambda: test_usage_api_record_stands_in_for_a_probe(Path(td) / 'usagerec'))
        run(lambda: test_usage_recover_is_a_per_account_switch(Path(td) / 'recoverswitch'))
        run(lambda: test_recover_marks_and_clears_needs_relogin(Path(td) / 'recovermark'))
        run(lambda: test_recover_spends_one_turn_and_rereads(Path(td) / 'recoverturn'))
        run(lambda: test_the_pull_reads_the_context_the_statusline_used_to_push(
            Path(td) / 'pull'))
        run(lambda: test_a_cleared_session_reads_zero_and_an_unsearchable_one_reads_unknown(
            Path(td) / 'pullclear'))
        run(lambda: test_the_newest_transcript_across_accounts_is_the_session(
            Path(td) / 'pullmove'))
        run(lambda: test_an_unknown_model_gets_no_context_figure(Path(td) / 'pullmodel'))
        run(lambda: test_the_daemon_pulls_only_for_an_open_session(Path(td) / 'pulltick'))
        run(lambda: test_a_turn_ending_refreshes_the_snapshot_through_the_hook(
            Path(td) / 'pullhook'))
        run(lambda: test_the_usage_poll_asks_one_account_per_tick_and_renews_only_on_failure(
            Path(td) / 'pullpoll'))
        run(lambda: test_a_failing_account_never_blocks_the_ones_behind_it(Path(td) / 'pollstarve'))
        run(lambda: test_one_accounts_usage_read_raising_is_contained(Path(td) / 'pollraise'))
        run(lambda: test_poll_usage_calls_recover_on_a_dead_token_and_at_the_5h_boundary(
            Path(td) / 'pollrecover'))
        run(lambda: test_a_reset_window_gets_one_turn_per_reset_and_never_while_exhausted(
            Path(td) / 'resetturn'))
        run(lambda: test_a_reading_past_the_carry_bound_is_carried_old_even_though_the_flag_is_false(
            Path(td) / 'carriedoldpin'))
        run(lambda: test_a_carried_reading_with_no_age_at_all_reads_carried_old_not_live(
            Path(td) / 'carriednoage'))
        run(lambda: test_context_tokens_override_falls_back_on_garbage())
        run(lambda: test_the_clamp_holds_a_small_window_agent_down())
        run(lambda: test_an_unknown_model_now_hands_off_on_tokens(Path(td) / 'ctxunknown'))
        run(lambda: test_a_stale_ctx_used_does_not_fire(Path(td) / 'ctxstale'))
        run(lambda: test_status_read_sums_a_raw_ctx_used_dict_the_way_collect_does(
            Path(td) / 'ctxsum'))
        run(lambda: test_a_new_account_gets_its_first_reading_without_anyone_asking(
            Path(td) / 'newacct'))
        run(lambda: test_status_edge_cases(Path(td) / 'se'))
        run(lambda: test_carry_bounds_and_losses(Path(td) / 'carry'))
        run(lambda: test_an_account_change_and_a_new_account_leave_project_memory_alone(Path(td) / 'memshare'))
        run(lambda: test_unobserved_spend_stays_off_unreadable_accounts(Path(td) / 'spend'))
        run(lambda: test_subscription_type_is_recorded_and_cached(Path(td) / 'subtype'))
        run(lambda: test_burn_rate_ignores_carried_samples(Path(td) / 'burn'))
        run(lambda: test_a_session_figure_is_not_shown_as_an_accounts(Path(td) / 'sessfig'))
        run(lambda: test_a_woken_agent_is_not_judged_on_the_snapshot_it_left(Path(td) / 'wakejudge'))
        run(lambda: test_find_transcript_walks_accounts_and_takes_only_a_plain_id(Path(td) / 'find_trans'))
        run(lambda: test_a_restart_finds_the_previous_transcript_without_a_status_path(Path(td) / 'restarttr'))
        run(lambda: test_only_the_agents_own_events_move_its_activity_clock(Path(td) / 'actclock'))
        run(lambda: test_a_dead_sessions_id_is_kept_only_from_a_matching_snapshot(Path(td) / 'deadacct'))
        run(lambda: test_a_moved_walled_wake_names_the_new_account(Path(td) / 'walledmoved'))
        run(lambda: test_a_resume_is_not_woken_onto_an_account_at_its_weekly_wall(Path(td) / 'walled7d'))
        run(lambda: test_a_start_with_no_room_says_why_and_is_not_a_failed_attempt(Path(td) / 'noroom'))
        run(lambda: test_a_move_has_nowhere_to_go_when_every_account_is_walled(Path(td) / 'walled7dmove'))
        run(lambda: test_a_usage_park_is_woken_when_the_reading_clears(Path(td) / 'parklevel1'))
        run(lambda: test_a_usage_park_stays_parked_while_the_reading_is_at_the_wall(Path(td) / 'parklevel2'))
        run(lambda: test_a_weekly_park_stays_parked_while_the_week_is_full(Path(td) / 'weekpark'))
        run(lambda: test_a_context_park_does_not_auto_clear_on_a_usage_reading(Path(td) / 'parklevel4'))
        run(lambda: test_an_assumed_account_does_not_auto_clear_a_park(Path(td) / 'parklevel5'))
        run(lambda: test_account_reading_prefers_recorded_then_reported_then_assumed(Path(td) / 'acctread'))
        run(lambda: test_supervise_reads_the_reported_account_not_an_assumed_preference(Path(td) / 'acctsupervise'))
        run(lambda: test_a_park_against_an_assumed_account_gets_a_short_recheck(Path(td) / 'acctrecheck'))
        run(lambda: test_a_resumed_handoff_also_fails_closed_on_an_assumed_account(Path(td) / 'acctrecheck2'))
        run(lambda: test_a_spawned_agent_gets_an_assumed_account_until_launch_confirms_it(Path(td) / 'acctspawn'))
        run(lambda: test_starts_for_waiting_work_that_keep_failing_stall_the_agent(Path(td) / 'startstall'))
        run(lambda: test_an_agent_latched_idle_before_the_stall_existed_stalls_with_its_reason(Path(td) / 'latchedidle'))
        run(lambda: test_a_near_wall_7d_account_is_still_a_candidate_but_not_guaranteed_the_pick(
            Path(td) / 'sevendaywall'))
        run(lambda: test_an_agent_is_judged_on_the_account_its_process_is_on(Path(td) / 'procacct'))
        run(lambda: test_a_move_is_not_recorded_while_the_process_is_still_on_the_old_account(Path(td) / 'movever'))
        run(lambda: test_a_forced_move_goes_through_the_verified_move_or_not_at_all(Path(td) / 'forced'))
        run(lambda: test_an_agent_cannot_force_a_move(Path(td) / 'forcedagent'))
        run(lambda: test_an_agent_at_the_wall_moves_to_an_account_with_room(Path(td) / 'moveacct'))
        run(lambda: test_a_move_blocked_by_a_dialog_retries_soon_not_at_the_old_accounts_wall(
            Path(td) / 'movedialog'))
        run(lambda: test_the_usage_limit_dialog_is_declined_with_the_key_that_was_measured(
            Path(td) / 'limitkey'))
        run(lambda: test_an_agent_parks_through_the_usage_limit_dialog(Path(td) / 'limitpark'))
        run(lambda: test_a_parked_agent_gets_past_a_usage_limit_dialog_before_its_wake(
            Path(td) / 'limitparked'))
        run(lambda: test_a_pinned_closed_agent_moves_instead_of_waiting_out_its_window(
            Path(td) / 'pinned'))
        run(lambda: test_a_stale_account_whose_window_rolled_over_can_take_work(Path(td) / 'stalebeta'))
        run(lambda: test_room_is_filtered_before_the_ranking_not_after(Path(td) / 'roomfirst'))
        run(lambda: test_the_runway_is_an_estimate_from_the_daemons_burn(Path(td) / 'runway'))
        run(lambda: test_the_active_cap_decides_where_the_next_agent_may_start(Path(td) / 'ar0'))
        run(lambda: test_the_pick_weighs_how_fast_an_account_is_filling(Path(td) / 'ar1'))
        run(lambda: test_at_the_wall_it_is_a_cost_comparison_not_a_line(Path(td) / 'ar2'))
        run(lambda: test_the_cold_start_is_measured_and_sets_how_long_to_wait(Path(td) / 'ar4'))
        run(lambda: test_a_stale_snapshot_shows_its_last_figures_and_still_is_offered(Path(td) / 'ar4c'))
        run(lambda: test_pick_account_judges_an_idle_account_on_its_floor_not_on_zero(Path(td) / 'idlefloor'))
        run(lambda: test_idle_floor_refuses_an_account_with_an_agent_mid_turn_on_it(Path(td) / 'idleactive'))
        run(lambda: test_idle_floor_refuses_a_snapshot_older_than_its_own_window(Path(td) / 'idleage'))
        run(lambda: test_idle_floor_margin_and_its_refusal_line(Path(td) / 'idlemargin'))
        run(lambda: test_a_sweep_after_a_move_does_not_keep_the_old_accounts_session(Path(td) / 'ar4e'))
        run(lambda: test_replaying_0919_makes_fewer_moves_than_the_old_rules(Path(td) / 'ar6'))
        run(lambda: test_a_turn_that_ends_on_the_usage_limit_is_at_the_wall(Path(td) / 'limitstop'))
        run(lambda: test_an_unknown_reset_does_not_make_the_runway_absurd(Path(td) / 'runwayunknown'))
        run(lambda: test_what_one_agent_costs_is_measured_from_stretches_of_work(Path(td) / 'burnobs'))
        run(lambda: test_an_agent_is_priced_by_the_nearest_kind_with_samples(Path(td) / 'burnkind'))
        run(lambda: test_the_price_of_an_agent_holds_still_across_ticks(Path(td) / 'burnticks'))
        run(lambda: test_a_cheap_agent_is_admitted_where_a_dear_one_is_not(Path(td) / 'burncheap'))
        run(lambda: test_every_real_caller_prices_the_agent_it_is_placing(Path(td) / 'burncallers'))
        run(lambda: test_the_daemon_feeds_the_estimate_each_tick(Path(td) / 'burntick'))
        run(lambda: test_the_plan_question_a_tick_starts_logs_to_the_tick_and_not_to_the_burn_check(Path(td) / 'plankick'))
        run(lambda: test_a_cold_start_is_not_priced_as_steady_work(Path(td) / 'burncold'))
        run(lambda: test_account_list_is_the_present_state_and_status_is_the_pick(Path(td) / 'acctlist'))
        run(lambda: test_choose_account_does_not_bounce_back_to_a_stale_full_account(
            Path(td) / 'nobounce'))
        run(lambda: test_move_requires_clear_headroom_not_bare_assignability(
            Path(td) / 'moveheadroom'))
        run(lambda: test_move_to_room_reaches_a_bootstrap_account(Path(td) / 'movebootstrap'))
        run(lambda: test_a_fold_with_no_account_says_which_and_why(Path(td) / 'foldacct'))
        run(lambda: test_a_moved_handoff_with_a_good_record_clears_the_streak(Path(td) / 'movedreset'))
        run(lambda: test_a_moved_handoff_with_a_failed_record_still_marks_typed(Path(td) / 'movedtyped'))
        run(lambda: test_a_usage_5h_handoff_still_parks_until_reset(Path(td) / 'usage5hpark'))
        run(lambda: test_a_hand_park_against_an_assumed_account_stays_forceable(Path(td) / 'handpark'))
        run(lambda: test_a_park_with_every_account_walled_is_a_clean_handoff(Path(td) / 'hdwalled'))
        run(lambda: test_env_of_is_not_fooled_by_an_env_looking_token_in_argv(Path(td) / 'envargv'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
