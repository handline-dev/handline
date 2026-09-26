"""Tests: what the daemon types into a session: delivery, dialogs, closes, blocks.

Run on its own: python3 tests/session/test_session_io.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




# ---------------------------------------------------------------- fail-closed
def test_failclosed(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)

    # A fresh snapshot whose payload carried no five_hour block is the quiet
    # case, not the unknown one: the collector is alive and nothing has been
    # spent in this window. Refusing here parked five idle agents once.
    write_status(home, "quiet", pct_5h=None, pct_7d=None)
    q = status.read("quiet")
    check("no 5h window in a fresh snapshot reads as quiet", q.short_source == "quiet",
          q.short_source)
    allowed, why = q.assignable()
    check("a quiet window assigns", allowed, why)

    # The weekly figure comes off the same file and was drawn as a measurement
    # while the 5h one beside it went to "?". Inside one window weekly usage
    # only rises, so an old reading is a floor - marked as carried, not drawn
    # as measured - and once the window has rolled it is not even a floor.
    write_status(home, "wk", pct_5h=10, pct_7d=60, reset_7d=int(time.time()) + 3600,
                 rl7_at=int(time.time()))
    w = status.read("wk")
    check("a fresh weekly figure is live", w.long_source == "live"
          and w.long_trusted == 60, w.long_source)
    write_status(home, "wk", pct_5h=10, pct_7d=60, reset_7d=int(time.time()) + 3600,
                 rl7_at=int(time.time()), collected_at=int(time.time()) - 100000)
    w = status.read("wk")
    check("a stale one is a floor, and says so",
          w.long_trusted == 60 and status.mark(w.long_source) in ("~", "!"),
          f"{w.long_trusted} {w.long_source}")
    write_status(home, "wk", pct_5h=10, pct_7d=60, reset_7d=int(time.time()) - 60,
                 rl7_at=int(time.time()))
    w = status.read("wk")
    check("and a window that has rolled over leaves no figure at all",
          w.long_trusted is None, str(w.long_trusted))
    check("so a finished week cannot block an account forever",
          w.assignable()[0] or "7d" not in w.assignable()[1], str(w.assignable()))
    check("and says the 0% was inferred", "inferred" in why, why)
    check("quiet headroom is the full window", q.short_headroom() == 100, str(q.short_headroom()))
    check("the raw reading stays null", q.short.pct is None, repr(q.short.pct))

    # Unknown is what fail-closed is for, and these are the shapes of it.
    write_status(home, "unreadable", pct_5h="lots")
    u = status.read("unreadable")
    check("an unreadable pct_5h is unknown", u.short_source == "unknown", u.short_source)
    allowed, why = u.assignable()
    check("and refuses assignment", not allowed, why)
    check("headroom is None when unknown", u.short_headroom() is None)

    (home / "accounts" / "nofield").mkdir(parents=True, exist_ok=True)
    (home / "accounts" / "nofield" / "status.json").write_text(
        json.dumps({"account": "nofield", "collected_at": int(time.time()), "session_id": "s"}))
    nf = status.read("nofield")
    check("a snapshot with no pct_5h field at all is unknown",
          nf.short_source == "unknown", nf.short_source)
    check("and refuses", not nf.assignable()[0])

    # A reading survives a payload that does not mention the window, while that
    # window is still open - the collector carries it and says so.
    write_status(home, "carried", pct_5h=40, reset_5h=int(time.time()) + 600,
                 rl_carried=True, rl_at=int(time.time()) - 300)
    c = status.read("carried")
    check("a carried reading is used", c.short_trusted == 40, str(c.short_trusted))
    allowed, why = c.assignable()
    check("a carried reading assigns", allowed, why)
    check("and is labelled carried", "carried" in why, why)

    # The API returns these as floats, sometimes untidy ones. Rounding must go
    # up, so a rounding error triggers sooner rather than later.
    write_status(home, "floaty", pct_5h=28.999999999999996, pct_7d=10.0)
    check("float percentage rounds up, not down", status.read("floaty").short.pct == 29,
          str(status.read("floaty").short.pct))
    write_status(home, "nearly", pct_5h=84.9, pct_7d=10)
    allowed, why = status.read("nearly").assignable()
    check("84.9% rounds to 85 and still assigns below the block", allowed, why)
    write_status(home, "overish", pct_5h=89.6, pct_7d=10)
    allowed, why = status.read("overish").assignable()
    check("89.6% rounds to 90 and still assigns: the wall is 100", allowed, why)
    write_status(home, "walled", pct_5h=99.6, pct_7d=10)
    allowed, why = status.read("walled").assignable()
    check("99.6% rounds to 100 and is refused", not allowed, why)

    write_status(home, "fresh", pct_5h=10, pct_7d=20)
    allowed, why = status.read("fresh").assignable()
    check("healthy account is assignable", allowed, why)
    check("headroom computed", status.read("fresh").short_headroom() == 90)

    write_status(home, "hot", pct_5h=100, pct_7d=20)
    allowed, _ = status.read("hot").assignable()
    check("5h at the wall refuses", not allowed)

    write_status(home, "weekly", pct_5h=5, pct_7d=95)
    allowed, why = status.read("weekly").assignable()
    check("7d at 95% no longer refuses - request #254 spends it to its own "
          "wall instead of holding back", allowed, why)
    write_status(home, "weekly_full", pct_5h=5, pct_7d=100)
    allowed, _ = status.read("weekly_full").assignable()
    check("7d at 100% refuses", not allowed)

    write_status(home, "stale", pct_5h=5, collected_at=int(time.time()) - 5000)
    allowed, why = status.read("stale").assignable()
    check("stale snapshot refuses", not allowed, why)

    allowed, why = status.read("ghost").assignable()
    check("missing status.json refuses", not allowed, why)

    (home / "accounts" / "broken").mkdir(parents=True, exist_ok=True)
    (home / "accounts" / "broken" / "status.json").write_text("{not json")
    allowed, why = status.read("broken").assignable()
    check("corrupt status.json refuses", not allowed, why)

    # Selection must prefer headroom, and must refuse when nothing qualifies.
    picked, _ = status.pick_account(["unreadable", "fresh", "hot"])
    check("pick_account chooses the healthy one", picked == "fresh", str(picked))
    picked, reasons = status.pick_account(["unreadable", "hot", "stale"])
    check("pick_account returns None when nothing qualifies", picked is None, str(picked))
    check("pick_account explains every candidate", len(reasons) == 3)

    # A quiet account has the most headroom there is, so it wins on headroom;
    # at equal headroom the account we actually have a reading for wins.
    picked, _ = status.pick_account(["quiet", "fresh"])
    check("pick_account takes the quiet account over a busier one", picked == "quiet",
          str(picked))
    write_status(home, "reported0", pct_5h=0)
    picked, _ = status.pick_account(["quiet", "reported0"])
    check("a reported 0% beats an inferred one", picked == "reported0", str(picked))

    write_status(home, "roomy", pct_5h=2)
    picked, _ = status.pick_account(["fresh", "roomy"])
    check("pick_account maximizes headroom", picked == "roomy", str(picked))

    # request #154/#165 (c): an account nothing has ever run on cannot be
    # picked by default - only bootstrap=True makes it a last resort.
    picked, _ = status.pick_account(["ghost", "hot"])
    check("pick_account: a never-run account is not picked by default",
          picked is None, str(picked))
    picked, reasons = status.pick_account(["ghost", "hot"], bootstrap=True)
    check("pick_account: bootstrap=True picks it once nothing else qualifies",
          picked == "ghost", str(picked))
    check("pick_account: and says why", "bootstrap" in dict(reasons)["ghost"],
          dict(reasons)["ghost"])
    picked, _ = status.pick_account(["ghost", "fresh"], bootstrap=True)
    check("pick_account: bootstrap never wins over an account that already qualifies",
          picked == "fresh", str(picked))
    picked, _ = status.pick_account(["stale"], bootstrap=True)
    check("pick_account: bootstrap does not rescue a refusal that is not 'no reading at all'",
          picked is None, str(picked))


def test_daemon_success_clears_streak(home: Path) -> None:
    """A handoff that works - record AND clear both land - must end the
    streak, or every agent eventually stalls just from having been busy.

    Orca must be reachable for the clear to land at all (#1975): without it
    `_reclaim_context` fails every time regardless of the record, and the
    streak would correctly stay put, not exercising the success path this
    test is for.
    """
    _daemon_home(home, 100)
    con = db.connect()
    real = (orcamod.clear_context, orcamod.alive, orcamod.idle_now, orcamod.live_terminals)
    try:
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        orcamod.live_terminals = lambda: {"term_x"}
        for _ in range(3):
            db.bump_counter(con, "backend", "throttle_count")
        readable_agent(con, home, "backend")
        sessions_are("backend")
        db.set_agent_state(con, "backend", dmn_states.QUEUED)
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a successful handoff clears the throttle streak",
              row["throttle_count"] == 0, str(dict(row)))
    finally:
        orcamod.clear_context, orcamod.alive, orcamod.idle_now, orcamod.live_terminals = real
        orcamod.available = lambda: False
        con.close()


def test_the_screen_names_what_is_in_the_way(home: Path) -> None:
    """A dialog is quoted from the screen - title and options - never matched
    against a list of the dialogs somebody has seen."""
    restore_world()
    real = orcamod._screen
    orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
    try:
        orcamod._screen = lambda h: _screen_of(EXIT_DIALOG)
        b = orcamod.screen_blocker("term_x")
        check("screen: the exit confirmation is a dialog", b and b.kind == orcamod.MODAL, str(b))
        check("screen: it is named by its own title",
              b and b.detail.startswith('"Background work is running"'), str(b))
        check("screen: with the options that answer it",
              b and "1. Exit and stop tasks / 2. Move to background and exit / 3. Stay" in b.detail
              and "❯" not in b.detail, str(b))

        orcamod._screen = lambda h: _screen_of(["a", "b", "c", "d", "Enter to confirm · Esc to cancel"])
        check("screen: a layout with no rule is quoted, last three lines above the footer",
              orcamod.screen_blocker("term_x").detail == "b / c / d")

        # A transcript that quotes the footer, well above the bottom rows, is not
        # a dialog - and with a real, empty composer under it, nothing is in the way.
        quoted = ["the footer says Enter to confirm and Esc to cancel"] + ["x"] * 10 + COMPOSER_EMPTY
        orcamod._screen = lambda h: _screen_of(quoted)
        check("screen: the footer words in the transcript are not a dialog",
              orcamod.screen_blocker("term_x") is None)

        # `draft` is never consulted, whether it is stale (the deadlock this
        # guard used to exist for) or genuinely current - #1157: the screen
        # cannot tell the two apart, so neither blocks anything.
        orcamod._screen = lambda h: _screen_of(COMPOSER_EMPTY, draft=PASTED_DRAFT)
        check("screen: a draft the screen does not show is never consulted",
              orcamod.screen_blocker("term_x") is None)
        orcamod._screen = lambda h: _screen_of(COMPOSER_EMPTY, draft="half-typed reply")
        check("screen: a recognized composer box is never blocking, whatever draft says",
              orcamod.screen_blocker("term_x") is None)
        orcamod._screen = lambda h: _screen_of(COMPOSER_EMPTY, draft="")
        check("screen: an empty composer is an empty input", orcamod.screen_blocker("term_x") is None)
        orcamod._screen = lambda h: _screen_of(["prompt"])
        check("screen: a screen with no composer box at all fails closed, not blank forever",
              orcamod.screen_blocker("term_x").kind == orcamod.UNREADABLE)

        # A layout with one row under the box, not two, is still found - the
        # triple is searched for, not assumed to sit a fixed distance from
        # the end.
        orcamod._screen = lambda h: _screen_of(ONE_HINT_ROW_COMPOSER)
        check("screen: a composer with a different number of rows under it "
              "is still read, not UNREADABLE",
              orcamod.screen_blocker("term_x") is None)
        # The background picker has a composer's shape and typed text goes to
        # a new session, so it is a block of its own - never a clean send.
        orcamod._screen = lambda h: _screen_of(BACKGROUND_PICKER)
        b = orcamod.screen_blocker("term_x")
        check("screen: the background picker is not a clean composer",
              b is not None and b.kind == orcamod.BACKGROUNDED, str(b))
        check("screen: it is quoted from its own banner and sessions",
              b and b.detail.startswith("Your conversation moved to the background")
              and "Completed" in b.detail and "ocp inbox management" in b.detail
              and "❯" not in b.detail and "─" not in b.detail, str(b))
        check("screen: and says why in a sentence",
              b and blockedmod.reason(b.kind, b.detail).startswith("its conversation moved to the background"))
        check("screen: modal_open refuses it", REAL_ORCA["modal_open"]("term_x"))
        hostile_picker = ["\033]0;PWNED\x07\033[31mYour conversation moved to the background"] \
            + BACKGROUND_PICKER[1:]
        orcamod._screen = lambda h: _screen_of(hostile_picker)
        b = orcamod.screen_blocker("term_x")
        check("screen: the picker's own text is sanitised like a dialog's",
              b and "\033" not in b.detail and "PWNED" not in b.detail and "Your conversation" in b.detail,
              str(b))
        quoted = ["the hint says enter to open"] + ["x"] * 10 + COMPOSER_EMPTY
        orcamod._screen = lambda h: _screen_of(quoted)
        check("screen: the hint quoted in the transcript, above the bottom rows, is not the picker",
              orcamod.screen_blocker("term_x") is None)
        # #1252: on every one of 8 live agents, the old fixed window reached
        # 1-2 rows of the agent's own transcript - and "enter to open" is
        # ordinary English an agent reporting on this very feature writes
        # (auditor's own #1229 review contains it). This is that shape: the
        # phrase sits one row above a real, ordinary composer.
        fleet_false_positive = [FLEET_HINT_QUOTE] + COMPOSER_EMPTY
        orcamod._screen = lambda h: _screen_of(fleet_false_positive)
        check("screen: a transcript row quoting the hint, one row above a "
              "real composer, is not the picker - it is idle, not blocked",
              orcamod.screen_blocker("term_x") is None)
        # A real picker still has to be found when it is a different shape
        # than the fixture above - fewer session rows, harbor's one-hint-row
        # composer layout - never by shrinking the window (7f01f52 exists to
        # fail closed on a picker nobody has measured yet, not to fail open
        # by guessing a number that happens to fit one fixture).
        orcamod._screen = lambda h: _screen_of(BACKGROUND_PICKER_SHORT)
        b = orcamod.screen_blocker("term_x")
        check("screen: a shorter, differently-shaped picker is still found",
              b is not None and b.kind == orcamod.BACKGROUNDED, str(b))
        check("screen: quoted from its own banner and session, not the composer",
              b and b.detail.startswith("Your conversation moved to the background")
              and "ocp inbox management" in b.detail
              and "❯" not in b.detail and "─" not in b.detail, str(b))
        # auditor's #1285 B1: the composer's own hint says the picker's words
        # but the banner that would confirm it is not there - a picker this
        # code does not recognise, never nothing-in-the-way. A column-
        # alignment guess about the session row (dropped for this reason)
        # would have read this as a clean composer and typed into it.
        orcamod._screen = lambda h: _screen_of(PICKER_HINT_WITH_NO_BANNER)
        b = orcamod.screen_blocker("term_x")
        check("screen: an unconfirmed picker-shaped hint fails closed, never a clean send",
              b is not None and b.kind == orcamod.UNREADABLE)
        # #1285 S2: the banner alone, with no "enter to open" below the box,
        # is not the picker either - the position guard is load-bearing on
        # its own, not merely redundant with the banner.
        orcamod._screen = lambda h: _screen_of(BANNER_ABOVE_ORDINARY_COMPOSER)
        check("screen: the banner above an ordinary composer's own hint is not the picker",
              orcamod.screen_blocker("term_x") is None)
        orcamod._screen = lambda h: _screen_of(BANNER_AND_HINT_QUOTED_ABOVE_BOX)
        check("screen: the picker's words quoted above the composer box are not the picker",
              orcamod.screen_blocker("term_x") is None)
        # purser's #1294, measured: the common form of the picker.
        orcamod._screen = lambda h: _screen_of(PICKER_CURRENT_SESSION_SELECTED)
        b = orcamod.screen_blocker("term_x")
        check("screen: the picker with the current session selected is BACKGROUNDED",
              b is not None and b.kind == orcamod.BACKGROUNDED, str(b))
        orcamod._screen = lambda h: _screen_of(PICKER_HINT_ONLY_ENTER_TO_RETURN)
        b = orcamod.screen_blocker("term_x")
        check("screen: the picker's hint with no banner fails closed, not a clean composer",
              b is not None and b.kind == orcamod.UNREADABLE, str(b))
        # auditor's #1352 should-fix: "space to reply" alone proved the whole
        # change (every other picker fixture carries it too), so nothing
        # pinned "enter to open" - the marker #1252 and this branch were
        # built on. Cutting _BACKGROUNDED_HINTS in half left the suite green.
        orcamod._screen = lambda h: _screen_of(PICKER_HINT_ONLY_ENTER_TO_OPEN)
        b = orcamod.screen_blocker("term_x")
        check("screen: 'enter to open' alone, with no 'space to reply', is still BACKGROUNDED",
              b is not None and b.kind == orcamod.BACKGROUNDED, str(b))
        # An empty detail must not render a dangling colon - what the founder
        # saw on the #1252 false positive, where tail[:hint] was empty.
        check("screen: an empty BACKGROUNDED detail has no dangling colon",
              orcamod.Blocker(orcamod.BACKGROUNDED, "").reason()
              == "its conversation moved to the background")
        check("screen: an empty MODAL detail has no dangling colon",
              orcamod.Blocker(orcamod.MODAL, "").reason() == "a dialog is open on its screen")
        orcamod._screen = lambda h: _screen_of(EXIT_DIALOG, source="stream")
        check("screen: a stream is not a screen - fail closed",
              orcamod.screen_blocker("term_x").kind == orcamod.UNREADABLE)
        orcamod._screen = lambda h: None
        check("screen: nothing read is fail closed too",
              orcamod.screen_blocker("term_x").kind == orcamod.UNREADABLE)

        # An agent paints its own screen: a dialog's title and options are
        # exactly the hostile-text case auditor measured against describe_modal
        # (blocked-delivery #981/#987's should-fix) - checked here too, since
        # this module builds the same text from the same screen.
        hostile = EXIT_DIALOG[:3] + ["   \033]0;PWNED\x07\033[31mTitle\033[0m", "   ❯ 1. yes",
                                     "     2. Stay", EXIT_DIALOG[-1]]
        orcamod._screen = lambda h: _screen_of(hostile)
        b = orcamod.screen_blocker("term_x")
        check("screen: an OSC/CSI sequence in the dialog's own text does not reach the quote",
              b and "\033" not in b.detail and "PWNED" not in b.detail and "Title" in b.detail,
              str(b))
        footer = len(hostile) - 1
        d = orcamod.parse_dialog(hostile, footer)
        check("screen: nor the parsed title used to decide what is safe to press",
              d and "\033" not in d.title and "PWNED" not in d.title, str(d))
    finally:
        orcamod._screen = real


def test_a_dialog_in_the_way_of_work_is_said_once_and_clears_itself(home: Path) -> None:
    """The property: work ocp cannot give, because of what is on the agent's
    screen, reaches the founder - on the list at T1, as one request at T2, once,
    across ticks and a restart - and both go the tick it can be given."""
    con = _blocked_world(home)
    lines: list[str] = []
    orcamod.live_terminals = lambda: {"term_x"}
    seen = {"screen": orcamod.Blocker(orcamod.MODAL, orcamod.describe_modal(EXIT_DIALOG, len(EXIT_DIALOG) - 1))}
    reads = []
    orcamod.screen_blocker = lambda h: (reads.append(h), seen["screen"])[1]
    # In the past by more than the longest wait, so the synthetic times below
    # are all no later than the clock the list itself reads.
    t0 = int(time.time()) - 2 * blockedmod.ESCALATE_AFTER_S - 3000
    try:
        blockedmod.sweep(con, lines.append, t0)
        rec = db.block_get(con, "backend")
        check("blocked: work waiting and a dialog open is recorded", rec and rec["kind"] == "modal",
              str(dict(rec) if rec else None))
        check("blocked: it names the work", rec and "request #1" in rec["work"], str(rec and rec["work"]))
        check("blocked: the first sighting says so in the log, once", len(lines) == 1, str(lines))
        check("blocked: not on the founder's list yet", not blockedmod.founder_rows(con, t0))

        blockedmod.sweep(con, lines.append, t0 + 5)
        check("blocked: a check inside the recheck window does not read the screen again",
              len(reads) == 1, str(reads))
        blockedmod.sweep(con, lines.append, t0 + blockedmod.SHOW_AFTER_S - 1)
        check("blocked: nor one second short of T1",
              not blockedmod.founder_rows(con, t0 + blockedmod.SHOW_AFTER_S - 1))

        at = t0 + blockedmod.SHOW_AFTER_S
        rows = [w for w in blockedmod.founder_rows(con, at)]
        check("blocked: on the list at T1, red, with the dialog's title",
              len(rows) == 1 and rows[0]["red"] and "Background work is running" in rows[0]["what"]
              and rows[0]["who"] == "backend" and rows[0]["since"] == t0, str(rows))
        check("blocked: ps and the bridge read that list",
              any(w["kind"] == "blocked" and w["who"] == "backend" for w in actions.founder_waits(con)),
              str(actions.founder_waits(con)))
        check("blocked: no request before T2", not _escalations(con), str(_escalations(con)))

        at = t0 + blockedmod.ESCALATE_AFTER_S - 1
        blockedmod.sweep(con, lines.append, at)
        check("blocked: nor one second short of T2", not _escalations(con))
        blockedmod.sweep(con, lines.append, t0 + blockedmod.ESCALATE_AFTER_S)
        esc = _escalations(con)
        check("blocked: one request at T2", len(esc) == 1, str(esc))
        body = db.get_request(con, esc[0]["id"])["body"]
        check("blocked: it says what is in the way, what waits, and what to do",
              "Background work is running" in body and "request #1" in body
              and "t on the agents panel" in body, body)
        check("blocked: the record holds its number", db.block_get(con, "backend")["request_id"] == esc[0]["id"])

        for extra in (30, 200, 900):
            blockedmod.sweep(con, lines.append, t0 + blockedmod.ESCALATE_AFTER_S + extra)
        blockedmod._checked.clear()      # a restart forgets when it last looked
        blockedmod.sweep(con, lines.append, t0 + blockedmod.ESCALATE_AFTER_S + 1000)
        check("blocked: however long it lasts, across ticks and a restart, still one",
              len(_escalations(con)) == 1, str(_escalations(con)))
        check("blocked: the request is not listed a second time beside its own row",
              sum(1 for w in actions.founder_waits(con) if w["kind"] in ("blocked", "escalation")) == 1,
              str([w["kind"] for w in actions.founder_waits(con)]))
        check("blocked: STUCK gives the dialog, not the state's guess",
              any("cannot be given its work" in r["what"] and "Background work is running" in r["why"]
                  for r in actions.stuck(con))
              and not any("mid-turn" in r["why"] for r in actions.stuck(con)),
              str(actions.stuck(con)))
        check("blocked: and one row for the agent, not one per request",
              sum(1 for r in actions.stuck(con) if r["agent"] == "backend") == 1,
              str(actions.stuck(con)))

        seen["screen"] = None                 # the founder answered it
        blockedmod._checked.clear()
        blockedmod.sweep(con, lines.append, t0 + blockedmod.ESCALATE_AFTER_S + 1100)
        check("blocked: the record goes the tick it can be given its work", db.block_get(con, "backend") is None)
        check("blocked: the request closes itself",
              _escalations(con)[0]["state"] == "done" and "cleared" in _escalations(con)[0]["verdict"],
              str([dict(r) for r in _escalations(con)]))
        check("blocked: nothing is left on the list",
              not any(w["kind"] in ("blocked", "escalation") for w in actions.founder_waits(con)))

        seen["screen"] = orcamod.Blocker(orcamod.MODAL, '"Another"')
        blockedmod._checked.clear()
        blockedmod.sweep(con, lines.append, t0 + 2000)
        blockedmod.sweep(con, lines.append, t0 + 2000 + blockedmod.ESCALATE_AFTER_S)
        check("blocked: a second episode is a second request",
              len(_escalations(con)) == 2, str(_escalations(con)))
    finally:
        con.close()


def test_what_is_not_a_block(home: Path) -> None:
    """Both directions: nothing is said for what is normal, and an agent that
    really cannot be given its work is said for however the row labels it."""
    con = _blocked_world(home)
    modal = orcamod.Blocker(orcamod.MODAL, '"Dialog"')
    screen = {"b": modal}
    orcamod.screen_blocker = lambda h: screen["b"]
    now = int(time.time())

    def verdict(state=dmn_states.IDLE, b=modal, closed=False):
        db.block_clear(con, "backend")
        blockedmod._checked.clear()
        db.set_agent_state(con, "backend", state)
        con.execute("UPDATE agent_state SET closed_at = ? WHERE agent = 'backend'",
                    (now if closed else None,))
        screen["b"] = b
        blockedmod.sweep(con, lambda _m: None, now)
        rec = db.block_get(con, "backend")
        return rec["kind"] if rec else None
    try:
        check("not blocked: an idle agent with nothing on its screen", verdict(b=None) is None)
        check("blocked: an idle agent behind a dialog", verdict() == "modal")
        check("blocked: a RUNNING row behind a dialog - the row is not evidence", verdict(dmn_states.RUNNING) == "modal")
        unreadable = orcamod.Blocker(orcamod.UNREADABLE)
        check("blocked: a screen that cannot be read - the callers that fail closed on it are not alone",
              verdict(b=unreadable) == "unreadable")
        check("blocked: unreadable blocks a RUNNING agent too",
              verdict(dmn_states.RUNNING, unreadable) == "unreadable")
        check("not blocked: a closed agent is the wake path's", verdict(closed=True) is None)
        con.execute("UPDATE agent_state SET closed_at = NULL WHERE agent = 'backend'")

        check("blocked: STALLED, which the daemon will not touch, with work waiting",
              verdict(dmn_states.STALLED, None) == "stalled")
        db.set_agent_state(con, "backend", dmn_states.IDLE)

        db.set_terminal(con, "backend", None, "wt")
        con.execute("UPDATE agent_state SET terminal = NULL WHERE agent = 'backend'")
        db.block_clear(con, "backend")
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, now)
        check("blocked: no terminal at all", (db.block_get(con, "backend") or {"kind": None})["kind"] == "no terminal")
        db.set_terminal(con, "backend", "term_x", "wt")

        orcamod.alive = lambda h: False
        check("blocked: a terminal Orca no longer has", verdict() == "no terminal")
        orcamod.alive = lambda h: True

        orcamod.available = lambda: False
        check("blocked: orca itself unreachable - the sibling of unreadable, not left silent either",
              verdict() == "orca down")
        orcamod.available = lambda: True

        obsmod.live_sessions = lambda: {}
        check("blocked: no session in a terminal the row says is open", verdict() == "no session")
        sessions_are("backend")

        # No work, no block: a dialog on an agent nobody wants anything from is its own business.
        con.execute("UPDATE requests SET state = 'done', announced_at = 1 WHERE target = 'backend'")
        check("not blocked: a dialog with nothing waiting", verdict() is None)

        # An answer it asked for is work, the same as a request.
        rid = db.add_request(con, "backend", "marina", "how", None, "peer", "async", None, 0, None)
        db.close_if_queued(con, rid, "done", "like so", announced=False)
        check("blocked: an unannounced answer is work waiting", verdict() == "modal"
              and "answer #%d" % rid in db.block_get(con, "backend")["work"])

        # A permission prompt is already the hooks' row; it is not said twice.
        waitmod = blockedmod.waiting
        real = waitmod.of
        waitmod.of = lambda row: {"tool": "Bash", "command": "ls", "since": now}
        try:
            check("not blocked: an agent at a permission prompt is already on the list",
                  verdict(dmn_states.RUNNING) is None)
        finally:
            waitmod.of = real
    finally:
        con.close()


def test_a_reminder_a_dialog_blocks_is_said(home: Path) -> None:
    """Harbor in the wild: IDLE with its head told and open for hours, and the
    reminder to resolve it never typed because a dialog sat on its screen.
    Nothing was undelivered - the reminder is the work - and it is the same
    silence."""
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    sessions_are("backend")
    con = db.connect()
    now = int(time.time())
    try:
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        head = db.add_request(con, "marina", "backend", "the first thing", None,
                              "peer", "async", None, 0, None)
        db.mark_delivered(con, head)
        db.set_cursor(con, "notify:backend", head)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 3600,))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (now - 3600, head))
        orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, '"Continue?"')
        blockedmod.sweep(con, lambda _m: None, now)
        rec = db.block_get(con, "backend")
        check("reminder: a dialog on its screen blocks the reminder, and it is recorded",
              rec and rec["kind"] == "modal" and rec["detail"] == '"Continue?"'
              and f"reminder to resolve #{head}" in rec["work"], str(rec and dict(rec)))
        check("reminder: the dialog is quoted so the founder knows what is on screen",
              "Continue?" in blockedmod.reason(rec["kind"], rec["detail"]))

        # Not yet due: nothing to type, so nothing is in the way.
        db.block_clear(con, "backend")
        con.execute("UPDATE events SET ts = ?", (now - 60,))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (now - 60, head))
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, now)
        check("reminder: one that is not due yet is not work", db.block_get(con, "backend") is None)

        # Already sent: nothing left to type.
        con.execute("UPDATE events SET ts = ?", (now - 3600,))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (now - 3600, head))
        db.set_cursor(con, "nudge:backend", head)
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, now)
        check("reminder: one already sent is not work", db.block_get(con, "backend") is None)
    finally:
        con.close()


def test_a_stale_draft_never_blocks_delivery_again(home: Path) -> None:
    """#1106: keeper, purser and chat sat IDLE for 8 hours, each reported as
    'text is left unsent in its input' while every real input box was empty -
    Orca's own remembered `draft` outliving a send that did not complete, with
    nothing to clear it but the exact send it was refusing to make. #1130:
    the remedy did not hold - purser stalled again an hour later with the
    draft set to 'ocp inbox', the exact text that had just unblocked it. A
    draft equal to the last thing ocp itself sent is the ordinary steady
    state of every agent given work, not a one-off failure, so this is not
    an edge case: it is what happens to every agent, every time, on the path
    that matters most. Driven through the real reader against a screen that
    never changes and a `draft` that never clears: delivery must go through
    anyway, on the first tick, because the deadlock is closed at the source
    - draft is not read at all any more - not worked around."""
    con = _blocked_world(home)
    orcamod.live_terminals = lambda: {"term_x"}
    real = (orcamod._screen, orcamod.screen_blocker, orcamod.modal_open)
    try:
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
        orcamod.modal_open = REAL_ORCA["modal_open"]
        # draft = the literal text ocp last sent this agent ('ocp inbox',
        # #1130's own case) - not stale garbage, the ordinary steady state.
        orcamod._screen = lambda h: {"handle": h, "status": "running", "source": "screen",
                                     "tail": list(COMPOSER_EMPTY), "draft": "ocp inbox"}
        now = int(time.time())
        for step in (0, blockedmod.RECHECK_S + 1, blockedmod.SHOW_AFTER_S + 1):
            blockedmod._checked.clear()
            blockedmod.sweep(con, lambda _m: None, now + step)
        check("stale draft: never opens a block at all - the render says empty, every time",
              db.block_get(con, "backend") is None)

        sent: list[str] = []
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        check("stale draft: and delivery actually goes through, draft or no draft",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and sent, str(sent))
    finally:
        (orcamod._screen, orcamod.screen_blocker, orcamod.modal_open) = real
        con.close()


def test_the_human_and_strangers_are_not_blocked_agents(home: Path) -> None:
    """The human is never typed at, and a state row left by an agent that is no
    longer on the team has nobody to be given anything."""
    con = _blocked_world(home)
    orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, '"A"')
    try:
        for name in ("human", "gone"):
            db.set_agent_state(con, name, dmn_states.QUEUED)
            db.add_request(con, "marina", name, "hello", None, "peer", "async", None, 0, None)
        blockedmod.sweep(con, lambda _m: None, int(time.time()))
        check("blocked: only a registered agent has a block",
              [b["agent"] for b in db.blocks(con)] == ["backend"], str([b["agent"] for b in db.blocks(con)]))
    finally:
        con.close()


def test_the_tick_looks_for_blocks(home: Path) -> None:
    """Nothing here works unless the loop the daemon actually runs calls it."""
    con = _blocked_world(home)
    orcamod.live_terminals = lambda: {"term_x"}
    orcamod.idle_now = lambda h: True
    orcamod.send = lambda h, text, enter=True: (False, "refused")
    orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, '"A dialog"')
    try:
        dmn_evaluate.tick(con, lambda _m: None)
        rec = db.block_get(con, "backend")
        check("tick: a tick opens the block for an agent behind a dialog",
              rec is not None and rec["kind"] == "modal", str(rec and dict(rec)))
    finally:
        con.close()


def test_a_dialog_ocps_own_exit_raised_is_said_at_once(home: Path) -> None:
    """`/exit` met background shells and the dialog is ocp's doing: it does not
    wait for T1, it stands with nothing waiting, and ocp does not answer it."""
    con = _blocked_world(home)
    con.execute("UPDATE requests SET state = 'done', announced_at = 1")
    typed: list = []
    orcamod.send = lambda h, text, enter=True: (typed.append(text), (True, ""))[1]
    screen = {"b": orcamod.Blocker(orcamod.MODAL, '"Background work is running"')}
    orcamod.screen_blocker = lambda h: screen["b"]
    lines: list[str] = []
    now = int(time.time())
    try:
        blockedmod.note_close_failure(con, "backend", lines.append)
        rec = db.block_get(con, "backend")
        check("close: the dialog it raised is recorded", rec and rec["origin"] == "close", str(rec and dict(rec)))
        check("close: on the list at once, not at T1", rec and rec["show_at"] <= int(time.time()))
        check("close: and said in the log", any("ocp's own /exit" in m for m in lines), str(lines))
        blockedmod.sweep(con, lambda _m: None, now)
        check("close: it stands with nothing waiting, while the dialog does",
              db.block_get(con, "backend") is not None)
        check("close: ocp typed nothing at it", typed == [], str(typed))
        screen["b"] = None
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, now + 60)
        check("close: it goes when the dialog does", db.block_get(con, "backend") is None)

        screen["b"] = orcamod.Blocker(orcamod.UNREADABLE)
        blockedmod.note_close_failure(con, "backend", lines.append)
        check("close: an unreadable screen is not the dialog's doing", db.block_get(con, "backend") is None)
    finally:
        con.close()


def test_a_failed_close_says_why_and_records_the_dialog(home: Path) -> None:
    """The reason a close did not happen used to be dropped where it was made."""
    con = _blocked_world(home)
    con.execute("UPDATE requests SET state = 'done', announced_at = 1")
    now = int(time.time())
    orcamod.wait_idle = lambda h, timeout_ms=0: True
    orcamod.send = lambda h, text, enter=True: (True, "")
    orcamod.close_session = lambda h, *a, **k: (True, "/exit typed")
    orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, '"Background work is running"')
    real = dmn_delivery._wait_session_gone
    dmn_delivery._wait_session_gone = lambda agent: False
    lines: list[str] = []
    try:
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=8, reset_5h=now + 1800)
        agent_dir = home / "agents" / "backend"
        write_status_json(agent_dir, {
            "account": "alpha", "collected_at": now, "session_id": "sess-1", "pct_5h": 20, "ctx_pct": 8})
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - dmn_states.IDLE_CLOSE_S - 60,))
        for _ in range(3):
            dmn_evaluate.supervise(con, "backend", lines.append, int(time.time()), {}, [])
        check("close failed: the daemon says why, once per attempt",
              sum("idle but not closed" in m for m in lines) == 1, str(lines))
        rec = db.block_get(con, "backend")
        check("close failed: and the dialog it raised is on the record",
              rec and rec["origin"] == "close" and rec["kind"] == "modal", str(rec and dict(rec)))
    finally:
        dmn_delivery._wait_session_gone = real
        con.close()


def test_a_close_refused_by_a_dialog_is_said_once_not_every_tick(home: Path) -> None:
    """A dialog left open refuses the next close on every tick, and leaves no
    close_failed event to stop them: the reason is said when it starts."""
    con = _blocked_world(home)
    con.execute("UPDATE requests SET state = 'done', announced_at = 1")
    now = int(time.time())
    orcamod.close_session = lambda h, *a, **k: (False, "a modal is open on its screen; not typing /exit at it")
    lines: list[str] = []
    try:
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=8, reset_5h=now + 1800)
        agent_dir = home / "agents" / "backend"
        write_status_json(agent_dir, {
            "account": "alpha", "collected_at": now, "session_id": "sess-1", "pct_5h": 20, "ctx_pct": 8})
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - dmn_states.IDLE_CLOSE_S - 60,))
        for _ in range(4):
            dmn_evaluate.supervise(con, "backend", lines.append, int(time.time()), {}, [])
        said = [m for m in lines if "idle but not closed" in m]
        check("close refused: said once over four ticks, with the reason",
              len(said) == 1 and "a modal is open" in said[0], str(lines))
    finally:
        con.close()


def test_a_close_never_stops_background_work(home: Path) -> None:
    """Task #31: a session whose own footer counts work still running behind its
    prompt is not idle. No close types `/exit` at it, and ocp never chooses
    'Exit and stop tasks' - the dialog that offers it is declined."""
    bg_hint = "  ⏵⏵ auto mode on · 2 monitors · ← for agents · ↓ to manage"
    composer_bg = COMPOSER_EMPTY[:-1] + [bg_hint]
    check("background_work: reads the session's own count from the hint row",
          _bg_of(composer_bg) == "2 monitors", _bg_of(composer_bg))
    check("background_work: a shell count is read the same way",
          _bg_of(COMPOSER_EMPTY[:-1] + ["  ⏵⏵ auto mode on · 1 shell · ← for agents · ↓ to manage"]) == "1 shell")
    check("background_work: several kinds are all named",
          _bg_of(COMPOSER_EMPTY[:-1] + ["  ⏵⏵ auto mode on · 1 shell · 2 monitors · ↓ to manage"]) == "1 shell, 2 monitors")
    check("background_work: 'to manage' with no count still says there is work",
          _bg_of(COMPOSER_EMPTY[:-1] + ["  ⏵⏵ auto mode on · ← for agents · ↓ to manage"]) == "background tasks")
    check("background_work: an ordinary idle composer says nothing",
          _bg_of(COMPOSER_EMPTY) == "", _bg_of(COMPOSER_EMPTY))
    check("background_work: a screen that cannot be read says None, not that nothing runs",
          _bg_of(None) is None and _bg_of(["no composer here"]) is None)

    con, term, ok, why = _close_at(home, EXIT_DIALOG, screen=composer_bg)
    check("close: a session with background work is refused before anything is typed",
          not ok and term.sent == [] and "background work is running (2 monitors)" in why, f"{ok} {why} {term.sent}")
    check("close: a refusal for background work is not a failure to close",
          con.execute("SELECT 1 FROM events WHERE agent = 'backend' AND kind = 'close_failed'").fetchone() is None
          and blockedmod.dialog_note(con, "backend") == "")
    con.close()

    con, term, ok, why = _close_at(home / "b", EXIT_DIALOG)
    keys = [t for t, _ in term.sent if t != "/exit"]
    check("close: a dialog /exit raises anyway is declined with Esc, never a digit",
          not ok and keys == ["\x1b"], f"{ok} {why} {term.sent}")
    check("close: and says the session was kept",
          "session kept" in why and dmn_delivery.session_present("backend"), why)
    check("close: and is recorded, so the next tick does not type /exit again",
          con.execute("SELECT 1 FROM events WHERE agent = 'backend' AND kind = 'close_failed'").fetchone() is not None)
    check("close: the choice to stop tasks is never on the record",
          con.execute("SELECT 1 FROM events WHERE agent = 'backend' AND kind = 'dialog_exit_chosen'").fetchone() is None)
    con.close()

    # Where ocp is not ending a session the dialog is still only declined.
    restore_world()
    con = _blocked_world(home / "f")
    db.set_terminal(con, "backend", "term_x", "wt")
    term = _ExitTerm(EXIT_DIALOG, "1", sessions_are("backend"))
    term.screen = list(EXIT_DIALOG)
    term.install()
    blockedmod.past_dialog(con, "backend", lambda m: None)
    check("exit: past_dialog (the clear path) presses Esc, never a digit",
          term.sent == [("\x1b", False)], str(term.sent))
    con.close()


def test_a_dialog_ocp_can_decline_is_cleared_and_reported(home: Path) -> None:
    con, term = _decline_world(home)
    lines: list[str] = []
    t0 = int(time.time()) - 5000
    try:
        blockedmod.sweep(con, lines.append, t0)
        check("clear: the first sighting presses nothing - a person may be answering it",
              term.sent == [] and db.block_get(con, "backend") is not None, str(term.sent))
        blockedmod._checked.clear()
        blockedmod.sweep(con, lines.append, t0 + blockedmod.PRESS_AFTER_S - 1)
        check("clear: nor a second short of the wait", term.sent == [], str(term.sent))
        blockedmod._checked.clear()
        blockedmod.sweep(con, lines.append, t0 + blockedmod.PRESS_AFTER_S)
        check("clear: after it, exactly one key, Esc", term.sent == [("\x1b", False)], str(term.sent))
        check("clear: the block is gone and never reached the founder's list",
              db.block_get(con, "backend") is None
              and not any(w["kind"] == "blocked" for w in actions.founder_waits(con)))
        check("clear: the log says how", any('cleared by ocp: pressed Esc on "Background work is running"' in m
                                              for m in lines), str(lines))
        rep = _reports(con)
        check("clear: the founder is told, as a report that asks nothing",
              len(rep) == 1 and rep[0]["fyi"] == 1 and rep[0]["state"] == "queued"
              and 'cleared by ocp: pressed Esc on "Background work is running"' in rep[0]["body"]
              and "nothing was chosen" in rep[0]["body"] and "request #1" in rep[0]["body"],
              str([dict(r) for r in rep]))
        ev = con.execute("SELECT payload FROM events WHERE kind = 'dialog_declined'").fetchall()
        check("clear: and it is an event on the record", len(ev) == 1, str(ev))
        check("clear: the work is deliverable now - nothing else typed at the dialog",
              term.sent == [("\x1b", False)])
        blockedmod._checked.clear()
        blockedmod.sweep(con, lines.append, t0 + 200)
        check("clear: an agent whose screen is clear is left alone", term.sent == [("\x1b", False)])
    finally:
        con.close()


def test_ocps_own_exit_dialog_is_pressed_at_once_and_a_working_agent_never(home: Path) -> None:
    con, term = _decline_world(home)
    con.execute("UPDATE requests SET state = 'done', announced_at = 1")
    now = int(time.time())
    try:
        blockedmod.note_close_failure(con, "backend", lambda _m: None)
        blockedmod.sweep(con, lambda _m: None, now)
        check("own exit: its own dialog is pressed on the first sweep, no person being in it",
              term.sent == [("\x1b", False)] and db.block_get(con, "backend") is None, str(term.sent))

        # An agent that is working may be waiting on a person for that dialog.
        for state in (dmn_states.RUNNING, dmn_states.HANDOFF):
            db.block_clear(con, "backend")
            blockedmod._checked.clear()
            term.sent.clear()
            term.bring_back(EXIT_DIALOG)
            db.set_agent_state(con, "backend", state)
            db.add_request(con, "marina", "backend", "more", None, "peer", "async", None, 0, None)
            blockedmod.sweep(con, lambda _m: None, now + 5000)
            blockedmod.sweep(con, lambda _m: None, now + 6000)
            check(f"working: nothing is pressed at a {state} agent's dialog", term.sent == [], str(term.sent))
    finally:
        con.close()


def test_a_dialog_ocp_cannot_decline_is_reported_not_pressed(home: Path) -> None:
    """The trust dialog's Esc would leave the session ('No, exit'), the relaunch
    would meet it again, and ocp would be pressing a loop of its own making: so
    it presses nothing and the founder is told which choice they have."""
    con, term = _decline_world(home, TRUST_DIALOG)
    lines: list[str] = []
    t0 = int(time.time()) - 5000
    try:
        for step in (0, 60, 120, 400, 700):
            blockedmod._checked.clear()
            blockedmod.sweep(con, lines.append, t0 + step)
        check("cannot: nothing is pressed, however long it stands", term.sent == [], str(term.sent))
        check("cannot: the block stands", db.block_get(con, "backend") is not None)
        check("cannot: the reason is logged once, not every sweep",
              sum("did not press" in m for m in lines) == 1, str(lines))
        esc = _escalations(con)
        check("cannot: it reaches the founder at T2 saying why ocp did not press",
              len(esc) == 1 and "did not press it: none of its options only declines"
              in db.get_request(con, esc[0]["id"])["body"], str([dict(r) for r in esc]))
        row = [w for w in blockedmod.founder_rows(con, t0 + 700) if w["who"] == "backend"]
        check("cannot: and on the list, with the same reason",
              row and "ocp did not press it" in row[0]["what"], str(row))
    finally:
        con.close()


def test_a_dialog_that_keeps_coming_back_stops_being_pressed_and_is_escalated(home: Path) -> None:
    """A dialog that returns after ocp cleared it is a loop: pressed
    MAX_PRESSES times, then the founder is told at once, not at T2."""
    con, term = _decline_world(home)
    t0 = int(time.time()) - 20000
    try:
        for cycle in range(blockedmod.MAX_PRESSES):
            term.bring_back(EXIT_DIALOG)
            blockedmod._checked.clear()
            at = t0 + cycle * 1800
            blockedmod.sweep(con, lambda _m: None, at)
            blockedmod._checked.clear()
            blockedmod.sweep(con, lambda _m: None, at + blockedmod.PRESS_AFTER_S)
            check(f"loop: press {cycle + 1} clears it", db.block_get(con, "backend") is None)
        check("loop: pressed exactly MAX_PRESSES times", len(term.sent) == blockedmod.MAX_PRESSES, str(term.sent))
        check("loop: each was reported as done", len(_reports(con)) == blockedmod.MAX_PRESSES)

        term.bring_back(EXIT_DIALOG)
        at = t0 + blockedmod.MAX_PRESSES * 1800
        for step in (0, 60, 130):
            blockedmod._checked.clear()
            blockedmod.sweep(con, lambda _m: None, at + step)
        check("loop: the dialog that comes back again is NOT pressed", len(term.sent) == blockedmod.MAX_PRESSES,
              str(term.sent))
        esc = _escalations(con)
        body = db.get_request(con, esc[0]["id"])["body"] if esc else ""
        check("loop: the founder is told at once, well before T2, and why",
              len(esc) == 1 and "has stopped pressing" in body and "came back each time" in body,
              body or str([dict(r) for r in esc]))
        check("loop: and it is on the list saying so",
              any("stopped pressing" in w["what"]
                  for w in blockedmod.founder_rows(con, at + blockedmod.SHOW_AFTER_S)))

        # The window is finite: a dialog after it is a new event.
        term.sent.clear()
        blockedmod._checked.clear()
        later = at + blockedmod.PRESS_WINDOW_S + 100
        db.block_clear(con, "backend")
        term.bring_back(EXIT_DIALOG)
        blockedmod.sweep(con, lambda _m: None, later)
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, later + blockedmod.PRESS_AFTER_S)
        check("loop: outside the window it is pressed again", term.sent == [("\x1b", False)], str(term.sent))
    finally:
        con.close()


def test_esc_that_does_not_close_a_dialog_is_not_pressed_again_and_again(home: Path) -> None:
    con, term = _decline_world(home, ignores_esc=True)
    t0 = int(time.time()) - 5000
    try:
        for step in range(0, 400, 30):
            blockedmod._checked.clear()
            blockedmod.sweep(con, lambda _m: None, t0 + step)
        check("ignored: pressed MAX_IGNORED times and no more", len(term.sent) == blockedmod.MAX_IGNORED, str(term.sent))
        check("ignored: the founder is told at once", len(_escalations(con)) == 1
              and "Esc did not close it" in db.get_request(con, _escalations(con)[0]["id"])["body"])
    finally:
        con.close()


def test_nothing_is_pressed_for_what_is_not_a_dialog(home: Path) -> None:
    """A screen ocp cannot read is a block, and is never a key."""
    for name, term in (("unreadable screen", _Terminal(["prompt"])), ("a stream", _Terminal(EXIT_DIALOG, source="stream"))):
        con, _ = _decline_world(home / name.replace(" ", ""))
        term.install()
        t0 = int(time.time()) - 5000
        try:
            for step in (0, 60, 300, 700):
                blockedmod._checked.clear()
                blockedmod.sweep(con, lambda _m: None, t0 + step)
            check(f"never: {name} - no key at all", term.sent == [], str(term.sent))
        finally:
            con.close()


def test_press_history_is_scoped_to_one_dialog_not_the_whole_agent(home: Path) -> None:
    """Auditor's #1026 blocking finding, driven end to end: three different
    dialogs, each cleared once over five hours, must never read as one dialog
    looping - and a brand-new, unrelated block must never escalate on sight
    because of that history. Scoped by the block's own `title` column (raw,
    from parse_dialog), never reconstructed from `detail` - auditor's second
    finding on the first fix: a title with a quote in it, or one over
    DETAIL_CHARS, does not round-trip through describe_modal's rendering."""
    con = _blocked_world(home)
    now = int(time.time())
    for i, title in enumerate(("Background work is running",
                               "Do you trust the files in this folder?",
                               "Overwrite the file?")):
        db.press_add(con, "backend", now - (5 - i) * 3600, title, "cleared")
    fourth = {"kind": orcamod.MODAL, "detail": '"A fourth, unrelated dialog" - 1. yes / 2. Stay',
              "title": "A fourth, unrelated dialog"}
    check("press history: three unrelated dialogs, each cleared once, is not a loop",
          blockedmod.stopped_pressing(con, "backend", fourth, now) == "",
          blockedmod.stopped_pressing(con, "backend", fourth, now))
    same = {"kind": orcamod.MODAL, "detail": '"Background work is running" - 1. yes / 2. Stay',
            "title": "Background work is running"}
    check("press history: but the SAME dialog's own history still counts against it",
          "cleared it 1 times" not in blockedmod.stopped_pressing(con, "backend", same, now))

    # Auditor's #1050 regression on the first fix: a title with a quote in it,
    # or over DETAIL_CHARS, did not round-trip through a reconstruction from
    # `detail` - five real clears of the SAME dialog went uncounted, silently.
    # `title` is the raw value now, compared to the raw value, never rendered.
    for kind, title in (("quote", 'Delete "build" and stop tasks?'),
                        ("long", "Q" * (orcamod.DETAIL_CHARS + 50))):
        agent = f"quirky-{kind}"        # separate agents: five clears must stay under MAX_PRESSES_AGENT
        for i in range(5):
            db.press_add(con, agent, now - i * 60, title, "cleared")
        rec = {"kind": orcamod.MODAL, "detail": f'"{title}" - 1. yes / 2. Stay', "title": title}
        stopped = blockedmod.stopped_pressing(con, agent, rec, now)
        check(f"press history: a {kind} title's own five clears are still counted",
              "cleared it 5 times" in stopped, stopped)

    # Auditor's #1071 should-fix: the per-dialog key is itself agent-painted
    # text, so a title that varies (a counter, no malice required) never
    # repeats and never trips MAX_PRESSES. A plain per-agent cap, across every
    # title, is the absolute stop that does not depend on the key.
    for i in range(blockedmod.MAX_PRESSES_AGENT):
        db.press_add(con, "gamer", now - i * 60, f"Background work is running ({i})", "cleared")
    varying = {"kind": orcamod.MODAL, "detail": "irrelevant", "title": "Background work is running (never seen before)"}
    stopped = blockedmod.stopped_pressing(con, "gamer", varying, now)
    check("press history: a varying title cannot outrun the per-agent cap",
          f"cleared {blockedmod.MAX_PRESSES_AGENT} dialogs" in stopped, stopped)
    check("press history: an agent with no history at all still presses freely",
          blockedmod.stopped_pressing(con, "no-history-agent",
                                      {"kind": orcamod.MODAL, "detail": "x", "title": "new"}, now) == "")

    # Auditor's #1087: checking the per-agent cap first must not hand a
    # single looping dialog the per-agent wording - one dialog, cleared past
    # the cap, is still described as one dialog that came back, never as
    # several dialogs with titles that varied (neither is true of it).
    for i in range(blockedmod.MAX_PRESSES_AGENT):
        db.press_add(con, "looper", now - i * 60, "Background work is running", "cleared")
    same_title = {"kind": orcamod.MODAL, "detail": "irrelevant", "title": "Background work is running"}
    stopped = blockedmod.stopped_pressing(con, "looper", same_title, now)
    check("press history: past the per-agent cap, one dialog is still said as one dialog",
          f"cleared it {blockedmod.MAX_PRESSES_AGENT} times" in stopped and "came back each time" in stopped
          and "different titles" not in stopped, stopped)

    # The escalation gate: a brand-new UNREADABLE block on the same agent must
    # not jump SHOW_AFTER_S/ESCALATE_AFTER_S because of that stale MODAL history.
    orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.UNREADABLE)
    lines: list[str] = []
    try:
        blockedmod.sweep(con, lines.append, now)
        esc = con.execute("SELECT id FROM requests WHERE target = 'human' "
                          "AND kind = 'escalate'").fetchall()
        check("press history: a fresh, unrelated block is not escalated on sight",
              not esc, str(esc))
        rec = db.block_get(con, "backend")
        check("press history: it is only tracked, not yet raised",
              rec and rec["kind"] == "unreadable" and rec["request_id"] is None, str(dict(rec) if rec else None))
    finally:
        con.close()


