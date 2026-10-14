"""Tests: the daemon tick, parks, wakes, handoffs and the lock.

Run on its own: python3 tests/session/test_daemon_session.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




def test_a_first_backfill_reads_a_slice_per_tick_not_the_whole_history(home: Path) -> None:
    from handline.usage import tokenlog
    _daemon_home(home, 20)
    proj = vendors.config_dir("alpha") / "projects" / "-w-purser"
    proj.mkdir(parents=True)
    line = _call_line("r", 1)
    for name in ("s1", "s2"):
        (proj / f"{name}.jsonl").write_text("".join(_call_line(f"req_{name}{i}", 1) for i in range(50)))
    con = db.connect()
    try:
        first = tokenlog.ingest(con, "alpha", budget=len(line) * 10)
        rest = tokenlog.ingest(con, "alpha", budget=len(line) * 1000)
        check("tokens: a byte budget bounds a tick, and the next tick carries on",
              0 < first <= 10 and first + rest == 100, str((first, rest)))
    finally:
        con.close()


def test_a_record_bigger_than_the_budget_is_read_whole_not_stalled_forever(home: Path) -> None:
    from handline.usage import tokenlog
    _daemon_home(home, 20)
    proj = vendors.config_dir("alpha") / "projects" / "-w-purser"
    proj.mkdir(parents=True)
    log = proj / "s1.jsonl"
    huge = _call_line("req_huge", 1, cwd="x" * 5000)
    small = _call_line("req_small", 2)
    log.write_text(huge + small)
    con = db.connect()
    try:
        now = int(time.time())
        got = tokenlog.ingest(con, "alpha", budget=200, now=now)
        check("tokens: a record past the tick's budget is read whole, not stalled",
              got == 1 and con.execute("SELECT COUNT(*) FROM token_calls").fetchone()[0] == 1)
        again = tokenlog.ingest(con, "alpha", budget=200, now=now)
        check("tokens: the record behind it is reached on the next tick", again == 1)
    finally:
        con.close()


def test_a_unicode_line_separator_inside_a_record_does_not_split_it(home: Path) -> None:
    from handline.usage import tokenlog
    _daemon_home(home, 20)
    proj = vendors.config_dir("alpha") / "projects" / "-w-purser"
    proj.mkdir(parents=True)
    log = proj / "s1.jsonl"
    obj = json.loads(_call_line("req_sep", 8).rstrip("\n"))
    obj["cwd"] = "/w/purser weird"
    log.write_text(json.dumps(obj, ensure_ascii=False) + "\n")
    con = db.connect()
    try:
        got = tokenlog.ingest(con, "alpha", now=int(time.time()))
        check("tokens: a record carrying U+2028 is kept whole, str.splitlines is not the separator",
              got == 1)
    finally:
        con.close()


def test_dump_and_the_read_guard_refuse_exactly_the_same_characters(home: Path) -> None:
    """#1609: 'one definition of unsafe value shared by dump and read, not
    two lists that can drift apart.' A truth table over every codepoint in
    textsafe.UNSAFE_IN_VALUE plus a sample of ordinary ones, checked
    against both dump() and refuse_unsafe_values() - not just that they
    import the same constant (a future edit to either function's own
    condition could still drift, the way #1566/#1580 found one test's worth
    of drift inside dump() itself), but that the two actually agree,
    character by character."""
    # auditor #1729: the rows below are generated from the set they are
    # checking, so they agree with themselves whatever it holds. The four the
    # set adds to CONTROL_CHARS are named literally here instead - they are
    # the ones that end a line rather than drive a terminal, and the read
    # guard let U+2028 and U+2029 through before this branch.
    for c in "\t\n\u2028\u2029":
        check(f"unsafe in a stored value though not on a screen: {hex(ord(c))}",
              c in textsafe.UNSAFE_IN_VALUE and c not in textsafe.CONTROL_CHARS)

    ordinary = "a1 .-_'\"#[]{}:,&*|>~"
    for c in sorted(textsafe.UNSAFE_IN_VALUE) + list(ordinary):
        unsafe = c in textsafe.UNSAFE_IN_VALUE
        try:
            yamlcompat.dump({"k": f"a{c}b"})
            dump_refused = False
        except yamlcompat.ConfigError as e:
            dump_refused = "does not write control characters" in str(e)
        try:
            yamlcompat.refuse_unsafe_values({"k": f"a{c}b"}, "src")
            read_refused = False
        except yamlcompat.ConfigError:
            read_refused = True
        check(f"dump/read agree on {hex(ord(c))} {c!r}: unsafe={unsafe}",
              dump_refused == unsafe and read_refused == unsafe,
              f"dump_refused={dump_refused} read_refused={read_refused}")


def test_daemon_evaluates_parked(home: Path) -> None:
    """Section 8.4: if only running agents were evaluated, an all-parked system
    would never wake anything again."""
    check("daemon: PARKED is evaluated every tick", dmn_states.PARKED in dmn_states.EVALUATED)
    check("daemon: THROTTLED is evaluated every tick", dmn_states.THROTTLED in dmn_states.EVALUATED)
    check("daemon: QUEUED is evaluated every tick", dmn_states.QUEUED in dmn_states.EVALUATED)
    check("daemon: STALLED is not auto-evaluated", dmn_states.STALLED not in dmn_states.EVALUATED)


def test_dev_mode_config_round_trips(home: Path) -> None:
    """Task #64: off by default, `hl dev-mode on/off` flips config.yaml
    (the same file `account cap` writes to), and a plain `hl dev-mode`
    reads it back without changing it."""
    use_home(home)
    check("dev-mode: off by default", devsetting.dev_mode_enabled() is False,
          str(devsetting.dev_mode_enabled()))
    out = _capture(lambda: climod.main(["dev-mode"]))
    check("cli dev-mode: prints the current state with no argument",
          out.strip() == "off", out)
    out = _capture(lambda: climod.main(["dev-mode", "on"]))
    check("cli dev-mode on: confirms the flip", "on" in out, out)
    check("dev-mode: on reaches daemon.py's own reader",
          devsetting.dev_mode_enabled() is True, str(devsetting.dev_mode_enabled()))
    out = _capture(lambda: climod.main(["dev-mode"]))
    check("cli dev-mode: reads back on without an argument", out.strip() == "on", out)
    climod.main(["dev-mode", "off"])
    check("dev-mode: off turns it back off, not just adds a second key",
          devsetting.dev_mode_enabled() is False, str(devsetting.dev_mode_enabled()))


def test_bridge_dev_mode_flag_turns_it_on_and_its_absence_leaves_it(home: Path) -> None:
    use_home(home)
    _capture(lambda: climod.main(["bridge", "--once"]))
    check("bridge without --dev-mode leaves it off", devsetting.dev_mode_enabled() is False, "")
    _capture(lambda: climod.main(["bridge", "--once", "--dev-mode"]))
    check("bridge --dev-mode turns it on", devsetting.dev_mode_enabled() is True, "")
    _capture(lambda: climod.main(["bridge", "--once"]))
    check("bridge without --dev-mode does not turn it off", devsetting.dev_mode_enabled() is True, "")
    climod.main(["dev-mode", "off"])


def test_an_owed_clear_that_holds_everything_says_why_once(home: Path) -> None:
    """T456: settle_owed_clear returns True while a clear is owed and the tick then does nothing else
    for the agent - it must not do so silently."""
    from handline.daemon import handoff as dmn_handoff
    _daemon_home(home, 20)
    con = db.connect()
    real = (dmn_delivery.session_present, dmn_delivery._type_gate)
    lines: list[str] = []
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_cursor(con, "clear_owed:backend", int(time.time()))
        dmn_delivery.session_present = lambda a: True
        dmn_delivery._type_gate = lambda *a, **k: "backend was typed at moments ago; letting the screen catch up"
        held = [dmn_handoff.settle_owed_clear(con, "backend", lines.append) for _ in range(3)]
        check("owed clear: it holds the agent's tick", held == [True, True, True])
        check("owed clear: and says why, once", len([m for m in lines if "moments ago" in m and "owed" in m]) == 1,
              str(lines))
        db.clear_cursor(con, "clear_owed:backend")
        dmn_handoff.settle_owed_clear(con, "backend", lines.append)
        check("owed clear: the reason is forgotten once nothing is owed", not dmn_handoff._OWED_SAID.get("backend"))
    finally:
        dmn_delivery.session_present, dmn_delivery._type_gate = real
        con.close()


def test_daemon_ignores_stale_status(home: Path) -> None:
    """A frozen status.json must not be believed (the collector may be dead)."""
    _daemon_home(home, 40)
    write_status(home, "alpha", pct_5h=40, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - 100000)
    st = status.read("alpha")
    check("stale snapshot: assignable() refuses", not st.assignable()[0])
    check("stale snapshot: pct is not trusted for triggers", st.short_trusted is None,
          str(st.short_trusted))
    t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used)
    check("stale snapshot: an unreadable figure is not a wall, so nothing fires on it",
          not t.fired, t.reason)


def test_ocp_presses_only_the_key_that_declines(home: Path) -> None:
    """Safe by construction, not by a list of dialogs: Esc, and only where the
    dialog itself says Esc cancels and offers an option that just declines. What
    cannot be decided is not pressed."""
    restore_world()
    cases = [
        ("the /exit confirmation (measured)", EXIT_DIALOG, True),
        ("any confirm dialog with a plain No", YES_NO_DIALOG, True),
        ("the trust dialog: its declining option is 'No, exit'", TRUST_DIALOG, False),
        ("a tool permission prompt: 'No, and tell Claude...' is a choice, Esc there rejects a call",
         PERMISSION_PROMPT, False),
        ("a decoy 'Cancel' elsewhere does not excuse what the screen's own "
         "(esc) mark says Esc does ('No, exit')", TRUST_DIALOG_WITH_A_DECOY_CANCEL, False),
        ("the (esc) mark itself on the inert option is enough, on its own",
         [ln.replace("2. Move to background and exit", "2. Stay (esc)")
          .replace("3. Stay", "3. Move to background and exit") for ln in EXIT_DIALOG], True),
        ("(esc) on a free-text line between options is not an option - refuse",
         TRUST_DIALOG_WITH_FREE_TEXT_ESC_LINE, False),
        ("(esc) on the title itself is not an option - refuse",
         TRUST_DIALOG_WITH_ESC_MARK_ON_THE_TITLE, False),
        ("footer without the Enter-confirms half", EXIT_DIALOG[:-1] + ["   Esc to cancel"], False),
        ("footer without the Esc-cancels half", EXIT_DIALOG[:-1] + ["   Enter to confirm"], False),
        ("no rule opening the panel", [ln for ln in EXIT_DIALOG if "▔" not in ln], False),
        ("no title under the rule", EXIT_DIALOG[:3] + EXIT_DIALOG[6:], False),
        ("one option only", EXIT_DIALOG[:2] + [EXIT_DIALOG[3], "   ❯ 1. Stay", EXIT_DIALOG[-1]], False),
        ("options not numbered from 1 in order",
         [ln.replace("2. Move", "5. Move") for ln in EXIT_DIALOG], False),
        ("an option 'Stay' hidden in a longer label",
         [ln.replace("3. Stay", "3. Stay and lose the work") for ln in EXIT_DIALOG], False),
        ("no option that declines, though Esc cancels",
         [ln.replace("3. Stay", "3. Continue") for ln in EXIT_DIALOG], False),
    ]
    for name, tail, presses in cases:
        term = _Terminal(tail)
        term.install()
        outcome, title, why = termmod.decline_dialog("term_x")
        check(f"press: {name} - {'Esc' if presses else 'nothing'}",
              (term.sent == [("\x1b", False)]) if presses else term.sent == [],
              f"{outcome} {why} sent={term.sent}")
        if presses:
            check(f"press: {name} - the dialog goes, and it says so", outcome == termmod.CLEARED, outcome)
        else:
            check(f"press: {name} - and says why", outcome == termmod.NOT_PRESSED and why, f"{outcome} {why}")

    # What is on screen NOW decides: a screen read as a dialog by the block, and
    # gone by the time of the key, gets nothing.
    term = _Terminal(CLEARED_SCREEN)
    term.install()
    check("press: no dialog now, no key", termmod.decline_dialog("term_x")[0] == termmod.NOT_PRESSED
          and term.sent == [])
    for name, term in (("a stream, not a screen", _Terminal(EXIT_DIALOG, source="stream")),
                       ("a screen that cannot be read", None)):
        if term:
            term.install()
        else:
            termmod._screen = lambda h: None
            termmod.send = lambda h, text, enter=True: (_ for _ in ()).throw(AssertionError("typed"))
        check(f"press: {name} - nothing", termmod.decline_dialog("term_x")[0] == termmod.NOT_PRESSED
              and (term.sent == [] if term else True))

    term = _Terminal(EXIT_DIALOG, ignores_esc=True)
    term.install()
    out = termmod.decline_dialog("term_x")
    check("press: Esc that leaves the dialog standing is reported once, not repeated",
          out[0] == termmod.STILL_OPEN and term.sent == [("\x1b", False)], f"{out} {term.sent}")
    check("press: the screen is read before the key and again after it", term.reads >= 3, str(term.reads))
    check("press: the only key that is ever named is Esc",
          termmod.ESCAPE == "\x1b" and "Enter" not in repr(termmod.declining_key))

    # `draft` is not consulted here either (#1157): it is polluted by ocp's own
    # sends, and refusing on it would leave a stale one's dialog unpressed.
    pressed = []
    termmod._screen = lambda h: dict(_screen_of(EXIT_DIALOG), draft="a person's half-typed reply")
    termmod.send = lambda h, text, enter=True: (pressed.append((text, enter)),
                                                (True, ""))[1]
    out = termmod.decline_dialog("term_x")
    check("press: a non-empty draft on the same read as the dialog does not stop the press",
          out[0] != termmod.NOT_PRESSED and pressed == [("\x1b", False)], f"{out} sent={pressed}")

    # A read that fails right after the key is not evidence the dialog went -
    # the same screen fails closed everywhere else in this module.
    calls = {"n": 0}

    def flaky_then_gone(h):
        calls["n"] += 1
        if calls["n"] == 1:
            return _screen_of(EXIT_DIALOG)                       # read before the key
        if calls["n"] == 2:
            return _screen_of(EXIT_DIALOG, source="stream")      # one bad read right after
        return _screen_of(CLEARED_SCREEN)                        # then it really is gone
    sent: list = []
    termmod._screen = flaky_then_gone
    termmod.send = lambda h, text, enter=True: (sent.append((text, enter)), (True, ""))[1]
    out = termmod.decline_dialog("term_x")
    check("press: an unreadable screen right after Esc is not read as cleared",
          out[0] == termmod.CLEARED and calls["n"] >= 3, f"{out} reads={calls['n']}")

    # And if it never comes back readable, that is reported as still open, not
    # as a clear nobody can stand behind.
    reads = {"n": 0}

    def never_readable_after(h):
        reads["n"] += 1
        return _screen_of(EXIT_DIALOG) if reads["n"] == 1 else _screen_of(EXIT_DIALOG, source="stream")
    termmod._screen = never_readable_after
    out = termmod.decline_dialog("term_x")
    check("press: a screen that stays unreadable is still open, never a false clear",
          out[0] == termmod.STILL_OPEN, out)

    # A DIFFERENT dialog showing up right after the key is not the one Esc
    # was pressed on still standing - that one is gone, whatever this new one
    # is, and still-open must never be counted against its title instead.
    term = _Terminal(EXIT_DIALOG, after=YES_NO_DIALOG)
    term.install()
    out = termmod.decline_dialog("term_x")
    check("press: a different dialog appearing after Esc is not the same one still open",
          out[0] == termmod.CLEARED and out[1] == "Background work is running", out)


def test_a_config_that_will_not_parse_is_never_overwritten_by_a_setting(home: Path) -> None:
    """T86: every writer of config.yaml read a file that failed to parse as
    empty, and then wrote that one key over the whole of it."""
    from handline.infra.yamlcompat import ConfigError
    _daemon_home(home, 20)
    cfg = paths.config_file()
    writers = {
        "cap": lambda: loadmod.set_cap("alpha", 3),
        "default cap": lambda: loadmod.set_default_cap(3),
        "label": lambda: loadmod.set_label("alpha", "Main"),
        "recover": lambda: loadmod.set_usage_recover("alpha", False),
        "park timeout": lambda: idle_close.set_close_timeout(120),
        "cache expiry": lambda: idle_close.set_ignore_cache_expiry(True),
        "flag": lambda: idle_close.set_config_flag("some_flag", True),
        "dev mode": lambda: devsetting.set_dev_mode(True),
    }
    for broken in ("keep: [unclosed\nother: 1\n", "- a\n- b\n"):
        cfg.write_text(broken)
        for name, write in writers.items():
            try:
                write()
                raised = False
            except ConfigError:
                raised = True
            check(f"config: {name} refuses a file that is not a mapping, and leaves it as it was",
                  raised and cfg.read_text() == broken, repr(cfg.read_text()))
    cfg.write_text("keep: 1\n")
    loadmod.set_cap("alpha", 3)
    check("config: a good file keeps every other setting",
          loadmod._config() == {"keep": 1, "active_cap_alpha": 3}, str(loadmod._config()))
    cfg.unlink()
    idle_close.set_close_timeout(120)
    check("config: no file yet is written from nothing", loadmod._config() == {"park_timeout_s": 120})
    ok, why = (lambda: (cfg.write_text("keep: [unclosed\n"), actions.set_account_cap("alpha", "2"))[1])()
    check("config: the screen's cap setter says why instead of failing", not ok and why, why)


def test_background_work_is_tracked_while_running_too(home: Path) -> None:
    """Founder #1975/#2125: track_background_work used to skip RUNNING
    entirely, so a cursor set once stayed frozen for as long as a turn ran -
    the screen's 'background Nm' detail (shown for RUNNING too, T195/T300)
    grew from a stale timestamp no matter what the session's own background
    work actually did. It must read every tick regardless of state."""
    _daemon_home(home, 20)
    con = db.connect()
    names = ("available", "alive", "background_work", "live_terminals")
    real = {n: getattr(termmod, n) for n in names}
    bg = ["1 shell"]
    try:
        orca_seen()
        termmod.background_work = lambda h: bg[0]
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="thinking")
        row = db.get_agent_state(con, "backend")
        dmn_delivery.track_background_work(con, "backend", row)
        check("background: RUNNING still picks up work the screen reports",
              dmn_delivery.has_background_work(con, "backend"))
        since1 = dmn_delivery.bg_since(con, "backend")

        bg[0] = ""
        dmn_delivery.track_background_work(con, "backend", row)
        check("background: RUNNING still notices it end",
              not dmn_delivery.has_background_work(con, "backend"))

        bg[0] = "1 shell"
        dmn_delivery.track_background_work(con, "backend", row)
        since2 = dmn_delivery.bg_since(con, "backend")
        check("background: a fresh start while RUNNING is a fresh 'since', not the frozen one",
              since2 is not None and since2 >= since1, (since1, since2))
    finally:
        for n, f in real.items():
            setattr(termmod, n, f)
        con.close()


def test_a_screen_with_background_work_is_kept_as_a_sample(home: Path) -> None:
    """T592: what tells an idle agent with background work from one mid-turn is on its screen, which
    only the daemon can read. The rows the tick just read are kept - labelled with the db's state -
    once per interval, and only the newest few files stay."""
    from handline.daemon import screensamples
    _daemon_home(home, 20)
    con = db.connect()
    names = ("available", "alive", "background_work", "last_tail", "live_terminals")
    real = {n: getattr(termmod, n) for n in names}
    bg = ["1 shell"]
    rows = ["Some output", "\u256d" + "\u2500" * 20, "\u2570" + "\u2500" * 20, "  1 shell \u00b7 to manage"]
    try:
        orca_seen()
        termmod.background_work = lambda h: bg[0]
        termmod.last_tail = lambda h: rows
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        row = db.get_agent_state(con, "backend")
        dmn_delivery.track_background_work(con, "backend", row)
        files = sorted(screensamples.dir_().glob("backend-*.txt"))
        text = files[0].read_text() if files else ""
        check("sample: a screen with background work is written", len(files) == 1, str(files))
        check("sample: labelled with the db's state and what the session says",
              "db state: IDLE" in text and "session says: 1 shell" in text and "agent: backend" in text, text)
        check("sample: and the rows themselves follow, top first", text.rstrip().endswith("to manage"), text)
        check("sample: private to its owner", oct(files[0].stat().st_mode & 0o777) == "0o600")

        dmn_delivery.track_background_work(con, "backend", row)
        check("sample: one per interval, not one per tick",
              len(list(screensamples.dir_().glob("backend-*.txt"))) == 1)

        bg[0] = ""
        db.clear_cursor(con, "screen_sample:backend")
        dmn_delivery.track_background_work(con, "backend", row)
        check("sample: none when the session reports no background work",
              len(list(screensamples.dir_().glob("backend-*.txt"))) == 1)

        bg[0] = "1 shell"
        for i in range(screensamples.KEEP + 5):
            screensamples.record(con, "backend", row, "1 shell", rows, now=int(time.time()) + (i + 1) * 1000)
        check("sample: only the newest few are kept",
              len(list(screensamples.dir_().glob("*.txt"))) == screensamples.KEEP)
        real_screen, real_last = termmod._screen, real["last_tail"]
        termmod.background_work = real["background_work"]
        termmod._screen = lambda h, limit=40: {"source": "screen", "tail": rows}
        try:
            termmod.background_work("term_y")
            check("sample: the real read keeps the rows it took, for the sample", real_last("term_y") == rows)
        finally:
            termmod._screen = real_screen
        termmod.last_tail = lambda h: None
        db.clear_cursor(con, "screen_sample:backend")
        check("sample: no rows read, nothing written and no failure",
              screensamples.record(con, "backend", row, "1 shell", None) is False)
    finally:
        for n, f in real.items():
            setattr(termmod, n, f)
        con.close()


def test_a_closed_session_shows_no_background_work(home: Path) -> None:
    """T405: the founder closed a session that had background work; its `bgwork`
    cursor stayed, so `hl team` kept saying 'RUNNING background 8h, stale'. A
    session that is gone runs nothing: the screen reads it IDLE straight away,
    and the next tick drops the cursor."""
    _daemon_home(home, 20)
    con = db.connect()
    names = ("available", "alive", "background_work", "live_terminals")
    real = {n: getattr(termmod, n) for n in names}
    try:
        orca_seen()
        termmod.background_work = lambda h: "1 shell"
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        dmn_delivery.track_background_work(con, "backend", db.get_agent_state(con, "backend"))
        seen = screenstate.screen_state(con, db.get_agent_state(con, "backend"))
        check("closed: setup - an open session with background work reads idle, with the background note",
              seen[0] == "IDLE" and seen[1].startswith("background"), str(seen))

        db.set_closed(con, "backend", "sess-1")
        row = db.get_agent_state(con, "backend")
        check("closed: the moment it is recorded closed the row is not RUNNING",
              screenstate.screen_state(con, row) == ("IDLE", "") and not dmn_delivery.has_background_work(con, "backend"),
              str(screenstate.screen_state(con, row)))
        check("closed: a closed session has no background 'since'", dmn_delivery.bg_since(con, "backend") is None)
        dmn_delivery.track_background_work(con, "backend", row)
        check("closed: the tick drops the stale cursor, so a wake starts clean",
              not db.get_cursor(con, "bgwork:backend"))
    finally:
        for n, f in real.items():
            setattr(termmod, n, f)
        con.close()


def test_t52_idle_timeout_and_cache_expiry_overrides(home: Path) -> None:
    """T52: config.yaml's global park timeout and cache-expiry-ignore switch,
    and agent.yaml's own override of either - the agent's own setting always
    wins over the global one, cleared it falls back to the global one, and
    the global one unset is today's behaviour (IDLE_CLOSE_S, RESUME_WARM_S)."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        now = int(time.time())
        st = types.SimpleNamespace(session_id="s")

        check("idle timeout: today's behaviour is IDLE_CLOSE_S with nothing set",
              idle_close.close_timeout_s("backend") == dmn_timing.IDLE_CLOSE_S)
        idle_close.set_close_timeout(120)
        check("idle timeout: the global default is read back",
              idle_close.default_close_timeout_s() == 120
              and idle_close.close_timeout_s("backend") == 120)
        idle_close.set_agent_close_timeout("backend", idle_close.NEVER)
        check("idle timeout: the agent's own override wins over the global default",
              idle_close.close_timeout_s("backend") == idle_close.NEVER
              and idle_close.default_close_timeout_s() == 120)

        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 3600,))
        row = db.get_agent_state(con, "backend")
        check("idle timeout: never closes with the per-agent override set",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))
        idle_close.set_agent_close_timeout("backend", None)
        check("idle timeout: clearing the override falls back to the global default",
              idle_close.close_timeout_s("backend") == 120
              and dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))

        for bad in (lambda: idle_close.set_close_timeout(-1),
                   lambda: idle_close.set_agent_close_timeout("backend", -1)):
            try:
                bad()
                check("idle timeout: a negative value is refused", False)
            except ValueError:
                check("idle timeout: a negative value is refused", True)

        later = now + dmn_timing.RESUME_WARM_S + 60
        check("cache expiry: today's behaviour expires it past RESUME_WARM_S",
              dmn_lifecycle.cache_expired(con, "backend", "sess", later))
        idle_close.set_ignore_cache_expiry(True)
        check("cache expiry: the global switch stops it expiring",
              not dmn_lifecycle.cache_expired(con, "backend", "sess", later))
        idle_close.set_agent_ignore_cache_expiry("backend", False)
        check("cache expiry: an explicit per-agent 'off' wins over the global 'on'",
              dmn_lifecycle.cache_expired(con, "backend", "sess", later))
    finally:
        con.close()


