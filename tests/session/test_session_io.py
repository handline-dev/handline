"""Tests: what is read off a session's screen (what is in the way, Orca's idle line), the launch prompt, the trust
key, and the guards they lean on.

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


def test_the_screen_names_what_is_in_the_way(home: Path) -> None:
    """A dialog is quoted from the screen - title and options - never matched
    against a list of the dialogs somebody has seen."""
    restore_world()
    real = termmod._screen
    termmod.screen_blocker = REAL_TERMINAL["screen_blocker"]
    try:
        termmod._screen = lambda h: _screen_of(EXIT_DIALOG)
        b = termmod.screen_blocker("term_x")
        check("screen: the exit confirmation is a dialog", b and b.kind == termmod.MODAL, str(b))
        check("screen: it is named by its own title",
              b and b.detail.startswith('"Background work is running"'), str(b))
        check("screen: with the options that answer it",
              b and "1. Exit and stop tasks / 2. Move to background and exit / 3. Stay" in b.detail
              and "❯" not in b.detail, str(b))

        termmod._screen = lambda h: _screen_of(["a", "b", "c", "d", "Enter to confirm · Esc to cancel"])
        check("screen: a layout with no rule is quoted, last three lines above the footer",
              termmod.screen_blocker("term_x").detail == "b / c / d")

        # A transcript that quotes the footer, well above the bottom rows, is not
        # a dialog - and with a real, empty composer under it, nothing is in the way.
        quoted = ["the footer says Enter to confirm and Esc to cancel"] + ["x"] * 10 + COMPOSER_EMPTY
        termmod._screen = lambda h: _screen_of(quoted)
        check("screen: the footer words in the transcript are not a dialog",
              termmod.screen_blocker("term_x") is None)

        # `draft` is never consulted, whether it is stale (the deadlock this
        # guard used to exist for) or genuinely current - #1157: the screen
        # cannot tell the two apart, so neither blocks anything.
        termmod._screen = lambda h: _screen_of(COMPOSER_EMPTY, draft=PASTED_DRAFT)
        check("screen: a draft the screen does not show is never consulted",
              termmod.screen_blocker("term_x") is None)
        termmod._screen = lambda h: _screen_of(COMPOSER_EMPTY, draft="half-typed reply")
        check("screen: a recognized composer box is never blocking, whatever draft says",
              termmod.screen_blocker("term_x") is None)
        termmod._screen = lambda h: _screen_of(COMPOSER_EMPTY, draft="")
        check("screen: an empty composer is an empty input", termmod.screen_blocker("term_x") is None)
        termmod._screen = lambda h: _screen_of(["prompt"])
        check("screen: a screen with no composer box at all fails closed, not blank forever",
              termmod.screen_blocker("term_x").kind == termmod.UNREADABLE)

        # A layout with one row under the box, not two, is still found - the
        # triple is searched for, not assumed to sit a fixed distance from
        # the end.
        termmod._screen = lambda h: _screen_of(ONE_HINT_ROW_COMPOSER)
        check("screen: a composer with a different number of rows under it "
              "is still read, not UNREADABLE",
              termmod.screen_blocker("term_x") is None)
        # The background picker has a composer's shape and typed text goes to
        # a new session, so it is a block of its own - never a clean send.
        termmod._screen = lambda h: _screen_of(BACKGROUND_PICKER)
        b = termmod.screen_blocker("term_x")
        check("screen: the background picker is not a clean composer",
              b is not None and b.kind == termmod.BACKGROUNDED, str(b))
        check("screen: it is quoted from its own banner and sessions",
              b and b.detail.startswith("Your conversation moved to the background")
              and "Completed" in b.detail and "hl inbox management" in b.detail
              and "❯" not in b.detail and "─" not in b.detail, str(b))
        check("screen: and says why in a sentence",
              b and b.reason().startswith("its conversation moved to the background"))
        check("screen: modal_open refuses it", REAL_TERMINAL["modal_open"]("term_x"))
        hostile_picker = ["\033]0;PWNED\x07\033[31mYour conversation moved to the background"] \
            + BACKGROUND_PICKER[1:]
        termmod._screen = lambda h: _screen_of(hostile_picker)
        b = termmod.screen_blocker("term_x")
        check("screen: the picker's own text is sanitised like a dialog's",
              b and "\033" not in b.detail and "PWNED" not in b.detail and "Your conversation" in b.detail,
              str(b))
        quoted = ["the hint says enter to open"] + ["x"] * 10 + COMPOSER_EMPTY
        termmod._screen = lambda h: _screen_of(quoted)
        check("screen: the hint quoted in the transcript, above the bottom rows, is not the picker",
              termmod.screen_blocker("term_x") is None)
        # #1252: on every one of 8 live agents, the old fixed window reached
        # 1-2 rows of the agent's own transcript - and "enter to open" is
        # ordinary English an agent reporting on this very feature writes
        # (auditor's own #1229 review contains it). This is that shape: the
        # phrase sits one row above a real, ordinary composer.
        fleet_false_positive = [FLEET_HINT_QUOTE] + COMPOSER_EMPTY
        termmod._screen = lambda h: _screen_of(fleet_false_positive)
        check("screen: a transcript row quoting the hint, one row above a "
              "real composer, is not the picker - it is idle, not blocked",
              termmod.screen_blocker("term_x") is None)
        # A real picker still has to be found when it is a different shape
        # than the fixture above - fewer session rows, harbor's one-hint-row
        # composer layout - never by shrinking the window (7f01f52 exists to
        # fail closed on a picker nobody has measured yet, not to fail open
        # by guessing a number that happens to fit one fixture).
        termmod._screen = lambda h: _screen_of(BACKGROUND_PICKER_SHORT)
        b = termmod.screen_blocker("term_x")
        check("screen: a shorter, differently-shaped picker is still found",
              b is not None and b.kind == termmod.BACKGROUNDED, str(b))
        check("screen: quoted from its own banner and session, not the composer",
              b and b.detail.startswith("Your conversation moved to the background")
              and "hl inbox management" in b.detail
              and "❯" not in b.detail and "─" not in b.detail, str(b))
        # auditor's #1285 B1: the composer's own hint says the picker's words
        # but the banner that would confirm it is not there - a picker this
        # code does not recognise, never nothing-in-the-way. A column-
        # alignment guess about the session row (dropped for this reason)
        # would have read this as a clean composer and typed into it.
        termmod._screen = lambda h: _screen_of(PICKER_HINT_WITH_NO_BANNER)
        b = termmod.screen_blocker("term_x")
        check("screen: an unconfirmed picker-shaped hint fails closed, never a clean send",
              b is not None and b.kind == termmod.UNREADABLE)
        # #1285 S2: the banner alone, with no "enter to open" below the box,
        # is not the picker either - the position guard is load-bearing on
        # its own, not merely redundant with the banner.
        termmod._screen = lambda h: _screen_of(BANNER_ABOVE_ORDINARY_COMPOSER)
        check("screen: the banner above an ordinary composer's own hint is not the picker",
              termmod.screen_blocker("term_x") is None)
        termmod._screen = lambda h: _screen_of(BANNER_AND_HINT_QUOTED_ABOVE_BOX)
        check("screen: the picker's words quoted above the composer box are not the picker",
              termmod.screen_blocker("term_x") is None)
        # purser's #1294, measured: the common form of the picker.
        termmod._screen = lambda h: _screen_of(PICKER_CURRENT_SESSION_SELECTED)
        b = termmod.screen_blocker("term_x")
        check("screen: the picker with the current session selected is BACKGROUNDED",
              b is not None and b.kind == termmod.BACKGROUNDED, str(b))
        termmod._screen = lambda h: _screen_of(PICKER_HINT_ONLY_ENTER_TO_RETURN)
        b = termmod.screen_blocker("term_x")
        check("screen: the picker's hint with no banner fails closed, not a clean composer",
              b is not None and b.kind == termmod.UNREADABLE, str(b))
        # auditor's #1352 should-fix: "space to reply" alone proved the whole
        # change (every other picker fixture carries it too), so nothing
        # pinned "enter to open" - the marker #1252 and this branch were
        # built on. Cutting _BACKGROUNDED_HINTS in half left the suite green.
        termmod._screen = lambda h: _screen_of(PICKER_HINT_ONLY_ENTER_TO_OPEN)
        b = termmod.screen_blocker("term_x")
        check("screen: 'enter to open' alone, with no 'space to reply', is still BACKGROUNDED",
              b is not None and b.kind == termmod.BACKGROUNDED, str(b))
        # An empty detail must not render a dangling colon - what the founder
        # saw on the #1252 false positive, where tail[:hint] was empty.
        check("screen: an empty BACKGROUNDED detail has no dangling colon",
              termmod.Blocker(termmod.BACKGROUNDED, "").reason()
              == "its conversation moved to the background")
        check("screen: an empty MODAL detail has no dangling colon",
              termmod.Blocker(termmod.MODAL, "").reason() == "a dialog is open on its screen")
        termmod._screen = lambda h: _screen_of(EXIT_DIALOG, source="stream")
        check("screen: a stream is not a screen - fail closed",
              termmod.screen_blocker("term_x").kind == termmod.UNREADABLE)
        termmod._screen = lambda h: None
        check("screen: nothing read is fail closed too",
              termmod.screen_blocker("term_x").kind == termmod.UNREADABLE)

        # An agent paints its own screen: a dialog's title and options are
        # exactly the hostile-text case auditor measured against describe_modal
        # (blocked-delivery #981/#987's should-fix) - checked here too, since
        # this module builds the same text from the same screen.
        hostile = EXIT_DIALOG[:3] + ["   \033]0;PWNED\x07\033[31mTitle\033[0m", "   ❯ 1. yes",
                                     "     2. Stay", EXIT_DIALOG[-1]]
        termmod._screen = lambda h: _screen_of(hostile)
        b = termmod.screen_blocker("term_x")
        check("screen: an OSC/CSI sequence in the dialog's own text does not reach the quote",
              b and "\033" not in b.detail and "PWNED" not in b.detail and "Title" in b.detail,
              str(b))
        footer = len(hostile) - 1
        d = termmod.parse_dialog(hostile, footer)
        check("screen: nor the parsed title used to decide what is safe to press",
              d and "\033" not in d.title and "PWNED" not in d.title, str(d))
    finally:
        termmod._screen = real


def test_the_actor_column_is_added_to_an_existing_session_starts_table(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    db.note_session_start(con, "backend", "wake", True, "s-1", "", "p")
    con.execute("ALTER TABLE session_starts DROP COLUMN actor")
    con.close()
    con = support.upgrade()
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(session_starts)")}
        row = db.session_starts(con, "backend")[0]
        check("session starts: an existing table gains the actor column, its old rows reading ''",
              "actor" in cols and row["actor"] == "" and row["cause"] == "wake", str(cols))
    finally:
        con.close()


def test_a_launch_takes_its_prompt_once(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    f = home / "p.txt"
    f.write_text("carry on")
    check("launch: the prompt file is read", launch.take_prompt(str(f)) == "carry on")
    check("launch: and removed, so no later launch reads it", not f.exists())
    check("launch: a missing file is no prompt", launch.take_prompt(str(f)) is None)


def test_orca_idle_parsing() -> None:
    """`ok` says the wait command ran; `satisfied` says the agent is idle.

    Reading `ok` alone treats a timeout as idle, and clearing mid-turn throws
    away work the fold has not recorded yet.
    """
    real = orcacli._run
    try:
        orcacli._run = lambda *a, **k: (True, json.dumps(
            {"ok": True, "result": {"wait": {"satisfied": True}}}))
        check("orca: satisfied=true reads as idle", termmod.wait_idle("h", 1000))

        orcacli._run = lambda *a, **k: (True, json.dumps(
            {"ok": True, "result": {"wait": {"satisfied": False}}}))
        check("orca: a timed-out wait is not idle", not termmod.wait_idle("h", 1000))

        orcacli._run = lambda *a, **k: (False, "boom")
        check("orca: a failed wait is not idle", not termmod.wait_idle("h", 1000))

        orcacli._run = lambda *a, **k: (True, "not json at all")
        check("orca: unparseable output is not idle", not termmod.wait_idle("h", 1000))
    finally:
        orcacli._run = real


def test_confirm_trust_presses_down_then_enter() -> None:
    """Request 156 item 1b's two keys, measured against a live prompt (see
    claude.py's own comment on TRUST_PROMPT_DOWN_ARROW): one Down off the
    default 'No, exit' lands on 'Yes, I trust this folder', and a bare Enter
    after that confirms it. Wrong order or a missing key each press whatever
    the cursor happened to be sitting on instead."""
    from handline.vendors import claude as claudemod
    calls: list[list[str]] = []
    real, delay = tmuxbackend._tmux, tmuxbackend.ENTER_DELAY_S
    tmuxbackend.ENTER_DELAY_S = 0
    try:
        tmuxbackend._tmux = lambda args, *a, **k: (calls.append(args), (0, "", ""))[1]
        ok, why = termmod.confirm_trust("%1")
        check("confirm_trust: reports success", ok, why)
        check("confirm_trust: sends exactly two keys", len(calls) == 2, str(calls))
        check("confirm_trust: the first moves down, with no Enter",
              calls[0] == ["send-keys", "-t", "%1", "Down"] and claudemod.TRUST_PROMPT_DOWN_ARROW == "\x1b[B",
              str(calls[0]))
        check("confirm_trust: the second is a bare Enter", calls[1] == ["send-keys", "-t", "%1", "Enter"], str(calls[1]))
        tmuxbackend._tmux = lambda *a, **k: (1, "", "can't find pane: %1")
        ok, why = termmod.confirm_trust("%1")
        check("confirm_trust: a failed first key is reported, not silently retried",
              not ok and "trust dialog" in why, why)
    finally:
        tmuxbackend._tmux, tmuxbackend.ENTER_DELAY_S = real, delay


def test_a_second_done_loses_the_race(home: Path) -> None:
    """done_evidence is written once: a call that lost the race must not
    overwrite the winner's."""
    _board_world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "raced", "worker", "manager")
        check("race: the first done wins", db.mark_task_done(con, t, "first"))
        check("race: the second reports it lost", not db.mark_task_done(con, t, "second"))
        check("race: the first evidence stands", db.get_task(con, t)["done_evidence"] == "first")

        # The loser that passed the "already done" read before the winner wrote.
        real = db.get_task
        t2 = db.new_task(con, "raced two", "worker", "manager")
        stale = dict(real(con, t2))
        db.mark_task_done(con, t2, "winner")
        db.get_task = lambda c, i: stale
        try:
            ok, msg = actions.mark_task_done(con, t2, "worker", reason="loser")
        finally:
            db.get_task = real
        check("race: a done that read the row before the winner wrote is refused",
              not ok and "someone else" in msg, msg)
        check("race: and the winner's evidence stands", real(con, t2)["done_evidence"] == "winner")
    finally:
        con.close()


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_failclosed(home))
        run(lambda: test_the_screen_names_what_is_in_the_way(Path(td) / 'blk1'))
        run(lambda: test_the_actor_column_is_added_to_an_existing_session_starts_table(Path(td) / 'actorcol'))
        run(lambda: test_a_launch_takes_its_prompt_once(Path(td) / 'takeprompt'))
        skip(lambda: test_orca_idle_parsing(), ORCA_UNMAINTAINED)
        run(lambda: test_confirm_trust_presses_down_then_enter())
        run(lambda: test_a_second_done_loses_the_race(Path(td) / 'taskrace'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