def test_idle_session_is_closed_and_resumed(home: Path) -> None:
    """Memory is the scarce thing on one laptop running a team: an idle session
    holds 250-500MB and earns nothing with it. Closing keeps the terminal, and
    waking resumes the same session rather than starting a stranger in it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[tuple[str, str]] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        # A session is running in that terminal - nothing is typed into one
        # where the process table says there is only a shell - and it is gone
        # once /exit has been sent, the mirror of what a relaunch does.
        table = sessions_are("backend")

        def send(handle: str, text: str, enter: bool = True):
            sent.append((handle, text))
            if text == "/exit":
                table.pop("backend", None)
            elif " launch " in text:
                take_launched_prompt(text)
                table["backend"] = 4242
            return True, ""

        orcamod.send = send
        db.set_terminal(con, "backend", "term_x", "wt")
        now = int(time.time())
        agent_dir = home / "agents" / "backend"
        write_status_json(agent_dir, {
            "account": "alpha", "collected_at": now, "session_id": "sess-1",
            "pct_5h": 20, "ctx_pct": 8, "transcript_path": "/tmp/sess-1.jsonl"})
        st = status.read_for_agent("backend", "alpha")

        # Nothing has been seen from it yet - that is not the same as idle.
        row = db.get_agent_state(con, "backend")
        check("close: an agent we have never seen is left alone",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))

        db.add_event(con, "backend", "Stop", {}, )
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 60,))
        row = db.get_agent_state(con, "backend")
        check("close: a recently active agent is left alone",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))

        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.IDLE_CLOSE_S - 60,))
        row = db.get_agent_state(con, "backend")
        check("close: an agent idle past the threshold is closed",
              dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))
        check("close: unless it is working", not dmn_lifecycle.should_close(
            con, "backend", dmn_states.RUNNING, row, now, st))
        check("close: or a human has to look at it", not dmn_lifecycle.should_close(
            con, "backend", dmn_states.STALLED, row, now, st))
        check("close: or it is being handed off", not dmn_lifecycle.should_close(
            con, "backend", dmn_states.HANDOFF, row, now, st))

        # Memory is recoverable the moment anything wants it; the context we
        # would lose by starting a stranger instead is not recoverable at all.
        blind = status.read_for_agent("backend", "alpha")
        blind.session_id = None
        check("close: or we cannot name the session we would resume",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, blind))

        # Work waiting for it is the one case where closing only adds delay.
        db.add_request(con, "reviewer", "backend", "please look at this", None,
                       "peer", "async", None, 0, None)
        row = db.get_agent_state(con, "backend")
        check("close: an agent with work waiting is left alone",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))
        con.execute("UPDATE requests SET state = 'done'")

        # Closing records which session to bring back.
        write_status(home, "alpha", pct_5h=20, ctx_pct=8)
        (agent_dir / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()), "session_id": "sess-1",
            "pct_5h": 20, "ctx_pct": 8,
            "transcript_path": "/tmp/sess-1.jsonl"}))
        ok, _ = dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        check("close: the session is ended", ok and sent and sent[-1][1] == "/exit",
              str(sent[-1:]))
        row = db.get_agent_state(con, "backend")
        check("close: it is recorded as closed", row["closed_at"] is not None)
        check("close: with the session to resume", row["resume_session"] == "sess-1",
              str(row["resume_session"]))
        check("close: and the terminal is kept", row["terminal"] == "term_x")

        # Waking puts the agent back into the shell its session left behind,
        # and only for something waiting for it.
        sent.clear()
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: with nothing waiting it stays closed", not ok and not sent, f"{why} {sent}")
        support.something_waiting(con, "backend")
        ok, _ = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        launched = [t for _h, t in sent if t.startswith("ocp") or "launch" in t]
        check("wake: the agent is launched again", ok and launched, str(sent))
        check("wake: resuming the same session, not starting a stranger",
              "--resume sess-1" in launched[0], launched[0] if launched else "")
        row = db.get_agent_state(con, "backend")
        check("wake: the closed mark is cleared", row["closed_at"] is None)
        told = launched_prompt(launched[0])
        check("wake: a resumed agent is handed the message, not told it is back",
              "something for you" in told and "Nothing was lost" not in told, told[:120])
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available = real
        con.close()


def test_a_close_is_recorded_only_once_the_process_has_ended(home: Path) -> None:
    """`/exit` typed is not a session ended: one was eaten outright and another
    took 31s to die, and either way a row marked closed over a live process was
    reverted by the sweep as "running again" while a relaunch typed at that
    terminal landed in the old session as a prompt."""
    _daemon_home(home, 20)
    con = db.connect()
    typed: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available,
            orcamod.screen_blocker, db.set_closed)
    state = {"exiting": False, "reads": 0, "leaves_after": 3, "modal": False}
    seen_at_mark: list[dict] = []
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, "dialog") if state["modal"] else None
        sessions_are("backend")

        def live() -> dict:
            if state["exiting"]:
                state["reads"] += 1
                if state["leaves_after"] is not None and state["reads"] > state["leaves_after"]:
                    return {}
            return {"backend": 4200}
        obsmod.live_sessions = live

        def send(handle: str, text: str, enter: bool = True):
            typed.append(text)
            state["exiting"] = text == "/exit"
            return True, ""
        orcamod.send = send

        def set_closed(c, agent, session_id):
            seen_at_mark.append(live())
            real[5](c, agent, session_id)
        db.set_closed = set_closed

        db.set_terminal(con, "backend", "term_x", "wt")
        agent_dir = home / "agents" / "backend"
        write_status_json(agent_dir, {
            "account": "alpha", "collected_at": int(time.time()), "session_id": "sess-1",
            "pct_5h": 20, "ctx_pct": 8})
        st = status.read_for_agent("backend", "alpha")

        # It dies a few reads after /exit, as a slow teardown does.
        ok, why = dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: a session that leaves the process table is closed", ok, why)
        check("close: and was already gone when the row was marked",
              seen_at_mark and "backend" not in seen_at_mark[-1], str(seen_at_mark))

        # It never dies: nothing is marked, nothing is retried, nothing is killed.
        db.clear_closed(con, "backend")
        con.execute("DELETE FROM events WHERE kind = 'close'")
        typed.clear()
        state.update(exiting=False, reads=0, leaves_after=None)
        ok, why = dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        row = db.get_agent_state(con, "backend")
        check("close: a session still running after /exit is not closed", not ok, why)
        check("close: and says so", "still running" in why, why)
        check("close: the row is not marked closed, so the sweep has nothing to revert",
              row["closed_at"] is None, str(row["closed_at"]))
        check("close: no close event is written",
              not con.execute("SELECT 1 FROM events WHERE kind = 'close'").fetchone())
        check("close: /exit is typed once, not again at whatever ate it", typed == ["/exit"],
              str(typed))

        # A modal on screen: /exit and Enter would answer it.
        typed.clear()
        state.update(exiting=False, reads=0, leaves_after=3, modal=True)
        ok, why = dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: nothing is typed at a modal", not typed and not ok and "dialog is open" in why,
              f"{typed} {why}")
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available,
         orcamod.screen_blocker, db.set_closed) = real
        con.close()


def test_a_close_that_will_not_take_is_not_tried_again_every_tick(home: Path) -> None:
    """The idle schedule asks `should_close` every tick. A session that does
    not die after /exit must not be typed at again, and must not hold the
    daemon for the whole wait again, on each of them."""
    _daemon_home(home, 20)
    con = db.connect()
    typed: list[str] = []
    waits: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available,
            orcamod.modal_open, dmn_delivery._wait_session_gone)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.modal_open = lambda h: False
        orcamod.send = lambda h, text, enter=True: (typed.append(text), (True, ""))[1]
        sessions_are("backend")               # and it stays there whatever is typed
        dmn_delivery._wait_session_gone = lambda agent: (waits.append(agent), False)[1]
        db.set_terminal(con, "backend", "term_x", "wt")
        now = int(time.time())
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=8,
                     reset_5h=now + 1800)
        agent_dir = home / "agents" / "backend"
        write_status_json(agent_dir, {
            "account": "alpha", "collected_at": now, "session_id": "sess-1",
            "pct_5h": 20, "ctx_pct": 8})
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.IDLE_CLOSE_S - 60,))
        st = status.read_for_agent("backend", "alpha")
        check("close: the idle session is due to be closed",
              dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, db.get_agent_state(con, "backend"), now, st))

        for _ in range(3):
            dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        check("close: three ticks over a session that will not die type /exit once",
              typed.count("/exit") == 1, str(typed))
        check("close: and wait for it once", len(waits) == 1, str(waits))
        check("close: the failure is on the record",
              db.last_event_ts(con, "backend", "close_failed") is not None)
        row = db.get_agent_state(con, "backend")
        check("close: and the row is not marked closed", row["closed_at"] is None)
        check("close: a failed close is not activity - it does not restart the idle clock",
              db.last_activity(con, "backend") == now - dmn_states.IDLE_CLOSE_S - 60)

        # One idle window later it is tried again.
        later = now + dmn_states.IDLE_CLOSE_S + 1
        check("close: after a full idle window it is due again",
              dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, later, st))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available,
         orcamod.modal_open, dmn_delivery._wait_session_gone) = real
        con.close()


def test_wake_without_a_closed_session_uses_the_handoff(home: Path) -> None:
    """Closing is for memory; a handoff is for context. An agent that was
    cleared rather than closed has no session worth resuming - what it needs is
    its own record."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.alive, orcamod.available, orcamod.send)
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        sent: list[str] = []
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        # Cleared, not closed: the session is still running, which is what
        # makes typing the handoff into it the right move.
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        keep_handoff("backend", "# handoff · backend\n- was mid-way through the routing fix")
        ok, _ = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: a cleared agent gets its handoff", ok and "routing fix" in support.pointed_record(sent[-1]),
              sent[-1][:120] if sent else "")
        check("wake: and is told its context was cleared",
              "context was cleared" in sent[-1])
        check("wake: no launch command is typed at an agent that is still running",
              not any("launch" in t for t in sent), str(sent))
        check("wake: a handoff that has been handed over is not handed over again",
              kept_handoff("backend") is None)
    finally:
        orcamod.alive, orcamod.available, orcamod.send = real
        con.close()