def test_the_live_trust_screen_is_never_pressed(home: Path) -> None:
    """auditor's #1630 3.3: TRUST_DIALOG above is reconstructed - numbered,
    Yes first. The live screen has unnumbered options with "No, exit" first,
    so parse_dialog cannot read it. It must still read as a dialog, and
    neither key path may press anything at it, alone or from the sweep."""
    restore_world()
    tail = LIVE_TRUST_PROMPT_CAPTURE.split("\n")
    footer = termmod._footer_row(tail)
    check("live trust: the footer is found", footer is not None, str(footer))
    check("live trust: parse_dialog gives None",
          footer is not None and termmod.parse_dialog(tail, footer) is None)
    term = _Terminal(tail)
    term.install()
    seen = termmod.screen_blocker("term_x")
    check("live trust: screen_blocker reads it as a dialog",
          seen is not None and seen.kind == termmod.MODAL, str(seen))
    outcome, _title, why = termmod.decline_dialog("term_x")
    check("live trust: decline_dialog presses nothing, and says why",
          outcome == termmod.NOT_PRESSED and why and term.sent == [], f"{outcome} {why} {term.sent}")

    con, term = _decline_world(home, tail)
    t0 = int(time.time()) - 5000
    try:
        for step in (0, blockedmod.PRESS_AFTER_S, blockedmod.DECISION_AFTER_S):
            blockedmod._checked.clear()
            blockedmod.sweep(con, lambda _m: None, t0 + step)
        check("live trust: the sweep presses nothing either, past PRESS_AFTER_S",
              term.sent == [] and db.block_get(con, "backend") is not None, str(term.sent))
    finally:
        con.close()


def test_a_running_daemon_is_reported_whatever_daemon_pid_says(home: Path) -> None:
    """The recurring bug: a daemon alive under the service, and a pidfile from
    earlier naming a dead pid and a boot time that has since drifted (macOS
    moves kern.boottime by a second when the clock steps). Every screen read
    the file and said the daemon was not running; the start path asked the lock
    and said it was. Both now ask the lock."""
    use_home(home)
    env = {**os.environ, "OCP_HOME": str(home)}
    (home / "daemon.pid").write_text(json.dumps({"pid": 999999, "boot": 1}))

    check("daemon: nobody holding the lock reads as not running, whatever any file says",
          daemonlock.running_elsewhere() is None)
    holder, pid = _hold_daemon_lock_in_another_process(env)
    try:
        check("daemon: the process holding the lock is the one reported",
              daemonlock.running_elsewhere() == pid, str(daemonlock.running_elsewhere()))
        check("daemon: a probe leaves the hold with its holder",
              lock.holder(daemonlock.DAEMON_LOCK) == pid)
        with lock.singleton(daemonlock.DAEMON_LOCK) as free:
            check("daemon: and a second daemon is refused, by the same fact", not free)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            clidaemon.cmd_daemon_status(None)
        check("ps: says running, and names the pid",
              f"daemon: running, pid {pid}" in buf.getvalue(), buf.getvalue())
        r = doctor.Report()
        doctor._check_daemon(r)
        check("doctor: says running, with no stale-pidfile warning",
              r.rows[-1][0] == doctor.OK and str(pid) in r.rows[-1][1]
              and not any(lv != doctor.OK for lv, _, _ in r.rows), str(r.rows))
    finally:
        holder.kill()
        holder.wait(timeout=30)

    check("daemon: the name a dead holder left behind is not a running daemon",
          daemonlock.running_elsewhere() is None, str(daemonlock.running_elsewhere()))
    check("daemon: ...and needs no clearing by anything", lock.holder(daemonlock.DAEMON_LOCK) is None)


def test_a_holder_this_process_cannot_signal_is_still_reported(home: Path) -> None:
    """T146 (founder #737/#746): a daemon held from a sandbox that cannot
    signal it (`kill(pid, 0)` raising EPERM, not ESRCH) must still read as
    running, not as a dead holder's leftover.
    """
    use_home(home)
    env = {**os.environ, "OCP_HOME": str(home)}
    holder, pid = _hold_daemon_lock_in_another_process(env)
    orig_kill = os.kill
    try:
        def fake_kill(p, sig):
            if p == pid:
                raise PermissionError("denied")
            return orig_kill(p, sig)
        os.kill = fake_kill
        check("lock: EPERM on kill(0) still names the real holder, not 0",
              lock.holder(daemonlock.DAEMON_LOCK) == pid)
        check("daemon: still reported running from inside the sandbox",
              daemonlock.running_elsewhere() == pid)
    finally:
        os.kill = orig_kill
        holder.kill()
        holder.wait(timeout=30)


def test_the_lock_names_only_its_current_holder(home: Path) -> None:
    use_home(home)
    check("lock: no file yet, no holder", lock.holder(daemonlock.DAEMON_LOCK) is None)
    with lock.singleton(daemonlock.DAEMON_LOCK) as mine:
        check("lock: taken", mine)
        check("lock: the holder writes its own pid", lock.holder(daemonlock.DAEMON_LOCK) == os.getpid())
        with lock.singleton(daemonlock.DAEMON_LOCK) as second:
            check("lock: a refused second taker does not overwrite the name", not second)
        check("lock: ...the holder is still named", lock.holder(daemonlock.DAEMON_LOCK) == os.getpid())

        # T153: a held lock whose file still names the previous holder, dead,
        # reads as held-but-nameless (0), never as that dead pid - in a sandbox
        # too, where a signal to a live process is refused (EPERM, and so the
        # holder is named, above) but one to a dead pid still fails ESRCH.
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        path = home / "locks" / f"{daemonlock.DAEMON_LOCK}.{lock.SINGLETON}"
        fd = os.open(path, os.O_WRONLY)
        os.pwrite(fd, str(gone.pid).ljust(lock._PID_WIDTH).encode(), 0)
        os.close(fd)
        check("lock: a dead pid left in the file is read as nameless", lock.holder(daemonlock.DAEMON_LOCK) == 0)
        real_kill = os.kill

        def refused(pid, sig):
            if pid == gone.pid:
                raise ProcessLookupError(3, "No such process")
            raise PermissionError(1, "Operation not permitted")
        os.kill = refused
        try:
            check("lock: a sandbox refusing the signal never turns the name into a pid",
                  lock.holder(daemonlock.DAEMON_LOCK) == 0)
        finally:
            os.kill = real_kill
        fd = os.open(path, os.O_WRONLY)
        os.pwrite(fd, str(os.getpid()).ljust(lock._PID_WIDTH).encode(), 0)
        os.close(fd)
    check("lock: released, nobody is named although the file still holds the pid",
          lock.holder(daemonlock.DAEMON_LOCK) is None)
    with lock.terminal("agent-x") as t:
        check("lock: a terminal hold takes no pid", t)
    check("lock: and is not a daemon", lock.holder(daemonlock.DAEMON_LOCK) is None)


