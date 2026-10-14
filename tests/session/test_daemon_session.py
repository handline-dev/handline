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


def test_daemon_ignores_stale_status(home: Path) -> None:
    """A frozen status.json must not be believed (the collector may be dead)."""
    _daemon_home(home, 40)
    write_status(home, "alpha", pct_5h=40, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - 100000)
    st = status.read("alpha")
    check("stale snapshot: assignable() refuses", not st.assignable()[0])
    check("stale snapshot: pct is not trusted for triggers", st.short_trusted is None,
          str(st.short_trusted))


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


def test_every_session_start_is_kept_with_its_cause_and_what_it_was_given(home: Path) -> None:
    """Each start the chat marks - why it began, whether it resumed, the handoff record and first prompt
    it was given - is kept by `db.session_starts` (the session's SessionStart claims it in the Go daemon, go/internal/hooks)."""
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

        launch._keep_what_it_starts_with("backend", ["--resume", "s-old"], "an event", "restart")
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

        got = db.session_starts(con, "backend")
        check("session starts: they come back oldest first", [r["cause"] for r in got] == ["wake", "restart", "launch"],
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
        run(lambda: test_daemon_ignores_stale_status(Path(td) / 'd5e'))
        run(lambda: test_ocp_presses_only_the_key_that_declines(Path(td) / 'dcl0'))
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
        run(lambda: test_every_session_start_is_kept_with_its_cause_and_what_it_was_given(Path(td) / 'keephand'))
        skip(lambda: test_orca_failing_to_list_terminals_reads_as_unknown(), ORCA_UNMAINTAINED)
        run(lambda: test_a_refused_launch_leaves_its_prompt(Path(td) / 'launchrefused'))
        run(lambda: test_the_claude_driver_reads_when_a_keychain_credential_changed(Path(td) / 'mdat'))
        run(lambda: test_who_is_asking_is_not_guessed(Path(td) / 'whoasks'))
        run(lambda: test_a_launch_records_its_fingerprint_and_says_when_it_cannot(Path(td) / 'recfp'))
        run(lambda: test_process_env_reads_a_gone_pid_as_unreadable(Path(td) / 'procenv'))
        run(lambda: test_live_sessions_fails_the_whole_reading_on_an_unmappable_pid())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