def test_reconcile_does_not_clear_closed_at_on_a_failed_wake(home: Path) -> None:
    """QUEUED with closed_at set is ambiguous, so reconcile leaves it alone.

    A wake interrupted after relaunching but before its write, and a wake that
    failed outright, leave the same two columns behind - and step 2's own
    wake_agent produces the second one whenever a reboot's dead terminal cannot
    be relaunched. Clearing on that shape gets the failure case backwards and
    takes resume_session with it, which is the one thing a close must never
    lose. Only a live witness, the process table, separates them.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.alive, orcamod.available)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: False  # the terminal a reboot leaves behind
        db.set_terminal(con, "backend", "term_gone", "wt")
        db.set_closed(con, "backend", "sess-1")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed, daemon was down since")

        logged: list[str] = []
        dmn_reconciler.reconcile(con, logged.append)
        row = db.get_agent_state(con, "backend")
        check("reconcile: step 2's own wake attempt is logged as failing",
              any("could not wake at reconciliation" in m for m in logged), str(logged))
        check("reconcile: closed_at survives a failed wake attempt",
              row["closed_at"] is not None, str(dict(row)))
        check("reconcile: resume_session survives it too - it is not repairable once gone",
              row["resume_session"] == "sess-1", str(dict(row)))
        # And the deadline stays on the row. Clearing it before the wake had
        # worked is how a failed wake became a permanent sleep: due_agents
        # selects on wake_at, so a row with none is never looked at again.
        check("reconcile: the wake stays due, so a later restart retries it",
              row["wake_at"] is not None and row["state"] == dmn_states.PARKED, str(dict(row)))

        # The terminal comes back - a human reopened it, or Orca was slow to
        # start - and the next restart does what this one could not.
        sent: list[str] = []
        table = sessions_are()
        orcamod.alive = lambda h: True
        orcamod.live_terminals = lambda: {"term_gone"}
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.send = relaunching_send(sent, table)
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        dmn_reconciler.reconcile(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("reconcile: the retry resumes the session it kept",
              any("--resume sess-1" in t for t in sent), str(sent))
        check("reconcile: and only then is the deadline cleared",
              row["state"] == dmn_states.QUEUED and row["wake_at"] is None, str(dict(row)))
    finally:
        orcamod.alive, orcamod.available = real
        con.close()


def test_the_daemon_never_types_into_a_trust_prompt_via_deliver(home: Path) -> None:
    """Auditor's #339: `deliver_queued`/`_tell` and `deliver_answers` must not
    type into the folder-trust dialog, mark a request delivered, or mark an
    answer announced underneath it - even with Orca reporting the terminal idle.
    What refuses is ocp's own `screen_blocker`, reading the live capture as a
    dialog. The sweep reports it from there (#1630)."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.send, orcamod.alive, orcamod.idle_now, orcamod.available,
            orcamod.screen_blocker, orcamod._screen)
    try:
        orca_idle()
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
        orcamod._screen = lambda h: _screen_of(LIVE_TRUST_PROMPT_CAPTURE.split("\n"))
        sessions_are("backend")
        seen = orcamod.screen_blocker("term_x")
        check("trust screen: screen_blocker reads it as a dialog",
              seen is not None and seen.kind == orcamod.MODAL, str(seen))

        rid = db.add_request(con, "reviewer", "backend", "look at this", None,
                             "peer", "fire_and_forget", None, 0, None)
        ok = dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("deliver_queued: nothing is typed into a trust dialog",
              not ok and not sent, str(sent))
        check("deliver_queued: the row is not marked delivered underneath it",
              db.get_request(con, rid)["delivered_at"] is None)

        rid2 = db.add_request(con, "backend", "reviewer", "spec?", None,
                              "peer", "async", None, 0, None)
        askmod.resolve(rid2, "yes")
        ok = dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        check("deliver_answers: nothing is typed into a trust dialog either",
              not ok and not sent, str(sent))
        check("deliver_answers: the answer is not marked announced underneath it",
              rid2 in [r["id"] for r in db.unannounced_answers(con, "backend")])
    finally:
        (orcamod.send, orcamod.alive, orcamod.idle_now, orcamod.available,
         orcamod.screen_blocker, orcamod._screen) = real
        con.close()