# ---------------------------------------------------------------- service
def test_service_plist(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    orig = service.go_arguments
    try:
        service.go_arguments = GO_ARGUMENTS
        plist = service.build_plist(home=home)
        path = service.write_plist(path=home / "test.plist", home=home)
    finally:
        service.go_arguments = orig
    check("service: label is set", plist["Label"] == service.LABEL)
    check("service: RunAtLoad", plist["RunAtLoad"] is True)
    check("service: KeepAlive is a bare true, not SuccessfulExit-gated",
          plist["KeepAlive"] is True)
    check("service: ThrottleInterval bounds the restart rate",
          isinstance(plist["ThrottleInterval"], int) and plist["ThrottleInterval"] > 0)
    check("service: stdout log is under OCP_HOME/logs",
          plist["StandardOutPath"] == str(home / "logs" / "daemon.out.log"))
    check("service: stderr log is under OCP_HOME/logs",
          plist["StandardErrorPath"] == str(home / "logs" / "daemon.err.log"))
    env = plist["EnvironmentVariables"]
    check("service: env carries PATH", bool(env.get("PATH")))
    check("service: env carries HOME", bool(env.get("HOME")))
    check("service: env carries OCP_HOME, pointed at this home",
          env.get("OCP_HOME") == str(home))
    check("service: program is an absolute path (launchd inherits no PATH)",
          Path(plist["ProgramArguments"][0]).is_absolute())

    check("service: write_plist creates the file", path.exists())
    back = service.read_plist(path=path)
    check("service: plist round-trips through plistlib", back == plist, str(back))


def test_service_launchctl_gated(home: Path) -> None:
    """No test may shell out to real launchctl - every call goes through
    run_launchctl, so replacing that one name fakes the whole daemon."""
    home.mkdir(parents=True, exist_ok=True)
    plist_file = home / "LaunchAgents" / f"{service.LABEL}.plist"
    calls: list[list[str]] = []

    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def fake_ok(args):
        calls.append(args)
        return FakeResult(0, "", "")

    orig_plist_path = service.plist_path
    orig_runner = service.run_launchctl
    orig_args = service.go_arguments
    try:
        service.go_arguments = GO_ARGUMENTS
        service.plist_path = lambda: plist_file
        service.run_launchctl = fake_ok
        ok, msg = service.install(home=home)
        check("service: install() reports success when launchctl does", ok, msg)
        check("service: install() writes the plist", plist_file.exists())
        check("service: a first install (nothing loaded yet) skips bootout",
              "bootout" not in [c[0] for c in calls])
        check("service: install() bootstraps the built plist",
              calls[0] == ["bootstrap", service.domain_target(), str(plist_file)])
        check("service: install() confirms launchd lists the job, then enables it",
              [c[0] for c in calls[1:3]] == ["print", "enable"], str(calls))

        # Reinstalling over a loaded job must bootout first, so a stale plist is not left running.
        calls.clear()
        launchd = {"loaded": True, "bootstraps": 0, "fail_first": False}

        def fake_launchd(args):
            calls.append(args)
            if args[0] == "print":
                return FakeResult(0, "\tpid = 1\n", "") if launchd["loaded"] else FakeResult(113, "", "")
            if args[0] == "bootout":
                launchd["loaded"] = False
            elif args[0] == "bootstrap":
                launchd["bootstraps"] += 1
                if launchd["fail_first"] and launchd["bootstraps"] == 1:
                    return FakeResult(1, "", "boom")
                launchd["loaded"] = True
            return FakeResult(0, "", "")

        service.run_launchctl = fake_launchd
        ok, msg = service.install(home=home)
        check("service: reinstalling over a loaded job boots it out first",
              ok and calls[1][0] == "bootout", str(calls))

        # If the new plist fails to load, a previously-working job must not
        # end up unsupervised - the old plist is restored and reloaded.
        calls.clear()
        launchd.update(loaded=True, bootstraps=0, fail_first=True)
        ok, msg = service.install(home=home)
        check("service: install() reports failure when bootstrap fails", not ok, msg)
        check("service: a failed reinstall still retries bootstrap with the restored plist",
              launchd["bootstraps"] == 2)

        calls.clear()

        def fake_status(args):
            calls.append(args)
            return FakeResult(0, "\tpid = 4242\n\tlast exit code = 0\n", "")

        service.run_launchctl = fake_status
        st = service.status()
        check("service: status() reports installed", st["installed"])
        check("service: status() reports loaded from a fake `print`", st["loaded"])
        check("service: status() parses the pid", st["pid"] == 4242)
        check("service: status() parses the last exit code", st["last_exit_code"] == "0")

        calls.clear()
        service.run_launchctl = fake_ok
        ok, msg = service.uninstall()
        check("service: uninstall() on a home that is not the live one is refused: nothing booted out, the plist kept",
              not ok and "is not the live home" in msg and calls == [] and plist_file.exists(), f"{msg} {calls}")
        os.environ["OCP_SERVICE_LABEL"] = "dev.orcapipe.rehearsal"
        try:
            ok, msg = service.uninstall()
        finally:
            del os.environ["OCP_SERVICE_LABEL"]
        check("service: uninstall() reports success", ok, msg)
        check("service: uninstall() removes the plist", not plist_file.exists())
        check("service: uninstall() boots the job out", calls[0][0] == "bootout")
    finally:
        service.go_arguments = orig_args
        service.plist_path = orig_plist_path
        service.run_launchctl = orig_runner


def test_service_reinstall_waits_for_the_old_job_to_exit(home: Path) -> None:
    """launchd keeps the label for a while after bootout, until the old daemon has exited; a bootstrap
    in that window failed with `5: Input/output error` and the restore failed the same way, leaving
    no job loaded until a second install."""
    home.mkdir(parents=True, exist_ok=True)
    plist_file = home / "LaunchAgents" / f"{service.LABEL}.plist"
    calls: list[str] = []
    launchd = {"loaded": True, "exiting": 0}

    class Res:
        def __init__(self, code=0, err=""):
            self.returncode, self.stdout, self.stderr = code, ("\tpid = 1\n" if code == 0 else ""), err

    def fake(args):
        calls.append(args[0])
        if args[0] == "print":
            if launchd["exiting"]:
                launchd["exiting"] -= 1
                return Res()
            return Res() if launchd["loaded"] else Res(113)
        if args[0] == "bootout":
            launchd["loaded"] = False
            launchd["exiting"] = 3
        elif args[0] == "bootstrap":
            if launchd["loaded"] or launchd["exiting"]:
                return Res(5, "Bootstrap failed: 5: Input/output error")
            launchd["loaded"] = True
        return Res()

    orig = service.plist_path, service.run_launchctl, service.UNLOAD_WAIT_S, service.go_arguments
    try:
        service.go_arguments = GO_ARGUMENTS
        service.write_plist(path=plist_file, home=home)
        service.plist_path = lambda: plist_file
        service.run_launchctl = fake
        ok, msg = service.install(home=home)
        check("service: one reinstall over a running job leaves it loaded", ok and launchd["loaded"], msg)
        check("service: it bootstraps only once launchd has dropped the old label, and only once",
              calls.count("bootstrap") == 1 and calls.index("bootstrap") > calls.index("bootout") + 3,
              str(calls))

        calls.clear()
        launchd.update(loaded=True, exiting=10**6)
        service.UNLOAD_WAIT_S = 0.3
        ok, msg = service.install(home=home)
        check("service: an old job that will not exit is reported, not raced with a bootstrap",
              not ok and "still exiting" in msg and "bootstrap" not in calls, msg)
    finally:
        service.plist_path, service.run_launchctl, service.UNLOAD_WAIT_S, service.go_arguments = orig


def test_service_install_names_only_a_daemon_launchd_does_not_run_as_hand_started(home: Path) -> None:
    import contextlib
    import io
    from handline.cli import service as cli_service
    orig = cli_service.daemonlock.running_elsewhere, service.status, service.install
    try:
        service.install = lambda allow_live_home=False: (True, "installed")
        cli_service.daemonlock.running_elsewhere = lambda: 4242
        for launchd_pid, noted in ((4242, False), (None, True), (77, True)):
            service.status = lambda p=launchd_pid: {"installed": True, "loaded": p is not None,
                                                    "pid": p, "last_exit_code": None}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli_service.cmd_service_install(None)
            check(f"service install: the 'running by hand' note {'shows' if noted else 'is quiet'} "
                  f"when launchd's pid is {launchd_pid}", ("by hand" in out.getvalue()) == noted, out.getvalue())
    finally:
        cli_service.daemonlock.running_elsewhere, service.status, service.install = orig


def test_service_status_when_not_installed(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    orig = service.plist_path
    called = {"n": 0}

    def must_not_run(args):
        called["n"] += 1
        raise AssertionError("run_launchctl called for an uninstalled service")

    orig_runner = service.run_launchctl
    try:
        service.plist_path = lambda: home / "nope.plist"
        service.run_launchctl = must_not_run
        st = service.status()
        check("service: status() skips launchctl when no plist exists",
              not st["installed"] and not st["loaded"] and called["n"] == 0)
    finally:
        service.plist_path = orig
        service.run_launchctl = orig_runner


def test_service_status_when_the_plist_cannot_be_looked_at(home: Path) -> None:
    """T431: a sandboxed agent cannot stat ~/Library/LaunchAgents; launchd is asked,
    so a daemon it runs is not reported as hand-started."""
    import os
    walled = home / "walled"
    walled.mkdir(parents=True, exist_ok=True)
    orig, orig_runner = service.plist_path, service.run_launchctl
    calls: list = []

    class Printed:
        returncode, stderr = 0, ""
        stdout = "\tpid = 31338\n\tlast exit code = 0\n"

    def fake_print(args):
        calls.append(args)
        return Printed()

    try:
        service.plist_path = lambda: walled / "job.plist"
        service.run_launchctl = fake_print
        os.chmod(walled, 0)
        st = service.status()
        check("service: an unlookable plist makes status() ask launchd",
              st["installed"] and st["loaded"] and st["pid"] == 31338 and calls, str(st))
        check("service: that pid is supervised by launchd, not hand-started",
              service.supervisor_of(31338) == f"launchd ({service.LABEL})")
    finally:
        os.chmod(walled, 0o700)
        service.plist_path, service.run_launchctl = orig, orig_runner


def test_service_supervisor_of(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    orig = service.status
    try:
        service.status = lambda: {"installed": True, "loaded": True, "pid": 111,
                                  "last_exit_code": "0"}
        check("service: pid matches launchd -> supervised",
              service.supervisor_of(111) == f"launchd ({service.LABEL})")
        check("service: pid does not match launchd -> hand-started",
              "hand-started" in service.supervisor_of(222))

        service.status = lambda: {"installed": False, "loaded": False, "pid": None,
                                  "last_exit_code": None}
        check("service: no pid, no service -> not running",
              service.supervisor_of(None) == "not running")

        service.status = lambda: {"installed": True, "loaded": False, "pid": None,
                                  "last_exit_code": None}
        check("service: no pid, installed but unloaded -> says so",
              "not loaded" in service.supervisor_of(None))
    finally:
        service.status = orig


def test_service_plist_runs_the_go_daemon_and_the_live_home_needs_a_word(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    exe = home / "handlined"
    exe.write_text("")
    real = (service.go_executable, paths.live_homes, service.shutil.which)
    service.go_executable = lambda: exe
    paths.live_homes = lambda: [(home / "live").resolve()]
    service.shutil.which = lambda name: f"/opt/{name}"
    try:
        os.environ["OCP_SERVICE_LABEL"] = "dev.orcapipe.rehearsal"
        go = service.build_plist(home=home)
        del os.environ["OCP_SERVICE_LABEL"]
        args = go["ProgramArguments"]
        check("service: the Go daemon runs on the scratch home with the production flags",
              args == [str(exe), "-home", str(home), "-structured-cli", "/opt/claude", "-structured-tick", "-terminal-tick", "-cutover"], str(args))
        check("service: the Go job carries a PATH that holds python3's directory",
              "/opt" in go["EnvironmentVariables"]["PATH"].split(":"), str(go["EnvironmentVariables"]))
        try:
            service.build_plist(home=home)
            unlabelled = False
        except service.ServiceError:
            unlabelled = True
        check("service: a scratch home on the live label is refused for the Go daemon", unlabelled)
        os.environ["OCP_SERVICE_LABEL"] = "dev.orcapipe.rehearsal"
        try:
            service.build_plist(home=home / "live")
            refused = False
        except service.ServiceError:
            refused = True
        live = service.build_plist(home=home / "live", allow_live_home=True)["ProgramArguments"]
        check("service: the rehearsal label names the job and the plist", go["Label"] == "dev.orcapipe.rehearsal")
        check("service: the live home is refused without the word, and with it gets -allow-real-home",
              refused and live[1] == "-allow-real-home" and "-home" not in live, str(live))
        exe.unlink()
        try:
            service.build_plist(home=home)
            missing = False
        except service.ServiceError:
            missing = True
        check("service: a Go daemon that is not built is refused", missing)
    finally:
        os.environ.pop("OCP_SERVICE_LABEL", None)
        service.go_executable, paths.live_homes, service.shutil.which = real


def _daemon_impl() -> str | None:
    """The daemon_impl word in config.yaml, as `hl service use` wrote it."""
    try:
        return yamlcompat.load(paths.config_file().read_text()).get(service.IMPL_KEY)
    except OSError:
        return None


def test_service_use_sets_the_daemon_the_next_install_runs(home: Path) -> None:
    import argparse, contextlib, io
    from handline.cli import service as cli_service
    use_home(home)
    rc = cli_service.cmd_service_use(argparse.Namespace(impl="go"))
    check("service use: go is the setting afterwards", rc == 0 and _daemon_impl() == "go")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli_service.cmd_service_use(argparse.Namespace(impl="python"))
    check("service use: python is refused in one line that says why, and the setting stays go",
          rc == 1 and _daemon_impl() == "go" and out.getvalue() == service.PYTHON_RETIRED + "\n", out.getvalue())
    fresh = home / "not-made-yet" / "home"
    os.environ["OCP_HOME"] = str(fresh)
    rc = cli_service.cmd_service_use(argparse.Namespace(impl="go"))
    check("service use: go on a home that does not exist yet makes it", rc == 0 and _daemon_impl() == "go")


def test_service_use_from_an_agent_session_is_a_proposal(home: Path) -> None:
    import argparse, os
    from handline.cli import service as cli_service
    from handline.team import ask, hr
    use_home(home)
    got = []
    real_propose, real_asking, saved = hr.propose, ask.asking_agent, os.environ.get("OCP_AGENT")
    hr.propose = lambda kind, name, requester, why, sets: (got.append((kind, name, sets, why)) or (True, "proposed"))
    ask.asking_agent = lambda: "worker"
    os.environ["OCP_AGENT"] = "worker"
    try:
        rc = cli_service.cmd_service_use(argparse.Namespace(impl="go", why="the cutover"))
        check("service use: an agent's session raises a global proposal with the value and why",
              rc == 0 and got == [("global", "service-use", {"value": "go"}, "the cutover")], got)
        check("service use: and changes nothing yet", _daemon_impl() is None)
        check("service use: without --why it is refused",
              cli_service.cmd_service_use(argparse.Namespace(impl="go", why=None)) == 1 and len(got) == 1)
        hr._set_global("service-use", "go")
        check("service use: approving the proposal sets daemon_impl", _daemon_impl() == "go")
        ok, said = real_propose("global", "service-use", requester="worker", why="x", sets={"value": "rust"})
        check("service use: a daemon that does not exist is not proposed", not ok, said)
        ok, said = real_propose("global", "service-use", requester="worker", why="x", sets={"value": "python"})
        check("service use: nor is python, and the refusal says it is gone", not ok and "is gone" in said, said)
        check("service use: an agent's session is refused python before any proposal",
              cli_service.cmd_service_use(argparse.Namespace(impl="python", why="back")) == 1 and len(got) == 1)
    finally:
        hr.propose, ask.asking_agent = real_propose, real_asking
        hr._set_global("service-use", "python")
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved


def test_service_crash_loop_reads_the_go_daemons_start_line(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    log = service.err_log(home)
    log.parent.mkdir(parents=True, exist_ok=True)
    now = time.localtime()
    lines = [time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(time.mktime(now) - s)) + ' handlined abc1234 serving "/x/daemon.sock"' for s in (0, 15, 30, 45)]
    log.write_text("\n".join(lines) + "\n")
    check("service: four starts of the Go daemon in a minute is a crash loop", service.crash_loop_detected(home, window_s=300, threshold=4))
    log.write_text(lines[0] + "\n")
    check("service: one start is not", not service.crash_loop_detected(home, window_s=300, threshold=4))
    spread = [time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(time.mktime(now) - s)) + ' handlined abc1234 serving "/x/daemon.sock"' for s in (0, 3600, 7200, 10800)]
    log.write_text("\n".join(spread) + "\n")
    check("service: nor are starts spread across hours", not service.crash_loop_detected(home, window_s=300, threshold=4))
    refusals = [line.replace('handlined abc1234 serving "/x/daemon.sock"', "handlined: another daemon holds the lock") for line in lines]
    log.write_text("\n".join(refusals) + "\n")
    check("service: four refusals of the Go daemon in a minute is a crash loop", service.crash_loop_detected(home, window_s=300, threshold=4))
    quoted = [line.replace("handlined:", "caller said handlined: and") for line in refusals]
    log.write_text("\n".join(quoted) + "\n")
    check("service: a line that only contains ' handlined: ' is not a start", not service.crash_loop_detected(home, window_s=300, threshold=4))
    log.write_text("\n".join("x " + line for line in refusals) + "\n")
    check("service: nor is one that has something before the date", not service.crash_loop_detected(home, window_s=300, threshold=4))


def test_service_log_rotation(tmp: Path) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    log = tmp / "daemon.out.log"
    log.write_text("keep this line\n" * 100)
    size_before = log.stat().st_size

    check("service: rotate_log is a no-op below the size threshold",
          not service.rotate_log(log, max_bytes=size_before + 1, backups=2))

    rotated = service.rotate_log(log, max_bytes=size_before, backups=2)
    check("service: rotate_log rotates once over threshold", rotated)
    backup = log.with_name(f"{log.name}.1.gz")
    check("service: rotate_log gzips the old content", backup.exists())
    check("service: rotate_log truncates the live file in place", log.stat().st_size == 0)

    import gzip as gzmod
    with gzmod.open(backup, "rt") as f:
        saved = f.read()
    check("service: the rotated backup keeps the lines that explain the last incident",
          "keep this line" in saved)

    # A second rotation must not clobber the first backup - it shifts it down.
    log.write_text("second incident\n" * 100)
    service.rotate_log(log, max_bytes=size_before, backups=2)
    backup2 = log.with_name(f"{log.name}.2.gz")
    check("service: an older backup is kept, not overwritten", backup2.exists())
    with gzmod.open(backup2, "rt") as f:
        check("service: the shifted backup is the first incident, not the second",
              "keep this line" in f.read())


def test_a_running_agent_is_told_when_work_arrives(home: Path) -> None:
    """`ocp ask` writes a row and an inbox file; neither reaches the agent's
    screen. Without delivery, every request needs a human to nudge a terminal -
    which is how this system was actually being run."""
    _daemon_home(home, 20)
    con = db.connect()
    fixture_clock = db.last_activity(con, "backend")
    sent: list[str] = []
    real = (termmod.alive, termmod.idle_now, termmod.send, termmod.available)
    try:
        orca_seen()
        termmod.idle_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        # A running agent, so there is something in that terminal to tell.
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)

        check("deliver: nothing is sent when nothing is waiting",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None))

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", "task", 0, None)
        check("deliver: a waiting request reaches the agent",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None))
        check("deliver: and it is told how to read it", "hl inbox" in sent[-1], sent[-1])
        check("deliver: and who it is from", "reviewer" in sent[-1], sent[-1])

        sent.clear()
        check("deliver: the same request is not announced twice",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # Request #189: one at a time. A second request behind the first is
        # queued, but the head has not changed - backend is not re-told, and
        # it is not shown the second one's contents at all.
        db.add_request(con, "reviewer", "backend", "and this", None,
                       "peer", "async", "task", 0, None)
        check("deliver: a request behind the head does not get its own delivery",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))

        # Typing at an agent mid-turn leaves the text in its input box, unsent.
        termmod.idle_now = lambda h: False
        db.add_request(con, "reviewer", "backend", "later", None,
                       "peer", "async", "task", 0, None)
        sent.clear()
        check("deliver: a busy agent is left alone until it is free",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # Being told is not the agent doing something, so it must not count as
        # activity - or an agent we keep reminding never looks idle enough to
        # close, and it never was awake in the first place.
        check("deliver: being told is not activity",
              db.last_activity(con, "backend") == fixture_clock,
              str(db.last_activity(con, "backend")))

        # Resolving the head reveals the next one - a different id - and
        # resolve() delivers it right away, the same as asking delivers to an
        # idle target right away rather than waiting for the next tick.
        termmod.idle_now = lambda h: True
        head = db.head_of_queue(con, "backend")
        sent.clear()
        db.set_cursor(con, "batch:backend", 0)            # the minute is up
        askmod.resolve(head["id"], "done")
        check("deliver: resolving the head delivers what is behind it once the minute is up",
              sent and "and this" in sent[-1] and "look at this" not in sent[-1], str(sent))
    finally:
        termmod.alive, termmod.idle_now, termmod.send, termmod.available = real
        con.close()


def test_an_agent_is_never_handed_another_agents_session(home: Path) -> None:
    """Two agents share an account, and the account file belongs to whichever
    session wrote it last. Carrying its session id through the fallbacks told a
    woken agent to grep a stranger's transcript, in a stranger's repository -
    which happened to auditor in production, pointed at marina's."""
    use_home(home)
    agents.register("selfless")
    write_status(home, "beta", pct_5h=20, ctx_pct=25, session_id="MARINA-SESSION",
                 transcript_path="/tmp/marina.jsonl")

    st = status.read_for_agent("selfless", "beta")
    check("identity: an agent that has never run has no session",
          st.session_id is None, str(st.session_id))
    check("identity: nor a transcript to be sent to",
          st.transcript_path is None, str(st.transcript_path))
    check("identity: nor a context to be judged on", st.ctx_pct is None, str(st.ctx_pct))

    # The ordinary case, not an edge: nothing refreshes an agent's own file
    # while its session is closed, so by wake time it is stale by construction.
    f = paths.agent_status_file("selfless")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        "account": "beta", "collected_at": int(time.time()) - status.STALE_AFTER_S - 60,
        "session_id": "MY-OWN-SESSION", "ctx_pct": 8,
        "transcript_path": "/tmp/mine.jsonl"}))
    st = status.read_for_agent("selfless", "beta")
    check("identity: a stale own snapshot is not topped up from the account file",
          st.session_id is None and st.transcript_path is None,
          f"{st.session_id} {st.transcript_path}")

    f.write_text(json.dumps({
        "account": "beta", "collected_at": int(time.time()),
        "session_id": "MY-OWN-SESSION", "ctx_pct": 8,
        "transcript_path": "/tmp/mine.jsonl"}))
    st = status.read_for_agent("selfless", "beta")
    check("identity: a fresh one is its own", st.session_id == "MY-OWN-SESSION",
          str(st.session_id))

    # And the prompt says nothing about a transcript it cannot name.
    con = db.connect()
    try:
        f.unlink()
        told = dmn_lifecycle.wake_prompt("selfless", resumed=False, was_closed=True, con=con)
        check("wake: no transcript is named when none can be named confidently",
              "/tmp/marina.jsonl" not in told and "Grep it" not in told, told[:200])
        told = dmn_lifecycle.wake_prompt("selfless", resumed=True, waiting="#4 from marina, waiting for you:\nhi")
        check("wake: a resume is handed the message and nothing else",
              told == "#4 from marina, waiting for you:\nhi", told)
    finally:
        con.close()


def test_a_woken_session_is_not_closed_while_its_wake_waits_for_the_first_turn(home: Path) -> None:
    """The wake of an agent with no pane holds its terminal until the session's
    first turn is over. The session is running for all of it, so the closed mark
    comes off when the session takes its prompt - not at the end - and the stuck
    list does not send a person to `w` while that hold would refuse it."""
    from handline.infra import lock
    _daemon_home(home, 20)
    con = db.connect()
    at_wait: list = []
    real = (spawnmod.start_in, termmod.send)
    try:
        orca_seen()
        table = sessions_are()
        termmod.idle_now = lambda h: True

        def wait_idle(h, timeout_ms=0):
            row = db.get_agent_state(con, "backend")
            at_wait.append((row["closed_at"], row["resume_session"]))
            db.set_cursor(con, "ahead:backend:99", 1)       # a tell made to the running session meanwhile
            return True
        termmod.wait_idle = wait_idle

        def start_in(agent, worktree, cmd=None):
            db.set_terminal(con, agent, "term_new", worktree)
            table[agent] = 4242
            take_launched_prompt(cmd)
            return True, "started"
        spawnmod.start_in = start_in
        termmod.live_terminals = lambda: {"term_new"}
        wt = home / "wt"
        wt.mkdir()
        db.set_terminal(con, "backend", None, str(wt))
        db.set_closed(con, "backend", "sess-4")
        db.add_request(con, "reviewer", "backend", "look at this", None, "peer", "async", None, 0, None)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()), "session_id": "sess-4", "pct_5h": 20}))

        ok, said = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: the session was running, and not closed, when the wake began to wait for its first turn",
              ok and at_wait == [(None, None)], f"{ok} {said} {at_wait}")
        check("wake: and it is not closed afterwards either",
              not db.get_agent_state(con, "backend")["closed_at"])
        check("wake: a tell made to the session while the wake waited is not voided by the end of the wake",
              db.get_cursor(con, "ahead:backend:99") == 1, str(db.get_cursor(con, "ahead:backend:99")))

        db.set_closed(con, "backend", "sess-4")
        with lock.terminal("backend") as mine:
            state = actions._delivery_state(con, "backend", {}, {"term_new"})
            check("stuck: while the terminal is held the reason is not an invitation to press w",
                  mine and state[0] == "closed, starting" and state[2] and " w " not in state[1],
                  str(state))
        state = actions._delivery_state(con, "backend", {}, {"term_new"})
        check("stuck: once it is free the closed reason is back", state[0] != "closed, starting", str(state))
        check("stuck: an agent whose lock file does not exist is not held", lock.terminal_busy("never-locked") is False)
    finally:
        spawnmod.start_in, termmod.send = real
        con.close()


def test_every_session_start_is_kept_with_its_cause_and_what_it_was_given(home: Path) -> None:
    """Each start the chat marks - why it began, whether it resumed, the handoff record and first prompt
    it was given - is kept by `db.session_starts`, claimed by the session's own SessionStart."""
    _daemon_home(home, 20)
    con = db.connect()
    real_agent = os.environ.get("OCP_AGENT")
    try:
        os.environ["OCP_AGENT"] = "backend"
        db.set_handoff(con, "backend", "the record: carry on with #12")
        launch._keep_what_it_starts_with("backend", [], "pick the task back up", "wake")
        rows = db.session_starts(con, "backend")
        check("session starts: a fresh launch keeps its cause, the record and the prompt, before any session names itself",
              len(rows) == 1 and rows[0]["cause"] == "wake" and rows[0]["resumed"] == 0
              and rows[0]["text"] == "the record: carry on with #12" and rows[0]["prompt"] == "pick the task back up"
              and rows[0]["session_id"] is None, str([dict(r) for r in rows]))
        support._fire_hook("SessionStart", source="clear", session_id="s-clear")
        check("session starts: a /clear's SessionStart does not claim it", db.session_starts(con, "backend")[0]["session_id"] is None)
        support._fire_hook("SessionStart", source="startup", session_id="s-new")
        check("session starts: the new session's SessionStart names it", db.session_starts(con, "backend")[0]["session_id"] == "s-new")

        launch._keep_what_it_starts_with("backend", ["--resume", "s-old"], "an event", "restart")
        support._fire_hook("SessionStart", source="resume", session_id="s-old")
        rows = db.session_starts(con, "backend")
        check("session starts: a resume is kept too - resumed, its own session, the event it was given, and the "
              "record its system prompt carries (`context_for` puts a waiting one there for a resume as well)",
              len(rows) == 2 and rows[1]["cause"] == "restart" and rows[1]["resumed"] == 1
              and rows[1]["session_id"] == "s-old" and rows[1]["text"] == "the record: carry on with #12"
              and "the record: carry on with #12" in launch.context_for("backend")
              and rows[1]["prompt"] == "an event", str([dict(r) for r in rows]))

        launch._keep_what_it_starts_with("backend", [], "p", "restart", "orcapipe")
        last = db.session_starts(con, "backend", limit=1)[0]
        check("session starts: who asked for a restart is kept with it", last["actor"] == "orcapipe", str(dict(last)))
        con.execute("DELETE FROM session_starts WHERE id = (SELECT MAX(id) FROM session_starts)")

        db.set_handoff(con, "backend", None)
        launch._keep_what_it_starts_with("backend", [], None, None)
        last = db.session_starts(con, "backend", limit=1)[0]
        check("session starts: a launch by hand with nothing is a 'launch' with no text and no prompt",
              last["cause"] == "launch" and last["text"] == "" and last["prompt"] == "", str(dict(last)))

        con.execute("UPDATE session_starts SET ts = ts - 7200 WHERE cause = 'launch'")
        support._fire_hook("SessionStart", source="startup", session_id="s-late")
        got = db.session_starts(con, "backend")
        check("session starts: they come back oldest first, and a launch too old is not claimed",
              [r["cause"] for r in got] == ["wake", "restart", "launch"] and got[2]["session_id"] is None,
              str([dict(r) for r in got]))
        check("session starts: a limit gives the newest, still in order",
              [r["cause"] for r in db.session_starts(con, "backend", limit=2)] == ["restart", "launch"])

        db.set_handoff(con, "backend", "x" * (db.handoff.SESSION_TEXT_MAX_CHARS + 500))
        for _ in range(db.handoff.SESSION_STARTS_KEPT + 5):
            launch._keep_what_it_starts_with("backend", [], "p", "wake")
        got = db.session_starts(con, "backend")
        check("session starts: a record is cut and an agent keeps only its latest few",
              len(got) == db.handoff.SESSION_STARTS_KEPT
              and len(got[-1]["text"]) == db.handoff.SESSION_TEXT_MAX_CHARS, str(len(got)))
        check("session starts: and another agent's are not read", not db.session_starts(con, "frontend"))

        # And it is the launch itself that writes it: `exec_agent`, the process that becomes the session.
        db.set_handoff(con, "backend", "record via exec")
        pf = home / "wake-prompt.txt"
        pf.write_text("the wake prompt")
        real_exec = (launch.build, launch._record_terminal, launch.fingerprint, os.execve)
        launch.build = lambda *a, **k: (["claude"], {})
        launch._record_terminal = lambda *a, **k: None
        launch.fingerprint = lambda agent: "fp"
        os.execve = lambda *a: None
        try:
            launch.exec_agent("backend", [], False, str(pf), why="move")
        finally:
            launch.build, launch._record_terminal, launch.fingerprint, os.execve = real_exec
        last = db.session_starts(con, "backend", limit=1)[0]
        check("session starts: exec_agent writes it, with the cause it was told and the prompt as it took it",
              last["cause"] == "move" and last["text"] == "record via exec" and last["prompt"] == "the wake prompt",
              str(dict(last)))
    finally:
        if real_agent is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = real_agent
        con.close()


def test_an_agent_idle_past_its_prompt_cache_comes_back_fresh_with_its_work(home: Path) -> None:
    """A resume after the cache expired re-reads the whole transcript uncached;
    a fresh conversation reads the handoff. The agent that comes back fresh must
    still be able to carry on: the handoff taken when it was closed names the
    request it had in hand and its transcript, is in the new session's system
    prompt, and the request is still its own."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    sent: list[str] = []
    seen: list[str] = []
    real = (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send,
            termmod.available, termmod.clear_context)
    try:
        orca_seen()
        termmod.wait_idle = lambda h, timeout_ms=0: True
        termmod.idle_now = lambda h: True
        termmod.close_terminal = lambda h, tab=True: (False, "this test keeps the pane")
        table = sessions_are("backend")
        relaunch = relaunching_send(sent, table)

        def send(handle, text, enter=True):
            if " launch " in text:
                # What the new session is handed as its system prompt.
                seen.append(launch.context_for("backend"))
            return relaunch(handle, text, enter)
        termmod.send = send

        wt = home / "wt"
        wt.mkdir()
        subprocess.run(["git", "init", "-q", str(wt)], check=True)
        subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        db.set_terminal(con, "backend", "term_x", str(wt))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()), "session_id": "sess-7",
            "transcript_path": "/tmp/sess-7.jsonl", "pct_5h": 20}))
        rid = db.add_request(con, "reviewer", "backend", "review the picker branch", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        record = db.get_handoff(con, "backend") or ""
        check("cache: closing mid-task recorded the request in hand and the transcript",
              f"request: #{rid}" in record and "/tmp/sess-7.jsonl" in record, record)

        def wake_after(idle_s: int) -> tuple[list[str], list[str]]:
            con.execute("DELETE FROM events WHERE agent = 'backend' AND kind = 'Stop'")
            db.add_event(con, "backend", "Stop", {})
            con.execute("UPDATE events SET ts = ? WHERE agent = 'backend' AND kind = 'Stop'",
                        (int(time.time()) - idle_s,))
            sent.clear()
            seen.clear()
            db.set_handoff(con, "backend", record)
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
            check(f"cache: the wake after {idle_s}s goes through", ok, why)
            table["backend"] = 4242
            return list(sent), list(seen)

        # Still inside the cache's life: resumed, as before.
        warm_sent, _ = wake_after(dmn_timing.RESUME_WARM_S - 120)
        check("cache: an agent idle inside the cache's life is resumed",
              any("--resume sess-7" in t for t in warm_sent), str(warm_sent))

        # Past it: a fresh conversation that is handed the handoff.
        db.set_closed(con, "backend", "sess-7")
        support.something_waiting(con, "backend")
        table.clear()
        cold_sent, cold_ctx = wake_after(dmn_timing.RESUME_WARM_S + 60)
        check("cache: an agent idle past the cache's life is not resumed",
              cold_sent and not any("--resume" in t for t in cold_sent), str(cold_sent))
        check("cache: it is told why, and where its handoff is",
              any("fresh one" in t and "system prompt" in t for t in cold_sent), str(cold_sent))
        check("cache: the new session's system prompt carries the request in hand and the transcript",
              cold_ctx and f"request: #{rid}" in cold_ctx[0] and "/tmp/sess-7.jsonl" in cold_ctx[0],
              str(cold_ctx))
        head = db.head_of_queue(con, "backend")
        check("cache: the request is still its own, delivered and open",
              head is not None and head["id"] == rid and head["delivered_at"], str(head and dict(head)))
        check("cache: the wake is recorded as a fresh start for that reason",
              con.execute("SELECT 1 FROM events WHERE kind = 'wake' AND payload LIKE '%cache_expired%'").fetchone())
    finally:
        (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send,
         termmod.available, termmod.clear_context) = real
        con.close()


def test_the_cache_clock_decides_a_resume_not_whether_a_handoff_exists(home: Path) -> None:
    """A close that failed leaves no handoff; that must not switch the cache
    check off. An agent with no clock is treated as cold."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    try:
        now = int(time.time())
        db.add_event(con, "backend", "Stop", {})
        for handoff in (None, "# handoff"):
            db.set_handoff(con, "backend", handoff)
            for idle, cold in ((dmn_timing.RESUME_WARM_S + 60, True), (dmn_timing.RESUME_WARM_S - 120, False)):
                con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - idle,))
                check(f"cache: handoff={handoff!r}, idle {idle}s is {'cold' if cold else 'warm'}",
                      dmn_lifecycle.cache_expired(con, "backend", "sess-7", now) == cold)
        check("cache: with no session id there is nothing to skip",
              not dmn_lifecycle.cache_expired(con, "backend", None, now))
        con.execute("DELETE FROM events WHERE agent = 'backend'")
        for handoff in (None, "# handoff"):
            db.set_handoff(con, "backend", handoff)
            check(f"cache: no activity clock (handoff={handoff!r}) reads as cold",
                  dmn_lifecycle.cache_expired(con, "backend", "sess-7", now))
    finally:
        con.close()