def test_work_arriving_wakes_a_closed_agent(home: Path) -> None:
    """A closed session cannot read its own inbox. Without this, `ocp ask`
    queues into a terminal with nobody in it - reviewer sat closed with two
    requests pending and nothing would ever have brought it back."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        # The session runs until close_agent ends it, and then it does not -
        # which is what the tick below has to work out for itself.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-9", "pct_5h": 20}))

        # A turn can start between a human pressing `c` and confirming it, so
        # closing checks the state itself rather than trusting what the screen
        # was showing. `should_close` refuses the same two states.
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="was working")
        ok, why = dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                                  status.read_for_agent("backend", "alpha"))
        check("close: a session mid-turn is not closed out from under the work",
              not ok and "RUNNING" in why, why)
        check("close: and nothing was typed at it", "/exit" not in sent, str(sent))

        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="turn ended")
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        row = db.get_agent_state(con, "backend")
        check("close: and a closed session is not left labelled RUNNING",
              row["state"] != dmn_states.RUNNING, row["state"])

        db.add_request(con, "reviewer", "backend", "please look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        check("wake: work arriving brings a closed agent back",
              any("--resume sess-9" in t for t in sent), str(sent[:2]))
        check("wake: and it is no longer marked closed",
              db.get_agent_state(con, "backend")["closed_at"] is None)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available = real
        con.close()


def test_a_delivery_belongs_to_the_session_it_was_typed_into(home: Path) -> None:
    """The head is told once - to one session. A replacement that started
    fresh has read nothing, so it is told again; a resume keeps its id and is
    not."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)

        _session_file(home, "sess-A")
        check("session: the head is told to session A",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None))
        sent.clear()
        check("session: and not again while A is the session",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        _session_file(home, "sess-B")
        check("session: a fresh session B is told the same head",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and "look at this" in sent[-1],
              str(sent))
        sent.clear()
        check("session: and B is not told twice",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # A resume of B keeps B's id: a new process, the same session.
        sessions_are()
        sessions_are("backend")
        check("session: a resume of the same session is not told again",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # An id nobody can read is not a difference.
        (home / "agents" / "backend" / "status.json").unlink()
        check("session: an unreadable id re-tells nothing",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)
    finally:
        con.close()


def test_a_session_that_comes_back_is_told_a_head_an_earlier_one_was_told(home: Path) -> None:
    """T102: the head was typed into a session that then died unread. What
    starts or resumes next keeps the same session id, and is told all the same."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.add_request(con, "reviewer", "backend", "first", None, "peer", "async", None, 0, None)
        db.add_request(con, "reviewer", "backend", "second", None, "peer", "async", None, 0, None)
        _session_file(home, "sess-A")
        check("back: the head is told to session A",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and "first" in sent[-1], str(sent))
        sent.clear()

        db.set_closed(con, "backend", "sess-A")
        support.something_waiting(con, "backend")
        check("back: a closed agent is not told again",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)
        db.clear_closed(con, "backend")
        check("back: the session that is running again, with A's id, is told the head and the depth",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
              and "first" in sent[-1] and "1 more waiting" in sent[-1], str(sent))
        sent.clear()
        check("back: and only once",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # A reply in the head's thread goes ahead of the queue, but a session
        # that has read nothing hears the head before it (T111).
        head = db.head_of_queue(con, "backend")["id"]
        db.add_request(con, "reviewer", "backend", "a follow-up", None, "peer", "async",
                       None, 0, head)
        db.set_closed(con, "backend", "sess-A")
        support.something_waiting(con, "backend")
        db.clear_closed(con, "backend")
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("back: the restarted session is told the head before its follow-up",
              len(sent) == 1 and "first" in sent[0] and "follow-up to" not in sent[0], str(sent))
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("back: and the follow-up on the next pass, naming the open head",
              len(sent) == 2 and "a follow-up" in sent[1] and f"#{head}" in sent[1], str(sent))
    finally:
        con.close()


def test_what_is_typed_at_an_agent_is_never_more_than_three_lines(home: Path) -> None:
    """T105: Claude Code wraps typed text of four or more lines in
    <pasted_content>, and an agent may decline it as untrusted."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.add_request(con, "reviewer", "backend", "step one\n- a\n- b\n\nstep two", None,
                       "peer", "async", None, 0, None)
        db.add_request(con, "reviewer", "backend", "later", None, "peer", "async", None, 0, None)
        _session_file(home, "sess-A")
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("lines: a multi-line body with more waiting behind it is typed in three lines",
              len(sent) == 1 and sent[0].count("\n") == 2
              and "step one - a - b" in sent[0] and "step two" in sent[0]
              and "1 more waiting" in sent[0], repr(sent))
        sent.clear()

        for i in range(5):
            rid = db.add_request(con, "backend", "reviewer", f"q{i} " + "x" * 50, None,
                                 "peer", "async", None, 0, None)
            con.execute("UPDATE requests SET state = 'done', verdict = ? WHERE id = ?",
                        (f"answer {i}\nmore", rid))
        dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        check("lines: five answers, one of them cut, are typed in three lines",
              len(sent) == 1 and sent[0].count("\n") <= 2 and "also answered" in sent[0], repr(sent))
        sent.clear()

        dmn_delivery._send("term_x", "a\nb\nc\nd\ne")
        check("lines: whatever else is typed is squeezed to three, first and last kept",
              sent == ["a\nb c d\ne"], repr(sent))
    finally:
        con.close()


def test_a_reply_in_the_open_heads_thread_is_told_now_not_behind_it(home: Path) -> None:
    """A request told and open, its follow-up queued behind it, the agent idle:
    the follow-up used to wait for a resolve that could depend on it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        _session_file(home, "sess-A")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        head = db.add_request(con, "marina", "backend", "step six", None, "peer", "async", None, 0, None)
        other = db.add_request(con, "marina", "backend", "unrelated", None, "peer", "async", None, 0, None)
        reply = db.add_request(con, "marina", "backend", "on step six: leave it", None,
                               "peer", "async", None, 0, head)
        check("thread: the reply to a head not yet told does not jump it",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
              and "step six" in sent[-1] and "leave it" not in sent[-1], str(sent))
        sent.clear()
        check("thread: the reply is told now, though its head is still open",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
              and "leave it" in sent[-1] and f"follow-up to #{head}" in sent[-1], str(sent))
        sent.clear()
        check("thread: and once - the unrelated request stays behind the head",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)
        con.execute("UPDATE requests SET state = 'done', verdict = 'ok' WHERE id = ?", (head,))
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("thread: with the head closed, the reply is not told a second time",
              not any("leave it" in t for t in sent) and any("unrelated" in t for t in sent),
              str(sent))
    finally:
        con.close()


def test_nothing_is_typed_at_a_modal(home: Path) -> None:
    """Claude Code opens modals of its own at start; a typed line plus Enter
    answers whichever option is highlighted."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, "dialog")
        check("modal: a waiting request is not typed at an open modal",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))
        # Isolate the cause: no modal, nothing else in the way, only a screen
        # ocp cannot read.
        orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.UNREADABLE)
        rid = db.queued_for(con, "backend")[0]["id"]
        check("unreadable: a waiting request is not typed into a screen ocp cannot read",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))
        check("unreadable: and it stays queued to be retried, not dropped",
              [r["id"] for r in db.queued_for(con, "backend")] == [rid])
        orcamod.screen_blocker = lambda h: None
        check("modal: it is told once the modal is gone",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None))

        # The reader itself, against what Orca returns.
        orcamod.modal_open = REAL_ORCA["modal_open"]
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
        orcamod._screen = REAL_ORCA["_screen"]
        screen = {"ok": True}
        def fake_run(args, timeout=180):
            term = {"tail": screen["tail"], "source": screen.get("source", "screen")}
            if "draft" in screen:
                term["draft"] = screen["draft"]
            return screen["ok"], json.dumps({"result": {"terminal": term}})
        real_run = orcamod._run
        orcamod._run = fake_run
        try:
            screen["tail"] = ["Teach auto mode about your environment?", "Enter to confirm · Esc to cancel"]
            check("modal: the auto-mode prompt is read as a modal", orcamod.modal_open("h"))
            screen["tail"] = (["quoted: Esc to cancel", "old output", "more", "❯ ", "────",
                              "? for shortcuts"] + COMPOSER_EMPTY)
            check("modal: the words higher up in the transcript are not a modal",
                  not orcamod.modal_open("h"))
            screen["tail"] = ["❯ "]
            screen["source"] = "screen-unavailable"
            check("modal: anything but a rendered screen counts as a modal",
                  orcamod.modal_open("h"))
            screen["source"] = "screen"
            screen["tail"] = COMPOSER_EMPTY
            check("modal: an empty prompt is not", not orcamod.modal_open("h"))
            screen["tail"] = None
            check("modal: a screen that cannot be read counts as a modal", orcamod.modal_open("h"))
            # `draft` is not consulted at all (2026-09-21: a stale one rendered
            # identically to an empty box and blocked delivery for 8 hours
            # with nothing to clear it but the send it was refusing). Nor is
            # composer CONTENT (#1157: the screen cannot tell a person's
            # typing from ocp's own echoed sends), so a recognized composer
            # box - whatever it shows - is just an idle input, never a block.
            screen["tail"] = _composer_typed("orca half-typed")
            check("composer: text in a recognized box is not a block", orcamod.screen_blocker("h") is None)
            screen["tail"] = COMPOSER_EMPTY
            check("composer: an empty composer is an empty input", orcamod.screen_blocker("h") is None)
            screen["tail"] = ["> "]
            check("composer: no composer box at all fails closed",
                  orcamod.screen_blocker("h").kind == orcamod.UNREADABLE)
            screen["source"] = "screen-unavailable"
            check("composer: a screen that is not rendered fails closed too",
                  orcamod.screen_blocker("h").kind == orcamod.UNREADABLE)
            screen["source"] = "screen"
            screen["tail"] = _composer_typed("typed")
            orcamod.idle_now = lambda h: True
            check("composer: an idle agent with text in the input is still typeable",
                  orcamod.typeable_now("h"))
            sent_: list[str] = []
            orcamod.wait_idle = lambda h, t=0: True
            orcamod.alive = lambda h: True
            orcamod.send = lambda h, text, enter=True: (sent_.append(text), (True, ""))[1]
            ok_c, why_c = REAL_ORCA["close_session"]("h")
            check("composer: /exit is typed even with text in the box", ok_c and sent_ == ["/exit"], why_c)
            sent_.clear()
            ok_c, why_c = REAL_ORCA["clear_context"]("h")
            check("composer: /clear is typed even with text in the box", ok_c and sent_ == ["/clear"], why_c)
            sent_.clear()
            screen["tail"] = ["Confirm?", "Enter to confirm · Esc to cancel"]
            ok_c, why_c = REAL_ORCA["clear_context"]("h")
            check("modal: /clear is refused at an open modal", not ok_c and not sent_, why_c)
            screen["tail"] = COMPOSER_EMPTY
            ok_c, why_c = REAL_ORCA["clear_context"]("h")
            check("composer: with the input empty /clear is typed", ok_c and sent_ == ["/clear"], why_c)
        finally:
            orcamod._run = real_run
    finally:
        con.close()


def test_a_wake_types_nothing_at_a_shell_when_the_session_dies(home: Path) -> None:
    """The wake prompt is the session's launch argument. A session that starts
    and exits before it is ready must leave the shell with only the launch
    command typed at it, never the prompt."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    resumed_with: list[str] = []
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.resume_with = lambda h, text: (resumed_with.append(text), (True, ""))[1]
        table = sessions_are()

        def send(h, text, enter=True):
            sent.append(text)
            if " launch " in text:
                take_launched_prompt(text)
                table["backend"] = 4242        # the session starts ...
                orcamod.send = lambda *a, **k: (sent.append(a[1]), (True, ""))[1]
            return True, ""
        orcamod.send = send
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.QUEUED)
        db.set_closed(con, "backend", "sess-1")
        support.something_waiting(con, "backend")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        table.clear()                           # ... and is gone before anything else
        check("wake: the launch was typed", ok and len(sent) == 1 and " launch " in sent[0],
              str(sent))
        check("wake: the prompt rides the launch, not the terminal",
              "--prompt-file " in sent[0] and not resumed_with, str(sent))
        check("wake: nothing else reaches the shell", len(sent) == 1, str(sent))
    finally:
        con.close()


def test_a_launch_takes_its_prompt_once(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    f = home / "p.txt"
    f.write_text("carry on")
    check("launch: the prompt file is read", launch.take_prompt(str(f)) == "carry on")
    check("launch: and removed, so no later launch reads it", not f.exists())
    check("launch: a missing file is no prompt", launch.take_prompt(str(f)) is None)


def test_a_closed_agent_is_not_handed_off(home: Path) -> None:
    """Closing stops that session's status line, so 15 minutes later the
    snapshot is stale and usage_unknown fires - on an agent that is idle,
    healthy, and whose terminal is now a shell prompt. That would pay for a
    fold and then type a handoff full of backticks at bash: the exact failure
    this branch exists to remove, reached through its own new feature."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
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
            "session_id": "sess-2", "pct_5h": 20}))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        check("closed: the agent is marked closed",
              db.get_agent_state(con, "backend")["closed_at"] is not None)

        # Its own file stopped being written, and so did the account's.
        write_status(home, "alpha", pct_5h=20, pct_7d=10,
                     collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        st = status.read_for_agent("backend", "alpha")
        check("closed: its usage does read as unknown",
              st.short_trusted is None, str(st.short_trusted))
        check("closed: and an unknown figure is not a wall, so nothing would fire on it",
              not triggers.evaluate(st.short_trusted, None).fired)

        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("closed: but a closed agent is not handed off",
              row["state"] != dmn_states.PARKED, str(dict(row)))
        check("closed: nothing is typed at its shell", not sent, str(sent))
        check("closed: and no fold was paid for",
              not db.in_flight_handoffs(con) and row["continuations"] == 0, str(dict(row)))
        check("closed: it stays closed", row["closed_at"] is not None)

        # And the screens say what the daemon does. A warning nobody will act
        # on is the dashboard arguing with the daemon.
        check("closed: the daemon's own rule says it is not a candidate",
              not dmn_lifecycle.triggers_apply(row))
        frame = _snap(120, 40)
        check("closed: top does not warn about it", "usage_unknown" not in frame,
              [ln for ln in frame.splitlines() if "backend" in ln])
        check("closed: and says why instead", "closed" in frame,
              [ln for ln in frame.splitlines() if "backend" in ln])

        # The screen answers the founder's questions, not the operator's: no
        # snapshot age (its only news is the reason beside it), no cost column
        # (a sum over the sessions we can see is not the account's spend), no
        # model or effort (configuration, and on the agent's own page), no pid.
        plain = [tui._ANSI.sub("", ln) for ln in frame.split(chr(10))]
        check("screen: the daemon is reported as running, not as a pid",
              "pid" not in plain[0], plain[0])
        # The daemon's own note is not news. It is on every row, it is never
        # something a human acts on - "woke from PARKED at reconciliation" -
        # and a column that is always full of something unactionable is one a
        # reader learns to skip. The states that do need a human still say so.
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="woke at reconciliation")
        quiet = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
        check("screen: the daemon's bookkeeping is not on the team list",
              not any("backend" in ln and "reconciliation" in ln for ln in quiet),
              str([ln for ln in quiet if "backend" in ln])[:160])
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="three failed wakes")
        loud = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
        check("screen: but what needs a human is, with its reason",
              any("backend" in ln and "three failed wakes" in ln for ln in loud),
              str([ln for ln in loud if "backend" in ln])[:160])
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="")
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_a_close_that_stamps_sessionend_does_not_unexpire_a_stale_cache(home: Path) -> None:
    """The real shape: an agent quiet past RESUME_WARM_S, then closed by ocp
    a minute ago. ocp's own `/exit` fires SessionEnd at once (ocp/daemon/'s
    END_WAIT_S comment), in the same second as the `close` event - before the
    fix this pinned the clock to the close and a wake one minute later read
    the cache warm and resumed it dead. It must read cold instead."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        now = int(time.time())
        con.execute("DELETE FROM events WHERE agent = 'backend'")
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.RESUME_WARM_S - 3600,))
        for kind in ("close", "SessionEnd"):
            db.add_event(con, "backend", kind, {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend' AND kind IN "
                    "('close', 'SessionEnd')", (now - 60,))
        check("cache: a close a minute ago does not read as activity a minute ago",
              dmn_lifecycle.cache_expired(con, "backend", "sess-close", now), "read warm")
    finally:
        con.close()


def test_a_park_that_was_also_closed_is_woken_without_being_judged(home: Path) -> None:
    """An agent can be parked and closed at once, and then the wake_at branch is
    what restores its session - so the same rule has to hold there. It held only
    because that branch never re-read the row, which is the first thing anyone
    tidying this code would fix."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-8", "pct_5h": 20}))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed")
        support.something_waiting(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("parked+closed: the state this is about is reachable",
              row["state"] == dmn_states.PARKED and row["closed_at"] is not None, str(dict(row)))

        # Its own snapshot stopped with the session, and it was the only one here.
        write_status(home, "alpha", pct_5h=20, pct_7d=10,
                     collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("parked+closed: wake_at brings the same session back",
              any("--resume sess-8" in t for t in sent), str(sent))
        check("parked+closed: and it is not parked again on the reading it left",
              row["state"] != dmn_states.PARKED, str(dict(row)))
        check("parked+closed: nothing was folded for it",
              row["continuations"] == 0 and not db.in_flight_handoffs(con), str(dict(row)))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_reconcile_wakes_a_park_that_was_also_closed(home: Path) -> None:
    """The case above is proven through tick(), but a daemon that was down
    across the whole window - which is the case launchd exists for, not the
    exception - meets this agent at startup, in reconcile(), not in a later
    tick. Before this fix, reconcile's own due_agents step set state to
    QUEUED and cleared wake_at, which is exactly what disarms the wake_at
    branch in supervise() that would otherwise have woken it - so a closed,
    parked agent whose window rolled over while the daemon was dead came back
    QUEUED and stayed that way, forever, with no session and no path left
    that will ever relaunch it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-9", "pct_5h": 20}))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed, daemon was down since")
        support.something_waiting(con, "backend")

        sent.clear()
        dmn_reconciler.reconcile(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("reconcile: a closed, due park is relaunched at startup, not left QUEUED",
              any("--resume sess-9" in t for t in sent), str(sent))
        check("reconcile: closed_at is cleared, not stuck behind a nulled wake_at",
              row["closed_at"] is None, str(dict(row)))
        check("reconcile: triggers apply to it again", dmn_lifecycle.triggers_apply(row))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_a_due_park_with_nothing_waiting_stays_closed(home: Path) -> None:
    """A closed session is resumed to be handed a message. A deadline that
    comes due with none waiting spends the deadline and leaves the agent
    closed, from a tick and from reconcile alike (T124)."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-10", "pct_5h": 20}))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        for step, run_it in (("tick", lambda: dmn_evaluate.tick(con, lambda _m: None)),
                             ("reconcile", lambda: dmn_reconciler.reconcile(con, lambda _m: None))):
            db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                               detail="parked and closed")
            sent.clear()
            run_it()
            row = db.get_agent_state(con, "backend")
            check(f"{step}: nothing is resumed for a deadline with nothing waiting",
                  not any("--resume" in t for t in sent), str(sent))
            check(f"{step}: the deadline is spent and the agent stays closed",
                  row["wake_at"] is None and row["closed_at"] is not None
                  and row["state"] != dmn_states.PARKED, str(dict(row)))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_nothing_is_typed_into_a_shell_with_no_session(home: Path) -> None:
    """Orca runs claude as a login shell's child, so a session that ends leaves
    the terminal standing and our handle still working: `send` succeeds and the
    text lands at a bash prompt. What we send is not inert there - a handoff's
    backticks run and its `>` redirects - and `/exit` at that prompt exits the
    shell, taking the terminal, the worktree and the only way back with it. So
    every path that types checks the process table first, and a table it could
    not read counts as no.
    """
    _daemon_home(home, 50)
    con = db.connect()
    sent: list[str] = []
    cleared: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.wait_idle,
            orcamod.send, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True          # the terminal outlives the session
        orcamod.idle_now = lambda h: True       # and a bare shell probes as idle
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (
            cleared.append(h), (True, "context cleared"))[1]
        table = sessions_are()                  # the shell is there; nobody is in it
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-5", "pct_5h": 50}))
        st = status.read_for_agent("backend", "alpha")

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        check("shell: a waiting request is not announced to a shell",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))
        check("shell: and it stays queued, for the wake to deliver",
              len(db.queued_for(con, "backend")) == 1)

        ok, why = dmn_lifecycle.close_agent(con, "backend", lambda _m: None, st)
        check("shell: /exit is not typed at it - that would end the shell too",
              not ok and not sent, f"{why} {sent}")
        check("shell: and its terminal is left recorded",
              db.get_agent_state(con, "backend")["terminal"] == "term_x")

        keep_handoff("backend", "# handoff · backend\n- carry on\n")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("shell: a handoff is not typed at it either",
              not ok and not sent, f"{why} {sent}")

        # The clear is gated where it is sent, not only where it is decided:
        # a handoff resumed after a restart reaches it without passing the
        # check perform_handoff makes.
        ok, why = dmn_delivery._reclaim_context(con, "backend", lambda _m: None)
        check("shell: /clear is not typed at it, whoever asked for it",
              not ok and not cleared, f"{why} {cleared}")

        t = triggers.Trigger(True, "context", "context 700,000 >= 120,000")
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("shell: no handoff is run for a session that is not there",
              not cleared and not sent and not db.in_flight_handoffs(con), str(sent))
        check("shell: so no fold is paid for either",
              row["state"] != dmn_states.PARKED and row["continuations"] == 0, str(dict(row)))

        # Fail-closed: "the process table could not be read" is not "no session",
        # but it is not permission to send, which is the only question here.
        obsmod.live_sessions = lambda: None
        db.add_request(con, "reviewer", "backend", "and this", None,
                       "peer", "async", None, 0, None)
        check("shell: an unreadable process table is a no, not a maybe",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))

        # And with a session running, the same call does its job.
        table["backend"] = 4242
        obsmod.live_sessions = lambda: dict(table)
        check("shell: a running agent is told what is waiting",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and
              any("ocp inbox" in t2 for t2 in sent), str(sent))

        # One listing per tick, not one per send: the sweep already asked.
        calls = {"n": 0}
        base = dict(table)

        def counted():
            calls["n"] += 1
            return dict(base)

        obsmod.live_sessions = counted
        db.set_cursor(con, "notify:backend", 0)
        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        check("shell: the tick reads the process table once, and the sends reuse it",
              calls["n"] == 1 and any("ocp inbox" in t2 for t2 in sent),
              f"{calls['n']} listings, sent {sent}")
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.wait_idle,
         orcamod.send, orcamod.clear_context) = real
        con.close()


def test_a_stalled_closed_agent_keeps_its_marker_when_walled(home: Path) -> None:
    """A STALLED (FATAL) agent needs a human - `w` is the one thing that
    clears that mark. Its own detail lives in the state cell itself
    (screen_state()), never the last column a wall's yellow ⏳ also uses - so
    there is nothing for the wall to crowd out any more: a FATAL agent whose
    account also crossed 90% still reads FATAL, with why, whatever else the
    row has room to say.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-stalled")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="ran out of attempts")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("stalled+walled: both conditions actually hold here",
              dmn_lifecycle.would_wake(con, row) and launch.wake_wait("backend", row, con),
              dict(row))
        frame = tui._ANSI.sub("", _snap(200, 40))
        line = next((ln for ln in frame.splitlines() if "backend" in ln), "")
        check("stalled+walled: the state cell says FATAL with its own detail",
              "FATAL" in line and "ran out of attempts" in line, line)
    finally:
        con.close()


def test_a_delivery_and_a_handoff_in_the_same_tick_the_handoff_must_not_type(
        home: Path) -> None:
    """#1973 (see the comment above `TYPE_SETTLE_S`). `typeable_now` is left
    stubbed True throughout - the stale reading the incident had - so what
    has to catch this is the settle window alone."""
    _daemon_home(home, 20)
    con = db.connect()
    typed: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.typeable_now, orcamod.send,
            orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        # The stale reading itself: idle before the notify, and left exactly
        # as idle after it - the incident's own screen never told the truth
        # in between, so nothing here may depend on it changing.
        orcamod.typeable_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (typed.append(text), (True, "sent"))[1]
        orcamod.clear_context = lambda h, **kw: (typed.append("/clear"), (True, "context cleared"))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)

        ok = dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("race: the delivery itself goes through", ok and len(typed) == 1, typed)

        t = triggers.evaluate(10, 300_000)
        check("race: setup - the trigger actually fires", t.fired, t.reason)
        fired = dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        check("race: the handoff in the same tick does not type - only the "
              "delivery's own line is on the record",
              typed == [typed[0]], typed)
        check("race: and it is reported as deferred (True), not degraded (False)",
              fired is True, fired)

        # Past the settle window, the same trigger folds normally - this is
        # not a guard that got stuck on.
        db.set_cursor(con, "typed_at:backend", int(time.time()) - dmn_delivery.TYPE_SETTLE_S - 1)
        con.execute("DELETE FROM handoff")     # MIN_HANDOFF_INTERVAL_S, not a real one
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        check("race: once settled, the same condition hands off",
              "/clear" in typed, typed)
    finally:
        (orcamod.available, orcamod.alive, orcamod.typeable_now, orcamod.send,
         orcamod.clear_context) = real
        con.close()


def test_a_close_right_after_a_delivery_in_the_same_tick_must_not_type(
        home: Path) -> None:
    """BLOCKING (#1973 review): `deliver_answers` and `close_agent` can both
    run in the same tick - `should_close` reads nothing `deliver_answers`
    changes - and close typed `/exit` straight after the answer typed: the
    same settle gap #1973 is about, on the close door rather than the
    handoff door. `wait_idle`, `close_session`'s own fresh idle probe, is
    left stubbed True throughout, so only `close_agent`'s own settle check
    can catch this."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-close-race", "pct_5h": 20}))

        rid = db.add_request(con, "backend", "reviewer", "spec?", None,
                             "peer", "async", None, 0, None)
        # `resolve` pushes the answer at once (`push_answer` ->
        # `deliver_answers`) rather than leaving it for the next tick - this
        # is the delivery the race is against.
        ok, out = askmod.resolve(rid, "yes")
        check("close race: the answer is delivered as part of resolving it",
              ok and len(sent) == 1, (out, sent))

        st = status.read_for_agent("backend", "alpha")
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None, st)
        check("close race: nothing typed after the answer - no /exit followed it",
              len(sent) == 1, sent)
        check("close race: and the agent was not marked closed underneath it",
              db.get_agent_state(con, "backend")["closed_at"] is None)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send = real
        con.close()