def test_a_cold_wake_that_fails_leaves_no_handoff_behind(home: Path) -> None:
    """A record no session was handed would be typed at the next live session."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    real = (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send,
            termmod.available, termmod.clear_context, termmod.relaunch,
            termmod.typeable_now, termmod.resume_with)
    logs: list[str] = []
    try:
        orca_seen()
        termmod.relaunch = lambda handle, cmd, started=None: (False, "no session started")
        con.execute("DELETE FROM events WHERE agent = 'backend'")
        db.set_closed(con, "backend", "sess-7")
        support.something_waiting(con, "backend")
        db.set_handoff(con, "backend", None)
        ok, _ = dmn_lifecycle.wake_agent(con, "backend", logs.append)
        check("cold wake: a failed launch is reported", not ok)
        check("cold wake: its own note is not activity, so the next wake is still cold",
              db.last_activity(con, "backend") is None
              and dmn_lifecycle.cache_expired(con, "backend", "sess-7", int(time.time())))
        check("cold wake: it leaves no handoff on the row", db.get_handoff(con, "backend") is None)
        # The session turned up after all and the sweep cleared the closed mark:
        # the next wake must not type a parked-and-cleared prompt at it.
        db.clear_closed(con, "backend")
        typed: list[str] = []
        sessions_are("backend")
        termmod.idle_now = lambda h: True
        termmod.typeable_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (typed.append(text), (True, ""))[1]
        termmod.resume_with = lambda h, text: (typed.append(text), (True, ""))[1]
        ok2, _ = dmn_lifecycle.wake_agent(con, "backend", logs.append)
        check("cold wake: the next wake at a live session types nothing", not ok2 and not typed,
              str(typed))
        db.set_closed(con, "backend", "sess-7")
        support.something_waiting(con, "backend")
        check("cold wake: the closed mark and resume id survive",
              (db.get_agent_state(con, "backend") or {})["resume_session"] == "sess-7")
        check("cold wake: a transcript it could not find is logged and on the record",
              any("degraded" in m for m in logs)
              and con.execute("SELECT 1 FROM events WHERE kind = 'handoff' AND payload LIKE '%degraded%'").fetchone(),
              str(logs))
    finally:
        (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send,
         termmod.available, termmod.clear_context, termmod.relaunch,
         termmod.typeable_now, termmod.resume_with) = real
        con.close()


def test_a_wake_does_not_call_a_fresh_start_a_park(home: Path) -> None:
    """Telling an agent that could not be resumed "your context was cleared,
    below is your own handoff record" says it was parked when it was not. A
    fresh start after a close finds its record in the system prompt of the
    session it started, so the prompt points there and does not repeat it."""
    _daemon_home(home, 20)
    keep_handoff("backend", "# handoff · backend\n- was mid-way through the routing fix")
    con = db.connect()
    try:
        told = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("wake: a fresh start after a close is not called a park",
              "You were parked" not in told, told[:140])
        check("wake: and its handoff is in the system prompt, not typed again",
              "routing fix" not in told and "system prompt above" in told, told[:200])
        db.set_handoff(con, "backend", None)
        told = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("wake: with none recorded it is told so",
              "no handoff" in told.lower(), told[:200])
    finally:
        con.close()

    keep_handoff("backend", "# handoff · backend\n- was mid-way through the routing fix")
    con = db.connect()
    try:
        told = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=False, con=con)
    finally:
        con.close()
    check("wake: a real park still gets its record",
          "You were parked" in told and "routing fix" in told, told[:200])


def test_a_move_prompt_says_nothing_of_a_request_or_a_missing_transcript(home: Path) -> None:
    """T169: a move's handoff is already in the new session's system prompt, so the wake prompt
    names neither the request in hand nor a transcript it could not find - only a transcript that
    was found, and only where a fresh start would say all three."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        moved = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, moved_to="beta", con=con)
        fresh = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("move prompt: no request line", "request:" not in moved, moved)
        check("move prompt: no transcript-not-found line", "could not be found" not in moved, moved)
        check("move prompt: only the move itself is said", moved.startswith("Your account was at its 5h limit")
              and "\n\n" not in moved, moved)
        check("fresh start with no record: the same two lines are said (so the move's silence is chosen)",
              "request:" in fresh and "could not be found" in fresh, fresh)

        db.add_event(con, "backend", "UserPromptSubmit",
                     {"session_id": "sess-9", "transcript_path": "/tmp/sess-9.jsonl"})
        moved = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, moved_to="beta", con=con)
        check("move prompt: a transcript that is found is still named",
              "sess-9" in moved and "/tmp/sess-9.jsonl" in moved, moved)
        check("move prompt: and still no request line", "request:" not in moved, moved)
    finally:
        con.close()


def test_the_previous_sessions_transcript_path_can_come_from_its_own_hook_event(home: Path) -> None:
    """T169: when the status snapshot names no path, the newest hook event of a session that was
    talked to supplies it - but only for the very session that is being restarted."""
    _daemon_home(home, 20)
    con = db.connect()
    f = paths.agent_status_file("backend")
    f.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(**fields) -> None:
        f.write_text(json.dumps({"account": "beta", "collected_at": int(time.time()), **fields}))

    try:
        db.add_event(con, "backend", "SessionStart",
                     {"session_id": "sess-0", "transcript_path": "/tmp/sess-0.jsonl"})
        check("previous session: a session that was never talked to is not the one restarted from",
              handoffmod.previous_session(con, "backend", "beta") == (None, None),
              str(handoffmod.previous_session(con, "backend", "beta")))

        db.add_event(con, "backend", "Stop", {"session_id": "sess-1", "transcript_path": "/tmp/sess-1.jsonl"})
        check("previous session: with no snapshot, id and path both come from the event",
              handoffmod.previous_session(con, "backend", "beta") == ("sess-1", "/tmp/sess-1.jsonl"),
              str(handoffmod.previous_session(con, "backend", "beta")))

        snapshot(session_id="sess-1")
        check("previous session: a snapshot with the id but no path takes the event's path for that id",
              handoffmod.previous_session(con, "backend", "beta") == ("sess-1", "/tmp/sess-1.jsonl"),
              str(handoffmod.previous_session(con, "backend", "beta")))

        snapshot(session_id="sess-2")
        got = handoffmod.previous_session(con, "backend", "beta")
        check("previous session: the event's path is never lent to a different session",
              got[0] == "sess-2" and got[1] != "/tmp/sess-1.jsonl", str(got))

        snapshot(session_id="sess-2", transcript_path="/tmp/sess-2.jsonl")
        check("previous session: a snapshot that names both is used as it is",
              handoffmod.previous_session(con, "backend", "beta") == ("sess-2", "/tmp/sess-2.jsonl"),
              str(handoffmod.previous_session(con, "backend", "beta")))
    finally:
        con.close()


def test_orca_failing_to_list_terminals_reads_as_unknown() -> None:
    """The sweep's unknown-versus-empty distinction has to survive into the implementation, not only the fake."""
    real_run = orcacli._run
    try:
        orcacli._run = lambda *a, **k: (False, "connection refused")
        check("sweep: Orca failing to answer reads as unknown, not as empty",
              termmod.live_terminals() is None, str(termmod.live_terminals()))
        orcacli._run = lambda *a, **k: (True, "not json at all")
        check("sweep: and so does an answer we cannot parse",
              termmod.live_terminals() is None)
        orcacli._run = lambda *a, **k: (True, json.dumps(
            {"result": {"terminals": [{"handle": "term_a"}, {"nohandle": 1}]}}))
        check("sweep: a real answer is the set of handles",
              termmod.live_terminals() == {"term_a"}, str(termmod.live_terminals()))
    finally:
        orcacli._run = real_run


def test_a_wake_does_not_claim_a_session_it_did_not_start(home: Path) -> None:
    """orcapipe.glm, 09-23: the wake's launch was refused, and a launch typed
    by hand started the agent's session a moment later. Seeing that session,
    the wake logged "relaunched with its wake prompt" for a prompt nothing had
    taken. A session is the wake's only once its launch has taken the prompt
    file, and a refused launch leaves the file where it was. Once one is
    running, nothing more is typed: a fallback launch would land in it as a
    prompt.
    """
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (termmod.available, termmod.alive, termmod.wait_idle, termmod.send,
            dmn_delivery.WAKE_SESSION_TIMEOUT_S)
    try:
        dmn_delivery.WAKE_SESSION_TIMEOUT_S = 1
        orca_seen()
        termmod.wait_idle = lambda h, timeout_ms=0: True
        table = sessions_are()

        def refused_while_someone_else_launches(h, text, enter=True):
            if not text and enter:
                return True, ""        # a launch's Enter on its own (T663)
            sent.append(text)
            if " launch " in text:
                table["backend"] = 4300     # the hand-typed launch, no prompt file
            return True, ""
        termmod.send = refused_while_someone_else_launches
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", None)
        support.something_waiting(con, "backend")

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a session its launch did not start is not its wake", not ok, why)
        check("wake: so it is not recorded as woken",
              not any(e["kind"] == "wake" for e in con.execute(
                  "SELECT kind FROM events WHERE agent='backend'")), why)
        check("wake: and the prompt nothing took is removed",
              not any((home / "wake").glob("backend*")),
              str(list((home / "wake").glob("*"))))
        check("wake: and a running agent is not counted toward STALLED",
              row["wake_failures"] == 0, str(dict(row)))

        # With a resume id there is a fresh start to fall back to, and its
        # command would be typed into the session already running.
        table.clear()
        sent.clear()
        db.set_closed(con, "backend", "sess-abc")
        support.something_waiting(con, "backend")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: nothing is typed into a session it did not start",
              not ok and [" launch " in t for t in sent] == [True], f"{why} {sent}")
        check("wake: and that one is not counted either",
              row["wake_failures"] == 0, str(dict(row)))

        # Its own session, come up after the wait gave up, is no reason to
        # type the fallback into it either - but it is a counted failure.
        table.clear()
        sent.clear()
        db.set_closed(con, "backend", "sess-abc")
        support.something_waiting(con, "backend")
        realr = termmod.relaunch

        def own_but_late(h, cmd, **kw):
            sent.append(cmd)
            take_launched_prompt(cmd)
            table["backend"] = 4301
            return False, "no session seen within the timeout"
        termmod.relaunch = own_but_late
        try:
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        finally:
            termmod.relaunch = realr
        row = db.get_agent_state(con, "backend")
        check("wake: nothing more is typed into its own late session",
              not ok and len(sent) == 1, f"{why} {sent}")
        check("wake: and that one is counted", row["wake_failures"] == 1, str(dict(row)))

        # Auditor #1982 note 2, closed by #44's single writer: the closed
        # branch's very first send was never checked at all - only the read
        # taken after a failed attempt could catch a foreign session, so one
        # already live before the wake even started still got typed at.
        table.clear()
        sent.clear()
        db.reset_counter(con, "backend", "wake_failures")
        db.set_closed(con, "backend", "sess-def")
        support.something_waiting(con, "backend")
        table["backend"] = 4321        # live before this wake starts at all
        obsmod.live_sessions = lambda: dict(table)
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a session already live before the wake starts is never typed into",
              not ok and not sent, f"{why} {sent}")
        check("wake: and it is not counted either", row["wake_failures"] == 0, str(dict(row)))

        # Auditor #1982 note 2, second half: the check that ran after a
        # failed attempt was the only one - a session coming up right after
        # it, before the next attempt's own send, still got that send typed
        # into it. Each attempt now checks again, immediately before it types.
        table.clear()
        sent.clear()
        db.reset_counter(con, "backend", "wake_failures")
        db.set_closed(con, "backend", "sess-def")
        support.something_waiting(con, "backend")
        calls = {"n": 0}

        def appears_before_the_second_attempt():
            calls["n"] += 1
            # 1: this attempt's own pre-send check, empty. 2: the check after
            # it fails, still empty. 3: the fresh start's pre-send check -
            # a session has shown up in the gap since.
            return {"backend": 4321} if calls["n"] >= 3 else {}
        obsmod.live_sessions = appears_before_the_second_attempt
        realr = termmod.relaunch

        def refused_once(h, cmd, **kw):
            sent.append(cmd)
            return False, "refused"
        termmod.relaunch = refused_once
        try:
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        finally:
            termmod.relaunch = realr
        row = db.get_agent_state(con, "backend")
        check("wake: a session that appears before the fresh-start attempt stops that one too",
              not ok and len(sent) == 1, f"{why} {sent}")
        check("wake: and it is not counted, since it is not this wake's own",
              row["wake_failures"] == 0, str(dict(row)))

        # Auditor #2071 should-fix: the pre-send check must not always read a
        # live session as foreign. One that comes up here can be the previous
        # attempt's own, come up late after its relaunch call already
        # returned False - the same rule the post-send check above already
        # applies.
        table.clear()
        sent.clear()
        db.reset_counter(con, "backend", "wake_failures")
        db.set_closed(con, "backend", "sess-def")
        support.something_waiting(con, "backend")
        calls = {"n": 0}

        def appears_after_its_own_attempt_took_the_prompt():
            calls["n"] += 1
            # 1: the first attempt's own pre-send check, empty. 2: the check
            # after it fails, still empty - its launch already took the
            # prompt. 3: the fresh start's pre-send check - that same
            # attempt's own session has shown up since.
            return {"backend": 4322} if calls["n"] >= 3 else {}
        obsmod.live_sessions = appears_after_its_own_attempt_took_the_prompt
        realr = termmod.relaunch

        def took_but_no_session_seen(h, cmd, **kw):
            sent.append(cmd)
            take_launched_prompt(cmd)
            return False, "no session seen within the timeout"
        termmod.relaunch = took_but_no_session_seen
        try:
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        finally:
            termmod.relaunch = realr
        row = db.get_agent_state(con, "backend")
        check("wake: a session that turns out to be its own attempt's is not called foreign",
              not ok and why == "no session seen within the timeout", f"{why} {sent}")
        check("wake: so it counts toward STALLED, like any other failed attempt",
              row["wake_failures"] == 1, str(dict(row)))
    finally:
        (termmod.available, termmod.alive, termmod.wait_idle, termmod.send,
         dmn_delivery.WAKE_SESSION_TIMEOUT_S) = real
        con.close()


def test_a_wake_refuses_to_type_when_the_process_table_is_unreadable(home: Path) -> None:
    """T146 round 2 (auditor #1992): `live_sessions` returning None must not
    read as "nothing running" - typing over a session that might be there
    reaches it as a prompt. Refused the same way a confirmed foreign session
    is, but not counted against the wake-failure streak."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (termmod.available, termmod.alive, termmod.wait_idle, termmod.send,
            obsmod.live_sessions)
    try:
        orca_seen()
        termmod.wait_idle = lambda h, timeout_ms=0: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        obsmod.live_sessions = lambda: None
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-unknown")
        support.something_waiting(con, "backend")

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: an unreadable process table types nothing",
              not ok and not sent, f"{why} {sent}")
        check("wake: and says so, not a false foreign-session claim",
              "could not be confirmed" in why, why)
        check("wake: and it is not counted against the streak",
              row["wake_failures"] == 0, str(dict(row)))
    finally:
        (termmod.available, termmod.alive, termmod.wait_idle, termmod.send,
         obsmod.live_sessions) = real
        con.close()


def test_a_refused_launch_leaves_its_prompt(home: Path) -> None:
    """The prompt file is taken only by a launch that goes ahead: the daemon
    reads a file still there as "my launch started nothing"."""
    f = home / "prompt.txt"
    home.mkdir(parents=True, exist_ok=True)
    f.write_text("carry on")

    def refuse(*a, **k):
        raise SystemExit("refusing to start it there.")
    realb, realx, realr = launch.build, os.execve, launch._record_terminal
    launch.build = refuse
    os.execve = lambda *a, **k: None
    launch._record_terminal = lambda *a, **k: None
    try:
        try:
            launch.exec_agent("backend", prompt_file=str(f))
            refused = False
        except SystemExit:
            refused = True
        check("launch: a refused launch leaves its prompt file",
              refused and f.read_text() == "carry on", str(refused))
        launch.build = lambda *a, **k: (["x"], {})
        launch.exec_agent("backend", prompt_file=str(f))
        check("launch: a launch that goes ahead takes it", not f.exists())
    finally:
        launch.build, os.execve, launch._record_terminal = realb, realx, realr


def test_a_start_and_a_push_both_refuse_a_full_account_before_opening_a_terminal(home: Path) -> None:
    """`session.start` (control.start_result) opened a terminal on a full account and left `hl launch` to refuse inside it, where a message's
    push (`_start_for_work`) said so first. Both say the same sentence now and open nothing."""
    from handline.actions import control
    from handline.session import term as termmod_
    from handline.team import ask as askmod_, spawn as spawn_
    _daemon_home(home, 100)
    credentialed(home, "alpha")        # logged in: only the wall is what is in the way
    con = db.connect()
    opened: list = []
    real = (termmod_.available, termmod_.open_terminal, termmod_.create_worktree, spawn_._confirm_session, spawn_.register_worker,
            spawn_.ensure_extras, spawn_.pick_base)
    try:
        termmod_.available = lambda: True
        termmod_.open_terminal = lambda cmd, cwd=None, title=None: (opened.append(cmd), ("h1", None))[1]
        termmod_.create_worktree = lambda repo, name, base: (str(home), None)
        spawn_._confirm_session = lambda agent: None
        spawn_.register_worker = lambda *a, **k: None
        spawn_.ensure_extras = lambda *a, **k: (None, [])
        spawn_.pick_base = lambda repo, base: ("main", "test")
        con.execute("UPDATE agent_state SET terminal = NULL, closed_at = NULL, worktree = NULL, resume_session = NULL WHERE agent = 'backend'")
        con.commit()
        ok, said = control.start_result(con, "backend", str(home))
        check("full account: session.start is refused with the room sentence", not ok and "not started" in said and launch.no_room("backend") in said
              and "logged in" not in said, said)
        rid = db.add_request(con, "reviewer", "backend", "look at this", None, "peer", "async", None, 0, None)
        told = askmod_.push_here(con, "backend", rid)
        check("full account: a pushed message says the same and starts nothing", launch.no_room("backend") in told or "queued" in told, told)
        check("full account: no terminal was opened by either", opened == [], str(opened))
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5, reset_5h=int(time.time()) + 1800)
        ok, said = control.start_result(con, "backend", str(home))
        check("an account with room: the same start opens its terminal", ok and len(opened) == 1, said)
    finally:
        (termmod_.available, termmod_.open_terminal, termmod_.create_worktree, spawn_._confirm_session, spawn_.register_worker,
         spawn_.ensure_extras, spawn_.pick_base) = real
        con.close()


def test_a_walled_wake_says_so_everywhere(home: Path) -> None:
    """A closed agent whose resume is blocked at its account's 5h wall used to
    say nothing about it: `hl ps` showed "·closed", `hl triggers` said
    "nothing to hand off", and the daemon's own refusal lived in a log line.
    One predicate now answers all of them, so a screen cannot drift from what
    the daemon actually acts on - and it has to fall silent again the moment
    the account has room, since nothing here is stored state.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (termmod.available,)
    try:
        termmod.available = lambda: True
        termmod.live_terminals = lambda: None    # the bridge's stuck panel asks; this is about the wall
        db.set_closed(con, "backend", "sess-wall")
        support.something_waiting(con, "backend")
        # A screen only shows the wall next to a wait that is real: queued
        # work is what would actually make the daemon try this wake.
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        # A detail of its own is the ordinary case for this row - an answer
        # sitting undelivered is the example this feature exists for - so the
        # budget fix has to hold with one competing for the line, not only
        # on the empty-detail row every other check here leaves behind.
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="request 13 resolved: done")
        reset_at = int(time.time()) + 1800
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=reset_at)
        when = time.strftime("%H:%M", time.localtime(reset_at))

        row = db.get_agent_state(con, "backend")
        reason = launch.wake_wait_reason("backend", row, con)
        check("walled: names the account and when it rolls over",
              reason is not None and "alpha" in reason and when in reason, reason)

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("walled: the daemon's own refusal is the same sentence, with only why it did not move after it",
              not ok and (why == reason or why.startswith(reason + "; not moved: ")), f"{why!r} != {reason!r}")

        ps_out = _capture(lambda: climod.main(["ps"]))
        # `hl team` is asked from somewhere - `reviewer` is a peer of every
        # agent in DESIGN_TEAM, so backend is on its list.
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        check("walled: `hl ps` says it", reason in ps_out, ps_out)
        check("walled: `hl team` says it too - the fourth surface",
              "alpha" in team_out and when in team_out, team_out)
        # It is computed, not stored: once the account has room the sentence
        # disappears from every screen on its own, with nothing to clear.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("walled: and clears the moment the account has room",
              launch.wake_wait_reason("backend", row, con) is None)
        ps_out = _capture(lambda: climod.main(["ps"]))
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        check("walled: and every screen falls silent with it",
              "rolls over" not in ps_out and when not in team_out,
              "still walled somewhere after the window rolled over")
    finally:
        (termmod.available,) = real
        con.close()