def test_an_answer_for_a_cold_session_closes_it_instead_of_being_typed(home: Path) -> None:
    """T84: a line typed into an open session whose cache has expired makes the
    vendor re-read the whole transcript uncached. The daemon closes it there
    and the wake delivers on a fresh start; a warm session, or a cold one with
    nothing to say, is left alone."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-cold", "pct_5h": 20}))
        st = status.read_for_agent("backend", "alpha")
        now = int(time.time())
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.RESUME_WARM_S - 60,))
        row = db.get_agent_state(con, "backend")
        log = lambda _m: None

        check("cold typing: nothing to say, a cold session is left open",
              dmn_lifecycle._cold_before_typing(con, "backend", row, now, st, log) is None
              and not sent)
        rid = db.add_request(con, "backend", "reviewer", "spec?", None,
                             "peer", "async", None, 0, None)
        con.execute("UPDATE requests SET state = 'done', verdict = 'yes' WHERE id = ?", (rid,))
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.RESUME_WARM_S + 600,))
        check("cold typing: a warm session with an answer waiting is left to be typed into",
              dmn_lifecycle._cold_before_typing(con, "backend", row, now, st, log) is None
              and not sent)
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.RESUME_WARM_S - 60,))
        dmn_evaluate.supervise(con, "backend", log, now, {}, [])
        check("cold typing: a cold session with an answer waiting is closed by the tick",
              db.get_agent_state(con, "backend")["closed_at"], str(sent))
        check("cold typing: the answer was never typed into the old session",
              all("yes" not in t for t in sent), str(sent))
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send = real
        con.close()


def _cold_backend(home: Path, con, sent: list) -> tuple:
    """An open IDLE `backend` whose last activity is past the cache's life."""
    orca_seen()
    orcamod.wait_idle = lambda h, timeout_ms=0: True
    orcamod.send = relaunching_send(sent, sessions_are("backend"))
    db.set_terminal(con, "backend", "term_x", "wt")
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
    (home / "agents" / "backend" / "status.json").write_text(json.dumps({
        "account": "alpha", "collected_at": int(time.time()),
        "session_id": "sess-cold", "pct_5h": 20}))
    now = int(time.time())
    db.add_event(con, "backend", "Stop", {})
    con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                (now - dmn_states.RESUME_WARM_S - 60,))
    return status.read_for_agent("backend", "alpha"), now


def test_a_cold_session_close_that_fails_falls_back_to_typing(home: Path) -> None:
    """T84 review #2391: a close that keeps failing must not hold the answer
    forever - it is typed as on main, and the close is not tried every tick."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send, dmn_lifecycle.close_agent)
    tries: list[int] = []
    try:
        st, now = _cold_backend(home, con, sent)
        rid = db.add_request(con, "backend", "reviewer", "spec?", None,
                             "peer", "async", None, 0, None)
        con.execute("UPDATE requests SET state = 'done', verdict = 'yes' WHERE id = ?", (rid,))
        dmn_lifecycle.close_agent = lambda *a, **k: (tries.append(1) or (False, "refused"))
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("cold close fails: the failing tick types nothing into the cold session",
              not sent and tries, str((sent, tries)))
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now + 5, {}, [])
        check("cold close fails: the next tick types the answer, and does not close again",
              any("yes" in t for t in sent), str((sent, tries)))
        check("cold close fails: and the cold path does not try the close again",
              db.get_cursor(con, "cold_close_try:backend") == now, str(tries))
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send, dmn_lifecycle.close_agent = real
        con.close()


def test_a_cold_session_with_a_head_or_a_nudge_due_is_closed_once(home: Path) -> None:
    """T84 review #2391: an untold head closes a cold session instead of being
    typed; a forgotten head closes it once, not again after the fresh session
    is told the head and sits idle cold."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send, dmn_lifecycle.close_agent)
    session_changed = dmn_delivery._session_changed
    try:
        st, now = _cold_backend(home, con, sent)
        log = lambda _m: None
        rid = db.add_request(con, "marina", "backend", "carry on", None,
                             "peer", "async", None, 0, None)
        row = db.get_agent_state(con, "backend")
        check("cold head: an untold head closes the cold session",
              dmn_lifecycle._cold_before_typing(con, "backend", row, now, st, log) is True
              and db.get_agent_state(con, "backend")["closed_at"]
              and not any("carry on" in t for t in sent), str(sent))

        # Fresh session told the head, forgotten: idle and cold again. The
        # real close is inside the previous close's settle window, so stub it.
        dmn_lifecycle.close_agent = lambda *a, **k: (True, "closed")
        db.clear_closed(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.set_cursor(con, "notify:backend", rid)
        dmn_delivery._session_changed = lambda *a: False   # the fresh session was told it
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (now - 4000, rid))
        row = db.get_agent_state(con, "backend")
        st = status.read_for_agent("backend", "alpha")
        check("cold nudge: a forgotten head closes the cold session",
              dmn_lifecycle._cold_before_typing(con, "backend", row, now, st, log) is True, str(sent))
        db.clear_closed(con, "backend")
        db.set_cursor(con, "notify:backend", rid)   # the woken session is told the head again
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        row = db.get_agent_state(con, "backend")
        check("cold nudge: the same head does not close it again",
              dmn_lifecycle._cold_before_typing(con, "backend", row, now + 4000, st, log) is None)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send, dmn_lifecycle.close_agent = real
        dmn_delivery._session_changed = session_changed
        con.close()


def test_a_request_already_told_still_closes_a_cold_idle_session(home: Path) -> None:
    """T154 (founder #789), auditor #821: should_close's queued-work guard
    used to hold a session open for as long as anything about it was
    unresolved, delivered or not - even past its prompt cache's life, where a
    resume costs the same re-read a wake's cold start does anyway.
    `_cold_before_typing` declines here (the request was already told, and
    already nudged once) - it is `should_close`'s own idle window that closes
    it. A request already told before the close is not on its own a reason to
    wake it back up (that would close it right back up next window, forever,
    #821) - only something new is: an undelivered request or an answer, and
    that wake also retells what was already open, once its fresh session is
    on screen (`_session_changed`)."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send)
    session_changed = dmn_delivery._session_changed
    try:
        st, now = _cold_backend(home, con, sent)
        rid = db.add_request(con, "marina", "backend", "carry on", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)
        db.set_cursor(con, "notify:backend", rid)
        db.set_notified_session(con, "backend", "sess-cold")
        db.set_cursor(con, "nudge:backend", rid)   # already reminded once; nothing left to nudge for
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?",
                    (now - dmn_states.RESUME_WARM_S - 60, rid))
        row = db.get_agent_state(con, "backend")

        check("cold queue close: the cold path alone has nothing to close it for",
              dmn_lifecycle._cold_before_typing(con, "backend", row, now, st, lambda _m: None) is None)
        check("cold queue close: should_close does, past its own idle window",
              dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))

        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("cold queue close: the tick closes it",
              db.get_agent_state(con, "backend")["closed_at"], str(sent))
        check("cold queue close: the request itself is untouched, left for the wake",
              [r["id"] for r in db.queued_for(con, "backend")] == [rid])

        # Nothing new for it: ticks pass, cache stays gone, and it stays
        # closed rather than relaunching for the same open request forever.
        del sent[:]
        for later in (now + 5, now + 3600, now + 7200):
            dmn_evaluate.supervise(con, "backend", lambda _m: None, later, {}, [])
        check("cold queue close: not woken again for the request it already told",
              db.get_agent_state(con, "backend")["closed_at"] and not sent, str(sent))

        # Something new arrives - the wake it deserves, and it retells the
        # old request too, once its fresh session is on screen.
        db.add_request(con, "marina", "backend", "and one more thing", None,
                       "peer", "async", None, 0, None)
        dmn_delivery._session_changed = lambda *a: True   # the wake's fresh session, once up
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now + 7205, {}, [])
        check("cold queue close: the new request wakes it",
              any(" launch " in t for t in sent), str(sent))
        del sent[:]
        db.add_event(con, "backend", "Stop", {})   # the fresh session's own first turn
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend' AND id = "
                    "(SELECT MAX(id) FROM events WHERE agent = 'backend')", (now + 7208,))
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now + 7210, {}, [])
        check("cold queue close: the old, already-told request is retold first",
              any("carry on" in t for t in sent), str(sent))
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.available, orcamod.send = real
        dmn_delivery._session_changed = session_changed
        con.close()


def test_a_degraded_park_does_not_auto_clear(home: Path) -> None:
    """DEGRADED is not a usage park. `DEGRADED_RETRY_S` is the only thing
    that re-hands a failed handoff's own retry, and clearing it early on a
    usage reading alone - which is not what parked this agent - would drop
    that deadline instead.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage", hoff_state="degraded")
        db.set_agent_state(con, "backend", dmn_states.DEGRADED,
                           wake_at=int(time.time()) + 3600, detail="handoff timed out")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: DEGRADED is not scoped in - it keeps its own deadline",
              row["state"] == dmn_states.DEGRADED and row["wake_at"] is not None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_a_close_gets_past_the_dialog_before_it_types_exit(home: Path) -> None:
    """Every path that ends a session - idle close, move, relaunch - goes
    through the one function, so the dialog is got past there and not only
    on a park."""
    con, term = _park_behind(home, LIMIT_DIALOG)
    orcamod.close_session = REAL_ORCA["close_session"]
    real = dmn_delivery._wait_session_gone
    dmn_delivery._wait_session_gone = lambda agent: True
    try:
        st = status.read_for_agent("keeper", "alpha")
        ok, why = dmn_lifecycle._end_session(con, "keeper", lambda _m: None, st, resume=None)
        check("close through: it ended", ok, why)
        check("close through: Esc, then /exit", [t for t, _ in term.sent] == ["\x1b", "/exit"], str(term.sent))
    finally:
        dmn_delivery._wait_session_gone = real
        con.close()


def test_a_dialog_ocp_cannot_get_past_stalls_the_agent_with_the_reason(home: Path) -> None:
    """Not deferred as 'mid-turn' tick after tick: the first sighting waits, and
    one that has stood as long as a block takes to reach the founder stops the
    agent with the dialog's own words. The usage-limit dialog with an Esc that
    does nothing is the one a handoff presses, so it is the one that can stand."""
    con, term = _park_behind(home, LIMIT_DIALOG, ends_at_limit=False, ignores_esc=True)
    lines: list[str] = []
    try:
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lines.append)
        row = db.get_agent_state(con, "keeper")
        check("unpassable: at first it waits, and says a dialog is why, not a turn",
              row["state"] != dmn_states.STALLED and any("a dialog is in the way" in m for m in lines)
              and not any("mid-turn" in m for m in lines), str(lines))
        db.set_cursor(con, "dialog_since:keeper", int(time.time()) - blockedmod.ESCALATE_AFTER_S - 1)
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lines.append)
        row = db.get_agent_state(con, "keeper")
        check("unpassable: past the wait it is STALLED, with the dialog in the reason",
              row["state"] == dmn_states.STALLED and "What do you want to do?" in row["detail"], str(dict(row)))
        check("unpassable: only Esc was ever typed at it", {t for t, _ in term.sent} == {"\x1b"}, str(term.sent))
    finally:
        con.close()


def test_a_working_agents_dialog_is_not_pressed_by_a_handoff_that_has_not_begun(home: Path) -> None:
    """A permission prompt on a working agent is a person's to answer, and
    perform_handoff deciding whether to begin is not ending anything. Esc is
    admitted for that dialog (it names Esc itself), so what keeps it unpressed
    is the caller."""
    con, term = _park_behind(home, EXIT_DIALOG, ends_at_limit=False)
    lines: list[str] = []
    try:
        dmn_handoff.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lines.append)
        check("working: nothing is pressed", term.sent == [], str(term.sent))
        check("working: it is deferred as a turn, and is not stalled however long it stands",
              any("mid-turn" in m for m in lines)
              and db.get_agent_state(con, "keeper")["state"] != dmn_states.STALLED, str(lines))
    finally:
        con.close()


def test_each_dialog_that_cannot_be_passed_starts_its_own_clock(home: Path) -> None:
    """The stall measures how long THIS dialog has stood: one that stood for
    ten minutes and was answered does not stall the agent the moment a
    different one appears."""
    con, term = _park_behind(home, LIMIT_DIALOG, ends_at_limit=False)
    first = [_RULE_ROW, "   Apply the migration?", "   ❯ 1. Yes, apply it",
             "     2. Yes, and never ask again", YES_NO_DIALOG[-1]]
    second = [ln.replace("Apply the migration?", "Delete the branch?") for ln in first]
    try:
        term.tail = list(first)
        check("clock: an unpassable dialog is a reason", dmn_delivery._past_dialog(con, "keeper", lambda _m: None))
        db.set_cursor(con, "dialog_since:keeper", int(time.time()) - blockedmod.ESCALATE_AFTER_S - 1)
        check("clock: standing that long, it stalls",
              dmn_delivery._stalled_on_dialog(con, "keeper", "x", lambda _m: None))
        db.set_agent_state(con, "keeper", dmn_states.IDLE)
        term.tail = list(second)
        check("clock: a different dialog is still a reason", dmn_delivery._past_dialog(con, "keeper", lambda _m: None))
        check("clock: but has stood for seconds, so it does not stall",
              not dmn_delivery._stalled_on_dialog(con, "keeper", "y", lambda _m: None))
    finally:
        con.close()


def test_dialogs_whose_titles_did_not_parse_do_not_share_a_clock(home: Path) -> None:
    """Two unparseable dialogs cannot be told apart, so the second is never
    counted as the first: it starts its own clock rather than stalling at once."""
    con, _term = _park_behind(home, LIMIT_DIALOG, ends_at_limit=False)
    real = blockedmod.past_dialog
    blockedmod.past_dialog = lambda *_a, **_k: ("its title or options could not be parsed", "")
    try:
        dmn_delivery._past_dialog(con, "keeper", lambda _m: None)
        db.set_cursor(con, "dialog_since:keeper", int(time.time()) - blockedmod.ESCALATE_AFTER_S - 1)
        dmn_delivery._past_dialog(con, "keeper", lambda _m: None)
        check("clock: an unparsed dialog never inherits the last one's clock",
              not dmn_delivery._stalled_on_dialog(con, "keeper", "z", lambda _m: None))
    finally:
        blockedmod.past_dialog = real
        con.close()


def test_a_dialog_on_its_last_try_says_which_dialog_in_the_stall(home: Path) -> None:
    """The stall that repeated failed handoffs end in named a count and nothing
    else, so the person who found it did not know a dialog was the cause."""
    con = _blocked_world(home)
    try:
        db.block_open(con, "backend", orcamod.MODAL, '"Overwrite the file?" - 1. Yes / 2. No',
                      "request #1", int(time.time()), show_at=0, origin="close", title="Overwrite the file?")
        note = blockedmod.dialog_note(con, "backend")
        check("stall note: names the dialog", "Overwrite the file?" in note, note)
        db.block_clear(con, "backend")
        check("stall note: nothing to add when no dialog is the reason",
              blockedmod.dialog_note(con, "backend") == "")
    finally:
        con.close()


def test_a_pinned_agent_stays_pinned_when_nothing_else_has_clear_room(home: Path) -> None:
    """The rescue only fires when another account genuinely has clear room -
    with nothing better, the agent still waits out its own window, exactly
    as before this fix."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    con = db.connect()
    real = orcamod.available
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        support.something_waiting(con, "keeper")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        write_status(home, "beta", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        orcamod.available = lambda: True
        ok, why = dmn_lifecycle._wake_agent(con, "keeper", lambda _m: None)
        check("pinning: with nothing better, it still waits for its own window",
              not ok and "alpha" in why and "5h window" in why, why)
        row = db.get_agent_state(con, "keeper")
        check("pinning: resume_session is untouched when nothing rescues it",
              row["resume_session"] == "sess-old", str(dict(row)))
    finally:
        orcamod.available = real
        con.close()


def test_wake_on_a_self_clearing_park_refuses_and_says_when(home: Path) -> None:
    """actions.can()/daemon.park_wakeable(): the property from #1620/#1625 -
    only a park a person started by hand is worth `w` forcing early. Proof:
    one check per key behaviour, failing when its own line is reverted."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        wake = int(time.time()) + 900
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=wake, detail="usage; wakes at x")
        ok, why = actions.can(con, "backend", "wake")
        check("wake: a usage-wall park refuses, and says when it clears itself",
              not ok and time.strftime("%H:%M", time.localtime(wake)) in why, why)

        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=wake, detail="moving; wakes at x")
        ok, why = actions.can(con, "backend", "wake")
        check("wake: a moving (short-retry) park refuses with its own reason, "
              "not the usage-wall's window-rollover sentence beside it",
              not ok and "moving accounts" in why and "window rolls over" not in why, why)

        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=wake,
                           detail=askmod.await_reply_detail(99, "keeper"))
        ok, why = actions.can(con, "backend", "wake")
        check("wake: a park awaiting its own reply refuses, naming the reply",
              not ok and "reply" in why, why)

        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=wake, detail="by hand; wakes at x")
        ok, why = actions.can(con, "backend", "wake")
        check("wake: a park started by hand is the one that stays wakeable", ok, why)

        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="ran out")
        ok, why = actions.can(con, "backend", "wake")
        check("wake: FATAL (STALLED) is wakeable too - this is what unstall did", ok, why)
        said = actions.wake(con, "backend")
        check("wake: pressing it on FATAL actually clears the stall (unstall, under wake)",
              "STALLED -> QUEUED" in said, said)
        check("wake: ...and the row really moved",
              db.get_agent_state(con, "backend")["state"] == dmn_states.QUEUED)
    finally:
        con.close()


def test_orca_idle_parsing() -> None:
    """`ok` says the wait command ran; `satisfied` says the agent is idle.

    Reading `ok` alone treats a timeout as idle, and clearing mid-turn throws
    away work the fold has not recorded yet.
    """
    real = orcamod._run
    try:
        orcamod._run = lambda *a, **k: (True, json.dumps(
            {"ok": True, "result": {"wait": {"satisfied": True}}}))
        check("orca: satisfied=true reads as idle", orcamod.wait_idle("h", 1000))

        orcamod._run = lambda *a, **k: (True, json.dumps(
            {"ok": True, "result": {"wait": {"satisfied": False}}}))
        check("orca: a timed-out wait is not idle", not orcamod.wait_idle("h", 1000))

        orcamod._run = lambda *a, **k: (False, "boom")
        check("orca: a failed wait is not idle", not orcamod.wait_idle("h", 1000))

        orcamod._run = lambda *a, **k: (True, "not json at all")
        check("orca: unparseable output is not idle", not orcamod.wait_idle("h", 1000))
    finally:
        orcamod._run = real


def test_confirm_trust_presses_down_then_enter() -> None:
    """Request 156 item 1b's two keys, measured against a live prompt (see
    claude.py's own comment on TRUST_PROMPT_DOWN_ARROW): one Down off the
    default 'No, exit' lands on 'Yes, I trust this folder', and a bare Enter
    after that confirms it. Wrong order or a missing key each press whatever
    the cursor happened to be sitting on instead."""
    from ocp.vendors import claude as claudemod
    calls: list[list[str]] = []
    real = orcamod._run
    try:
        orcamod._run = lambda args, *a, **k: (calls.append(args), (True, "{}"))[1]
        ok, why = orcamod.confirm_trust("term_x")
        check("confirm_trust: reports success", ok, why)
        check("confirm_trust: sends exactly two commands", len(calls) == 2, str(calls))
        check("confirm_trust: the first moves down, with no --enter",
              calls[0] == ["terminal", "send", "--terminal", "term_x",
                           "--text", claudemod.TRUST_PROMPT_DOWN_ARROW],
              str(calls[0]))
        check("confirm_trust: the second is a bare Enter",
              calls[1] == ["terminal", "send", "--terminal", "term_x", "--text", "", "--enter"],
              str(calls[1]))
    finally:
        orcamod._run = real

    real = orcamod._run
    try:
        orcamod._run = lambda *a, **k: (False, "no such terminal")
        ok, why = orcamod.confirm_trust("term_x")
        check("confirm_trust: a failed first key is reported, not silently retried",
              not ok and "trust dialog" in why, why)
    finally:
        orcamod._run = real