def test_a_walled_wake_is_not_shown_with_nothing_waiting(home: Path) -> None:
    """A closed agent on a walled account, with no queued work, no answer it
    asked for and no wake_at set, is idle by choice - nothing is trying to
    wake it, so nothing should read "waiting" beside it. Before this, every
    closed agent on a walled account read as walled, which on a live team
    with six closed agents and nothing due was six rows of noise for zero
    real waits.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-idle")
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("walled: nothing waiting means would_wake says no",
              not dmn_lifecycle.would_wake(con, row), dict(row))

        # A deadline that has not arrived yet is not "due" - a bare
        # `row["wake_at"]` truthy check would have said yes to any parked
        # agent with any deadline at all, due or not.
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes later")
        row = db.get_agent_state(con, "backend")
        check("walled: a deadline that has not come due yet is not waiting either",
              not dmn_lifecycle.would_wake(con, row), dict(row))

        ps_out = _capture(lambda: climod.main(["ps"]))
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        check("walled: `hl ps` stays quiet with nothing waiting",
              "rolls over" not in ps_out, ps_out)
        check("walled: `hl team` stays quiet too", "rolls over" not in team_out, team_out)

        # `wake_wait` itself still answers - it is the daemon's own call site
        # (a move's wake included) that needs the raw answer unconditionally,
        # before it spends a wake attempt finding the wall out the hard way.
        check("walled: but wake_wait itself is unconditional - the daemon "
              "still needs the raw answer to decide a wake before trying it",
              launch.wake_wait("backend", row, con) is not None)
    finally:
        con.close()


def test_a_walled_wake_with_no_reset_reads_unknown(home: Path) -> None:
    """A snapshot with no `reset_5h` still reports a wall - the account read
    95% either way - it just cannot say when that clears."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-wall")
        support.something_waiting(con, "backend")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5)
        row = db.get_agent_state(con, "backend")
        reason = launch.wake_wait_reason("backend", row, con)
        check("walled: no reset_5h in the snapshot reads as unknown, not a crash",
              reason == "waiting for alpha's 5h window (100%, rolls over unknown)", reason)
    finally:
        con.close()


def test_a_resumed_agent_is_rescued_from_a_7d_wall_too(home: Path) -> None:
    """auditor #1753 blocking: `_wake_agent`'s rescue always asked `_move_plan`
    for a 5h-kind reset, even when the wait it was rescuing from was
    `wait.window == "7d"`. `_move_plan` reads `reset_5h` for that hardcoded
    "usage" kind, and a 5h window rolls over long before a 7d one - so the
    rescue judged a four-day wait by a clock that had already reset, read it
    as cheap to wait out, and never looked for room elsewhere. Passing
    `wait.window` through as the kind is the fix; this pins it the way
    test_a_pinned_closed_agent_moves_instead_of_waiting_out_its_window pins
    the 5h case, which this one does not overlap with (that account is fine
    on 7d and walled on 5h; this one is the other way around)."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")
    con = db.connect()
    real = (termmod.available, termmod.relaunch, termmod.resume_with, dmn_delivery.wait_for_session,
           obsmod.live_sessions)
    launched: list[str] = []
    try:
        obsmod.live_sessions = lambda: {}
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        support.something_waiting(con, "keeper")
        keep_handoff("keeper", "# handoff · keeper\nprevious session: sess-old")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        # alpha: room on 5h, walled on 7d, and its 5h clock is soon enough that
        # the bug's hardcoded "usage" kind reads the wait as cheap to ride out.
        write_status(home, "alpha", pct_5h=10, pct_7d=100,
                     reset_5h=int(time.time()) + 1800, reset_7d=int(time.time()) + 4 * 86400)
        write_status(home, "beta", pct_5h=10, pct_7d=5, reset_5h=int(time.time()) + 1800)
        termmod.available = lambda: True
        termmod.relaunch = lambda h, cmd, **kw: (launched.append(cmd), (True, "relaunched"))[1]
        termmod.resume_with = lambda h, text: (True, "handed back")
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        ok, why = dmn_lifecycle._wake_agent(con, "keeper", lambda _m: None)
        check("7d rescue: the wake succeeds instead of waiting out alpha's weekly wall",
              ok, why)
        check("7d rescue: the relaunch is a fresh start, not --resume on the walled account",
              launched and "--resume" not in launched[-1], str(launched))
        moved = [e for e in db.events_after(con, 0, agent="keeper")
                 if e["kind"] == "moved_account"]
        check("7d rescue: the move is on the record",
              moved and '"from": "alpha"' in moved[-1]["payload"]
              and '"to": "beta"' in moved[-1]["payload"], str(moved))
    finally:
        (termmod.available, termmod.relaunch, termmod.resume_with, dmn_delivery.wait_for_session,
        obsmod.live_sessions) = real
        con.close()


def test_a_close_and_a_wake_each_clear_the_dialog_the_agent_was_stuck_at(home: Path) -> None:
    """The founder's report: `hl ps` kept saying DIALOG, and its stall request stayed open, after the session had been closed and
    woken. A close and a wake each end the session whose screen showed the dialog, so its block, the request that block raised and a
    start-up dialog stall with its decision all go."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send, termmod.available)

    def stuck() -> tuple[int, int]:
        """The state a dialog leaves: a block with the request it raised, and a start-up dialog stall with its decision."""
        db.block_open(con, "backend", termmod.MODAL, "a dialog", "", int(time.time()), show_at=0, origin="close", title="Exit")
        blocked_rid = askmod.raise_decision(con, "backend", "[blocked] backend cannot be given its work", "delivery blocked")
        db.block_set_request(con, ["backend"], blocked_rid)
        why = "backend's terminal is showing a start-up dialog no standing rule answers"
        stall_rid = askmod.raise_decision(con, "backend", f"{dmn_lifecycle.DIALOG_DECISION} {why}", why)
        db.set_agent_state(con, "backend", dmn_states.STALLED, wake_at=None, detail=why)
        return blocked_rid, stall_rid

    def cleared(label: str, rids: tuple[int, int]) -> None:
        row = db.get_agent_state(con, "backend")
        check(f"{label}: the block is gone, so hl ps no longer reads DIALOG", db.block_get(con, "backend") is None)
        check(f"{label}: both requests it raised are closed",
              all(db.get_request(con, r)["state"] != "queued" for r in rids), str([db.get_request(con, r)["state"] for r in rids]))
        check(f"{label}: and the stall no longer holds the row", row["state"] != dmn_states.STALLED, str(dict(row)))

    try:
        orca_seen()
        termmod.wait_idle = lambda h, timeout_ms=0: True
        termmod.idle_now = lambda h: True
        termmod.close_terminal = lambda h, tab=True: (False, "this test keeps the pane")
        table = sessions_are("backend")
        termmod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-4", "pct_5h": 20}))
        rids = stuck()
        ok, said = dmn_lifecycle.close_agent(con, "backend", lambda _m: None, status.read_for_agent("backend", "alpha"))
        check("close: the session closes", ok, said)
        cleared("close", rids)

        rids = stuck()
        db.add_request(con, "reviewer", "backend", "look at this", None, "peer", "async", None, 0, None)
        sent.clear()
        ok, why = dmn_lifecycle._wake_agent(con, "backend", lambda _m: None)
        check("wake: the session comes back", ok, why)
        cleared("wake", rids)
    finally:
        (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send, termmod.available) = real
        con.close()


def test_the_claude_driver_reads_when_a_keychain_credential_changed(home: Path) -> None:
    """What `security find-generic-password` (no -w) prints carries "mdat", the time a login
    last wrote the item; that, not the secret, is what ends a login-expired mark."""
    from handline.vendors import claude as claudemod
    out = ('attributes:\n    "cdat"<timedate>=0x00  "20261001000000Z\\000"\n'
           '    "mdat"<timedate>=0x00  "20261003021500Z\\000"\n')
    real = (claudemod.subprocess.run, claudemod.sys.platform)
    try:
        claudemod.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=out, returncode=0)
        claudemod.sys.platform = "darwin"
        got = claudemod.Claude().credential_modified(home / "none" / ".claude")
        check("the keychain's mdat is read as UTC", got == 1790993700.0, str(got))
        claudemod.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout="", returncode=44)
        check("an item that cannot be seen has no time",
              claudemod.Claude().credential_modified(home / "none" / ".claude") is None)
    finally:
        claudemod.subprocess.run, claudemod.sys.platform = real


def test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s(home: Path) -> None:
    """#1669 proof: a lower default means more folds, and the founder's own
    warning was that too-frequent handoffs are worse than too-rare - a
    threshold low enough to re-fire immediately after a clear would thrash,
    and MIN_HANDOFF_INTERVAL_S must be what stops that, not a state that only
    happens to prevent it. Driven directly and repeatedly with the same
    still-fired trigger (the realistic worst case: nothing in this fixture
    updates the account's own reading between calls, exactly as a status file
    that has not refreshed yet would look in production) - skipped while
    inside the window, allowed again once it has passed. A control alongside
    it, same driving, an agent whose context_tokens override raises the
    limit so the same reading never fires at all - the new figure genuinely
    not applied - folds zero times across the same number of calls."""
    _daemon_home(home, 20)
    agents.register("keeper")
    agents.register("keeper_ctl")
    agent_file = agents.agent_file("keeper_ctl")
    agent_file.write_text(agent_file.read_text() + "context_tokens: 10000000\n")
    reseal(agent_file)
    con = db.connect()
    cleared = {"term_keeper": 0, "term_ctl": 0}
    real = (termmod.available, termmod.alive, termmod.idle_now, termmod.clear_context,
            dmn_delivery.session_present)
    try:
        db.set_terminal(con, "keeper", "term_keeper", "wt")
        db.set_terminal(con, "keeper_ctl", "term_ctl", "wt")
        db.set_account(con, "keeper", "alpha")
        db.set_account(con, "keeper_ctl", "alpha")
        termmod.available = lambda: True
        termmod.alive = lambda h: True
        termmod.idle_now = lambda h, **kw: True

        def clear(h, **kw):
            cleared[h] += 1
            return True, "cleared"
        termmod.clear_context = clear
        dmn_delivery.session_present = lambda a: True

        limit_real = status.effective_context_limit(agents.read("keeper"), "claude-sonnet-5")
        limit_ctl = status.effective_context_limit(agents.read("keeper_ctl"), "claude-sonnet-5")
        check("control: the override actually raises the limit above the reading",
              limit_ctl > 300_000 > limit_real, (limit_real, limit_ctl))
        t_real = triggers.evaluate(10, 300_000, ctx_limit=limit_real)
        t_ctl = triggers.evaluate(10, 300_000, ctx_limit=limit_ctl)
        check("real: 300,000 tokens fires at the default limit", t_real.fired, t_real.reason)
        check("control: the same reading does not fire once the limit is raised",
              not t_ctl.fired, t_ctl.reason)

        logs: list[str] = []
        for _ in range(4):
            dmn_handoff.perform_handoff(con, "keeper", t_real, logs.append)
        check("real: the first call actually folds", cleared["term_keeper"] == 1, cleared)
        check("real: driven three more times immediately, it does not fold again",
              cleared["term_keeper"] == 1, cleared)
        check("real: the interval guard is what stopped it, by name",
              sum("too soon" in ln for ln in logs) == 3, logs)

        # Past the interval: the same still-fired trigger is allowed through again -
        # a state that only happened to block it would not let this one through.
        con.execute("UPDATE handoff SET updated = ? WHERE agent = 'keeper'",
                    (int(time.time()) - dmn_timing.MIN_HANDOFF_INTERVAL_S - 1,))
        # The first clear also started the single writer's 15s settle window
        # (#2064) - real here, not backdated with it, so it is cleared the same
        # way the interval above was, or this "later" call reads as "moments
        # ago" and the gate, not the interval, is what would have let it through.
        db.clear_cursor(con, "typed_at:keeper")
        dmn_handoff.perform_handoff(con, "keeper", t_real, logs.append)
        check("real: once the interval has passed, the same condition folds again",
              cleared["term_keeper"] == 2, cleared)

        # The control is `not t_ctl.fired` above, not a call to perform_handoff:
        # supervise() only ever calls it behind `if t.fired`, and perform_handoff
        # itself does not re-check .fired - passing an unfired trigger to it
        # directly would fold anyway, which would prove the opposite of the
        # point. The real daemon never reaches it for keeper_ctl at all; that is
        # what "the new figure not applied" means here.
        check("control: it never folds, because the gate the daemon actually uses "
              "never opens for it", cleared["term_ctl"] == 0, cleared)
    finally:
        (termmod.available, termmod.alive, termmod.idle_now,
         termmod.clear_context, dmn_delivery.session_present) = real
        con.close()