def test_clear_refuses_mid_turn() -> None:
    real_alive, real_wait, real_send = orcamod.alive, orcamod.wait_idle, orcamod.send
    sent = []
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.send = lambda h, t, enter=True: (sent.append(t), (True, ""))[1]

        orcamod.wait_idle = lambda h, t=0: False
        ok, why = orcamod.clear_context("h")
        check("orca: refuses to clear a busy agent", not ok and "mid-turn" in why, why)
        check("orca: and sends nothing", sent == [], str(sent))

        orcamod.wait_idle = lambda h, t=0: True
        ok, why = orcamod.clear_context("h")
        check("orca: clears an idle agent", ok, why)
        check("orca: by sending /clear", sent == ["/clear"], str(sent))

        orcamod.available = lambda: True
        orcamod.alive = lambda h: False
        ok, why = orcamod.clear_context("h")
        check("orca: will not clear a dead terminal", not ok, why)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.send = real_alive, real_wait, real_send


def test_a_context_handoff_backs_off_when_the_clear_keeps_failing(home: Path) -> None:
    """Should-fix from #1957: an immediate wake is only safe once the context
    is actually reclaimed. A /clear that keeps failing leaves the agent over
    its limit, so the trigger fires again next tick - nothing stopped that
    from spinning at full cadence forever, since only a failed record ever
    counted toward the degraded streak. A failed clear must count too.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.clear_context, orcamod.alive, orcamod.idle_now)
    try:
        db.set_account(con, "backend", "alpha")
        write_status(home, "alpha", pct_5h=19, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 9000)
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (False, "still mid-turn")
        readable_agent(con, home, "backend")
        sessions_are("backend")

        t = triggers.Trigger(True, "context", "context 700,000 >= 120,000")
        before = int(time.time())
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("failed clear: counts toward the degraded streak",
              row["throttle_count"] == 1, str(dict(row)))
        check("failed clear: backs the wake off rather than retrying at once",
              row["state"] == dmn_states.PARKED and row["wake_at"] is not None
              and row["wake_at"] > before + 30, str(dict(row)))
    finally:
        orcamod.clear_context, orcamod.alive, orcamod.idle_now = real
        con.close()


def test_repeated_failed_clears_reach_the_throttle_cap(home: Path) -> None:
    """#1975: a good record used to reset the streak by itself, so a clear
    that kept failing right after it never accumulated - throttle_count sat
    pinned at 1 and 8.6's STALLED escalation was unreachable for this case.
    Driving MAX_THROTTLE_STREAK failed-clear handoffs is the test #1975 asked
    for: it must fail if `_record_handoff` goes back to resetting on its own.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.clear_context, orcamod.alive, orcamod.idle_now)
    try:
        db.set_account(con, "backend", "alpha")
        write_status(home, "alpha", pct_5h=19, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 9000)
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (False, "still mid-turn")
        readable_agent(con, home, "backend")
        sessions_are("backend")

        t = triggers.Trigger(True, "context", "context 700,000 >= 120,000")
        for _ in range(dmn_states.MAX_THROTTLE_STREAK):
            # Hours apart in reality; backdate so the too-soon guard does not
            # skip a handoff that just fired (same as test_daemon_stall_escalation).
            con.execute("UPDATE handoff SET updated = updated - ? WHERE agent = 'backend'",
                        (dmn_states.MIN_HANDOFF_INTERVAL_S * 2,))
            dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("repeated failed clears: the streak reaches the cap, not pinned at 1",
              row["throttle_count"] == dmn_states.MAX_THROTTLE_STREAK, str(dict(row)))
    finally:
        orcamod.clear_context, orcamod.alive, orcamod.idle_now = real
        con.close()


def test_a_resumed_handoff_with_a_good_record_clears_the_streak(home: Path) -> None:
    """resume_handoff's own reset (step "requested" never runs a clear, so a
    good record is the whole of what it can vouch for) - covered on its own,
    since this path never reaches `_perform_handoff`'s record+clear reset."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        for _ in range(3):
            db.bump_counter(con, "backend", "throttle_count")
        readable_agent(con, home, "backend")
        db.start_handoff(con, "hoff-restart", "backend", None, "usage",
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")

        dmn_handoff.resume_handoff(con, row, lambda _m: None)
        got = db.get_agent_state(con, "backend")
        check("resumed handoff: a good record clears the streak on its own",
              got["throttle_count"] == 0, str(dict(got)))
    finally:
        con.close()


def test_a_queued_agents_blocked_reminder_surfaces_like_an_idle_ones(home: Path) -> None:
    """The other half of #83: `blocked.py`'s own row for a reminder that
    cannot land (`test_a_reminder_a_dialog_blocks_is_said` above, for IDLE)
    reads `forgotten_resolve` through `work_waiting` - so once that admits
    QUEUED, a QUEUED agent whose reminder is blocked by a dialog gets the
    same `delivery_blocks` row, the same bridge line, and the same
    escalation at `ESCALATE_AFTER_S` that an IDLE one already does."""
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    sessions_are("backend")
    con = db.connect()
    now = int(time.time())
    try:
        db.set_agent_state(con, "backend", dmn_states.QUEUED)
        head = db.add_request(con, "marina", "backend", "the first thing", None,
                              "peer", "async", None, 0, None)
        db.mark_delivered(con, head)
        db.set_cursor(con, "notify:backend", head)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 3600,))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (now - 3600, head))
        orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, '"Continue?"')
        blockedmod.sweep(con, lambda _m: None, now)
        rec = db.block_get(con, "backend")
        check("queued reminder: a dialog on a QUEUED agent's screen is recorded too",
              rec and rec["kind"] == "modal" and rec["detail"] == '"Continue?"'
              and f"reminder to resolve #{head}" in rec["work"], str(rec and dict(rec)))
    finally:
        con.close()


def test_a_parked_agent_with_queued_work_is_still_closed_while_idle(home: Path) -> None:
    """#83: a PARKED agent already had its one chance at delivery this same
    tick - `deliver_queued` runs before `should_close` in `supervise`, and a
    usage/forced park is walled, and a context/backoff/hand park with an open
    session was just delivered to - so by the time `should_close` runs,
    refusing over the queue only holds a session open that either could not
    be delivered to, or already was. A closed PARKED agent's wake
    already resumes the same session by its own session id (`_wake_agent`'s
    `closed` branch), so nothing about waking it back up is worse for having
    been closed. T154: an IDLE agent is closed anyway once the cache is gone
    - a resume then costs what the wake's cold start would anyway."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.add_request(con, "marina", "backend", "carry on", None,
                       "peer", "async", None, 0, None)
        row = db.get_agent_state(con, "backend")
        st = status_of("alpha", exists=True, session_id="sess-1")
        warm = int(time.time()) + dmn_states.IDLE_CLOSE_S + 100
        check("parked+queued close: an IDLE agent with queued work and a warm cache is not closed - "
              "it is about to be delivered to instead",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, warm, st))
        check("parked+queued close: a PARKED agent with the same queued work is - "
              "it already had its one shot at delivery this tick",
              dmn_lifecycle.should_close(con, "backend", dmn_states.PARKED, row, warm, st))
        cold = int(time.time()) + dmn_states.RESUME_WARM_S + 100
        check("parked+queued close: an IDLE agent is closed too once its cache has expired - "
              "the wake redelivers what is still queued",
              dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, cold, st))
    finally:
        con.close()


def test_a_wake_reads_the_screen_before_typing_into_it(home: Path) -> None:
    """The cursor on claude's folder-trust screen defaults to "No, exit" - a
    handoff typed there, followed by Enter, chooses exit every time, and the
    next wake finds the same screen again. Read before typing catches it
    instead of discovering it from the loop.
    """
    _daemon_home(home, 50)
    con = db.connect()
    real = (orcamod.resume_with, orcamod.alive, orcamod.trust_prompt_showing,
            orcamod.typeable_now)
    sent: list[str] = []
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        # As in production: screen_blocker reads the trust dialog as a modal,
        # so typeable_now is False while it shows.
        orcamod.typeable_now = lambda h: not orcamod.trust_prompt_showing(h)
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")

        orcamod.trust_prompt_showing = lambda h: True
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing is typed into a trust prompt", not ok and not sent, str((ok, sent)))
        row = db.get_agent_state(con, "backend")
        check("wake: the agent is stalled instead of retried into the same screen",
              row["state"] == dmn_states.STALLED, str(dict(row)))
        os.environ["OCP_AGENT"] = "human"
        printed = _capture(lambda: climod.main(["inbox", "human"]))
        os.environ.pop("OCP_AGENT", None)
        check("wake: and the founder's own queue is told, not just the screen",
              "trust" in printed.lower() and "backend" in printed, printed)

        orcamod.trust_prompt_showing = lambda h: False
        db.set_agent_state(con, "backend", dmn_states.PARKED, detail="reset for the next case")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: an ordinary screen is typed into as before", ok and sent, why)
    finally:
        (orcamod.resume_with, orcamod.alive, orcamod.trust_prompt_showing,
         orcamod.typeable_now) = real
        con.close()


def test_a_resume_that_fails_falls_back_to_fresh_and_the_head_is_told_after(home: Path) -> None:
    """T125: the head is marked delivered before the resume launches, with the
    message as its prompt. When that resume fails and a fresh start follows,
    the fresh session was not handed it: nothing is recorded as told, and the
    next tick tells it the ordinary way."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    launched: list[str] = []
    real = (orcamod.relaunch, orcamod.alive, orcamod.available, orcamod.send,
            orcamod.typeable_now, dmn_delivery.wait_for_session)
    table = sessions_are()
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.typeable_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]

        def relaunch(handle, cmd, started=None):
            launched.append(cmd)
            if "--resume" in cmd:
                return False, "the resume was refused"
            table.update({"backend": 4242})
            return True, "relaunched"
        orcamod.relaunch = relaunch
        dmn_delivery.wait_for_session = lambda agent, timeout=0, prompt=None: True
        db.set_terminal(con, "backend", "term_x", "wt")
        keep_handoff("backend", "# handoff · backend\n- was mid-way")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.set_closed(con, "backend", "sess-1")
        head = db.add_request(con, "reviewer", "backend", "look at the picker", None,
                              "peer", "async", None, 0, None)
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("fallback: the resume was tried, then a fresh start", ok
              and len(launched) == 2 and "--resume sess-1" in launched[0]
              and "--resume" not in launched[1], f"{why} {launched}")
        check("fallback: the head is marked delivered but is not recorded as told",
              db.get_request(con, head)["delivered_at"] is not None
              and not db.get_cursor(con, "notify:backend")
              and not db.get_notified_session(con, "backend"))
        check("fallback: and the fresh session was not handed it as a prompt",
              not any("look at the picker" in launched_prompt(c) for c in launched))
        _session_file(home, "sess-new")
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("fallback: the next tick tells it, once",
              len([t for t in sent if "look at the picker" in t]) == 1
              and db.get_cursor(con, "notify:backend") == head, str(sent))
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("fallback: and not again",
              len([t for t in sent if "look at the picker" in t]) == 1, str(sent))
    finally:
        (orcamod.relaunch, orcamod.alive, orcamod.available, orcamod.send,
         orcamod.typeable_now, dmn_delivery.wait_for_session) = real
        con.close()


def test_a_message_launched_behind_the_trust_screen_is_not_told_twice(home: Path) -> None:
    """T126: a resume launched with the message as its prompt, and then the
    trust screen refuses the wake. The session reads the message once past the
    screen; when the sweep later sees it running and clears the closed mark, that
    must not send the head again."""
    _daemon_home(home, 20)
    con = db.connect()
    wt = home / "worktree"
    wt.mkdir(parents=True, exist_ok=True)
    screen = (f"Accessing workspace:\n{wt}\n"
              f"Quick safety check: Is this a project you created or one you trust?\n"
              f"No, exit\nYes, I trust this folder\n")
    sent: list[str] = []
    real = (orcamod.relaunch, orcamod.alive, orcamod.available, orcamod.send, orcamod.typeable_now,
            orcamod.trust_prompt_showing, orcamod.screen_text, orcamod.wait_idle, trustmod.is_declared,
            dmn_delivery.wait_for_session, orcamod.time.sleep)
    table = sessions_are()
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, *a, **k: True
        orcamod.time.sleep = lambda s: None
        orcamod.relaunch = lambda h, cmd, started=None: (
            table.update({"backend": 4242}), (True, "relaunched"))[1]
        dmn_delivery.wait_for_session = lambda agent, timeout=0, prompt=None: True
        orcamod.trust_prompt_showing = lambda h: True
        orcamod.screen_text = lambda h: screen
        trustmod.is_declared = lambda p: False
        db.set_terminal(con, "backend", "term_x", str(wt))
        keep_handoff("backend", "# handoff · backend\n- carry on")
        db.set_agent_state(con, "backend", dmn_states.PARKED, detail="parked")
        db.set_closed(con, "backend", "sess-1")
        con.execute("UPDATE agent_state SET closed_at = closed_at - 100 WHERE agent = 'backend'")
        head = db.add_request(con, "reviewer", "backend", "look at the picker", None,
                              "peer", "async", None, 0, None)
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("trust: the wake is refused at the trust screen", not ok, why)
        check("trust: but the message it launched with is on record as told",
              db.get_cursor(con, "notify:backend") == head
              and db.get_notified_session(con, "backend") == "sess-1")
        db.clear_closed(con, "backend")            # the sweep sees a session running
        check("trust: and clearing the closed mark keeps it",
              db.get_cursor(con, "notify:backend") == head)
        orcamod.typeable_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        _session_file(home, "sess-1")
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("trust: so it is not told again", not sent, str(sent))
        # A tell made before the close is still voided, as T102 has it.
        db.set_closed(con, "backend", "sess-1")
        db.set_cursor(con, "notify:backend", head)
        con.execute("UPDATE cursors SET updated = updated - 1000 WHERE name = 'notify:backend'")
        db.clear_closed(con, "backend")
        check("trust: a tell from before the close is still voided",
              not db.get_cursor(con, "notify:backend"))
    finally:
        (orcamod.relaunch, orcamod.alive, orcamod.available, orcamod.send, orcamod.typeable_now,
         orcamod.trust_prompt_showing, orcamod.screen_text, orcamod.wait_idle, trustmod.is_declared,
         dmn_delivery.wait_for_session, orcamod.time.sleep) = real
        con.close()


def test_a_closed_agents_relaunch_stops_at_the_trust_screen(home: Path) -> None:
    """auditor's #1630 BLOCKING: a closed agent is woken by a relaunch that
    carries its prompt in --prompt-file, and a relaunch - on a moved agent, a
    config dir that has never trusted the folder - is where the trust screen
    appears. The wake is not done while claude sits at it: under standing
    consent it is pressed, otherwise it escalates naming the folder, and the
    agent stays closed with its handoff kept."""
    _daemon_home(home, 20)
    con = db.connect()
    wt = home / "worktree"
    wt.mkdir(parents=True, exist_ok=True)
    screen = (f"Accessing workspace:\n{wt}\n"
              f"Quick safety check: Is this a project you created or one you trust?\n"
              f"No, exit\nYes, I trust this folder\n")
    real = (orcamod.relaunch, orcamod.alive, orcamod.trust_prompt_showing, orcamod.screen_text,
            orcamod.confirm_trust, orcamod.wait_idle, trustmod.is_declared,
            dmn_delivery.wait_for_session, orcamod.time.sleep)
    showing = {"v": True}
    trusted: list[str] = []
    table = sessions_are()
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, *a, **k: True
        orcamod.time.sleep = lambda s: None
        orcamod.relaunch = lambda h, cmd, started=None: (
            table.update({"backend": 4242}), (True, "relaunched"))[1]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        orcamod.trust_prompt_showing = lambda h: showing["v"]
        orcamod.screen_text = lambda h: screen
        orcamod.confirm_trust = lambda h: (trusted.append(h), showing.update(v=False),
                                           (True, "trusted"))[2]
        db.set_terminal(con, "backend", "term_x", str(wt))

        def closed_with_a_handoff():
            table.clear()
            showing["v"] = True
            keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")
            db.set_agent_state(con, "backend", dmn_states.PARKED, detail="parked")
            db.set_closed(con, "backend", "sess-1")
            support.something_waiting(con, "backend")

        closed_with_a_handoff()
        trustmod.is_declared = lambda p: False
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("closed wake: an undeclared trust screen is not recorded as woken",
              not ok and str(wt) in why, why)
        check("closed wake: it is escalated to the founder, naming the folder",
              con.execute("SELECT COUNT(*) c FROM requests WHERE target = ? AND kind = "
                          "'escalate' AND body LIKE ?", (askmod.HUMAN, f"%{wt}%")
                          ).fetchone()["c"] == 1)
        check("closed wake: and stalled", row["state"] == dmn_states.STALLED, dict(row))
        check("closed wake: it stays closed", row["closed_at"] is not None, dict(row))
        check("closed wake: and keeps its handoff", db.get_handoff(con, "backend"), dict(row))
        check("closed wake: nothing was pressed", not trusted, trusted)

        closed_with_a_handoff()
        trustmod.is_declared = lambda p: True
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("closed wake: a declared, matching folder is pressed",
              trusted == ["term_x"], trusted)
        check("closed wake: then it is woken", ok and row["closed_at"] is None,
              str((ok, why, dict(row))))
    finally:
        (orcamod.relaunch, orcamod.alive, orcamod.trust_prompt_showing, orcamod.screen_text,
         orcamod.confirm_trust, orcamod.wait_idle, trustmod.is_declared,
         dmn_delivery.wait_for_session, orcamod.time.sleep) = real
        con.close()


def test_hooks_report_activity(home: Path) -> None:
    """A working agent must not still read as PARKED.

    agent_state otherwise only changes when the daemon changes it, so an agent
    that was parked and then given work directly stays PARKED - and the daemon
    would "wake" it mid-task by injecting a handoff it never needed.
    """
    orcamod.live_terminals = lambda: None    # doctor.run() below asks Orca; nothing here is about it
    use_home(home)
    agents.register("hooky")
    env = dict(os.environ, OCP_AGENT="hooky", OCP_ACCOUNT="alpha", OCP_HOME=str(home))

    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn_states.PARKED, wake_at=int(time.time()) + 9999,
                           detail="parked earlier")
    finally:
        con.close()

    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s",
                          "prompt": "do the thing"})
    subprocess.run([str(ROOT / "bin" / "ocp"), "hook", "UserPromptSubmit"],
                   input=payload, capture_output=True, text=True, env=env)

    con = db.connect()
    try:
        row = db.get_agent_state(con, "hooky")
        check("hooks: a prompt moves the agent to RUNNING", row["state"] == "RUNNING",
              str(dict(row)))
        check("hooks: and clears the stale wake_at", row["wake_at"] is None, str(dict(row)))

        # STALLED is the daemon's to clear, not the agent's.
        db.set_agent_state(con, "hooky", dmn_states.STALLED, detail="needs a human")
    finally:
        con.close()
    subprocess.run([str(ROOT / "bin" / "ocp"), "hook", "UserPromptSubmit"],
                   input=payload, capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        check("hooks: activity does not clear STALLED",
              db.get_agent_state(con, "hooky")["state"] == dmn_states.STALLED)
    finally:
        con.close()

    # A mid-handoff agent must not be yanked back to RUNNING either.
    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn_states.HANDOFF, detail="handing off")
    finally:
        con.close()
    subprocess.run([str(ROOT / "bin" / "ocp"), "hook", "SessionStart"],
                   input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}),
                   capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        check("hooks: activity does not interrupt a handoff",
              db.get_agent_state(con, "hooky")["state"] == dmn_states.HANDOFF)
    finally:
        con.close()

    # The other end of the turn. RUNNING is what stops work being delivered and
    # stops a session being closed, so an agent that entered it and never left
    # was taken out of service without anything saying so: three agents sat
    # RUNNING for over two hours, told about no request and holding their memory.
    def hook(name: str, **extra) -> None:
        subprocess.run([str(ROOT / "bin" / "ocp"), "hook", name],
                       input=json.dumps({"hook_event_name": name, "session_id": "s", **extra}),
                       capture_output=True, text=True, env=env)

    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn_states.RUNNING, detail="working")
    finally:
        con.close()
    hook("SubagentStop")
    con = db.connect()
    try:
        check("hooks: a subagent finishing does not mean the agent is free",
              db.get_agent_state(con, "hooky")["state"] == dmn_states.RUNNING,
              str(db.get_agent_state(con, "hooky")["state"]))
    finally:
        con.close()

    hook("Stop")
    con = db.connect()
    try:
        row = db.get_agent_state(con, "hooky")
        check("hooks: the end of a turn returns it to IDLE",
              row["state"] == dmn_states.IDLE, str(dict(row)))

        # The other way in. `ocp launch` records the terminal handle before
        # execve, and that row used to be born RUNNING - so a new agent was out
        # of service before it had done anything, and _mark_activity then
        # declined to rewrite a state that was already RUNNING.
        db.set_terminal(con, "newborn", "term_n", "wt")
        born = db.get_agent_state(con, "newborn")
        check("launch: recording a terminal does not claim the agent is working",
              born["state"] == dmn_states.IDLE, str(dict(born)))
        check("launch: and it can be closed and told about work like any idle one",
              born["state"] not in (dmn_states.RUNNING, dmn_states.HANDOFF))

        db.set_agent_state(con, "newborn", dmn_states.RUNNING, detail="working")
        db.set_terminal(con, "newborn", "term_n2", "wt")
        check("launch: and a later handle does not reset a state it knows nothing about",
              db.get_agent_state(con, "newborn")["state"] == dmn_states.RUNNING,
              str(dict(db.get_agent_state(con, "newborn"))))
        check("hooks: and it can be told about work again",
              row["state"] not in (dmn_states.RUNNING, dmn_states.HANDOFF))

        # Being stuck in RUNNING is silent by nature, so doctor says it out loud.
        db.set_agent_state(con, "hooky", dmn_states.RUNNING, detail="working")
        con.execute("UPDATE events SET ts = ? WHERE agent = 'hooky'",
                    (int(time.time()) - dmn_states.STUCK_RUNNING_S - 60,))
        con.commit()
    finally:
        con.close()
    rows = [d for lvl, name, d in doctor.run().rows if "RUNNING with nothing heard" in name]
    check("doctor: a stuck RUNNING agent is reported", len(rows) == 1, str(rows))

    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn_states.IDLE, detail="done")
    finally:
        con.close()


def test_clear_does_not_look_like_waking(home: Path) -> None:
    """/clear ends one session and starts another. If SessionStart counted as
    activity, the daemon's own context reclaim would clear the park it had just
    set, the unchanged usage would trigger again, and the agent would hand off
    in a loop - which is exactly what happened: 42 folds in seven minutes."""
    use_home(home)
    agents.register("loopy")
    env = dict(os.environ, OCP_AGENT="loopy", OCP_ACCOUNT="alpha", OCP_HOME=str(home))

    con = db.connect()
    try:
        db.set_agent_state(con, "loopy", dmn_states.PARKED, wake_at=int(time.time()) + 9999,
                           detail="parked by a handoff")
    finally:
        con.close()

    for event in ("SessionEnd", "SessionStart"):
        subprocess.run([str(ROOT / "bin" / "ocp"), "hook", event],
                       input=json.dumps({"hook_event_name": event, "session_id": "new"}),
                       capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        row = db.get_agent_state(con, "loopy")
        check("clear: a new session does not un-park the agent",
              row["state"] == dmn_states.PARKED, str(dict(row)))
        check("clear: and the wake_at survives", row["wake_at"] is not None, str(dict(row)))
    finally:
        con.close()

    # A real prompt still means real work.
    subprocess.run([str(ROOT / "bin" / "ocp"), "hook", "UserPromptSubmit"],
                   input=json.dumps({"hook_event_name": "UserPromptSubmit",
                                     "session_id": "new", "prompt": "do it"}),
                   capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        check("clear: a prompt still marks the agent as working",
              db.get_agent_state(con, "loopy")["state"] == "RUNNING")
    finally:
        con.close()


def test_an_idle_close_keeps_a_handoff_only_if_it_closed(home: Path) -> None:
    """A wake that has to start elsewhere has no live session to read the
    transcript path from, so the close takes the record while there is one.
    A close that did not happen leaves nothing behind to be read as current."""
    _daemon_home(home, 20)
    con = db.connect()
    real = dmn_lifecycle._end_session
    try:
        readable_agent(con, home, "backend")
        st = status.read_for_agent("backend", "alpha")
        st.session_id = "sess-1"
        dmn_lifecycle._end_session = lambda *a, **k: (True, "closed")
        dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: a successful close leaves the record for the wake",
              "sess-1" in (db.get_handoff(con, "backend") or ""))
        db.set_handoff(con, "backend", None)
        dmn_lifecycle._end_session = lambda *a, **k: (False, "no session process")
        dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: one that did not happen leaves none",
              db.get_handoff(con, "backend") is None)

        # A parked agent is closed on this machine within the hour, and its park
        # record names the session from before its /clear - the one the close's
        # own snapshot no longer does.
        park = "# handoff · backend\nprevious session: sess-BEFORE-PARK"
        db.set_handoff(con, "backend", park)
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 3600)
        dmn_lifecycle._end_session = lambda *a, **k: (True, "closed")
        dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: a park's record survives the close, not replaced by a newer one",
              db.get_handoff(con, "backend") == park, str(db.get_handoff(con, "backend")))
        dmn_lifecycle._end_session = lambda *a, **k: (False, "no session process")
        dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: and a close that failed does not delete it",
              db.get_handoff(con, "backend") == park, str(db.get_handoff(con, "backend")))

        # A record missing a fact is not kept: a wake that moves elsewhere would
        # drop the resume id, the only complete copy of the context.
        db.set_handoff(con, "backend", None)
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.set_terminal(con, "backend", "term_x", str(home / "gone"))
        dmn_lifecycle._end_session = lambda *a, **k: (True, "closed")
        dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: a record that could not read its worktree is not kept",
              db.get_handoff(con, "backend") is None, str(db.get_handoff(con, "backend")))

        # A launch hands the record over, so the next close records the session
        # that launch started rather than finding the park's and skipping.
        db.set_handoff(con, "backend", "# handoff · backend\nprevious session: sess-R1")
        realb, realx, realr = launch.build, os.execve, launch._record_terminal
        launch.build = lambda *a, **k: (["x"], {})
        os.execve = lambda *a, **k: None
        launch._record_terminal = lambda *a, **k: None
        try:
            launch.exec_agent("backend", prompt_file=str(home / "no-such-prompt"))
            kept = db.get_handoff(con, "backend")
            launch.exec_agent("backend")
        finally:
            launch.build, os.execve, launch._record_terminal = realb, realx, realr
        check("close: a daemon wake's launch leaves the record for its fallback launch",
              "sess-R1" in (kept or ""), str(kept))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        dmn_lifecycle._close_agent(con, "backend", lambda _m: None, st)
        check("close: after a launch handed the record over, the close takes its own",
              "sess-R1" not in (db.get_handoff(con, "backend") or ""),
              str(db.get_handoff(con, "backend")))
    finally:
        dmn_lifecycle._end_session = real
        con.close()


def test_a_second_done_or_close_loses_the_race(home: Path) -> None:
    """done_evidence and closed_by are each written once: a call that lost
    the race must not overwrite the winner's."""
    _board_world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "raced", "worker", "manager")
        check("race: the first done wins", db.mark_task_done(con, t, "first"))
        check("race: the second reports it lost", not db.mark_task_done(con, t, "second"))
        check("race: the first evidence stands", db.get_task(con, t)["done_evidence"] == "first")
        check("race: the first close wins", db.close_task(con, t, "closer-a"))
        check("race: the second close reports it lost", not db.close_task(con, t, "closer-b"))
        check("race: the first closer stands", db.get_task(con, t)["closed_by"] == "closer-a")

        # The loser that passed the "already closed" read before the winner wrote.
        real = db.get_task
        t2 = db.new_task(con, "raced two", "worker", "manager")
        db.mark_task_done(con, t2, "evidence")
        stale = dict(real(con, t2))
        db.close_task(con, t2, "winner")
        db.get_task = lambda c, i: stale
        try:
            ok, msg = actions.close_task(con, t2, "manager")
        finally:
            db.get_task = real
        check("race: a close that read the row before the winner wrote is refused",
              not ok and "someone else" in msg, msg)
        check("race: and the winner's identity stands", real(con, t2)["closed_by"] == "winner")
    finally:
        con.close()