def test_the_settle_window_actually_enforces_with_require_idle_false(home: Path) -> None:
    """`_type_gate(..., require_idle=False, require_settled=True)` - the
    shape the close and parked-move sites use - must actually enforce the
    settle window; with `require_settled` left at its default (False), the
    same call is a silent no-op. Pinned directly here, since driving it end
    to end needs a fuller scenario than this alone."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        check("settle: clear to type with no prior mark",
              dmn_delivery._type_gate(con, "backend", None, require_idle=False,
                             require_settled=True) is None)
        dmn_delivery._mark_typed(con, "backend")
        check("settle: refuses right after a mark",
              dmn_delivery._type_gate(con, "backend", None, require_idle=False,
                             require_settled=True) is not None)
        check("settle: require_settled=False (the old, buggy call) is a no-op instead",
              dmn_delivery._type_gate(con, "backend", None, require_idle=False) is None)
        db.set_cursor(con, "typed_at:backend", int(time.time()) - dmn_delivery.TYPE_SETTLE_S - 1)
        check("settle: clear again once the window has passed",
              dmn_delivery._type_gate(con, "backend", None, require_idle=False,
                             require_settled=True) is None)
    finally:
        con.close()


def test_the_post_lock_idle_reread_actually_gates(home: Path) -> None:
    """should-fix 2 (#1973 review): a stub left True throughout, as the race
    test above uses, proves nothing about the re-read `_type_gate` does once
    the lock is held - only a probe that changes between the two reads does.
    Here `typeable_now` answers True the first time (the pre-lock check every
    path already had) and False every time after (the re-read #1973 asked
    for), for `deliver_queued` and for `perform_handoff` in turn: each must
    type nothing."""
    _daemon_home(home, 20)
    con = db.connect()
    typed: list[str] = []
    real = (termmod.available, termmod.alive, termmod.typeable_now, termmod.send,
            termmod.clear_context)
    try:
        termmod.available = lambda: True
        termmod.alive = lambda h: True
        termmod.send = lambda h, text, enter=True: (typed.append(text), (True, "sent"))[1]
        termmod.clear_context = lambda h, **kw: (typed.append("/clear"), (True, "context cleared"))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)

        calls = [True, False]
        termmod.typeable_now = lambda h: calls.pop(0) if calls else False
        ok = dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("reread: deliver_queued reads idle before the lock but busy "
              "after, and types nothing", not ok and not typed, typed)

        calls[:] = [True, False]
        typed.clear()      # stands on its own; the first half's own failure must not carry over
        t = triggers.evaluate(10, 300_000)
        check("reread: setup - the trigger actually fires", t.fired, t.reason)
        fired = dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        check("reread: perform_handoff sees the same and types nothing too",
              not typed, typed)
        check("reread: and reports deferred (True), not degraded (False)",
              fired is True, fired)
    finally:
        (termmod.available, termmod.alive, termmod.typeable_now, termmod.send,
         termmod.clear_context) = real
        con.close()


def test_a_resumed_handoff_also_records_its_park(home: Path) -> None:
    """The event fixed for a fresh park applies just as much to one
    recovered after a restart - `resume_handoff` writes the same PARKED
    state through a different path, and it left the same gap.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.start_handoff(con, "hoff-restart", "backend", None, "usage",
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn_handoff.resume_handoff(con, row, lambda _m: None)
        events = db.events_after(con, 0, agent="backend", exclude_kinds=())
        check("resume: a recovered handoff's park is recorded too",
              any(e["kind"] == "park" for e in events), [e["kind"] for e in events])
    finally:
        con.close()


def test_a_resumed_hand_park_stays_forceable_too(home: Path) -> None:
    """resume_handoff has the same by-hand-mark-vs-assumed-account shape
    _perform_handoff does (auditor #1744 should-fix A) - a restart recovering
    a hand-triggered handoff must not turn it into an unforceable MOVING park
    just because the account is still assumed."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_account(con, "backend", "alpha", source="assumed")
        db.start_handoff(con, "hoff-restart-hand", "backend", None, dmn_states.BY_HAND_MARK,
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")
        check("resume hand: the account is only a guess, so the reading is assumed",
              agents.account_reading("backend", con).source == "assumed")

        dmn_handoff.resume_handoff(con, row, lambda _m: None)
        parked = db.get_agent_state(con, "backend")
        check("resume hand: the by-hand mark survives the assumed-account short retry",
              parked["detail"].startswith(dmn_states.BY_HAND_MARK), parked["detail"])
        ok, why = actions.can(con, "backend", "wake")
        check("resume hand: and w may still force it", ok, why)
    finally:
        con.close()


def test_the_whole_loop_runs_on_our_own_commands(home: Path) -> None:
    """Order a child, read what it sends back, report to the parent - all of it
    through `hl`, with no step that needs a human or a subagent.

    Each leg was there and none of them finished: `ocp ask` queued and left the
    rest to a tick, and a target with no terminal was a request for somebody to
    press a key. An agent that cannot finish the job it was given goes looking
    for one it can - which is a subagent, dead at the end of its session."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    typed: list[tuple[str, str]] = []
    real = (termmod.available, termmod.alive, termmod.idle_now, termmod.send,
            spawnmod.spawn, dmn_delivery.wait_for_session)
    try:
        orca_idle()
        termmod.send = lambda h, text, enter=True: (typed.append((h, text)), (True, ""))[1]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        def fake_spawn(agent, repo, base="main"):
            db.set_terminal(db.connect(), agent, f"term_{agent}", str(repo))
            return True, f"{agent}: worktree {repo}"

        spawnmod.spawn = fake_spawn
        hand_edit_team("""
teams:
  dev: [marina]
agents:
  manager: {}
  marina:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "dev.*", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
""")
        agents.register("manager")
        agents.register("marina", repo=str(home / "harbor"))
        db.set_terminal(con, "manager", "term_manager", "wt")
        sessions_are("manager", "marina")

        # 1. The manager orders its report. marina has nowhere to run, and that
        #    is the manager's problem to have solved, not a human's.
        ok, said, rid = askmod.ask("marina", "implement the smooth sea",
                                   requester="manager")
        check("loop: the order is allowed and lands", ok and rid, said)
        check("loop: the child was given somewhere to run on the way",
              db.get_agent_state(con, "marina")["terminal"] == "term_marina", said)
        check("loop: and it was told, by the command that sent it",
              any(h == "term_marina" and "waiting for you" in t for h, t in typed),
              str(typed)[:200])
        check("loop: the sender is told it arrived, not that it was queued",
              "delivered to marina" in said, said)

        # 2. marina answers. Resolving is reporting: the manager hears it now.
        typed.clear()
        okr, saidr = askmod.resolve(rid, "shipped on buddyron/marina, 40 tests")
        check("loop: the child's answer reaches its parent",
              okr and any(h == "term_manager" and "shipped on" in t for h, t in typed),
              f"{saidr} {typed}"[:200])

        # 3. The manager reports up to the human, which is a screen, not a
        #    terminal - so nothing is typed and it says where it went.
        typed.clear()
        oke, saide, eid = askmod.ask(actions.HUMAN, "sea is in, one nit left",
                                     requester="manager")
        check("loop: a report to the founder goes to the screen, not a terminal",
              oke and not typed and eid in [m["id"] for m in actions.messages(con)],
              f"{saide} {typed}"[:200])
    finally:
        (termmod.available, termmod.alive, termmod.idle_now, termmod.send,
         spawnmod.spawn, dmn_delivery.wait_for_session) = real
        con.close()


def test_stalled_survives_a_hook_that_fires_after_the_third_failed_wake(home: Path) -> None:
    """STALLED is protected the same way HANDOFF was: two failed wakes from
    earlier ticks already sit on the counter, this move's own wake is the
    third, and if its session comes up late - after `_wake_agent` has given up
    and written STALLED - and its hook fires anyway, `_mark_activity` refuses
    to move an agent out of a protected state. RUNNING is never reached, so the
    compare-and-set below is never given the chance to overrule STALLED. Three
    launches that started nothing is 8.6's human call, not something one more
    race should quietly clear."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    agents.register("mover")
    write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=int(time.time()) + 3600)
    real = (termmod.available, termmod.alive, termmod.close_session, termmod.relaunch,
            termmod.idle_now, dmn_delivery.wait_for_session)
    try:
        orca_idle()
        termmod.close_session = exit_ends_session("mover")
        sessions_are("mover")
        db.set_terminal(con, "mover", "term_x", "wt")
        db.set_account(con, "mover", "alpha")
        db.set_agent_state(con, "mover", dmn_states.PARKED, detail="parked earlier")
        db.bump_counter(con, "mover", "wake_failures")
        db.bump_counter(con, "mover", "wake_failures")
        keep_handoff("mover", "# handoff · mover\n## Next\n- carry on\n")
        dmn_delivery.wait_for_session = lambda agent, timeout=0: False

        def relaunch_fails_but_late(h, cmd, **kw):
            # The session comes up late - after `_wake_agent` has already
            # given up on this, its third, attempt - and its hook fires anyway.
            take_launched_prompt(cmd)
            _SESSION_TABLE["t"]["mover"] = 4242
            evmod._mark_activity(con, "mover", "UserPromptSubmit")
            return False, "no session seen within the timeout"
        termmod.relaunch = relaunch_fails_but_late

        write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=int(time.time()) + 4 * 3600)
        db.add_request(con, "marina", "mover", "carry on", None, "peer", "async", None, 0, None)
        t = triggers.evaluate(100, 10)
        moved = dmn_handoff._move_to_room(con, "mover", t, lambda _m: None)[0]
        check("stalled: the third failure is not recorded as a move", not moved)
        row = db.get_agent_state(con, "mover")
        check("stalled: it lands on STALLED, not clobbered by the late hook",
              row["state"] == dmn_states.STALLED and row["wake_failures"] == 3, str(dict(row)))
        check("stalled: the row still says closed - nothing here claims a live session",
              bool(row["closed_at"]) and row["account"] == "alpha", str(dict(row)))
    finally:
        (termmod.available, termmod.alive, termmod.close_session, termmod.relaunch,
         termmod.idle_now, dmn_delivery.wait_for_session) = real
        con.close()


def test_who_is_asking_is_not_guessed(home: Path) -> None:
    """A human at their own prompt is the human; a process that merely
    lost OCP_AGENT is not. Conflating them hands the decision target and the
    most privileged node in the routing table to whatever dropped the variable."""
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    agents.register("backend")
    class FakeStdin:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    real_env = os.environ.pop("OCP_AGENT", None)
    real_stdin = sys.stdin
    try:
        os.environ["OCP_AGENT"] = "backend"
        check("identity: an agent is whoever launch said it is",
              askmod.current_agent() == "backend" and askmod.asking_agent() == "backend")
        del os.environ["OCP_AGENT"]

        sys.stdin = FakeStdin(True)
        check("identity: an unset variable is still unset",
              askmod.current_agent() is None, str(askmod.current_agent()))
        check("identity: but a human at a terminal is the human",
              askmod.asking_agent() == askmod.HUMAN, str(askmod.asking_agent()))

        sys.stdin = FakeStdin(False)
        check("identity: a process that lost it is nobody",
              askmod.asking_agent() is None, str(askmod.asking_agent()))
        ok, msg, rid = askmod.ask("backend", "do this")
        check("identity: and is refused rather than promoted", not ok and rid is None, msg)
        check("identity: and told agents start via hl launch", "hl launch" in msg, msg)
        ok, _msg, rid = askmod.ask("backend", "do this", requester="reviewer")
        check("identity: saying so explicitly still works", ok and rid is not None, _msg)
        out = _capture(lambda: climod.main(["withdraw", str(rid)]))
        check("identity: withdraw from nobody is refused, not 'not yours'",
              "hl launch" in out, out)
        try:
            climod.main(["say", "backend", "x", "--of", "1685"])
            gone = False
        except SystemExit:
            gone = True
        check("identity: there is no --of to name a sender with", gone)
    finally:
        sys.stdin = real_stdin
        if real_env is not None:
            os.environ["OCP_AGENT"] = real_env
        else:
            os.environ.pop("OCP_AGENT", None)


def test_handoff_reclaims_context(home: Path) -> None:
    """A handoff must return the context, not just write a digest.

    Without this the context never drops, so a context trigger fires again on
    the next tick and the agent hands off forever, paying for a fold each time.
    """
    _daemon_home(home, 50)
    con = db.connect()
    cleared = []
    real_clear, real_alive, real_idle = termmod.clear_context, termmod.alive, termmod.idle_now
    try:
        orca_idle()
        termmod.clear_context = lambda h, wait=True, timeout_ms=0: (
            cleared.append(h), (True, "context cleared"))[1]
        # The agent is running: a handoff is for a session that has a context
        # to reclaim, and /clear at a shell prompt reclaims nothing.
        sessions_are("backend")

        # With no terminal recorded it must say so rather than pretend.
        db.set_terminal(con, "backend", None, None)
        ok, why = dmn_delivery._reclaim_context(con, "backend", lambda _m: None)
        check("handoff: says so when no terminal is recorded",
              not ok and "no terminal" in why, why)

        db.set_terminal(con, "backend", "term_x", "wt")
        t = triggers.Trigger(True, "context", "context 700,000 >= 120,000")
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        check("handoff: clears the agent's context", cleared == ["term_x"], str(cleared))
        row = db.get_agent_state(con, "backend")
        check("handoff: still parks afterwards", row["state"] == dmn_states.PARKED, str(dict(row)))
        check("handoff: the terminal survives the park", row["terminal"] == "term_x")
        # A busy agent must be left alone: folding would miss the work in
        # flight and clearing would throw it away.
        termmod.idle_now = lambda h: False
        cleared.clear()
        before = len(db.in_flight_handoffs(con))
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="mid-turn")
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        check("handoff: a mid-turn agent is deferred, not cleared", cleared == [], str(cleared))
        check("handoff: and no handoff is started for it",
              len(db.in_flight_handoffs(con)) == before)
        check("handoff: the agent keeps running",
              db.get_agent_state(con, "backend")["state"] == dmn_states.RUNNING)
    finally:
        termmod.clear_context, termmod.alive = real_clear, real_alive
        termmod.idle_now = real_idle
        con.close()


def test_a_resumed_context_handoff_wakes_at_once(home: Path) -> None:
    """resume_handoff's else-branch (a trigger with no real wall to sleep
    out) parks with an immediate wake, the same as a fresh handoff's does
    (#1903, #1957) - covered here for a `context` trigger, which the other
    resume tests never exercise (they all use `usage`, which takes the
    wake_after branch instead)."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        readable_agent(con, home, "backend")
        db.start_handoff(con, "hoff-restart-ctx", "backend", None, "context",
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")

        before = int(time.time())
        dmn_handoff.resume_handoff(con, row, lambda _m: None)
        got = db.get_agent_state(con, "backend")
        check("resumed context handoff: parks with an immediate wake",
              got["state"] == dmn_states.PARKED and got["wake_at"] is not None
              and got["wake_at"] <= before + 5, str(dict(got)))
    finally:
        con.close()


def test_wake_injects_handoff(home: Path) -> None:
    _daemon_home(home, 50)
    con = db.connect()
    sent = []
    real_resume, real_alive = termmod.resume_with, termmod.alive
    try:
        orca_idle()
        termmod.resume_with = lambda h, text: (sent.append(text), (True, "handoff injected"))[1]
        # Cleared, not closed - the session is still there to be handed its
        # own record back.
        sessions_are("backend")

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing to wake without a terminal", not ok, why)

        db.set_terminal(con, "backend", "term_x", "wt")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing to resume from without a handoff", not ok, why)

        keep_handoff("backend", "# handoff · backend\n## Next\n- finish the thing\n- and the other\n\n## Done\n- a\n- b\n")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: injects the handoff", ok and sent, why)
        record = Path(sent[-1].split(" is in ")[1].split(" - ")[0]) if sent else None
        check("wake: what is typed is one line naming a file, not the record (T106)",
              sent and "\n" not in sent[-1] and record is not None and record.is_absolute(), repr(sent))
        check("wake: and the file holds the whole record, structure kept",
              record is not None and record.exists()
              and "- finish the thing\n- and the other\n\n## Done\n- a\n- b" in record.read_text()
              and "fresh session" in record.read_text(), record and record.read_text())
        check("wake: the agent is told this is a fresh session with an empty context",
              "fresh session" in sent[-1] and "context is empty" in sent[-1], sent[-1][:80])
        check("wake: and gets its own record back",
              "finish the thing" in (record.read_text() if record else ""), sent[-1][:120])
        starts = db.session_starts(con, "backend")
        check("wake: the handoff given in place is kept as a start, with its text and the line typed",
              starts and starts[-1]["cause"] == "handoff" and starts[-1]["resumed"] == 0
              and "finish the thing" in starts[-1]["text"] and starts[-1]["prompt"] == sent[-1],
              str([dict(r) for r in starts]))
    finally:
        termmod.resume_with, termmod.alive = real_resume, real_alive
        con.close()


def test_a_drifted_launch_relaunches_instead_of_waking_in_place(home: Path) -> None:
    """A session still open when a park comes back is normally handed its
    handoff in place - `test_wake_injects_handoff` above. But the argv and
    system prompt `build()` produces are fixed at execve; a session launched
    under an older recipe never picks up a flag or a prompt section added
    since just by being typed at. Recorded drift catches it instead: the
    fingerprint this session started with no longer matches what `build()`
    would produce now, so the wake ends it (with its own session id to come
    back to) and relaunches fresh, the same way a closed agent's wake already
    does - rather than carrying the old recipe forward in place.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (termmod.available, termmod.alive, termmod.close_session, termmod.relaunch,
            termmod.resume_with, dmn_delivery.wait_for_session)
    sent: list[str] = []
    closed: list[str] = []
    launched: list[str] = []
    table = sessions_are("backend")
    try:
        termmod.available = lambda: True
        termmod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        f = paths.agent_status_file("backend")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"account": "alpha", "collected_at": int(time.time()),
                                 "session_id": "sess-old", "ctx_pct": 5}))
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")
        # Recorded as if an earlier `build()` launched this session - one that
        # cannot be what `launch.fingerprint` computes now, whatever it is.
        db.set_launch_fingerprint(con, "backend", "stale-fingerprint")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="usage; wakes soon")

        termmod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        termmod.close_session = lambda h, **kw: (closed.append(h), table.pop("backend", None),
                                                 (True, "session ended"))[2]
        termmod.relaunch = lambda h, cmd, started=None: (
            launched.append(cmd), table.update({"backend": 4242}), (True, "relaunched"))[2]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: a drifted launch's session is ended first, not typed into",
              closed == ["term_x"], str(closed))
        check("wake: and relaunched with its own session id to come back to",
              launched and "--resume sess-old" in launched[0], str(launched))
        check("wake: the fresh session is handed what is waiting for it, and nothing else",
              launched and "something for you" in launched_prompt(launched[0])
              and "Nothing was lost" not in launched_prompt(launched[0]), str(launched))
        check("wake: nothing is typed at the terminal for it", not sent, str(sent))
        check("wake: the relaunch itself is reported as a success", ok, why)
        row = db.get_agent_state(con, "backend")
        check("wake: the row is no longer marked closed once it is running again",
              row["closed_at"] is None, str(dict(row)))
    finally:
        (termmod.available, termmod.alive, termmod.close_session, termmod.relaunch,
         termmod.resume_with, dmn_delivery.wait_for_session) = real
        con.close()


def test_a_parked_row_with_no_fingerprint_also_relaunches(home: Path) -> None:
    """A row from before `launch_fingerprint` existed has nothing recorded to
    compare - not evidence the recipe still matches, since nothing has ever
    checked it. Left alone, this is the one case an old-format row could sit
    forever without picking up a flag or a prompt section `build()` has
    added since it last launched, silently, because there was never a
    mismatch to catch. It has to relaunch the same way real drift does.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (termmod.available, termmod.alive, termmod.close_session, termmod.relaunch,
            termmod.resume_with, dmn_delivery.wait_for_session)
    sent: list[str] = []
    closed: list[str] = []
    launched: list[str] = []
    table = sessions_are("backend")
    try:
        termmod.available = lambda: True
        termmod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        f = paths.agent_status_file("backend")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"account": "alpha", "collected_at": int(time.time()),
                                 "session_id": "sess-old", "ctx_pct": 5}))
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="usage; wakes soon")
        # No db.set_launch_fingerprint call at all - the row this migrates
        # from an older schema looks like.
        check("wake: the row starts with nothing recorded",
              db.get_agent_state(con, "backend")["launch_fingerprint"] is None)

        termmod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        termmod.close_session = lambda h, **kw: (closed.append(h), table.pop("backend", None),
                                                 (True, "session ended"))[2]
        termmod.relaunch = lambda h, cmd, started=None: (
            launched.append(cmd), table.update({"backend": 4242}), (True, "relaunched"))[2]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: an unrecorded fingerprint ends the session, not typed into",
              closed == ["term_x"], str(closed))
        check("wake: and relaunches with its own session id to come back to",
              launched and "--resume sess-old" in launched[0], str(launched))
        check("wake: the relaunch itself is reported as a success", ok, why)
    finally:
        (termmod.available, termmod.alive, termmod.close_session, termmod.relaunch,
         termmod.resume_with, dmn_delivery.wait_for_session) = real
        con.close()


def test_a_launch_records_its_fingerprint_and_says_when_it_cannot(home: Path) -> None:
    """Drift is judged against what `_record_terminal` wrote: if that write
    never lands, the daemon relaunches the session at every wake, so a failed
    write has to be said, not swallowed."""
    _daemon_home(home, 20)
    os.environ["TMUX"], os.environ["TMUX_PANE"] = f"/tmp/tmux-501/{SUITE_TMUX_SOCKET},1,0", "%41"     # a pane of our server
    real_set, real_err = db.set_launch_fingerprint, sys.stderr
    try:
        con = db.connect()
        launch._record_terminal("backend", "alpha", "fp-123")
        row = db.get_agent_state(con, "backend")
        check("record: the launch's fingerprint is written to the row",
              row and row["launch_fingerprint"] == "fp-123", str(row and dict(row)))
        check("record: and a row that matches it is not drifted",
              not launch.drifted("backend", {"launch_fingerprint": launch.fingerprint("backend")}))
        check("record: one that does not match is",
              launch.drifted("backend", {"launch_fingerprint": "fp-123"}))
        check("record: one with nothing recorded is",
              launch.drifted("backend", {"launch_fingerprint": None}))

        def boom(*a, **k):
            raise RuntimeError("disk full")
        db.set_launch_fingerprint = boom
        sys.stderr = io.StringIO()
        launch._record_terminal("backend", "alpha", "fp-456")
        said = sys.stderr.getvalue()
        check("record: a failed write is reported, and does not block the launch",
              "could not record this launch" in said and "disk full" in said, said)
        con.close()
    finally:
        db.set_launch_fingerprint, sys.stderr = real_set, real_err
        os.environ.pop("TMUX", None)
        os.environ.pop("TMUX_PANE", None)


def test_a_drifted_wake_that_cannot_relaunch_says_why(home: Path) -> None:
    """A drifted session that is neither relaunched nor left alone silently:
    with no session id to resume, or a session that will not end, the wake
    falls through to typing in place, and the log has to say that happened."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (termmod.available, termmod.alive, termmod.close_session, termmod.resume_with,
            dmn_delivery.wait_for_session)
    table = sessions_are("backend")
    try:
        termmod.available = lambda: True
        termmod.alive = lambda h: True
        termmod.wait_idle = lambda h, timeout_ms=0: True     # typeable_now asks whether it is idle
        db.set_terminal(con, "backend", "term_x", "wt")
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        keep_handoff("backend", "# handoff · backend\n## next\n- go\n")
        db.set_launch_fingerprint(con, "backend", "stale-fingerprint")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="usage; wakes soon")
        termmod.resume_with = lambda h, text: (True, "typed")
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True
        f = paths.agent_status_file("backend")
        f.parent.mkdir(parents=True, exist_ok=True)

        f.write_text(json.dumps({"account": "alpha", "collected_at": int(time.time()),
                                 "ctx_pct": 5}))
        # Nothing waiting: a resume would have nothing to say, so the session
        # is not ended for one; it is woken in place as before.
        logged: list[str] = []
        dmn_lifecycle.wake_agent(con, "backend", logged.append)
        check("drift wake: with nothing waiting the session is not ended to be resumed",
              not any("recipe changed" in m for m in logged), str(logged))
        support.something_waiting(con, "backend")
        logged.clear()
        dmn_lifecycle.wake_agent(con, "backend", logged.append)
        check("drift wake: no session id to resume is logged, not silent",
              any("no session id" in m and "in place" in m for m in logged), str(logged))

        f.write_text(json.dumps({"account": "alpha", "collected_at": int(time.time()),
                                 "session_id": "sess-old", "ctx_pct": 5}))
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="usage; wakes soon")
        support.something_waiting(con, "backend")
        termmod.close_session = lambda h, **kw: (False, "would not close")
        logged.clear()
        dmn_lifecycle.wake_agent(con, "backend", logged.append)
        check("drift wake: a session that will not end is logged, not silent",
              any("would not end" in m and "in place" in m for m in logged), str(logged))
    finally:
        (termmod.available, termmod.alive, termmod.close_session, termmod.resume_with,
         dmn_delivery.wait_for_session) = real
        con.close()


def test_process_env_reads_a_gone_pid_as_unreadable(home: Path) -> None:
    """A process whose environment did not come back is unreadable (None),
    never "read, but the variable was not set": a gone pid exits non-zero, and
    another user's process prints its command at rc 0 with no environment, so
    `ps eww` is byte-identical to `ps ww`."""
    real_run = obsmod.subprocess.run

    class FakeResult:
        def __init__(self, rc: int, out: str) -> None:
            self.returncode, self.stdout = rc, out

    def ps(eww: FakeResult, ww: FakeResult):
        return lambda argv, **k: eww if argv[1] == "eww" else ww

    gone, blank = FakeResult(1, ""), FakeResult(0, "")
    cmd = FakeResult(0, "/sbin/launchd\n")
    own = FakeResult(0, "/bin/ocp daemon OCP_HOME=/x\n")
    try:
        obsmod.subprocess.run = ps(gone, gone)
        check("process_env: a gone pid is unreadable, not an empty environment",
              obsmod.process_env(999991) is None)

        obsmod.subprocess.run = ps(blank, blank)
        check("process_env: empty stdout is unreadable even with rc 0",
              obsmod.process_env(999991) is None)

        obsmod.subprocess.run = ps(cmd, cmd)
        check("process_env: another user's process - command printed, no "
              "environment - is unreadable",
              obsmod.process_env(1) is None)

        obsmod.subprocess.run = ps(FakeResult(0, "/x --opt=1\n"),
                                   FakeResult(0, "/x --opt=1\n"))
        check("process_env: a command line carrying `=` itself is not an "
              "environment",
              obsmod.process_env(1) is None)

        obsmod.subprocess.run = ps(own, FakeResult(0, "/bin/ocp daemon\n"))
        check("process_env: an environment that came back comes through, "
              "past its argv",
              obsmod.process_env(999991) == " OCP_HOME=/x\n")
    finally:
        obsmod.subprocess.run = real_run

    check("process_env: root's launchd (pid 1) is unreadable on this machine",
          obsmod.process_env(1) is None)


def test_live_sessions_fails_the_whole_reading_on_an_unmappable_pid() -> None:
    """T146: a claude pid whose own `ps` calls miss fails the whole reading
    (None, like `process_table` already does), not just that one pid; a pid
    that is definitely another user's is left out on its own instead."""

    class FakeResult:
        def __init__(self, rc: int, out: str) -> None:
            self.returncode, self.stdout = rc, out

    def ps(eww: FakeResult, ww: FakeResult):
        return lambda argv, **k: eww if argv[1] == "eww" else ww

    real_run, real_table = obsmod.subprocess.run, obsmod.process_table
    table = [(101, "/usr/bin/claude")]
    obsmod.live_sessions = REAL_OBSERVE["live_sessions"]     # the test world answers "could not look"; this is about the real reading
    try:
        obsmod.process_table = lambda: table

        obsmod.subprocess.run = ps(FakeResult(1, ""), FakeResult(1, ""))
        check("live_sessions: a claude pid whose own ps calls miss fails the "
              "whole reading, not just that pid",
              obsmod.live_sessions() is None)

        obsmod.subprocess.run = ps(FakeResult(0, "/usr/bin/claude\n"),
                                   FakeResult(0, "/usr/bin/claude\n"))
        check("live_sessions: another user's claude (command with no "
              "environment) is left out, deciding nothing else",
              obsmod.live_sessions() == {})

        obsmod.subprocess.run = ps(FakeResult(0, "/usr/bin/claude OCP_AGENT=backend\n"),
                                   FakeResult(0, "/usr/bin/claude\n"))
        check("live_sessions: a readable OCP_AGENT is still found",
              obsmod.live_sessions() == {"backend": 101})
    finally:
        obsmod.subprocess.run, obsmod.process_table = real_run, real_table


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_a_first_backfill_reads_a_slice_per_tick_not_the_whole_history(
            Path(td) / 'tokenslice'))
        run(lambda: test_a_record_bigger_than_the_budget_is_read_whole_not_stalled_forever(
            Path(td) / 'tokenhuge'))
        run(lambda: test_a_unicode_line_separator_inside_a_record_does_not_split_it(
            Path(td) / 'tokensep'))
        run(lambda: test_dump_and_the_read_guard_refuse_exactly_the_same_characters(Path(td) / 'readguardset'))
        run(lambda: test_daemon_evaluates_parked(Path(td) / 'd0'))
        run(lambda: test_dev_mode_config_round_trips(Path(td) / 'devmodecfg'))
        run(lambda: test_bridge_dev_mode_flag_turns_it_on_and_its_absence_leaves_it(Path(td) / 'bridgedev'))
        run(lambda: test_an_owed_clear_that_holds_everything_says_why_once(Path(td) / 'owedsaid'))
        run(lambda: test_daemon_ignores_stale_status(Path(td) / 'd5e'))
        run(lambda: test_ocp_presses_only_the_key_that_declines(Path(td) / 'dcl0'))
        run(lambda: test_background_work_is_tracked_while_running_too(Path(td) / 'bgw1'))
        run(lambda: test_a_screen_with_background_work_is_kept_as_a_sample(Path(td) / 'bgsample'))
        run(lambda: test_a_closed_session_shows_no_background_work(Path(td) / 'bgw2'))
        run(lambda: test_t52_idle_timeout_and_cache_expiry_overrides(Path(td) / 'idletimeout'))
        run(lambda: test_the_live_trust_screen_is_never_pressed(Path(td) / 'livetrust'))
        run(lambda: test_a_running_daemon_is_reported_whatever_daemon_pid_says(Path(td) / 'd6'))
        run(lambda: test_a_holder_this_process_cannot_signal_is_still_reported(Path(td) / 'd6b2'))
        run(lambda: test_the_lock_names_only_its_current_holder(Path(td) / 'd6c'))
        run(lambda: test_service_plist(Path(td) / 'svc1'))
        run(lambda: test_service_launchctl_gated(Path(td) / 'svc2'))
        run(lambda: test_service_reinstall_waits_for_the_old_job_to_exit(Path(td) / 'svc2b'))
        run(lambda: test_service_install_names_only_a_daemon_launchd_does_not_run_as_hand_started(Path(td) / 'svc2c'))
        run(lambda: test_service_status_when_not_installed(Path(td) / 'svc3'))
        run(lambda: test_service_status_when_the_plist_cannot_be_looked_at(Path(td) / 'svc3b'))
        run(lambda: test_service_supervisor_of(Path(td) / 'svc4'))
        run(lambda: test_service_plist_runs_the_go_daemon_and_the_live_home_needs_a_word(Path(td) / 'svc6'))
        run(lambda: test_service_crash_loop_reads_the_go_daemons_start_line(Path(td) / 'svc7'))
        run(lambda: test_service_use_sets_the_daemon_the_next_install_runs(Path(td) / 'svc8'))
        run(lambda: test_service_use_from_an_agent_session_is_a_proposal(Path(td) / 'svc9'))
        run(lambda: test_service_log_rotation(Path(td) / 'svc6'))
        run(lambda: test_a_config_that_will_not_parse_is_never_overwritten_by_a_setting(Path(td) / 'cfgguard'))
        run(lambda: test_a_running_agent_is_told_when_work_arrives(Path(td) / 'deliver'))
        run(lambda: test_an_agent_is_never_handed_another_agents_session(Path(td) / 'ident'))
        run(lambda: test_every_session_start_is_kept_with_its_cause_and_what_it_was_given(Path(td) / 'keephand'))
        run(lambda: test_a_woken_session_is_not_closed_while_its_wake_waits_for_the_first_turn(Path(td) / 'wakeopen'))
        run(lambda: test_an_agent_idle_past_its_prompt_cache_comes_back_fresh_with_its_work(Path(td) / 'coldwake'))
        run(lambda: test_the_cache_clock_decides_a_resume_not_whether_a_handoff_exists(Path(td) / 'coldnone'))
        run(lambda: test_a_cold_wake_that_fails_leaves_no_handoff_behind(Path(td) / 'a_cold_wak'))
        run(lambda: test_a_wake_does_not_call_a_fresh_start_a_park(Path(td) / 'freshstart'))
        run(lambda: test_a_move_prompt_says_nothing_of_a_request_or_a_missing_transcript(Path(td) / 'movepr'))
        run(lambda: test_the_previous_sessions_transcript_path_can_come_from_its_own_hook_event(Path(td) / 'prevsess'))
        skip(lambda: test_orca_failing_to_list_terminals_reads_as_unknown(), ORCA_UNMAINTAINED)
        run(lambda: test_a_wake_does_not_claim_a_session_it_did_not_start(Path(td) / 'wakeforeign'))
        run(lambda: test_a_wake_refuses_to_type_when_the_process_table_is_unreadable(Path(td) / 'wakeunreadable'))
        run(lambda: test_a_refused_launch_leaves_its_prompt(Path(td) / 'launchrefused'))
        run(lambda: test_a_start_and_a_push_both_refuse_a_full_account_before_opening_a_terminal(Path(td) / 'fullacct'))
        run(lambda: test_a_walled_wake_says_so_everywhere(Path(td) / 'walled'))
        run(lambda: test_a_walled_wake_is_not_shown_with_nothing_waiting(Path(td) / 'walledquiet'))
        run(lambda: test_a_walled_wake_with_no_reset_reads_unknown(Path(td) / 'walledunknown'))
        run(lambda: test_a_resumed_agent_is_rescued_from_a_7d_wall_too(Path(td) / 'walled7drescue'))
        run(lambda: test_a_close_and_a_wake_each_clear_the_dialog_the_agent_was_stuck_at(Path(td) / 'dialogcleared'))
        run(lambda: test_the_claude_driver_reads_when_a_keychain_credential_changed(Path(td) / 'mdat'))
        run(lambda: test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s(Path(td) / 'ctxthrash'))
        run(lambda: test_the_settle_window_actually_enforces_with_require_idle_false(
            Path(td) / 'settleenforce'))
        run(lambda: test_the_post_lock_idle_reread_actually_gates(
            Path(td) / 'idlereread'))
        run(lambda: test_a_resumed_handoff_also_records_its_park(Path(td) / 'parklevel6'))
        run(lambda: test_a_resumed_hand_park_stays_forceable_too(Path(td) / 'parklevel6b'))
        run(lambda: test_the_whole_loop_runs_on_our_own_commands(Path(td) / 'loop'))
        run(lambda: test_stalled_survives_a_hook_that_fires_after_the_third_failed_wake(
            Path(td) / 'movestalled'))
        run(lambda: test_who_is_asking_is_not_guessed(Path(td) / 'whoasks'))
        run(lambda: test_handoff_reclaims_context(Path(td) / 'reclaim'))
        run(lambda: test_a_resumed_context_handoff_wakes_at_once(Path(td) / 'resumectxwake'))
        run(lambda: test_wake_injects_handoff(Path(td) / 'wake'))
        run(lambda: test_a_drifted_launch_relaunches_instead_of_waking_in_place(Path(td) / 'driftwake'))
        run(lambda: test_a_parked_row_with_no_fingerprint_also_relaunches(Path(td) / 'nofp'))
        run(lambda: test_a_launch_records_its_fingerprint_and_says_when_it_cannot(Path(td) / 'recfp'))
        run(lambda: test_a_drifted_wake_that_cannot_relaunch_says_why(Path(td) / 'driftlog'))
        run(lambda: test_process_env_reads_a_gone_pid_as_unreadable(Path(td) / 'procenv'))
        run(lambda: test_live_sessions_fails_the_whole_reading_on_an_unmappable_pid())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