def test_close_says_what_it_read_and_never_guesses(home: Path) -> None:
    _board_world(home)
    rc, out = _cli(["close", "T42", "fine"])
    check("close: a task number that is no task says no such task, and closes no session",
          rc == 1 and "no task T42" in out, out)
    rc, out = _cli(["close", "T42"])
    check("close: and the same with no evidence", rc == 1 and "no task T42" in out, out)
    rc, out = _cli(["close", "42"])
    check("close: a bare number is refused with both readings, closing nothing",
          rc == 1 and "task 42 or request 42" in out, out)
    _cli(["task", "new", "--owner", "worker", "t"])
    rc, out = _cli(["close", "T1", "fine"])
    check("close: a real task with evidence says why it refused "
          "(#71: evidence belongs to `ocp task done` now)",
          rc == 1 and "task T1" in out and "task done" in out, out)
    rc, out = _cli(["close", "T1"])
    check("close: a real task not done yet is refused too", rc == 1 and "not done" in out, out)
    rc, out = _cli(["close", "worker", "abc1234"])
    check("close: evidence on a name says it read an agent, and closed nothing",
          rc == 1 and "as an agent's name" in out and "Nothing was closed" in out, out)


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_failclosed(home))
        run(lambda: test_daemon_success_clears_streak(Path(td) / 'd5b'))
        run(lambda: test_the_screen_names_what_is_in_the_way(Path(td) / 'blk1'))
        run(lambda: test_a_dialog_in_the_way_of_work_is_said_once_and_clears_itself(Path(td) / 'blk2'))
        run(lambda: test_what_is_not_a_block(Path(td) / 'blk3'))
        run(lambda: test_a_reminder_a_dialog_blocks_is_said(Path(td) / 'blk9'))
        run(lambda: test_a_stale_draft_never_blocks_delivery_again(Path(td) / 'blk9b'))
        run(lambda: test_the_human_and_strangers_are_not_blocked_agents(Path(td) / 'blk10'))
        run(lambda: test_the_tick_looks_for_blocks(Path(td) / 'blk8'))
        run(lambda: test_a_dialog_ocps_own_exit_raised_is_said_at_once(Path(td) / 'blk5'))
        run(lambda: test_a_failed_close_says_why_and_records_the_dialog(Path(td) / 'blk6'))
        run(lambda: test_a_close_refused_by_a_dialog_is_said_once_not_every_tick(Path(td) / 'blk11'))
        run(lambda: test_a_close_never_stops_background_work(Path(td) / 'dcl0x'))
        run(lambda: test_a_dialog_ocp_can_decline_is_cleared_and_reported(Path(td) / 'dcl1'))
        run(lambda: test_ocps_own_exit_dialog_is_pressed_at_once_and_a_working_agent_never(Path(td) / 'dcl2'))
        run(lambda: test_a_dialog_ocp_cannot_decline_is_reported_not_pressed(Path(td) / 'dcl3'))
        run(lambda: test_a_dialog_that_keeps_coming_back_stops_being_pressed_and_is_escalated(Path(td) / 'dcl4'))
        run(lambda: test_esc_that_does_not_close_a_dialog_is_not_pressed_again_and_again(Path(td) / 'dcl5'))
        run(lambda: test_nothing_is_pressed_for_what_is_not_a_dialog(Path(td) / 'dcl6'))
        run(lambda: test_press_history_is_scoped_to_one_dialog_not_the_whole_agent(Path(td) / 'dcl7'))
        run(lambda: test_idle_session_is_closed_and_resumed(Path(td) / 'idle'))
        run(lambda: test_a_close_is_recorded_only_once_the_process_has_ended(Path(td) / 'exitwait'))
        run(lambda: test_a_close_that_will_not_take_is_not_tried_again_every_tick(Path(td) / 'exitretry'))
        run(lambda: test_wake_without_a_closed_session_uses_the_handoff(Path(td) / 'wakeh'))
        run(lambda: test_reconcile_does_not_clear_closed_at_on_a_failed_wake(Path(td) / 'crashwake'))
        run(lambda: test_the_daemon_never_types_into_a_trust_prompt_via_deliver(
            Path(td) / 'delivertrust'))
        run(lambda: test_work_arriving_wakes_a_closed_agent(Path(td) / 'wakework'))
        run(lambda: test_a_delivery_belongs_to_the_session_it_was_typed_into(Path(td) / 'sessdeliver'))
        run(lambda: test_a_session_that_comes_back_is_told_a_head_an_earlier_one_was_told(Path(td) / 'sessback'))
        run(lambda: test_what_is_typed_at_an_agent_is_never_more_than_three_lines(Path(td) / 'threelines'))
        run(lambda: test_a_reply_in_the_open_heads_thread_is_told_now_not_behind_it(Path(td) / 'threadreply'))
        run(lambda: test_nothing_is_typed_at_a_modal(Path(td) / 'modal'))
        run(lambda: test_a_wake_types_nothing_at_a_shell_when_the_session_dies(Path(td) / 'shellwake'))
        run(lambda: test_a_launch_takes_its_prompt_once(Path(td) / 'takeprompt'))
        run(lambda: test_a_closed_agent_is_not_handed_off(Path(td) / 'closedtrig'))
        run(lambda: test_a_close_that_stamps_sessionend_does_not_unexpire_a_stale_cache(Path(td) / 'sessend'))
        run(lambda: test_a_park_that_was_also_closed_is_woken_without_being_judged(Path(td) / 'pclosed'))
        run(lambda: test_reconcile_wakes_a_park_that_was_also_closed(Path(td) / 'pclosed2'))
        run(lambda: test_a_due_park_with_nothing_waiting_stays_closed(Path(td) / 'pclosed3'))
        run(lambda: test_nothing_is_typed_into_a_shell_with_no_session(Path(td) / 'shellgate'))
        run(lambda: test_a_stalled_closed_agent_keeps_its_marker_when_walled(Path(td) / 'walledstalled'))
        run(lambda: test_a_delivery_and_a_handoff_in_the_same_tick_the_handoff_must_not_type(
            Path(td) / 'deliverhandoffrace'))
        run(lambda: test_a_close_right_after_a_delivery_in_the_same_tick_must_not_type(
            Path(td) / 'closerace'))
        run(lambda: test_an_answer_for_a_cold_session_closes_it_instead_of_being_typed(
            Path(td) / 'coldtyping'))
        run(lambda: test_a_cold_session_close_that_fails_falls_back_to_typing(
            Path(td) / 'coldfail'))
        run(lambda: test_a_cold_session_with_a_head_or_a_nudge_due_is_closed_once(
            Path(td) / 'coldonce'))
        run(lambda: test_a_request_already_told_still_closes_a_cold_idle_session(
            Path(td) / 'coldqueue'))
        run(lambda: test_a_degraded_park_does_not_auto_clear(Path(td) / 'parklevel3'))
        run(lambda: test_a_close_gets_past_the_dialog_before_it_types_exit(Path(td) / 'limitclose'))
        run(lambda: test_a_dialog_ocp_cannot_get_past_stalls_the_agent_with_the_reason(
            Path(td) / 'dialogstall'))
        run(lambda: test_a_working_agents_dialog_is_not_pressed_by_a_handoff_that_has_not_begun(
            Path(td) / 'workingdialog'))
        run(lambda: test_each_dialog_that_cannot_be_passed_starts_its_own_clock(Path(td) / 'dialogclock'))
        run(lambda: test_dialogs_whose_titles_did_not_parse_do_not_share_a_clock(Path(td) / 'dialogblank'))
        run(lambda: test_a_dialog_on_its_last_try_says_which_dialog_in_the_stall(
            Path(td) / 'stallnote'))
        run(lambda: test_a_pinned_agent_stays_pinned_when_nothing_else_has_clear_room(
            Path(td) / 'pinnedstuck'))
        run(lambda: test_wake_on_a_self_clearing_park_refuses_and_says_when(Path(td) / 'selfclearwake'))
        run(lambda: test_orca_idle_parsing())
        run(lambda: test_confirm_trust_presses_down_then_enter())
        run(lambda: test_clear_refuses_mid_turn())
        run(lambda: test_a_context_handoff_backs_off_when_the_clear_keeps_failing(Path(td) / 'ctxclearfail'))
        run(lambda: test_repeated_failed_clears_reach_the_throttle_cap(Path(td) / 'throttlecap'))
        run(lambda: test_a_resumed_handoff_with_a_good_record_clears_the_streak(Path(td) / 'resumereset'))
        run(lambda: test_a_queued_agents_blocked_reminder_surfaces_like_an_idle_ones(
            Path(td) / 'queuedblocked'))
        run(lambda: test_a_parked_agent_with_queued_work_is_still_closed_while_idle(
            Path(td) / 'parkedqueuedclose'))
        run(lambda: test_a_wake_reads_the_screen_before_typing_into_it(Path(td) / 'trustscreen'))
        run(lambda: test_a_resume_that_fails_falls_back_to_fresh_and_the_head_is_told_after(Path(td) / 'fbk1'))
        run(lambda: test_a_message_launched_behind_the_trust_screen_is_not_told_twice(Path(td) / 'fbk2'))
        run(lambda: test_a_closed_agents_relaunch_stops_at_the_trust_screen(Path(td) / 'trustclosed'))
        run(lambda: test_hooks_report_activity(Path(td) / 'hooky'))
        run(lambda: test_clear_does_not_look_like_waking(Path(td) / 'loopy'))
        run(lambda: test_an_idle_close_keeps_a_handoff_only_if_it_closed(Path(td) / 'hdclose'))
        run(lambda: test_a_second_done_or_close_loses_the_race(Path(td) / 'taskrace'))
        run(lambda: test_close_says_what_it_read_and_never_guesses(Path(td) / 'taskcloseread'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
