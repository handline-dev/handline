"""Tests: the daemon tick, parks, wakes, handoffs and the lock.

Run on its own: python3 tests/session/test_daemon_session.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




def test_a_first_backfill_reads_a_slice_per_tick_not_the_whole_history(home: Path) -> None:
    from ocp.usage import tokenlog
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
    from ocp.usage import tokenlog
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
    from ocp.usage import tokenlog
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


def test_daemon_park(home: Path) -> None:
    """At the wall the daemon must park the agent with no human involved."""
    _daemon_home(home, 100)
    logs: list[str] = []
    con = db.connect()
    try:
        dmn_evaluate.tick(con, logs.append)
        row = db.get_agent_state(con, "backend")
        check("daemon: parks an agent that is over the limit",
              row["state"] == dmn_states.PARKED, str(dict(row)))
        check("daemon: park sets a wake_at", row["wake_at"] is not None)
        check("daemon: wake_at is the window reset", row["wake_at"] > int(time.time()))
        check("daemon: handoff counts as a continuation, not a failure",
              row["continuations"] == 1 and row["failed_attempts"] == 0, str(dict(row)))
        check("daemon: the handoff was recorded as done",
              all(h["state"] != "in_flight" for h in db.in_flight_handoffs(con)))
    finally:
        con.close()


def test_daemon_resume(home: Path) -> None:
    """A parked agent whose window rolled over resumes unattended."""
    _daemon_home(home, 100)
    con = db.connect()
    try:
        # Park it in the past, as if the reset had already happened.
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked earlier")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=3, pct_7d=10, ctx_pct=5)
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a due parked agent with real work returns to QUEUED",
              row["state"] == dmn_states.QUEUED, str(dict(row)))
        check("daemon: waking clears wake_at", row["wake_at"] is None)
    finally:
        con.close()


def test_daemon_kill_recovery(home: Path) -> None:
    """SIGKILL leaves an in-flight handoff; a restart must recover it."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        # Exactly what a SIGKILL mid-handoff leaves behind.
        db.start_handoff(con, "hid1", "backend", "sess", "usage",
                         int(time.time()) + 60)
        db.advance_handoff(con, "hid1", "folded")
        db.set_agent_state(con, "backend", dmn_states.HANDOFF, detail="killed mid-handoff")

        actions = dmn_reconciler.reconcile(con, lambda _m: None)
        check("daemon: reconciliation resumes the in-flight handoff",
              any("resumed in-flight handoff from step 'folded'" in a for a in actions),
              str(actions))
        # It must actually finish it, not just say so.
        check("daemon: the resumed handoff is no longer in flight",
              db.in_flight_handoffs(con) == [],
              str([dict(h) for h in db.in_flight_handoffs(con)]))
        row = db.get_agent_state(con, "backend")
        check("daemon: the resumed agent is parked, not stuck in HANDOFF",
              row["state"] == dmn_states.PARKED, str(dict(row)))

        # An agent mid-handoff is excluded from trigger evaluation (8.9).
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=90)
        before = len(db.in_flight_handoffs(con))
        dmn_evaluate.tick(con, lambda _m: None)
        check("daemon: no competing handoff is started",
              len(db.in_flight_handoffs(con)) == before, str(before))
    finally:
        con.close()


def test_the_daemon_marks_itself_before_its_socket_takes_a_verb(home: Path) -> None:
    """T408 (auditor #5913): a push made inside the daemon starts the session itself, and the flag
    that says so is up before the socket can take a push verb."""
    from ocp.team import ask as askmod
    _daemon_home(home, 20)
    seen = {}
    real_start, real_tick = dmn_socket_server.SocketServer.start, dmn_evaluate.tick
    real_send = dmn_client.send

    def start(self):
        seen["at socket start"] = askmod.IN_DAEMON

    def tick(con, log, **kw):
        seen["in a tick"] = askmod.IN_DAEMON
        dmn_client.send = lambda payload, timeout=None: seen.setdefault("sent", payload)
        try:
            askmod.push(con, "backend")
        finally:
            dmn_client.send = real_send
        return real_tick(con, log, **kw)
    dmn_socket_server.SocketServer.start, dmn_evaluate.tick = start, tick
    try:
        dmn_loop.run(once=True, verbose=False)
    finally:
        dmn_socket_server.SocketServer.start, dmn_evaluate.tick = real_start, real_tick
        dmn_client.send = real_send
    check("daemon: the in-daemon flag is up when the socket starts and during a tick",
          seen.get("at socket start") is True and seen.get("in a tick") is True, str(seen))
    check("daemon: a push made inside it is not sent over its own socket", "sent" not in seen, str(seen))
    check("daemon: the flag is down again once it stops", askmod.IN_DAEMON is False)


def test_reconcile_stalls_one_agents_unreadable_config_not_the_others(home: Path) -> None:
    """orcapipe #1571: a malformed agent.yaml reached in-process, uncaught,
    through resume_handoff -> agents.current_account -> account_of -> read -
    live before the fix: db.start_handoff with agent_state.account NULL (the
    real shape - a handoff started before an account was ever recorded),
    then dmn_loop.run(once=True) crashed the whole process at "daemon starting -
    reconciling", before its own while loop ever began. A second, healthy
    agent's in-flight handoff never got a chance to resolve.

    The fix stalls only the agent whose file cannot be read (8.5's counter,
    the same ceiling tick()'s per-agent guard uses) and keeps going."""
    _daemon_home(home, 20)
    agents.register("frontend")
    con = db.connect()
    try:
        db.set_terminal(con, "frontend", "term_y", "wt2")
        con.execute("UPDATE agent_state SET account = NULL WHERE agent IN "
                    "('backend', 'frontend')")
        af = agents.agent_file("backend")
        af.write_text(af.read_text().rstrip("\n") + '\nrepo: "unterminated\n')
        reseal(af)
        db.start_handoff(con, "hid-bad", "backend", "sess-bad", "usage",
                         int(time.time()) + 90)
        db.start_handoff(con, "hid-ok", "frontend", "sess-ok", "usage",
                         int(time.time()) + 90)

        rc = dmn_loop.run(once=True, verbose=False)
        check("reconcile: a malformed agent.yaml does not exit the daemon",
              rc == 0, str(rc))

        bad = db.get_agent_state(con, "backend")
        check("reconcile: the unreadable agent is counted, not silently retried forever",
              bad["failed_attempts"] == 1, str(dict(bad)))
        still_open = {h["agent"] for h in db.in_flight_handoffs(con)}
        check("reconcile: its handoff does not read agent.yaml, so it resolves",
              "backend" not in still_open, str(still_open))

        good = db.get_agent_state(con, "frontend")
        check("reconcile: a healthy agent's in-flight handoff still resolves "
              "in the same reconciliation the bad one failed in",
              good["state"] == dmn_states.PARKED and "frontend" not in still_open,
              str(dict(good)))
    finally:
        con.close()


def test_a_hand_edited_control_character_stalls_one_agent_live_through_the_daemon(
        home: Path) -> None:
    """#1609: the end-to-end case the #1571 fix exists for, run live through
    the daemon rather than only at agents.read. A hand-edited agent.yaml -
    never written by dump(), so this is the shape a hand edit actually takes
    - carrying a tab in repo (why a tab and not the C1 byte the unit test
    above covers: see the inline comment below). Now it is refused at
    agents.read, reached the same way the malformed-YAML case above is
    (resume_handoff -> account_of -> read), and #1571's guard stalls that
    one agent by name while a second, healthy agent's in-flight handoff
    still resolves in the same reconciliation."""
    _daemon_home(home, 20)
    agents.register("frontend")
    con = db.connect()
    try:
        db.set_terminal(con, "frontend", "term_y", "wt2")
        con.execute("UPDATE agent_state SET account = NULL WHERE agent IN "
                    "('backend', 'frontend')")
        af = agents.agent_file("backend")
        # A tab, not the C1 byte the unit test above also covers: real
        # PyYAML's own Reader already refuses C1 before agents.read's new
        # guard ever runs, so that case proves the daemon survives SOME
        # ConfigError but not specifically this one. A tab inside a quoted
        # scalar is accepted by both parsers (measured), so it is the guard
        # in refuse_unsafe_values that fires here, under either interpreter -
        # confirmed by mutation: removing that guard leaves this case
        # loading silently and this test fails, where the C1 byte would not
        # have caught the removal at all.
        af.write_text(af.read_text().rstrip("\n") + '\nrepo: "before\tafter"\n')
        db.start_handoff(con, "hid-bad", "backend", "sess-bad", "usage",
                         int(time.time()) + 90)
        db.start_handoff(con, "hid-ok", "frontend", "sess-ok", "usage",
                         int(time.time()) + 90)

        rc = dmn_loop.run(once=True, verbose=False)
        check("read guard: a control character in a hand-edited agent.yaml "
              "does not exit the daemon", rc == 0, str(rc))

        bad = db.get_agent_state(con, "backend")
        check("read guard: the agent carrying it is stalled by name, not retried forever",
              bad["failed_attempts"] == 1, str(dict(bad)))
        still_open = {h["agent"] for h in db.in_flight_handoffs(con)}
        check("read guard: its handoff does not read agent.yaml, so it resolves",
              "backend" not in still_open, str(still_open))

        good = db.get_agent_state(con, "frontend")
        check("read guard: a second, healthy agent still resolves in the "
              "same reconciliation the poisoned one failed in",
              good["state"] == dmn_states.PARKED and "frontend" not in still_open,
              str(dict(good)))
    finally:
        con.close()


def test_dev_mode_config_round_trips(home: Path) -> None:
    """Task #64: off by default, `ocp dev-mode on/off` flips config.yaml
    (the same file `account cap` writes to), and a plain `ocp dev-mode`
    reads it back without changing it."""
    use_home(home)
    check("dev-mode: off by default", dmn_devmode.dev_mode_enabled() is False,
          str(dmn_devmode.dev_mode_enabled()))
    out = _capture(lambda: climod.main(["dev-mode"]))
    check("cli dev-mode: prints the current state with no argument",
          out.strip() == "off", out)
    out = _capture(lambda: climod.main(["dev-mode", "on"]))
    check("cli dev-mode on: confirms the flip", "on" in out, out)
    check("dev-mode: on reaches daemon.py's own reader",
          dmn_devmode.dev_mode_enabled() is True, str(dmn_devmode.dev_mode_enabled()))
    out = _capture(lambda: climod.main(["dev-mode"]))
    check("cli dev-mode: reads back on without an argument", out.strip() == "on", out)
    climod.main(["dev-mode", "off"])
    check("dev-mode: off turns it back off, not just adds a second key",
          dmn_devmode.dev_mode_enabled() is False, str(dmn_devmode.dev_mode_enabled()))


def test_dev_mode_fingerprint_and_restart_decision(tmp_path: Path, home: Path) -> None:
    """`_dev_mode_should_restart` (called once a tick): the first call after
    dev mode turns on is the baseline, not a change; a later call with an
    untouched tree is quiet; one whose watched files actually changed asks
    to restart only when the import check passes, and remembers a broken
    version so it is not re-logged every tick."""
    use_home(home)
    root = tmp_path / "repo"
    (root / "ocp").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "ocp" / "core.py").write_text("x = 1\n")
    (root / "bin" / "ocp").write_text("#!/bin/sh\n")
    (root / "requirements.txt").write_text("")
    real_repo_root = paths.repo_root
    paths.repo_root = lambda: root
    logs: list[str] = []
    log = logs.append
    try:
        state = {"fp": None, "warned": None}
        check("dev-mode: while off, no baseline is kept",
              dmn_devmode._dev_mode_should_restart(state, log) is False and state["fp"] is None,
              str(state))

        dmn_devmode.set_dev_mode(True)
        check("dev-mode: turning on captures a baseline, not a restart",
              dmn_devmode._dev_mode_should_restart(state, log) is False and state["fp"] is not None,
              str(state))
        check("dev-mode: an untouched tree asks nothing on the next tick",
              dmn_devmode._dev_mode_should_restart(state, log) is False, str(state))

        real_check = dmn_devmode._dev_import_check
        dmn_devmode._dev_import_check = lambda log_: True
        try:
            time.sleep(0.01)
            (root / "ocp" / "sub").mkdir()
            (root / "ocp" / "sub" / "x.py").write_text("x = 2\n")
            check("dev-mode: a new file in a subpackage, with the import check stubbed clean, asks to restart",
                  dmn_devmode._dev_mode_should_restart(state, log) is True,
                  str(state) + " " + str(logs))
        finally:
            dmn_devmode._dev_import_check = real_check

        def failing_check(log_):
            log_("stub: fails to import")
            return False
        dmn_devmode._dev_import_check = failing_check
        try:
            time.sleep(0.01)
            (root / "ocp" / "core.py").write_text("x = 3\n")
            logs.clear()
            check("dev-mode: a changed file that fails the import check does not ask to restart",
                  dmn_devmode._dev_mode_should_restart(state, log) is False, str(state))
            check("dev-mode: it logs why",
                  any("fails to import" in m for m in logs), str(logs))
            logs.clear()
            check("dev-mode: the same broken fingerprint is not re-logged next tick",
                  dmn_devmode._dev_mode_should_restart(state, log) is False and logs == [], str(logs))
        finally:
            dmn_devmode._dev_import_check = real_check
    finally:
        paths.repo_root = real_repo_root
        dmn_devmode.set_dev_mode(False)


def test_dev_mode_watches_subpackages(tmp_path: Path) -> None:
    """`ocp/cli/` is a package: an edit under it must be seen as a change."""
    root = tmp_path / "repo"
    (root / "ocp" / "cli").mkdir(parents=True)
    (root / "ocp" / "top.py").write_text("x = 1\n")
    (root / "ocp" / "cli" / "ask.py").write_text("x = 1\n")
    names = {p.relative_to(root).as_posix() for p in dmn_devmode._watched_files(root)}
    check("dev-mode: a top-level and a subpackage file are both watched",
          {"ocp/top.py", "ocp/cli/ask.py"} <= names, str(names))
    before = dmn_devmode._code_fingerprint(root)
    (root / "ocp" / "cli" / "ask.py").write_text("x = 22\n")
    check("dev-mode: editing a subpackage file changes the fingerprint",
          dmn_devmode._code_fingerprint(root) != before)


def test_dev_mode_fingerprint_skips_a_missing_watched_file(tmp_path: Path) -> None:
    """auditor #2174 should-fix: `_code_fingerprint`'s try/except around
    stat() has to actually run to matter, and both restart-decision tests
    replace `_dev_mode_should_restart` whole, so the fingerprint code never
    executes under them. `_watched_files` no longer filters by `exists()`,
    so this skip is what stands between a missing requirements.txt/bin/ocp
    (or one caught mid-merge) and `_dev_mode_should_restart` never getting
    past 'check failed' every tick."""
    root = tmp_path / "repo"
    (root / "ocp").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "ocp" / "core.py").write_text("x = 1\n")
    (root / "bin" / "ocp").write_text("#!/bin/sh\n")
    # requirements.txt is listed by _watched_files but never created.
    fp = dmn_devmode._code_fingerprint(root)
    check("dev-mode fingerprint: a missing watched file does not raise",
          isinstance(fp, tuple), str(fp))
    check("dev-mode fingerprint: the missing file is left out, not fingerprinted as absent",
          "requirements.txt" not in str(fp), str(fp))
    check("dev-mode fingerprint: the files that do exist are still in it",
          len(fp) == 2, str(fp))


def test_dev_mode_import_check_runs_the_real_entrypoint(home: Path) -> None:
    """`_dev_import_check` execs `bin/ocp --help` - the actual restart path,
    not a bare `python -c import` - so a broken bash wrapper or a broken
    venv selection is caught the same way a broken module would be."""
    use_home(home)
    logs: list[str] = []
    check("dev-mode: the real bin/ocp --help passes its own import check",
          dmn_devmode._dev_import_check(logs.append) is True, str(logs))

    real_exe = service.ocp_executable
    service.ocp_executable = lambda: "/no/such/ocp-binary-at-all"
    try:
        check("dev-mode: an entrypoint that cannot even run fails closed",
              dmn_devmode._dev_import_check(logs.append) is False, str(logs))
        check("dev-mode: it says why", any(logs), str(logs))
    finally:
        service.ocp_executable = real_exe


def test_dev_mode_restart_execs_after_the_lock_and_db_are_released(home: Path) -> None:
    """The integration the loop makes: a tick that finds changed, importable
    code sets the same `stopping` flag SIGTERM does, finishes cleanly - the
    lock released and the connection closed exactly like every other clean
    exit - and only then calls the restart. Faked here by stubbing the
    restart itself (`os.execv` never returns, so a real call would end the
    test process); what is checked is that it is reached only after `run`
    has already given up the lock."""
    _daemon_home(home, 20)
    real_should, real_restart = dmn_devmode._dev_mode_should_restart, dmn_devmode._dev_mode_restart
    calls: list[bool] = []

    def fake_should(state, log):
        # True on the very first tick call - run() must still finish that
        # tick and release the lock before this fires.
        return True

    def fake_restart():
        calls.append(lock.holder(dmn_locks.DAEMON_LOCK) is not None)

    dmn_devmode._dev_mode_should_restart, dmn_devmode._dev_mode_restart = fake_should, fake_restart
    try:
        rc_box: list[int] = []
        out = _capture(lambda: rc_box.append(dmn_loop.run(once=False, verbose=True, max_ticks=5)))
        check("dev-mode restart: run() returns normally once execv is stubbed out",
              rc_box == [0], str(rc_box))
        check("dev-mode restart: it stopped after the first tick, not after max_ticks",
              "stopped after 1 tick(s)" in out, out)
        check("dev-mode restart: the restart was actually invoked",
              len(calls) == 1, str(calls))
        check("dev-mode restart: the daemon lock was already released when it ran",
              calls == [False], str(calls))
    finally:
        dmn_devmode._dev_mode_should_restart, dmn_devmode._dev_mode_restart = real_should, real_restart


def test_dev_mode_check_failing_degrades_not_crashes(home: Path) -> None:
    """auditor #2170 should-fix 1: `_dev_mode_should_restart` sits beside
    `rotate_logs` in the loop, and must be guarded the same way - a file
    gone between listing and stat()ing it (a merge in the main checkout is
    exactly when this runs) must not end a daemon a human started by hand
    the way an unguarded exception would."""
    _daemon_home(home, 20)
    real_should, real_sleep = dmn_devmode._dev_mode_should_restart, time.sleep

    def raising_should(state, log):
        raise RuntimeError("stub: vanished mid-stat")

    dmn_devmode._dev_mode_should_restart = raising_should
    time.sleep = lambda s: None  # two real ticks, not two real POLL_S waits
    try:
        out = _capture(lambda: check(
            "dev-mode check failing: run() still exits cleanly, not with an exception",
            dmn_loop.run(once=False, verbose=True, max_ticks=2) == 0, ""))
        check("dev-mode check failing: it ran both ticks it was asked for",
              "stopped after 2 tick(s)" in out, out)
        check("dev-mode check failing: it says why, not just that it failed",
              "dev-mode check failed" in out and "vanished mid-stat" in out, out)
    finally:
        dmn_devmode._dev_mode_should_restart, time.sleep = real_should, real_sleep


def test_dev_mode_restart_yields_to_a_signal_that_arrived_during_the_check(home: Path) -> None:
    """auditor #2170 should-fix 2: `_dev_import_check` can run for up to 30s
    (bin/ocp --help's own timeout). A real SIGTERM delivered to this process
    while that stub stands in for it must still end the daemon - not be
    overridden by the restart the check was in the middle of approving."""
    _daemon_home(home, 20)
    real_should, real_restart = dmn_devmode._dev_mode_should_restart, dmn_devmode._dev_mode_restart
    restart_calls: list[bool] = []

    def should_but_signal_first(state, log):
        # Stands in for a signal landing mid-way through the real 30s
        # subprocess call this replaces.
        os.kill(os.getpid(), signal.SIGTERM)
        return True

    def fake_restart():
        restart_calls.append(True)

    dmn_devmode._dev_mode_should_restart, dmn_devmode._dev_mode_restart = should_but_signal_first, fake_restart
    try:
        out = _capture(lambda: check(
            "dev-mode signal race: run() exits cleanly",
            dmn_loop.run(once=False, verbose=True, max_ticks=5) == 0, ""))
        check("dev-mode signal race: it stopped at the signal, not at max_ticks",
              "stopped after 1 tick(s)" in out, out)
        check("dev-mode signal race: the restart it was approving never ran",
              restart_calls == [], str(restart_calls))
        check("dev-mode signal race: it says why the restart was skipped",
              "restart skipped" in out, out)
    finally:
        dmn_devmode._dev_mode_should_restart, dmn_devmode._dev_mode_restart = real_should, real_restart


def test_reconcile_still_propagates_a_fault_that_is_not_one_agents(home: Path) -> None:
    """The control for the guard above: a fault that belongs to reconcile()
    itself, not to any one agent's file, must still stop the daemon - a
    handler that swallows both would hide the outage that actually needs a
    human, which is the failure mode explicitly not wanted here."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.start_handoff(con, "hid-x", "backend", "sess-x", "usage",
                         int(time.time()) + 90)
    finally:
        con.close()
    real = dmn_handoff.resume_handoff
    dmn_handoff.resume_handoff = lambda con, row, log: (_ for _ in ()).throw(
        sqlite3.OperationalError("database is locked"))
    try:
        try:
            dmn_loop.run(once=True, verbose=False)
            check("reconcile: a database fault is not mistaken for one agent's "
                  "bad file and swallowed", False, "run() returned instead of raising")
        except sqlite3.OperationalError:
            check("reconcile: a database fault is not mistaken for one agent's "
                  "bad file and swallowed", True)
    finally:
        dmn_handoff.resume_handoff = real


def test_reconcile_stalls_one_due_agents_unreadable_config_at_wake_too(home: Path) -> None:
    """The due_agents/wake_agent half of reconcile() (step 2) gets the same
    guard as step 1, for the same property - and auditor's #1579 found the
    live shape I could not: `_wake_agent`'s closed branch calls
    `launch.wake_wait(agent, row, con)`, and with `row["resume_session"]`
    set, `wake_wait` calls `agents.account_reading`, which falls through
    (no db column, no fresh status snapshot) to `account_of` -> `read` ->
    the same ConfigError step 1 guards. My first attempt forced the
    exception by replacing `wake_agent` itself, which is what hid this path
    from me - the test harness's `orcamod.available = lambda: False`
    (`_daemon_home`'s own default) makes `_wake_agent` return "no terminal
    to wake" before it ever reaches `wake_wait`, a property of the test
    environment, not of the code. Fixed here by setting it True instead, the
    idiom the rest of the suite already uses, and driving the real path with
    a genuinely malformed agent.yaml - no monkeypatch of daemon code.

    Calls reconcile() directly rather than dmn_loop.run(once=True): step 1's own
    test can go through run() because an in-flight handoff also excludes the
    agent from supervise() (db.in_flight_for), so reconcile()'s guard is the
    only one that ever sees this agent in that tick. Step 2's due-but-closed
    agent has no such exclusion - the same account_reading call this guards
    is also the first thing supervise() does for any EVALUATED agent, so
    going through the full tick() double-counts it against tick()'s own
    (already-tested, in test_only_the_agents_own_events_move_its_
    activity_clock's neighbourhood) per-agent guard. reconcile() in
    isolation is the actual unit this test is about."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        con.execute("UPDATE agent_state SET account = NULL WHERE agent = 'backend'")
        af = agents.agent_file("backend")
        af.write_text(af.read_text().rstrip("\n") + '\nrepo: "unterminated\n')
        reseal(af)
        db.set_closed(con, "backend", 1)
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.PARKED,
                           wake_at=int(time.time()) - 10, detail="due")
    finally:
        con.close()
    orcamod.available = lambda: True
    orcamod.live_terminals = lambda: {"term_x"}
    con = db.connect()
    try:
        actions = dmn_reconciler.reconcile(con, lambda _m: None)
        check("reconcile: an unreadable config at the wake half does not exit "
              "the daemon either - actions list came back, nothing raised",
              isinstance(actions, list), str(actions))
        row = db.get_agent_state(con, "backend")
        check("reconcile: it is counted the same way as step 1's guard",
              row["failed_attempts"] == 1, str(dict(row)))
        check("reconcile: still closed and still due, to retry - not dropped",
              row["closed_at"] and row["wake_at"], str(dict(row)))
    finally:
        con.close()
        orcamod.available = lambda: False


def test_daemon_handoff_timeout(home: Path) -> None:
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "hid2", "backend", "sess", "usage", int(time.time()) - 5)
        actions = dmn_reconciler.reconcile(con, lambda _m: None)
        check("daemon: an expired handoff is timed out",
              any("timed out" in a for a in actions), str(actions))
        row = db.get_agent_state(con, "backend")
        check("daemon: a timed-out handoff degrades", row["state"] == dmn_states.DEGRADED)
        check("daemon: timing out increments the throttle counter",
              row["throttle_count"] == 1, str(dict(row)))
    finally:
        con.close()


def test_daemon_stall_decision(home: Path) -> None:
    """STALLED must be reachable by the path the daemon actually takes.

    Driving real park -> wake -> degrade cycles, rather than hand-setting the
    counter, is the point: resetting the streak on every wake pinned it at 1
    and made this decision unreachable while a hand-bumped test still passed.
    """
    _daemon_home(home, 100)
    con = db.connect()
    try:
        # The default fixture has no snapshot of the session and no worktree,
        # so every handoff here is degraded for the facts it could not read.
        seen = []
        try:
            for _ in range(dmn_states.MAX_THROTTLE_STREAK + 3):
                row = db.get_agent_state(con, "backend")
                if row and row["state"] == dmn_states.STALLED:
                    break
                # Make it due, as the usage window rolling over would.
                if row and row["wake_at"]:
                    db.set_agent_state(con, "backend", row["state"],
                                       wake_at=int(time.time()) - 1, detail=row["detail"])
                # These cycles are hours apart in reality; backdate so the
                # handoff rate limiter does not read them as one runaway.
                con.execute("UPDATE handoff SET updated = updated - ? WHERE agent = 'backend'",
                            (dmn_states.MIN_HANDOFF_INTERVAL_S * 2,))
                dmn_evaluate.tick(con, lambda _m: None)
                cur = db.get_agent_state(con, "backend")
                seen.append((cur["state"], cur["throttle_count"]))

            check("daemon: the throttle streak accumulates across park/wake cycles",
                  max(t for _, t in seen) > 1, str(seen))
            final = db.get_agent_state(con, "backend")
            check("daemon: repeated degradation raises to STALLED",
                  final["state"] == dmn_states.STALLED, str(seen))

            dmn_evaluate.tick(con, lambda _m: None)
            check("daemon: STALLED is not cleared automatically",
                  db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED)
        finally:
            pass
    finally:
        con.close()


def test_daemon_degraded_recovers(home: Path) -> None:
    """DEGRADED must not be a silent terminal state."""
    check("daemon: DEGRADED is evaluated every tick", dmn_states.DEGRADED in dmn_states.EVALUATED)
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "h_exp", "backend", "s", "usage", int(time.time()) - 5)
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a timed-out handoff degrades with a wake_at",
              row["state"] == dmn_states.DEGRADED and row["wake_at"] is not None, str(dict(row)))
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        db.set_agent_state(con, "backend", dmn_states.DEGRADED, wake_at=int(time.time()) - 1)
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a due DEGRADED agent with real work returns to QUEUED",
              row["state"] in (dmn_states.QUEUED, dmn_states.PARKED), str(dict(row)))
    finally:
        con.close()


def test_a_stale_wake_at_does_not_claim_work_that_does_not_exist(home: Path) -> None:
    """Founder #645: a THROTTLED wake_at armed for an unrelated reason can
    outlive the turn it was set during - Stop only sets IDLE when the row was
    RUNNING, so a row already THROTTLED when Stop fires stays THROTTLED, work
    or no work, until this wake_at comes due. QUEUED is a claim about the
    queue; the write must look at it before making that claim, the same way
    `_move_plan`'s own "work waiting" does. Driven both ways on the same
    agent: an empty queue reads IDLE, not QUEUED - what ocp/actions/'s delivery
    gate and the bridge would both read as nothing to do - and a real
    undelivered request still reads QUEUED, so the fix narrows the claim
    rather than removing it."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.THROTTLED, wake_at=int(time.time()) - 1,
                           detail="throttled for an unrelated reason")
        check("stale wake: before the tick, the row still claims THROTTLED",
              db.get_agent_state(con, "backend")["state"] == dmn_states.THROTTLED)
        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("stale wake: with nothing queued, the wake reads IDLE, not QUEUED",
              row["state"] == dmn_states.IDLE and row["wake_at"] is None
              and row["detail"] == "wake_at reached; no requests waiting", str(dict(row)))
        check("stale wake: the queue really is empty, not just unread by the wake",
              db.head_of_queue(con, "backend") is None
              and not db.unannounced_answers(con, "backend"))

        db.set_agent_state(con, "backend", dmn_states.THROTTLED, wake_at=int(time.time()) - 1,
                           detail="throttled for an unrelated reason")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("stale wake: with a real request queued, the same wake still reads QUEUED",
              row["state"] == dmn_states.QUEUED and row["wake_at"] is None, str(dict(row)))
    finally:
        con.close()


def test_the_other_wake_writers_also_read_the_queue(home: Path) -> None:
    """reconcile's due wake and the usage-recovered wake claim QUEUED only with
    something queued, like the supervise wake: IDLE on an empty queue."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        for queued in (False, True):
            want = dmn_states.QUEUED if queued else dmn_states.IDLE
            if queued:
                db.add_request(con, "marina", "backend", "carry on", None, "peer",
                               "async", None, 0, None)
            db.set_agent_state(con, "backend", dmn_states.THROTTLED,
                               wake_at=int(time.time()) - 5, detail="stale")
            dmn_reconciler.reconcile(con, lambda _m: None)
            row = db.get_agent_state(con, "backend")
            check(f"reconcile wake: queued={queued} reads {want}",
                  row["state"] == want and row["wake_at"] is None, str(dict(row)))

            _park_via_handoff(con, "backend", "usage")
            db.set_agent_state(con, "backend", dmn_states.PARKED,
                               wake_at=int(time.time()) + 3600, detail="usage")
            write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                         reset_5h=int(time.time()) + 1800)
            dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
            row = db.get_agent_state(con, "backend")
            check(f"usage recovered: queued={queued} reads {want}",
                  row["state"] == want and row["wake_at"] is None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_an_agent_waiting_on_a_reply_is_not_at_a_usage_wall(home: Path) -> None:
    """An async `ocp ask` parks its asker with a "reply #N" detail while a live session waits for the
    answer. The agent's last handoff having been a usage move does not make that a usage park:
    typing at it must not be refused as "parked until its usage window rolls over" (the bridge
    then only queued the founder's message), and the usage-recovered wake must not move or wake it."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage_7d")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 3600,
                           detail="usage_7d; wakes at tomorrow")
        check("usage park: typing is refused as a wall",
              "usage window" in (dmn_delivery.wall_reason(con, "backend") or ""),
              str(dmn_delivery.wall_reason(con, "backend")))
        waiting_detail = askmod.await_reply_detail(9, "marina")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 3600,
                           detail=waiting_detail)
        check("reply wait: the same agent, parked to await a reply, is not at a wall",
              dmn_delivery.wall_reason(con, "backend") is None,
              str(dmn_delivery.wall_reason(con, "backend")))
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5, reset_5h=int(time.time()) + 1800)
        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("reply wait: the usage-recovered wake leaves it waiting for its reply",
              row["state"] == dmn_states.PARKED and row["detail"] == waiting_detail, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_a_park_on_a_reply_that_is_settled_is_dropped_on_the_next_tick(home: Path) -> None:
    """T456 (founder #5770): a resolve un-parks its asker only when nothing else it asked is
    outstanding, so an agent with a second ask open stayed "waiting for" an answer that had come,
    with what was told to it queued behind. The tick reads the park against its request."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: False
        db.set_terminal(con, "backend", "term_x", "wt")
        first = db.add_request(con, "backend", "marina", "the first ask", None, "peer", "async", None, 0, None)
        second = db.add_request(con, "backend", "marina", "the second ask", None, "peer", "async", None, 0, None)
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 3600,
                           detail=askmod.await_reply_detail(first, "marina"))
        lines: list[str] = []
        dmn_evaluate.supervise(con, "backend", lines.append, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: while its reply is still awaited it stays parked",
              row["state"] == dmn_states.PARKED and not lines, str((dict(row), lines)))
        askmod.resolve(first, "answered")             # the second ask is still open, so nothing un-parks it
        row = db.get_agent_state(con, "backend")
        check("park: setup - the resolve left it parked on the answered ask", row["state"] == dmn_states.PARKED,
              str(dict(row)))
        dmn_evaluate.supervise(con, "backend", lines.append, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: the next tick drops it, and says so",
              row["state"] != dmn_states.PARKED and any(f"#{first}" in m and "settled" in m for m in lines),
              str((dict(row), lines)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_an_owed_clear_that_holds_everything_says_why_once(home: Path) -> None:
    """T456: settle_owed_clear returns True while a clear is owed and the tick then does nothing else
    for the agent - it must not do so silently."""
    from ocp.daemon import handoff as dmn_handoff
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


def test_daemon_tick_expires_handoff(home: Path) -> None:
    """The deadline must be enforced every tick, not only at startup."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "h_tick", "backend", "s", "usage", int(time.time()) - 600)
        db.set_agent_state(con, "backend", dmn_states.HANDOFF)
        dmn_evaluate.tick(con, lambda _m: None)
        check("daemon: tick expires a handoff past its deadline",
              db.in_flight_handoffs(con) == [],
              str([dict(h) for h in db.in_flight_handoffs(con)]))
        check("daemon: the agent does not stay wedged in HANDOFF",
              db.get_agent_state(con, "backend")["state"] != dmn_states.HANDOFF)
    finally:
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
        outcome, title, why = orcamod.decline_dialog("term_x")
        check(f"press: {name} - {'Esc' if presses else 'nothing'}",
              (term.sent == [("\x1b", False)]) if presses else term.sent == [],
              f"{outcome} {why} sent={term.sent}")
        if presses:
            check(f"press: {name} - the dialog goes, and it says so", outcome == orcamod.CLEARED, outcome)
        else:
            check(f"press: {name} - and says why", outcome == orcamod.NOT_PRESSED and why, f"{outcome} {why}")

    # What is on screen NOW decides: a screen read as a dialog by the block, and
    # gone by the time of the key, gets nothing.
    term = _Terminal(CLEARED_SCREEN)
    term.install()
    check("press: no dialog now, no key", orcamod.decline_dialog("term_x")[0] == orcamod.NOT_PRESSED
          and term.sent == [])
    for name, term in (("a stream, not a screen", _Terminal(EXIT_DIALOG, source="stream")),
                       ("a screen that cannot be read", None)):
        if term:
            term.install()
        else:
            orcamod._screen = lambda h: None
            orcamod.send = lambda h, text, enter=True: (_ for _ in ()).throw(AssertionError("typed"))
        check(f"press: {name} - nothing", orcamod.decline_dialog("term_x")[0] == orcamod.NOT_PRESSED
              and (term.sent == [] if term else True))

    term = _Terminal(EXIT_DIALOG, ignores_esc=True)
    term.install()
    out = orcamod.decline_dialog("term_x")
    check("press: Esc that leaves the dialog standing is reported once, not repeated",
          out[0] == orcamod.STILL_OPEN and term.sent == [("\x1b", False)], f"{out} {term.sent}")
    check("press: the screen is read before the key and again after it", term.reads >= 3, str(term.reads))
    check("press: the only key that is ever named is Esc",
          orcamod.ESCAPE == "\x1b" and "Enter" not in repr(orcamod.declining_key))

    # `draft` is not consulted here either (#1157): it is polluted by ocp's own
    # sends, and refusing on it would leave a stale one's dialog unpressed.
    pressed = []
    orcamod._screen = lambda h: dict(_screen_of(EXIT_DIALOG), draft="a person's half-typed reply")
    orcamod.send = lambda h, text, enter=True: (pressed.append((text, enter)),
                                                (True, ""))[1]
    out = orcamod.decline_dialog("term_x")
    check("press: a non-empty draft on the same read as the dialog does not stop the press",
          out[0] != orcamod.NOT_PRESSED and pressed == [("\x1b", False)], f"{out} sent={pressed}")

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
    orcamod._screen = flaky_then_gone
    orcamod.send = lambda h, text, enter=True: (sent.append((text, enter)), (True, ""))[1]
    out = orcamod.decline_dialog("term_x")
    check("press: an unreadable screen right after Esc is not read as cleared",
          out[0] == orcamod.CLEARED and calls["n"] >= 3, f"{out} reads={calls['n']}")

    # And if it never comes back readable, that is reported as still open, not
    # as a clear nobody can stand behind.
    reads = {"n": 0}

    def never_readable_after(h):
        reads["n"] += 1
        return _screen_of(EXIT_DIALOG) if reads["n"] == 1 else _screen_of(EXIT_DIALOG, source="stream")
    orcamod._screen = never_readable_after
    out = orcamod.decline_dialog("term_x")
    check("press: a screen that stays unreadable is still open, never a false clear",
          out[0] == orcamod.STILL_OPEN, out)

    # A DIFFERENT dialog showing up right after the key is not the one Esc
    # was pressed on still standing - that one is gone, whatever this new one
    # is, and still-open must never be counted against its title instead.
    term = _Terminal(EXIT_DIALOG, after=YES_NO_DIALOG)
    term.install()
    out = orcamod.decline_dialog("term_x")
    check("press: a different dialog appearing after Esc is not the same one still open",
          out[0] == orcamod.CLEARED and out[1] == "Background work is running", out)


def test_an_agent_with_background_work_is_not_idle_until_it_ends(home: Path) -> None:
    """Task #31: a Stop with work still running behind the prompt leaves the row
    IDLE (or QUEUED) in the db, and everything that acts on idleness believed
    it. The session's own footer count marks the agent, and the mark drives all
    of it, in either state."""
    _daemon_home(home, 20)
    con = db.connect()
    names = ("available", "alive", "typeable_now", "send", "clear_context",
             "background_work", "live_terminals")
    real = {n: getattr(orcamod, n) for n in names}
    sent: list[str] = []
    cleared: list[str] = []
    bg = ["3 monitors"]
    sessions_are("backend")
    st = types.SimpleNamespace(session_id="s")
    try:
        orca_seen()
        orcamod.typeable_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        orcamod.clear_context = lambda h, *a, **k: (cleared.append(h), (True, "cleared"))[1]
        orcamod.background_work = lambda h: bg[0]
        db.set_terminal(con, "backend", "term_x", "wt")
        now = int(time.time())
        head = db.add_request(con, "marina", "backend", "carry on", None,
                              "peer", "async", None, 0, None)
        db.mark_delivered(con, head)
        db.set_cursor(con, "notify:backend", head)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_states.IDLE_CLOSE_S - 60,))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60, head))

        for state, detail in ((dmn_states.IDLE, "finished its turn"), (dmn_states.QUEUED, "moved alpha -> beta")):
            db.clear_cursor(con, "bgwork:backend")
            db.clear_cursor(con, "nudge:backend")
            db.clear_cursor(con, "nudge_try:backend")
            con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - dmn_states.IDLE_CLOSE_S - 60,))
            db.set_agent_state(con, "backend", state, detail=detail)
            del sent[:]
            bg[0] = "3 monitors"
            dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
            row = db.get_agent_state(con, "backend")
            word = tui.screen_state(con, row)[0]
            if state == dmn_states.IDLE:
                check(f"background {state}: the row is not shown working",
                      word == "IDLE", word)
            check(f"background {state}: no resolve nudge is typed at it",
                  not dmn_delivery.nudge_forgotten_resolve(con, "backend", row, now, lambda _m: None)
                  and not sent, str(sent))
            ok, why = dmn_delivery._reclaim_context(con, "backend", lambda _m: None)
            check(f"background {state}: /clear is not typed at it either",
                  not ok and not cleared and "3 monitors" in why, f"{why} {cleared}")
            del sent[:]           # T415: the agent was told its clear is coming - not a nudge
            db.clear_cursor(con, "clear_owed:backend")
            bg[0] = ""
            dmn_delivery.track_background_work(con, "backend", db.get_agent_state(con, "backend"))
            row = db.get_agent_state(con, "backend")
            check(f"background {state}: not nudged the tick its work ends - the idle clock starts there",
                  not dmn_delivery.nudge_forgotten_resolve(con, "backend", row, now, lambda _m: None)
                  and not sent, str(sent))
            later = int(time.time()) + dmn_delivery.NUDGE_AFTER_S + 60
            check(f"background {state}: and nudged like any idle agent once it has been idle",
                  dmn_delivery.nudge_forgotten_resolve(con, "backend", row, later, lambda _m: None) and sent,
                  str(sent))

        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        # Nothing queued for it now, so only the work itself can keep it open.
        con.execute("UPDATE requests SET state = 'done'")
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - dmn_states.IDLE_CLOSE_S - 60,))
        row = db.get_agent_state(con, "backend")
        check("background: it is idle-closable with no work running",
              dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))
        bg[0] = "1 shell"
        dmn_delivery.track_background_work(con, "backend", row)
        check("background: while work runs it is not idle-closed",
              not dmn_lifecycle.should_close(con, "backend", dmn_states.IDLE, row, now, st))
        # A read that fails while work runs is not the work ending.
        bg[0] = None
        events_before = db.last_event_ts(con, "backend", "background_work_ended")
        dmn_delivery.track_background_work(con, "backend", row)
        check("background: an unreadable screen leaves the mark and writes no end",
              dmn_delivery.has_background_work(con, "backend")
              and db.last_event_ts(con, "backend", "background_work_ended") == events_before)
        # The end restarts the idle clocks but is not a model turn: a dead prompt
        # cache must not read warm because a monitor finished.
        bg[0] = ""
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 2 * 3600,))
        dmn_delivery.track_background_work(con, "backend", row)
        check("background: the end is on the idle clock",
              dmn_delivery.idle_since(con, "backend") >= now - 5)
        check("background: but not on the activity clock the prompt cache is judged by",
              db.last_activity(con, "backend") <= now - 2 * 3600 + 5
              and dmn_lifecycle.cache_expired(con, "backend", "sess", now))
    finally:
        for n, f in real.items():
            setattr(orcamod, n, f)
        con.close()


def test_a_config_that_will_not_parse_is_never_overwritten_by_a_setting(home: Path) -> None:
    """T86: every writer of config.yaml read a file that failed to parse as
    empty, and then wrote that one key over the whole of it."""
    from ocp.infra.yamlcompat import ConfigError
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
        "dev mode": lambda: dmn_devmode.set_dev_mode(True),
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
    real = {n: getattr(orcamod, n) for n in names}
    bg = ["1 shell"]
    try:
        orca_seen()
        orcamod.background_work = lambda h: bg[0]
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
            setattr(orcamod, n, f)
        con.close()


def test_a_screen_with_background_work_is_kept_as_a_sample(home: Path) -> None:
    """T592: what tells an idle agent with background work from one mid-turn is on its screen, which
    only the daemon can read. The rows the tick just read are kept - labelled with the db's state -
    once per interval, and only the newest few files stay."""
    from ocp.daemon import screensamples
    _daemon_home(home, 20)
    con = db.connect()
    names = ("available", "alive", "background_work", "last_tail", "live_terminals")
    real = {n: getattr(orcamod, n) for n in names}
    bg = ["1 shell"]
    rows = ["Some output", "\u256d" + "\u2500" * 20, "\u2570" + "\u2500" * 20, "  1 shell \u00b7 to manage"]
    try:
        orca_seen()
        orcamod.background_work = lambda h: bg[0]
        orcamod.last_tail = lambda h: rows
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
        real_screen, real_last = orcamod._screen, real["last_tail"]
        orcamod.background_work = real["background_work"]
        orcamod._screen = lambda h, limit=40: {"source": "screen", "tail": rows}
        try:
            orcamod.background_work("term_y")
            check("sample: the real read keeps the rows it took, for the sample", real_last("term_y") == rows)
        finally:
            orcamod._screen = real_screen
        orcamod.last_tail = lambda h: None
        db.clear_cursor(con, "screen_sample:backend")
        check("sample: no rows read, nothing written and no failure",
              screensamples.record(con, "backend", row, "1 shell", None) is False)
    finally:
        for n, f in real.items():
            setattr(orcamod, n, f)
        con.close()


def test_a_closed_session_shows_no_background_work(home: Path) -> None:
    """T405: the founder closed a session that had background work; its `bgwork`
    cursor stayed, so `ocp team` kept saying 'RUNNING background 8h, stale'. A
    session that is gone runs nothing: the screen reads it IDLE straight away,
    and the next tick drops the cursor."""
    _daemon_home(home, 20)
    con = db.connect()
    names = ("available", "alive", "background_work", "live_terminals")
    real = {n: getattr(orcamod, n) for n in names}
    try:
        orca_seen()
        orcamod.background_work = lambda h: "1 shell"
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        dmn_delivery.track_background_work(con, "backend", db.get_agent_state(con, "backend"))
        seen = tui.screen_state(con, db.get_agent_state(con, "backend"))
        check("closed: setup - an open session with background work reads idle, with the background note",
              seen[0] == "IDLE" and seen[1].startswith("background"), str(seen))

        db.set_closed(con, "backend", "sess-1")
        row = db.get_agent_state(con, "backend")
        check("closed: the moment it is recorded closed the row is not RUNNING",
              tui.screen_state(con, row) == ("IDLE", "") and not dmn_delivery.has_background_work(con, "backend"),
              str(tui.screen_state(con, row)))
        check("closed: a closed session has no background 'since'", dmn_delivery.bg_since(con, "backend") is None)
        dmn_delivery.track_background_work(con, "backend", row)
        check("closed: the tick drops the stale cursor, so a wake starts clean",
              not db.get_cursor(con, "bgwork:backend"))
    finally:
        for n, f in real.items():
            setattr(orcamod, n, f)
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
              idle_close.close_timeout_s("backend") == dmn_states.IDLE_CLOSE_S)
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

        later = now + dmn_states.RESUME_WARM_S + 60
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
    footer = orcamod._footer_row(tail)
    check("live trust: the footer is found", footer is not None, str(footer))
    check("live trust: parse_dialog gives None",
          footer is not None and orcamod.parse_dialog(tail, footer) is None)
    term = _Terminal(tail)
    term.install()
    seen = orcamod.screen_blocker("term_x")
    check("live trust: screen_blocker reads it as a dialog",
          seen is not None and seen.kind == orcamod.MODAL, str(seen))
    outcome, _title, why = orcamod.decline_dialog("term_x")
    check("live trust: decline_dialog presses nothing, and says why",
          outcome == orcamod.NOT_PRESSED and why and term.sent == [], f"{outcome} {why} {term.sent}")

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
          dmn_locks.running_elsewhere() is None)
    holder, pid = _hold_daemon_lock_in_another_process(env)
    try:
        check("daemon: the process holding the lock is the one reported",
              dmn_locks.running_elsewhere() == pid, str(dmn_locks.running_elsewhere()))
        check("daemon: a probe leaves the hold with its holder",
              lock.holder(dmn_locks.DAEMON_LOCK) == pid)
        with lock.singleton(dmn_locks.DAEMON_LOCK) as free:
            check("daemon: and a second daemon is refused, by the same fact", not free)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            clidaemon.cmd_daemon_status(None)
        check("ps: says running, and names the pid",
              f"daemon: running, pid {pid}" in buf.getvalue(), buf.getvalue())
        r = doctor.Report()
        doctor._check_one_daemon(r)
        check("doctor: says running, with no stale-pidfile warning",
              r.rows[-1][0] == doctor.OK and str(pid) in r.rows[-1][1]
              and not any(lv != doctor.OK for lv, _, _ in r.rows), str(r.rows))
    finally:
        holder.kill()
        holder.wait(timeout=30)

    check("daemon: the name a dead holder left behind is not a running daemon",
          dmn_locks.running_elsewhere() is None, str(dmn_locks.running_elsewhere()))
    check("daemon: ...and needs no clearing by anything", lock.holder(dmn_locks.DAEMON_LOCK) is None)


def test_a_daemon_that_never_wrote_its_name_is_still_reported(home: Path) -> None:
    """A daemon started before the lock recorded its holder, still running under
    the service, holds the lock and names nobody. It is named from the process
    table, not read as not running."""
    use_home(home)
    env = {**os.environ, "OCP_HOME": str(home)}
    holder, pid = _hold_daemon_lock_in_another_process(env, write_name=False)
    orig = dmn_locks.processes
    try:
        check("lock: held without a name reads as held, not as free",
              lock.holder(dmn_locks.DAEMON_LOCK) == 0)
        dmn_locks.processes = lambda: [pid]
        check("daemon: named from the process table", dmn_locks.running_elsewhere() == pid)
    finally:
        dmn_locks.processes = orig
        holder.kill()
        holder.wait(timeout=30)


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
              lock.holder(dmn_locks.DAEMON_LOCK) == pid)
        check("daemon: still reported running from inside the sandbox",
              dmn_locks.running_elsewhere() == pid)
    finally:
        os.kill = orig_kill
        holder.kill()
        holder.wait(timeout=30)


def test_the_lock_names_only_its_current_holder(home: Path) -> None:
    use_home(home)
    check("lock: no file yet, no holder", lock.holder(dmn_locks.DAEMON_LOCK) is None)
    with lock.singleton(dmn_locks.DAEMON_LOCK) as mine:
        check("lock: taken", mine)
        check("lock: the holder writes its own pid", lock.holder(dmn_locks.DAEMON_LOCK) == os.getpid())
        with lock.singleton(dmn_locks.DAEMON_LOCK) as second:
            check("lock: a refused second taker does not overwrite the name", not second)
        check("lock: ...the holder is still named", lock.holder(dmn_locks.DAEMON_LOCK) == os.getpid())

        # T153: a held lock whose file still names the previous holder, dead,
        # reads as held-but-nameless (0), never as that dead pid - in a sandbox
        # too, where a signal to a live process is refused (EPERM, and so the
        # holder is named, above) but one to a dead pid still fails ESRCH.
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        path = home / "locks" / f"{dmn_locks.DAEMON_LOCK}.{lock.SINGLETON}"
        fd = os.open(path, os.O_WRONLY)
        os.pwrite(fd, str(gone.pid).ljust(lock._PID_WIDTH).encode(), 0)
        os.close(fd)
        check("lock: a dead pid left in the file is read as nameless", lock.holder(dmn_locks.DAEMON_LOCK) == 0)
        real_kill = os.kill

        def refused(pid, sig):
            if pid == gone.pid:
                raise ProcessLookupError(3, "No such process")
            raise PermissionError(1, "Operation not permitted")
        os.kill = refused
        try:
            check("lock: a sandbox refusing the signal never turns the name into a pid",
                  lock.holder(dmn_locks.DAEMON_LOCK) == 0)
        finally:
            os.kill = real_kill
        fd = os.open(path, os.O_WRONLY)
        os.pwrite(fd, str(os.getpid()).ljust(lock._PID_WIDTH).encode(), 0)
        os.close(fd)
    check("lock: released, nobody is named although the file still holds the pid",
          lock.holder(dmn_locks.DAEMON_LOCK) is None)
    with lock.terminal("agent-x") as t:
        check("lock: a terminal hold takes no pid", t)
    check("lock: and is not a daemon", lock.holder(dmn_locks.DAEMON_LOCK) is None)


def test_a_daemon_process_that_does_not_hold_the_lock_is_not_called_absent(home: Path) -> None:
    """The process table and the lock disagreeing is said, not settled in
    silence: a daemon that is starting reads that way for an instant, and a
    screen that answers "no daemon" over a list it just computed contradicts
    itself."""
    use_home(home)
    orig = dmn_locks.processes
    try:
        dmn_locks.processes = lambda: [4242]
        r = doctor.Report()
        doctor._check_one_daemon(r)
        check("doctor: a daemon process holding no lock warns, naming it",
              r.rows[-1][0] == doctor.WARN and "4242" in r.rows[-1][1], str(r.rows))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            clidaemon.cmd_daemon_status(None)
        check("ps: says the same, beside 'not running'",
              "daemon: not running" in buf.getvalue() and "pid 4242" in buf.getvalue(),
              buf.getvalue())
        dmn_locks.processes = lambda: []
        r = doctor.Report()
        doctor._check_one_daemon(r)
        check("doctor: no process and no lock is plainly no daemon",
              r.rows[-1][0] == doctor.OK and "no daemon" in r.rows[-1][1], str(r.rows))
    finally:
        dmn_locks.processes = orig


# ---------------------------------------------------------------- service
def test_service_plist(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    plist = service.build_plist(home=home)
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
    check("service: the interpreter is not pinned - bin/ocp's own shebang resolves it",
          "python" not in plist["ProgramArguments"][0].lower())

    path = service.write_plist(path=home / "test.plist", home=home)
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
    try:
        service.plist_path = lambda: plist_file
        service.run_launchctl = fake_ok
        ok, msg = service.install(home=home)
        check("service: install() reports success when launchctl does", ok, msg)
        check("service: install() writes the plist", plist_file.exists())
        check("service: a first install (nothing loaded yet) skips bootout",
              "bootout" not in [c[0] for c in calls])
        check("service: install() bootstraps the built plist",
              calls[0] == ["bootstrap", service.domain_target(), str(plist_file)])
        check("service: install() enables the job", calls[1][0] == "enable")

        # Reinstalling over a job the fake now reports as loaded (`print`
        # returns 0) must bootout first, so a stale plist is not left running.
        calls.clear()

        def fake_loaded(args):
            calls.append(args)
            if args[0] == "print":
                return FakeResult(0, "\tpid = 1\n", "")
            return FakeResult(0, "", "")

        service.run_launchctl = fake_loaded
        ok, msg = service.install(home=home)
        check("service: reinstalling over a loaded job boots it out first",
              ok and calls[1][0] == "bootout", str(calls))

        # If the new plist fails to load, a previously-working job must not
        # end up unsupervised - the old plist is restored and reloaded.
        calls.clear()
        attempts = {"n": 0}

        def fake_bootstrap_fails_once(args):
            calls.append(args)
            if args[0] == "print":
                return FakeResult(0, "\tpid = 1\n", "")
            if args[0] == "bootstrap":
                attempts["n"] += 1
                return FakeResult(0, "", "") if attempts["n"] > 1 else FakeResult(1, "", "boom")
            return FakeResult(0, "", "")

        service.run_launchctl = fake_bootstrap_fails_once
        ok, msg = service.install(home=home)
        check("service: install() reports failure when bootstrap fails", not ok, msg)
        check("service: a failed reinstall still retries bootstrap with the restored plist",
              attempts["n"] == 2)

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
        check("service: uninstall() reports success", ok, msg)
        check("service: uninstall() removes the plist", not plist_file.exists())
        check("service: uninstall() boots the job out", calls[0][0] == "bootout")
    finally:
        service.plist_path = orig_plist_path
        service.run_launchctl = orig_runner


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


def test_service_crash_loop(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    log = service.out_log(home)
    log.parent.mkdir(parents=True, exist_ok=True)

    now = time.localtime()

    def stamp(seconds_ago: int) -> str:
        t = time.localtime(time.mktime(now) - seconds_ago)
        return time.strftime("%H:%M:%S", t)

    # Four restarts inside a minute: a real crash loop.
    lines = [f"[{stamp(s)}] daemon starting - reconciling" for s in (0, 15, 30, 45)]
    log.write_text("\n".join(lines) + "\n")
    check("service: four restarts in a minute is a crash loop",
          service.crash_loop_detected(home, window_s=300, threshold=4))

    # One startup line, long-running since: not a loop.
    log.write_text(f"[{stamp(0)}] daemon starting - reconciling\n")
    check("service: a single startup is not a crash loop",
          not service.crash_loop_detected(home, window_s=300, threshold=4))

    # Restarts, but spread across hours: not a loop.
    lines = [f"[{stamp(s)}] daemon starting - reconciling" for s in (0, 3600, 7200, 10800)]
    log.write_text("\n".join(lines) + "\n")
    check("service: restarts spread across hours is not a crash loop",
          not service.crash_loop_detected(home, window_s=300, threshold=4))

    # The most likely real crash loop: a hand-started daemon holds the
    # daemon lock, launchd's copy exits at once every restart without ever
    # reaching reconcile. It must still be visible to the detector.
    lines = [f"[{stamp(s)}] daemon starting - already running (pid 1), exiting"
             for s in (0, 15, 30, 45)]
    log.write_text("\n".join(lines) + "\n")
    check("service: repeated startup refusals are a crash loop too",
          service.crash_loop_detected(home, window_s=300, threshold=4))


def test_daemon_refusal_logs_through_the_same_path(home: Path) -> None:
    """The refusal used to be a bare `print`, invisible to
    crash_loop_detected. It must share the logger and the 'daemon starting'
    wording every other startup line uses."""
    import contextlib
    import io

    use_home(home)
    buf = io.StringIO()
    with lock.singleton(dmn_locks.DAEMON_LOCK) as held:
        check("daemon: the lock is free to take in a fresh home", held)
        with contextlib.redirect_stdout(buf):
            rc = dmn_loop.run(once=True)
    out = buf.getvalue()
    check("daemon: refuses while another process holds the lock", rc == 1)
    check("daemon: the refusal is logged, not printed bare",
          "daemon starting - already running" in out, out)


def test_the_daemon_logs_the_code_sha_it_started_on(home: Path) -> None:
    """T431: a daemon left behind by merges is caught by comparing this line to main."""
    from ocp.infra import gitrun
    _daemon_home(home, 20)
    _, head, _ = gitrun.run(paths.repo_root(), "rev-parse", "--short", "HEAD")
    out = _capture(lambda: dmn_loop.run(once=True, verbose=True))
    check("daemon: the start line names the checkout's HEAD",
          head.strip() != "" and f"  code: {head.strip()}\n" in out, out[:300])

    real = dmn_loop.gitrun.run
    dmn_loop.gitrun.run = lambda *a, **k: (None, "", "git is not there")
    try:
        out = _capture(lambda: dmn_loop.run(once=True, verbose=True))
    finally:
        dmn_loop.gitrun.run = real
    check("daemon: when git fails the start line says unknown, not an empty sha",
          "  code: unknown\n" in out, out[:300])


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
    real = (orcamod.alive, orcamod.idle_now, orcamod.send, orcamod.available)
    try:
        orca_seen()
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
        check("deliver: and it is told how to read it", "ocp inbox" in sent[-1], sent[-1])
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
        orcamod.idle_now = lambda h: False
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
        orcamod.idle_now = lambda h: True
        head = db.head_of_queue(con, "backend")
        sent.clear()
        askmod.resolve(head["id"], "done")
        check("deliver: resolving the head delivers the one behind it, right away",
              sent and "and this" in sent[-1] and "look at this" not in sent[-1], str(sent))
    finally:
        orcamod.alive, orcamod.idle_now, orcamod.send, orcamod.available = real
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


def test_a_resume_woken_for_a_message_is_handed_that_message(home: Path) -> None:
    """Waking sends the agent a prompt it is now reading. Typing the message
    after it, in the same tick, lands at an agent mid-turn and leaves the text
    in its input box unsent - the failure delivery exists to avoid - and
    "carry on" ahead of it says nothing. So the message is the prompt (T124),
    and the ticks after it do not tell it again."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True      # even so, it must not be told yet
        orcamod.close_terminal = lambda h, tab=True: (False, "this test keeps the pane")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-4", "pct_5h": 20}))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        check("wake: the tick that wakes it brings the session back",
              any("--resume sess-4" in t for t in sent), str(sent))
        told = [t for t in sent if "look at this" in t]
        check("wake: the resume's first prompt is the message itself",
              len(told) == 1 and "from reviewer, waiting for you" in told[0]
              and "ocp inbox" in told[0], str(sent))
        check("wake: and not the generic line ahead of it",
              not any("Nothing was lost" in t for t in sent), str(sent))
        check("wake: the tell is on the row, for the session that read it",
              db.get_cursor(con, "notify:backend") is not None
              and db.get_notified_session(con, "backend") == "sess-4",
              str(db.get_notified_session(con, "backend")))

        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        check("wake: the following tick does not tell it again",
              not any("look at this" in t for t in sent), str(sent))

        # An answer waiting is a message too.
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        rid = db.add_request(con, "backend", "reviewer", "ship it?", None,
                             "peer", "async", None, 0, None)
        support._set_request_state(con, rid, "done", "yes, ship it")
        sent.clear()
        dmn_evaluate.tick(con, lambda _m: None)
        told = [t for t in sent if "Answers to what you asked" in t]
        check("wake: a resume waiting on an answer is handed the answer",
              len(told) == 1 and "yes, ship it" in told[0]
              and not any("Nothing was lost" in t for t in sent), str(sent))
        check("wake: and the answer is marked told", not db.unannounced_answers(con, "backend"),
              str(sent))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available) = real
        con.close()


def test_a_closed_session_closes_its_pane_and_the_wake_opens_a_new_terminal_that_resumes(home: Path) -> None:
    """T407: a session that ends takes its pane with it, and the wake starts a
    new terminal in the agent's worktree running the same launch a same-shell
    wake types - the resume id and the wake prompt - so no resume is lost.
    An agent that never closes keeps its pane."""
    from ocp.actions import control
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    closed: list[str] = []
    created: list[str] = []
    real = (spawnmod.start_in, orcamod.send)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        live = {"term_x"}
        orcamod.live_terminals = lambda: set(live)

        def close_terminal(handle, tab=True):
            closed.append(handle)
            live.discard(handle)
            return True, "closed"
        orcamod.close_terminal = close_terminal

        def start_in(agent, worktree, cmd=None):
            created.append(cmd)
            live.add("term_new")
            db.set_terminal(con, agent, "term_new", worktree)
            table[agent] = 4242
            sent.append(take_launched_prompt(cmd))
            return True, f"{agent}: started in {worktree}"
        spawnmod.start_in = start_in

        wt = home / "wt"
        wt.mkdir()
        db.set_terminal(con, "backend", "term_x", str(wt))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-4", "pct_5h": 20}))
        ok, said = dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                                             status.read_for_agent("backend", "alpha"))
        row = db.get_agent_state(con, "backend")
        check("pane: closing a session closes its terminal and drops the handle, keeping the worktree",
              ok and closed == ["term_x"] and not row["terminal"] and row["worktree"] == str(wt)
              and row["closed_at"] and row["resume_session"] == "sess-4", f"{ok} {said} {closed} {dict(row)}")
        check("pane: and records it", any(e["kind"] == "terminal_closed"
                                          for e in db.events_after(con, 0, agent="backend")))
        check("pane: a closed agent with no terminal is woken, not started fresh",
              dmn_lifecycle.start_or_wake_needed(con, "backend", row) is None
              and "wake brings it back" in dmn_lifecycle._start_for_work(con, "backend", lambda _m: None)
              and not created)

        asked = db.add_request(con, "reviewer", "backend", "look at this", None, "peer", "async", None, 0, None)
        need = dmn_lifecycle.start_or_wake_needed(con, "backend", db.get_agent_state(con, "backend"))
        check("pane: work waiting makes it a wake", need and need[0] == "wake", str(need))
        state = actions._delivery_state(con, "backend", dict(table), set(live))
        check("pane: and the stuck list gives the closed reason, not 'no terminal'",
              state[0] == "closed, waking" and state[2], str(state))
        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("pane: the wake opens a terminal running the resume with the wake prompt",
              len(created) == 1 and " launch backend" in created[0] and "--resume sess-4" in created[0]
              and "--prompt-file " in created[0], str(created))
        check("pane: the resume's first prompt is the message",
              any(t and "look at this" in t for t in sent), str(sent))
        check("pane: the new terminal is the agent's and it is no longer closed",
              row["terminal"] == "term_new" and not row["closed_at"], str(dict(row)))

        # A person opening the terminal of a closed agent with nothing waiting resumes it too.
        db.close_if_queued(con, asked, "done", "seen", announced=True)
        dmn_delivery._seen.clear()
        table.clear()
        created.clear()
        sent.clear()
        db.set_terminal(con, "backend", "term_new", str(wt))
        dmn_lifecycle.close_agent(con, "backend", lambda _m: None,
                                  status.read_for_agent("backend", "alpha"))
        db.set_closed(con, "backend", "sess-4")
        db.set_terminal(con, "backend", None, str(wt))
        said = control.start(con, "backend")
        check("pane: the terminal key on a closed agent resumes its session",
              len(created) == 1 and "--resume sess-4" in created[0]
              and any(t and "opened your terminal" in t for t in sent), f"{said} {created} {sent}")

        # One that never closes keeps its pane.
        idle_close.set_agent_close_timeout("backend", idle_close.NEVER)
        closed.clear()
        dmn_delivery._seen.clear()
        table["backend"] = 4200
        db.clear_closed(con, "backend")
        db.set_terminal(con, "backend", "term_x", str(wt))
        live.add("term_x")
        ok, said = dmn_lifecycle._end_session(con, "backend", lambda _m: None,
                                              status.read_for_agent("backend", "alpha"), resume="sess-4")
        check("pane: an agent set to never close keeps its terminal",
              ok and not closed and db.get_terminal(con, "backend") == "term_x", f"{ok} {said} {closed}")

        # A pane that would not close, and is still there, is kept: the next wake types into it.
        idle_close.set_agent_close_timeout("backend", None)
        db.clear_closed(con, "backend")
        table["backend"] = 4200
        orcamod.close_terminal = lambda h, tab=True: (False, "orca is busy")
        ok, said = dmn_lifecycle._end_session(con, "backend", lambda _m: None,
                                              status.read_for_agent("backend", "alpha"), resume="sess-4")
        check("pane: a terminal that did not close keeps its handle", ok and db.get_terminal(con, "backend") == "term_x"
              and "kept" in said, f"{ok} {said}")

        # ...and a move, which starts a session in the same pane at once, does not close it.
        db.clear_closed(con, "backend")
        table["backend"] = 4200
        closed.clear()
        orcamod.close_terminal = close_terminal
        ok, _ = dmn_lifecycle._end_session(con, "backend", lambda _m: None,
                                           status.read_for_agent("backend", "alpha"), resume=None,
                                           keep_pane=True)
        check("pane: a caller that relaunches at once keeps the pane", ok and not closed
              and db.get_terminal(con, "backend") == "term_x", f"{ok} {closed}")
    finally:
        spawnmod.start_in, orcamod.send = real
        con.close()


def test_a_parked_closed_agent_without_a_pane_is_woken_at_its_deadline(home: Path) -> None:
    """T407: the deadline wake of a closed park needed a terminal, and a closed
    agent has none now; a worktree to open one in is enough."""
    _daemon_home(home, 20)
    con = db.connect()
    created: list[str] = []
    real = spawnmod.start_in
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        table = sessions_are()

        def start_in(agent, worktree, cmd=None):
            created.append(cmd)
            db.set_terminal(con, agent, "term_x", worktree)
            table[agent] = 4242
            take_launched_prompt(cmd)
            return True, "started"
        spawnmod.start_in = start_in
        wt = home / "wt"
        wt.mkdir()
        rid = db.add_request(con, "reviewer", "backend", "long job", None, "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)
        db.set_terminal(con, "backend", None, str(wt))
        db.set_closed(con, "backend", "sess-4")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 5,
                           detail="waiting for the window")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()), "session_id": "sess-4", "pct_5h": 20}))
        row = db.get_agent_state(con, "backend")
        check("deadline: a closed park with only a told request has no wake cause but is waited for",
              dmn_lifecycle.closed_wake_cause(con, "backend", row) is None
              and dmn_lifecycle.waiting_for(con, "backend"))
        dmn_evaluate.tick(con, lambda _m: None)
        check("deadline: it is woken into a new terminal at its deadline",
              len(created) == 1 and "--resume sess-4" in created[0], str(created))
    finally:
        spawnmod.start_in = real
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
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.close_terminal = lambda h, tab=True: (False, "this test keeps the pane")
        table = sessions_are("backend")
        relaunch = relaunching_send(sent, table)

        def send(handle, text, enter=True):
            if " launch " in text:
                # What the new session is handed as its system prompt.
                seen.append(launch.context_for("backend"))
            return relaunch(handle, text, enter)
        orcamod.send = send

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
        warm_sent, _ = wake_after(dmn_states.RESUME_WARM_S - 120)
        check("cache: an agent idle inside the cache's life is resumed",
              any("--resume sess-7" in t for t in warm_sent), str(warm_sent))

        # Past it: a fresh conversation that is handed the handoff.
        db.set_closed(con, "backend", "sess-7")
        support.something_waiting(con, "backend")
        table.clear()
        cold_sent, cold_ctx = wake_after(dmn_states.RESUME_WARM_S + 60)
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
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
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
            for idle, cold in ((dmn_states.RESUME_WARM_S + 60, True), (dmn_states.RESUME_WARM_S - 120, False)):
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
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context, orcamod.relaunch,
            orcamod.typeable_now, orcamod.resume_with)
    logs: list[str] = []
    try:
        orca_seen()
        orcamod.relaunch = lambda handle, cmd, started=None: (False, "no session started")
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
        orcamod.idle_now = lambda h: True
        orcamod.typeable_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (typed.append(text), (True, ""))[1]
        orcamod.resume_with = lambda h, text: (typed.append(text), (True, ""))[1]
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
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context, orcamod.relaunch,
         orcamod.typeable_now, orcamod.resume_with) = real
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


def test_the_sweep_corrects_what_it_can_see(home: Path) -> None:
    """Every other decision reads a row we wrote ourselves, which is how a state
    that went wrong stayed wrong. This is the half that asks the world instead,
    and it corrects rather than reports - a supervisor that only files
    complaints leaves the human in the loop it exists to take them out of."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        orcamod.available = lambda: True

        # 1. Its terminal is gone. Nothing may be delivered to it and nothing
        #    can be closed in it, and both already key on having a handle.
        #    Confirmed gone is an answer, not an unread one: it was RUNNING and
        #    at a prompt, and both must end, the same as the "session died
        #    without us" branch below does when it has its own confirmation.
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
        waiting.note("backend", "Bash", {"command": "rm x"})
        orcamod.live_terminals = lambda: set()
        obsmod.live_sessions = lambda: {}
        gone_actions: list[str] = []
        dmn_reconciler.sweep(con, lambda _m: None, gone_actions)
        check("sweep: a terminal Orca does not list is dropped",
              db.get_agent_state(con, "backend")["terminal"] is None,
              str(db.get_agent_state(con, "backend")["terminal"]))
        check("sweep: confirmed gone ends closed, out of RUNNING",
              db.get_agent_state(con, "backend")["closed_at"] is not None
              and db.get_agent_state(con, "backend")["state"] == dmn_states.IDLE,
              str(dict(db.get_agent_state(con, "backend"))))
        check("sweep: and shows no wait",
              waiting.of(con, db.get_agent_state(con, "backend")) is None)
        check("sweep: the close is said, not silent - the founder reads it in the same line",
              gone_actions and "recorded as closed" in gone_actions[0], str(gone_actions))

        # 2. Its session died without us. Recording it is what makes work
        #    arriving wake it rather than be typed at a bash prompt. A fresh
        #    scenario, not a continuation of #1's - clear the close #1 left
        #    (with no status.json yet to read, it could not carry a resume id)
        #    so this one's own close is not skipped as already done.
        db.clear_closed(con, "backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha",
            "collected_at": int(time.time()) - status.STALE_AFTER_S - 60,
            "session_id": "sess-dead", "pct_5h": 20}))
        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: {}
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("sweep: a session with no process is recorded as closed",
              db.get_agent_state(con, "backend")["closed_at"] is not None)
        # A session that has just died leaves a snapshot that is stale by
        # construction, and its own id is the one thing here not in doubt.
        # Losing it loses the only pointer back to that agent's context.
        check("sweep: and keeps the id that could resume it",
              db.get_agent_state(con, "backend")["resume_session"] == "sess-dead",
              str(db.get_agent_state(con, "backend")["resume_session"]))
        check("sweep: so the daemon stops treating it as a candidate",
              not dmn_lifecycle.triggers_apply(db.get_agent_state(con, "backend")))

        # 3. And it came back without us - a human relaunched it. Believing our
        #    own note here would close a live session a second time.
        obsmod.live_sessions = lambda: {"backend": 4242}
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("sweep: a running session clears a stale closed mark",
              db.get_agent_state(con, "backend")["closed_at"] is None,
              str(dict(db.get_agent_state(con, "backend"))))

        # 4. And it is stuck in RUNNING. It cannot get out on its own -
        #    leaving RUNNING needs a Stop, a Stop needs a turn, and a turn needs
        #    work it will never be told about while it reads RUNNING.
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (int(time.time()) - dmn_states.STUCK_RUNNING_S - 60,))
        real_idle = orcamod.idle_now
        try:
            # One witness is not enough: the probe is the one we have twice
            # declined to trust on its own.
            orcamod.idle_now = lambda h: False
            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: a long turn is left alone even when our events are silent",
                  db.get_agent_state(con, "backend")["state"] == dmn_states.RUNNING)

            orcamod.idle_now = lambda h: True
            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: silent for too long and idle at its prompt is unstuck",
                  db.get_agent_state(con, "backend")["state"] == dmn_states.IDLE,
                  str(dict(db.get_agent_state(con, "backend"))))

            # A permission prompt or a dialog reads as idle to idle_now too -
            # nothing is streaming while it waits on a person - so idle_now
            # alone is not enough here either: a screen_blocker means it is
            # legitimately waiting, not stuck for no reason (T383).
            db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
            db.add_event(con, "backend", "Stop", {})
            con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                        (int(time.time()) - dmn_states.STUCK_RUNNING_S - 60,))
            orcamod.screen_blocker = lambda h: orcamod.Blocker(orcamod.MODAL, "Bash command")
            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: a screen_blocker (a live prompt) is not unstuck even when idle_now says yes",
                  db.get_agent_state(con, "backend")["state"] == dmn_states.RUNNING,
                  str(dict(db.get_agent_state(con, "backend"))))
            orcamod.screen_blocker = lambda h: None
            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: once the prompt clears, the same stuck agent is unstuck",
                  db.get_agent_state(con, "backend")["state"] == dmn_states.IDLE,
                  str(dict(db.get_agent_state(con, "backend"))))

            # And the other witness alone is not enough either.
            db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
            db.add_event(con, "backend", "UserPromptSubmit", {})
            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: an idle probe alone does not unstick a live agent",
                  db.get_agent_state(con, "backend")["state"] == dmn_states.RUNNING,
                  str(dict(db.get_agent_state(con, "backend"))))
            db.set_agent_state(con, "backend", dmn_states.IDLE, detail="done")

            # The probe is the only thing here that costs, and a "no" always
            # takes its full timeout - so an agent working a long turn is a
            # suspect on every tick for as long as the turn lasts. One per tick
            # bounds the tick however many are stuck at once.
            agents.register("stucktwo")
            probed: list[str] = []
            orcamod.idle_now = lambda h: (probed.append(h), True)[1]
            old_ts = int(time.time()) - dmn_states.STUCK_RUNNING_S - 60
            for name, handle in (("backend", "term_x"), ("stucktwo", "term_y")):
                db.set_terminal(con, name, handle, "wt")
                db.set_agent_state(con, name, dmn_states.RUNNING, detail="working")
                db.add_event(con, name, "Stop", {})
            con.execute("UPDATE events SET ts = ?", (old_ts,))
            orcamod.live_terminals = lambda: {"term_x", "term_y"}
            obsmod.live_sessions = lambda: {"backend": 1, "stucktwo": 2}

            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: only one suspect is probed per tick",
                  len(probed) == 1, str(probed))
            dmn_reconciler.sweep(con, lambda _m: None, [])
            check("sweep: and the next tick takes the other one",
                  set(probed) == {"term_x", "term_y"}, str(probed))
            check("sweep: so both are unstuck within a few seconds",
                  all(db.get_agent_state(con, n)["state"] == dmn_states.IDLE
                      for n in ("backend", "stucktwo")),
                  str([db.get_agent_state(con, n)["state"]
                       for n in ("backend", "stucktwo")]))
        finally:
            orcamod.idle_now = real_idle

        # None is not an empty set. Reading "I could not ask" as "there are
        # none" would tear the team down the first time Orca answered slowly.
        orcamod.live_terminals = lambda: None
        obsmod.live_sessions = lambda: {}
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("sweep: an unanswered Orca changes nothing",
              db.get_agent_state(con, "backend")["terminal"] == "term_x"
              and db.get_agent_state(con, "backend")["closed_at"] is None)

        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: None
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("sweep: an unreadable process table changes nothing",
              db.get_agent_state(con, "backend")["closed_at"] is None)
    finally:
        orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real
        con.close()

    # The distinction has to survive into the implementation, not only the fake.
    restore_world()
    real_run = orcamod._run
    try:
        orcamod._run = lambda *a, **k: (False, "connection refused")
        check("sweep: Orca failing to answer reads as unknown, not as empty",
              orcamod.live_terminals() is None, str(orcamod.live_terminals()))
        orcamod._run = lambda *a, **k: (True, "not json at all")
        check("sweep: and so does an answer we cannot parse",
              orcamod.live_terminals() is None)
        orcamod._run = lambda *a, **k: (True, json.dumps(
            {"result": {"terminals": [{"handle": "term_a"}, {"nohandle": 1}]}}))
        check("sweep: a real answer is the set of handles",
              orcamod.live_terminals() == {"term_a"}, str(orcamod.live_terminals()))
    finally:
        orcamod._run = real_run


def test_sweep_does_not_close_a_live_session_on_an_unmappable_pid(home: Path) -> None:
    """T146: a pid `observe.live_sessions` cannot map back to its agent reads
    as the whole reading being unreadable (its own fix) - the sweep's
    existing "unread answer" guard is what must then leave a live session
    alone, not a second look at this one agent."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="idle")
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}

        obsmod.live_sessions = lambda: None
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("sweep: an unmappable pid leaves a session alone, not closed",
              db.get_agent_state(con, "backend")["closed_at"] is None,
              str(dict(db.get_agent_state(con, "backend"))))

        # Genuinely gone this time: a decisive reading closes it.
        obsmod.live_sessions = lambda: {}
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("sweep: a decisive gone reading still closes it",
              db.get_agent_state(con, "backend")["closed_at"] is not None,
              str(dict(db.get_agent_state(con, "backend"))))
    finally:
        orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real
        con.close()


def test_an_agent_with_nowhere_to_run_is_not_handed_off(home: Path) -> None:
    """usage_unknown fires across a whole account at once, so it sweeps up the
    agents that are registered but were never started. There is nothing to fold,
    nothing to clear and nothing being spent - backend and manager were parked
    that way on 09-17 and manager's fold was charged for it."""
    _daemon_home(home, 100)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", None, None)
        row = db.get_agent_state(con, "backend")
        check("no terminal: the trigger itself still fires",
              triggers.evaluate(100, 5).fired)
        check("no terminal: but the agent is not a candidate",
              not dmn_lifecycle.triggers_apply(row), str(dict(row)))

        dmn_evaluate.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("no terminal: so it is not parked", row["state"] != dmn_states.PARKED, str(dict(row)))
        check("no terminal: and no fold was paid for",
              not db.in_flight_handoffs(con) and row["continuations"] == 0, str(dict(row)))

        # And with somewhere to run, the same trigger does its job.
        db.set_terminal(con, "backend", "term_x", "wt")
        dmn_evaluate.tick(con, lambda _m: None)
        check("no terminal: an agent that has one is still parked",
              db.get_agent_state(con, "backend")["state"] == dmn_states.PARKED,
              str(dict(db.get_agent_state(con, "backend"))))
    finally:
        con.close()


def test_a_wake_is_believed_only_when_a_session_appears(home: Path) -> None:
    """A launch that started nothing is not a wake: the closed mark and
    `resume_session` are the only way back to the agent's context, and nothing
    may be typed at a shell that refused the launch.
    """
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
            orcamod.resume_with, dmn_delivery.WAKE_SESSION_TIMEOUT_S)
    try:
        dmn_delivery.WAKE_SESSION_TIMEOUT_S = 1
        orca_seen()
        waits: list[int] = []
        orcamod.wait_idle = lambda h, timeout_ms=0: (waits.append(timeout_ms), True)[1]
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "handoff injected"))[1]
        table = sessions_are()          # the launch types, and nothing comes up
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-8")
        support.something_waiting(con, "backend")

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a launch that started nothing is not a wake", not ok, why)
        check("wake: so the closed mark stays", row["closed_at"] is not None, str(dict(row)))
        check("wake: and the session to resume is still there to try again",
              row["resume_session"] == "sess-8", str(dict(row)))
        check("wake: and it does not wait out a TUI that never started",
              not waits, str(waits))
        check("wake: it tries the resume, then a fresh start, and stops there",
              [" launch " in t for t in sent] == [True, True]
              and "--resume" in sent[0] and "--resume" not in sent[1], str(sent))

        # A resume id that cannot be launched - a pruned transcript, a worktree
        # moved out from under it - would otherwise be retried at every tick
        # forever, since it is now kept rather than cleared. A fresh start in
        # the same terminal loses the context and keeps the agent.
        sent.clear()
        db.set_handoff(con, "backend", "# handoff · backend\nwaiting")

        def send_launching(h, text, enter=True):
            sent.append(text)
            if " launch " in text and "--resume" not in text:
                take_launched_prompt(text)
                table.update({"backend": 4242})
            return True, ""
        orcamod.send = send_launching
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a resume that starts nothing falls back to a fresh start", ok, why)
        check("wake: and the agent is told there is no handoff behind it",
              any("could not be resumed" in launched_prompt(t)
                  for t in sent if "--prompt-file" in t), str(sent[-1:])[:120])
        check("wake: the record is cleared once the session has started, not by the launch",
              db.get_handoff(con, "backend") is None)
        check("wake: and its handoff is in the system prompt",
              any("system prompt above" in launched_prompt(t)
                  for t in sent if "--prompt-file" in t), str(sent[-1:])[:200])
        check("wake: the closed mark is cleared once something is running",
              row["closed_at"] is None, str(dict(row)))

        # Same answer when the process table cannot be read at all: an
        # unreadable table is not evidence that the launch worked, and what is
        # at stake here is the resume pointer.
        sent.clear()
        db.set_closed(con, "backend", "sess-8")
        support.something_waiting(con, "backend")
        table.clear()
        obsmod.live_sessions = lambda: None
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: an unreadable process table is not a session either",
              not ok and row["resume_session"] == "sess-8", f"{why} {dict(row)}")

        # Work arriving retries the wake every tick, so a launch that will
        # never work is a loop with no end and no report. 8.6 already has the
        # answer for that shape: stop, and leave it for a human.
        db.set_closed(con, "backend", "sess-8")
        support.something_waiting(con, "backend")
        db.reset_counter(con, "backend", "wake_failures")
        table.clear()
        obsmod.live_sessions = lambda: dict(table)
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a launch that never works ends in STALLED, not in a loop",
              row["state"] == dmn_states.STALLED, str(dict(row)))
        check("wake: and it still holds the session it would have resumed",
              row["resume_session"] == "sess-8" and row["closed_at"] is not None,
              str(dict(row)))
        # Its own count: the tick's exception handler and the handoff both stall
        # on failed_attempts, and a wake streak is not theirs to read.
        check("wake: failed wakes are counted apart from failed ticks",
              row["failed_attempts"] == 0 and row["wake_failures"] >= dmn_states.MAX_FAILED_ATTEMPTS,
              str(dict(row)))

        # A human starts it again in its terminal. The sweep sees the session
        # the stall said could not exist, and a stall the world has refuted is
        # ours to lift - leaving it would hide a live agent from every delivery.
        table["backend"] = 4242
        orcamod.live_terminals = lambda: {"term_x"}
        dmn_reconciler.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("wake: a stall the world refutes is lifted by the sweep",
              row["state"] == dmn_states.IDLE and row["closed_at"] is None, str(dict(row)))
        table.clear()

        # `ocp unstall` is the only other way out of STALLED, and it has to
        # clear this count as well: left at the cap, the sweep would later read
        # someone else's stall as one of its own and lift it.
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="stalled by wakes")
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        con.close()
        rc = climod.main(["unstall", "backend"])
        con = db.connect()
        row = db.get_agent_state(con, "backend")
        check("wake: unstall clears the wake streak, not only the other two",
              rc == 0 and row["wake_failures"] == 0 and row["state"] == dmn_states.QUEUED,
              str(dict(row)))

        # The 5h wall is the one failure that heals itself, and `ocp launch`
        # refuses on a reading the daemon has already got. Spending the wake
        # timeout to be told that, and counting it against the agent, is how a
        # window that rolls over in minutes took an agent out for hours.
        db.set_closed(con, "backend", "sess-8")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="closed and at the wall")
        db.reset_counter(con, "backend", "wake_failures")
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 600)
        sent.clear()
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: an account at the wall is not launched into", not ok and not sent,
              f"{why} {sent}")
        check("wake: and waiting for the window is not a failed attempt",
              row["wake_failures"] == 0 and row["state"] != dmn_states.STALLED, str(dict(row)))

        # And once it rolls over, the same call goes through.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        orcamod.send = relaunching_send(sent, table)
        ok, _ = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: and it goes through once the window rolls over", ok, str(sent))
        table.clear()   # the session that wake started is closed again below
        db.set_closed(con, "backend", "sess-8")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="closed again for the next case")

        # The same call, once the launch works.
        sent.clear()
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="cleared by hand")
        obsmod.live_sessions = lambda: dict(table)
        orcamod.send = relaunching_send(sent, table)
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a launch that starts a session is a wake", ok, why)
        check("wake: the closed mark is cleared only then",
              row["closed_at"] is None, str(dict(row)))
        check("wake: and the agent is handed what is waiting for it, not told it is back",
              any("something for you" in t for t in sent)
              and not any("Nothing was lost" in t for t in sent), str(sent))
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
         orcamod.resume_with, dmn_delivery.WAKE_SESSION_TIMEOUT_S) = real
        con.close()


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
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
            dmn_delivery.WAKE_SESSION_TIMEOUT_S)
    try:
        dmn_delivery.WAKE_SESSION_TIMEOUT_S = 1
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        table = sessions_are()

        def refused_while_someone_else_launches(h, text, enter=True):
            sent.append(text)
            if " launch " in text:
                table["backend"] = 4300     # the hand-typed launch, no prompt file
            return True, ""
        orcamod.send = refused_while_someone_else_launches
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
        realr = orcamod.relaunch

        def own_but_late(h, cmd, **kw):
            sent.append(cmd)
            take_launched_prompt(cmd)
            table["backend"] = 4301
            return False, "no session seen within the timeout"
        orcamod.relaunch = own_but_late
        try:
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        finally:
            orcamod.relaunch = realr
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
        realr = orcamod.relaunch

        def refused_once(h, cmd, **kw):
            sent.append(cmd)
            return False, "refused"
        orcamod.relaunch = refused_once
        try:
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        finally:
            orcamod.relaunch = realr
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
        realr = orcamod.relaunch

        def took_but_no_session_seen(h, cmd, **kw):
            sent.append(cmd)
            take_launched_prompt(cmd)
            return False, "no session seen within the timeout"
        orcamod.relaunch = took_but_no_session_seen
        try:
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        finally:
            orcamod.relaunch = realr
        row = db.get_agent_state(con, "backend")
        check("wake: a session that turns out to be its own attempt's is not called foreign",
              not ok and why == "no session seen within the timeout", f"{why} {sent}")
        check("wake: so it counts toward STALLED, like any other failed attempt",
              row["wake_failures"] == 1, str(dict(row)))
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
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
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
            obsmod.live_sessions)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
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


def test_a_walled_wake_says_so_everywhere(home: Path) -> None:
    """A closed agent whose resume is blocked at its account's 5h wall used to
    say nothing about it: `ocp ps` showed "·closed", `ocp triggers` said
    "nothing to hand off", and the daemon's own refusal lived in a log line.
    One predicate now answers all of them, so a screen cannot drift from what
    the daemon actually acts on - and it has to fall silent again the moment
    the account has room, since nothing here is stored state.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available,)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: None    # the bridge's stuck panel asks; this is about the wall
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
        check("walled: the daemon's own refusal is the same sentence, not a cousin of it",
              not ok and why == reason, f"{why!r} != {reason!r}")

        ps_out = _capture(lambda: climod.main(["ps"]))
        trig_out = _capture(lambda: climod.main(["triggers"]))
        # `ocp team` is asked from somewhere - `reviewer` is a peer of every
        # agent in DESIGN_TEAM, so backend is on its list.
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        check("walled: `ocp ps` says it", reason in ps_out, ps_out)
        check("walled: `ocp triggers` says it", reason in trig_out, trig_out)
        check("walled: `ocp team` says it too - the fourth surface",
              "alpha" in team_out and when in team_out, team_out)
        # It is computed, not stored: once the account has room the sentence
        # disappears from every screen on its own, with nothing to clear.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("walled: and clears the moment the account has room",
              launch.wake_wait_reason("backend", row, con) is None)
        ps_out = _capture(lambda: climod.main(["ps"]))
        trig_out = _capture(lambda: climod.main(["triggers"]))
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        wide = tui._ANSI.sub("", _snap(200, 40))
        check("walled: and every screen falls silent with it",
              "rolls over" not in ps_out and "rolls over" not in trig_out
              and when not in team_out and when not in wide,
              "still walled somewhere after the window rolled over")
    finally:
        (orcamod.available,) = real
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
        trig_out = _capture(lambda: climod.main(["triggers"]))
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        frame = tui._ANSI.sub("", _snap(200, 40))
        check("walled: `ocp ps` stays quiet with nothing waiting",
              "rolls over" not in ps_out, ps_out)
        check("walled: `ocp triggers` stays quiet too",
              "rolls over" not in trig_out, trig_out)
        check("walled: `ocp team` stays quiet too", "rolls over" not in team_out, team_out)
        check("walled: and the bridge",
              "rolls over" not in frame and "⏳" not in frame,
              str([ln for ln in frame.splitlines() if "backend" in ln]))

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
    real = (orcamod.available, orcamod.relaunch, orcamod.resume_with, dmn_delivery.wait_for_session,
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
        orcamod.available = lambda: True
        orcamod.relaunch = lambda h, cmd, **kw: (launched.append(cmd), (True, "relaunched"))[1]
        orcamod.resume_with = lambda h, text: (True, "handed back")
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
        (orcamod.available, orcamod.relaunch, orcamod.resume_with, dmn_delivery.wait_for_session,
        obsmod.live_sessions) = real
        con.close()


def test_a_park_that_cannot_be_woken_keeps_its_deadline(home: Path) -> None:
    """The deadline is the only thing that makes a parked agent due again, and
    supervise cleared it before knowing whether the wake worked. A closed park
    whose launch is refused - the account still at the wall when the window
    rolls - then had no deadline, was not due for reconcile either, and waited
    closed until some work happened to arrive."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.send)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True     # the relaunch waits for the shell
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are()
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-2")
        support.something_waiting(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", "task", 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 600)

        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: a wake the account refuses is not typed", not sent, str(sent))
        check("park: and the deadline stays, so the next tick tries again",
              row["state"] == dmn_states.PARKED and row["wake_at"] is not None, str(dict(row)))

        # The window rolls over and the same tick does what it could not.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        table = sessions_are()
        orcamod.send = relaunching_send(sent, table)
        dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: once it rolls over the park is woken and the deadline cleared",
              row["state"] == dmn_states.QUEUED and row["wake_at"] is None
              and any("--resume sess-2" in t for t in sent), str(dict(row)))

        # An agent that answers the prompt before the tick gets back to its row
        # is RUNNING, and RUNNING is the label that keeps the daemon out of a
        # working agent's way. The wake must not write QUEUED over it.
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed again")
        db.set_closed(con, "backend", "sess-2")
        support.something_waiting(con, "backend")
        hooked = db.connect()
        table = sessions_are()
        launching = relaunching_send(sent, table)
        orcamod.send = lambda h, text, enter=True: (
            evmod._mark_activity(hooked, "backend", "UserPromptSubmit")
            if " launch " in text else None, launching(h, text, enter))[1]
        try:
            dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        finally:
            hooked.close()
        row = db.get_agent_state(con, "backend")
        check("park: an agent that has already answered its prompt stays RUNNING",
              row["state"] == dmn_states.RUNNING, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive, orcamod.send = real
        con.close()


def test_a_parked_agent_is_unparked_by_its_own_context_limit(home: Path) -> None:
    """auditor #1813 should-fix 2: the PARKED branch's own triggers.evaluate
    call (ocp/daemon/'s un-park check, above the main evaluate a few lines
    down) shares only the ctx_limit *variable* with that other call, not the
    keyword argument itself - each call can drop its own ctx_limit=
    independently, and the earlier call-site test (built to reach the main
    evaluate) never parks an agent, so it never reaches this branch. Two
    agents parked for 'usage', both recovered on their 5h reading (5%) but
    both sitting on 200,000 context tokens - only a context_tokens override
    tells them apart, and only this branch's own ctx_limit= could catch a
    regression here."""
    _daemon_home(home, 20)
    agents.register("parkreal")
    agents.register("parkctl")
    agent_file = agents.agent_file("parkctl")
    agent_file.write_text(agent_file.read_text() + "context_tokens: 10000000\n")
    reseal(agent_file)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        write_status(home, "alpha", pct_5h=5, pct_7d=10, reset_5h=int(time.time()) + 1800)
        for name in ("parkreal", "parkctl"):
            db.set_terminal(con, name, f"term_{name}", "wt")
            db.set_account(con, name, "alpha")
            _park_via_handoff(con, name, "usage")
            db.set_agent_state(con, name, dmn_states.PARKED,
                               wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
            write_agent_status(name, "alpha", ctx_used=200_000, model_id="claude-sonnet-5")
            db.add_request(con, "marina", name, "carry on", None, "peer", "async", None, 0, None)
        dmn_evaluate.supervise(con, "parkreal", lambda _m: None, int(time.time()), {}, [])
        dmn_evaluate.supervise(con, "parkctl", lambda _m: None, int(time.time()), {}, [])
        real_row = db.get_agent_state(con, "parkreal")
        ctl_row = db.get_agent_state(con, "parkctl")
        check("park/ctx: the default-limit agent stays parked - its context, "
              "not its usage, is now what keeps the PARKED branch's own "
              "verdict fired", real_row["state"] == dmn_states.PARKED, dict(real_row))
        check("park/ctx: the overridden agent un-parks on the same usage "
              "reading, since its own PARKED-branch call site honoured the "
              "per-agent limit too", ctl_row["state"] != dmn_states.PARKED, dict(ctl_row))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


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
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
            dmn_delivery.session_present)
    try:
        db.set_terminal(con, "keeper", "term_keeper", "wt")
        db.set_terminal(con, "keeper_ctl", "term_ctl", "wt")
        db.set_account(con, "keeper", "alpha")
        db.set_account(con, "keeper_ctl", "alpha")
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h, **kw: True

        def clear(h, **kw):
            cleared[h] += 1
            return True, "cleared"
        orcamod.clear_context = clear
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
                    (int(time.time()) - dmn_states.MIN_HANDOFF_INTERVAL_S - 1,))
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
        (orcamod.available, orcamod.alive, orcamod.idle_now,
         orcamod.clear_context, dmn_delivery.session_present) = real
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
    real = (orcamod.available, orcamod.alive, orcamod.typeable_now, orcamod.send,
            orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.send = lambda h, text, enter=True: (typed.append(text), (True, "sent"))[1]
        orcamod.clear_context = lambda h, **kw: (typed.append("/clear"), (True, "context cleared"))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)

        calls = [True, False]
        orcamod.typeable_now = lambda h: calls.pop(0) if calls else False
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
        (orcamod.available, orcamod.alive, orcamod.typeable_now, orcamod.send,
         orcamod.clear_context) = real
        con.close()


def test_every_call_site_actually_hands_supervise_the_per_agent_context_limit(
        home: Path) -> None:
    """should-fix 1 (#1762): the thrash test above proves triggers.evaluate
    honours a ctx_limit it is handed - its control builds that trigger by
    calling evaluate() directly, so supervise() is never actually reached for
    the overridden agent. Nothing there proves the daemon, the bridge row or
    `ocp triggers` resolve and pass a per-agent limit rather than the flat
    default. Two agents, same account, same 200,000-token reading: only each
    call site's own status.effective_context_limit(agents.read(agent), ...)
    can tell them apart, so all three are driven through their real code
    path, not a stand-in for it."""
    _daemon_home(home, 20)
    agents.register("ctxreal")
    agents.register("ctxctl")
    agent_file = agents.agent_file("ctxctl")
    agent_file.write_text(agent_file.read_text() + "context_tokens: 10000000\n")
    reseal(agent_file)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
            dmn_delivery.session_present)
    try:
        db.set_terminal(con, "ctxreal", "term_real", "wt")
        db.set_terminal(con, "ctxctl", "term_ctl", "wt")
        db.set_account(con, "ctxreal", "alpha")
        db.set_account(con, "ctxctl", "alpha")
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h, **kw: True
        cleared = {"term_real": 0, "term_ctl": 0}

        def clear(h, **kw):
            cleared[h] += 1
            return True, "cleared"
        orcamod.clear_context = clear
        dmn_delivery.session_present = lambda a: True
        write_status(home, "alpha", pct_5h=10, pct_7d=10, reset_5h=int(time.time()) + 1800)
        for name in ("ctxreal", "ctxctl"):
            write_agent_status(name, "alpha", ctx_used=200_000, model_id="claude-sonnet-5")

        # 1. daemon.supervise - the call site both #1762 evaluate() calls share.
        dmn_evaluate.supervise(con, "ctxreal", lambda _m: None, int(time.time()), {}, [])
        dmn_evaluate.supervise(con, "ctxctl", lambda _m: None, int(time.time()), {}, [])
        check("supervise: the default-limit agent folds on 200,000 tokens",
              cleared["term_real"] == 1, cleared)
        check("supervise: the overridden agent, same reading, does not - "
              "the daemon's own call site honoured agent.yaml, not just evaluate()",
              cleared["term_ctl"] == 0, cleared)

        # The fold parked it, and a parked row is judged no further - so the
        # screens are read as of the next session, still on the same reading.
        for name in ("ctxreal", "ctxctl"):
            db.set_agent_state(con, name, dmn_states.IDLE)

        # 2. cli.health.cmd_triggers.
        trig_out = _capture(lambda: climod.main(["triggers"]))
        real_line = next(ln for ln in trig_out.splitlines() if "ctxreal" in ln)
        ctl_line = next(ln for ln in trig_out.splitlines() if "ctxctl" in ln)
        check("ocp triggers: fires for the default-limit agent",
              "YES" in real_line, real_line)
        check("ocp triggers: does not fire for the overridden agent, same reading",
              "YES" not in ctl_line, ctl_line)

        # 3. tui's bridge row.
        wide = tui._ANSI.sub("", _snap(200, 40))
        real_row = next(ln for ln in wide.splitlines() if "ctxreal" in ln)
        ctl_row = next(ln for ln in wide.splitlines() if "ctxctl" in ln)
        check("bridge: shows the fired context warning for the default-limit agent",
              "context" in real_row, real_row)
        check("bridge: does not for the overridden agent, same reading",
              "context" not in ctl_row, ctl_row)
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now,
         orcamod.clear_context, dmn_delivery.session_present) = real
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
    through `ocp`, with no step that needs a human or a subagent.

    Each leg was there and none of them finished: `ocp ask` queued and left the
    rest to a tick, and a target with no terminal was a request for somebody to
    press a key. An agent that cannot finish the job it was given goes looking
    for one it can - which is a subagent, dead at the end of its session."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    typed: list[tuple[str, str]] = []
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.send,
            spawnmod.spawn, dmn_delivery.wait_for_session)
    try:
        orca_idle()
        orcamod.send = lambda h, text, enter=True: (typed.append((h, text)), (True, ""))[1]
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
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.send,
         spawnmod.spawn, dmn_delivery.wait_for_session) = real
        con.close()


def test_an_agent_can_put_another_one_to_work(home: Path) -> None:
    """An agent with nowhere to run can be given work by nobody but a human.

    That is what sends an agent looking for a subagent instead - a thing it can
    make itself, which dies with its session, holds no account, has no inbox
    and appears on no screen. The daemon can make a worktree and a terminal;
    the only fact it was missing is which repository, and that is `repo:`."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    started: list = []
    real = (orcamod.available, spawnmod.spawn, dmn_delivery.wait_for_session)
    real_wake = dmn_lifecycle.wake_agent
    try:
        orcamod.available = lambda: True
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        def fake_spawn(agent, repo, base="main"):
            started.append((agent, str(repo)))
            db.set_terminal(db.connect(), agent, "term_new", str(repo))
            return True, f"{agent}: worktree {repo}"

        spawnmod.spawn = fake_spawn
        agents.register("marina")
        db.set_terminal(con, "marina", None, None)
        db.add_request(con, "manager", "marina", "implement the smooth sea", None,
                       "supervisor", "fire_and_forget", None, 0, None)

        # Without a repo nothing is invented: it says what is missing and to whom.
        said = dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        check("dispatch: with no repo it says what is missing, and does not guess",
              not started and "repo:" in said, said)

        agents.register("marina", repo=str(home / "checkout"))
        said = dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        check("dispatch: with one, the daemon gives it somewhere to run",
              started == [("marina", str((home / "checkout").resolve()))],
              f"{started} {said}")
        check("dispatch: and the terminal is recorded, so work can be delivered",
              db.get_agent_state(con, "marina")["terminal"] == "term_new")

        # A start that never takes is a streak, not a loop: three failures and
        # it is a human's problem, said out loud.
        db.set_terminal(con, "marina", None, None)
        spawnmod.spawn = lambda agent, repo, base="main": (False, "orca refused")
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        said = dmn_lifecycle._start_for_work(con, "marina", lambda _m: None)
        check("dispatch: a start that keeps failing is not retried forever",
              "attempts have failed" in said, said)

        # An answer is work too: a wake puts a session back into a terminal the
        # agent still has, so one that lost its terminal while waiting on an
        # answer has no way back at all. reviewer sat that way for a day.
        spawnmod.spawn = fake_spawn
        woken: list = []
        dmn_lifecycle.wake_agent = lambda con_, a, log_, **kw: (woken.append(a), (False, "no terminal to wake"))[1]
        started.clear()
        agents.register("reviewer", repo=str(home / "checkout"))
        db.set_terminal(con, "reviewer", None, None)
        db.reset_counter(con, "reviewer", "wake_failures")
        rid = db.add_request(con, "reviewer", "marina", "look at this branch", None,
                             "peer", "async", None, 0, None)
        askmod.resolve(rid, "looks right")
        db.set_closed(con, "reviewer", "sess_old")
        logged: list[str] = []
        dmn_evaluate.supervise(con, "reviewer", lambda m: logged.append(m),
                      int(time.time()), {}, [])
        check("dispatch: an agent with an answer and no terminal is given one",
              ("reviewer", str((home / "checkout").resolve())) in started,
              f"{started} {logged}")
        # The terminal it was just given holds a session that is still booting.
        # Typing a wake into it loses the opening characters and leaves the
        # rest in the input box, unsent.
        check("dispatch: and the tick does not then wake the terminal it just made",
              not woken, str(woken))

        # When there is nothing to wake and nothing can be started, the tick
        # says so - once. It repeated in silence for a day, and two of the
        # reasons it can give last for hours.
        db.set_terminal(con, "reviewer", None, None)
        spawnmod.spawn = lambda agent, repo, base="main": (False, "orca refused")
        logged.clear()
        for _ in range(3):
            dmn_evaluate.supervise(con, "reviewer", lambda m: logged.append(m),
                          int(time.time()), {}, [])
        said = [m for m in logged if "could not be started" in m]
        check("dispatch: a start that cannot happen is said out loud",
              len(said) == 1 and "orca refused" in said[0], str(logged))
        check("dispatch: and not once per tick for as long as it lasts",
              len([m for m in logged if "could not be started" in m]) == 1, str(logged))
    finally:
        (orcamod.available, spawnmod.spawn, dmn_delivery.wait_for_session) = real
        dmn_lifecycle.wake_agent = real_wake
        dmn_evaluate._SAID.clear()
        con.close()


def test_a_due_park_with_no_terminal_is_started_not_held(home: Path) -> None:
    """T406: a reboot leaves a parked, closed agent with its deadline due and no
    terminal. The wake needs a terminal, failed, and kept the park "still due"
    before the start for work that gives it one - so the row read "wakes at
    HH:MM", PENDING due, on every tick for good."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    started: list = []
    real = (orcamod.available, spawnmod.spawn, dmn_delivery.wait_for_session)
    try:
        orcamod.available = lambda: True
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        def fake_spawn(agent, repo, base="main"):
            started.append(agent)
            db.set_terminal(db.connect(), agent, "term_new", str(repo))
            return True, f"{agent}: worktree {repo}"

        spawnmod.spawn = fake_spawn
        agents.register("marina", repo=str(home / "checkout"))
        db.set_terminal(con, "marina", None, None)
        db.set_closed(con, "marina", "sess_old")
        support.something_waiting(con, "marina")
        db.set_agent_state(con, "marina", dmn_states.PARKED,
                           wake_at=int(time.time()) - 10, detail="usage; wakes at 01:59")

        dmn_reconciler.reconcile(con, lambda _m: None)
        row = db.get_agent_state(con, "marina")
        check("reconcile: a due park with no terminal is spent, not left due",
              row["wake_at"] is None and row["state"] != dmn_states.PARKED, str(dict(row)))

        db.set_agent_state(con, "marina", dmn_states.PARKED,
                           wake_at=int(time.time()) - 10, detail="usage; wakes at 01:59")
        dmn_evaluate.supervise(con, "marina", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "marina")
        check("tick: the same park is spent, and the agent is given a terminal for its work",
              started == ["marina"] and row["wake_at"] is None and row["terminal"] == "term_new",
              f"{started} {dict(row)}")

        # The other way in: only a pending message (the socket's `message` verb) is waiting.
        started.clear()
        db.set_terminal(con, "marina", None, None)
        db.set_closed(con, "marina", "sess_old")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        con.execute("UPDATE requests SET state = 'done'")
        db.add_pending(con, "marina", "hello", int(time.time()))
        dmn_evaluate.supervise(con, "marina", lambda _m: None, int(time.time()), {}, [])
        check("tick: a closed agent with no terminal and only a pending message is started for it",
              started == ["marina"], str(started))
    finally:
        (orcamod.available, spawnmod.spawn, dmn_delivery.wait_for_session) = real
        con.close()


def test_the_screens_say_a_closed_agent_is_woken_only_when_the_tick_wakes_it(home: Path) -> None:
    """T406: "the next tick wakes it" has to be true wherever it is said. The tick
    wakes a closed agent for what `closed_wake_cause` names, and the stuck screen
    reads the same list: an undelivered request its task is blocked on record for
    was said to wake it and never did, and one told before the close (T154) is
    held, which the screen now says instead."""
    import importlib
    stuck_screen = importlib.import_module("ocp.actions.stuck")
    _daemon_home(home, 20)
    con = db.connect()
    woken: list = []
    real_wake = dmn_lifecycle.wake_agent
    try:
        dmn_lifecycle.wake_agent = lambda con_, a, log_, **kw: (woken.append(a), (True, "woke"))[1]
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        db.set_closed(con, "backend", "sess-1")
        now = int(time.time())

        def shown() -> tuple:
            return stuck_screen._delivery_state(con, "backend", None, None, True)

        row = db.get_agent_state(con, "backend")
        check("closed: with nothing waiting the tick has no cause, and the screen does not promise one",
              dmn_lifecycle.closed_wake_cause(con, "backend", row) is None
              and shown()[0] != "closed, waking", str(shown()))

        ok, msg, tid = actions.task_new("product", "backend", "the blocked thing", "the blocked thing")
        check("setup: a task for it", ok, msg)
        ok, msg, blocker = actions.task_new("product", "reviewer", "unrelated", "unrelated")
        ok, msg = actions.add_block(con, tid, blocker, "product")
        check("setup: its task is blocked on record", ok, msg)
        con.execute("UPDATE requests SET delivered_at = NULL WHERE target = 'backend'")
        woken.clear()          # `task_new` wakes a closed agent itself, as any message does
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("closed: an undelivered request its task is blocked for is no cause, and no wake is tried",
              dmn_lifecycle.closed_wake_cause(con, "backend", row) is None and not woken
              and shown()[0] != "closed, waking", f"{woken} {shown()}")

        rid = db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
        cause = dmn_lifecycle.closed_wake_cause(con, "backend", row)
        check("closed: a request it has not been shown is a cause, and the screen says so",
              cause and cause[0] == "work" and shown()[0] == "closed, waking", f"{cause} {shown()}")
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("closed: and the tick wakes it", woken == ["backend"], str(woken))

        db.mark_delivered(con, rid)
        con.execute("UPDATE requests SET delivered_at = ? WHERE target = 'backend'", (now - 3600,))
        woken.clear()
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("closed: a request told before the close wakes nothing, and the screen says it is held",
              not woken and dmn_lifecycle.closed_wake_cause(con, "backend", row) is None
              and shown()[0] == "closed, holding" and not shown()[2], f"{woken} {shown()}")
    finally:
        dmn_lifecycle.wake_agent = real_wake
        dmn_evaluate._SAID.clear()
        con.close()


def test_the_daemon_starts_every_agent_with_waiting_work_before_its_first_tick(home: Path) -> None:
    """T408: after a reboot every agent with work waiting has no session, and the
    tick brings them up behind its usage polls, one after another. At start the
    daemon brings them up first: no terminal -> started in its worktree, closed
    with something to be told -> woken, nothing waiting -> left alone."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    order: list = []
    real = (orcamod.available, orcamod.live_terminals, spawnmod.spawn, dmn_delivery.wait_for_session,
            dmn_lifecycle.wake_agent, dmn_evaluate.tick)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        def fake_spawn(agent, repo, base="main"):
            order.append(("start", agent))
            db.set_terminal(db.connect(), agent, f"term_{agent}", str(repo))
            return True, f"{agent}: worktree {repo}"

        spawnmod.spawn = fake_spawn
        dmn_lifecycle.wake_agent = lambda con_, a, log_, **kw: (order.append(("wake", a)), (True, "woke"))[1]
        agents.register("marina", repo=str(home / "checkout"))
        agents.register("idler", repo=str(home / "checkout2"))
        for a in ("marina", "idler"):
            db.set_terminal(con, a, None, None)
        db.add_request(con, "manager", "marina", "implement the sea", None,
                       "supervisor", "fire_and_forget", None, 0, None)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-1")
        support.something_waiting(con, "backend")

        # What `supervise` leaves alone is left alone at start too - one decision, `start_or_wake_needed`.
        for a in ("stalled", "handed", "finished", "aboom"):
            agents.register(a, repo=str(home / "checkout3"))
            db.set_terminal(con, a, None, None)
            db.add_request(con, "manager", a, "work", None, "supervisor", "fire_and_forget", None, 0, None)
        db.set_agent_state(con, "stalled", dmn_states.STALLED, detail="fatal")
        db.start_handoff(con, "hid-t408", "handed", "sess", "usage", int(time.time()) + 90)
        con.execute("UPDATE agent_state SET state = 'DONE' WHERE agent = 'finished'")
        real_spawn = spawnmod.spawn

        def boom_spawn(agent, repo, base="main"):
            if agent == "aboom":
                raise RuntimeError("orca fell over")
            return real_spawn(agent, repo, base)

        spawnmod.spawn = boom_spawn
        logged: list[str] = []
        acts = dmn_evaluate.start_waiting(con, logged.append)
        spawnmod.spawn = real_spawn
        check("start: a stalled agent, one mid-handoff and one the tick does not evaluate are not started",
              not {"stalled", "handed", "finished"} & {a for k, a in order}, str(order))
        check("start: one agent's exception is logged and does not stop the rest",
              any("aboom" in m and "orca fell over" in m for m in logged) and ("start", "marina") in order,
              f"{logged} {order}")
        order.clear()
        for a in ("stalled", "handed", "finished", "aboom"):
            con.execute("UPDATE requests SET state = 'done' WHERE target = ?", (a,))
        db.set_terminal(con, "marina", None, None)
        acts = dmn_evaluate.start_waiting(con, lambda _m: None)
        check("start: an agent with work and no terminal is started, a closed one with work woken",
              ("start", "marina") in order and ("wake", "backend") in order, f"{order} {acts}")
        check("start: an agent with nothing waiting is left alone",
              ("start", "idler") not in order and ("wake", "idler") not in order, str(order))

        order.clear()
        dmn_evaluate.tick = lambda con_, log_, samples=None: (order.append("tick"), dmn_evaluate.Tick(0, []))[1]
        con.close()
        db.set_terminal(db.connect(), "marina", None, None)
        dmn_loop.run(once=True, verbose=False)
        check("start: and the loop does it before the first tick",
              ("start", "marina") in order and order.index(("start", "marina")) < order.index("tick"),
              str(order))
    finally:
        (orcamod.available, orcamod.live_terminals, spawnmod.spawn, dmn_delivery.wait_for_session,
         dmn_lifecycle.wake_agent, dmn_evaluate.tick) = real
        dmn_evaluate._SAID.clear()


def test_a_clear_held_by_background_work_is_owed_not_failed(home: Path) -> None:
    """T415 (founder #2563): five context handoffs landed their record and skipped the
    /clear for "background work is running", each counted a degraded handoff, and the
    auditor went STALLED with its context still climbing. Now the clear is owed: with the
    cache lapsed it is typed at once; otherwise the agent is told, and it is typed when the
    work ends or CLEAR_WAIT_S is up. None of it is a failure, and nothing else is typed at
    the agent meanwhile."""
    _daemon_home(home, 50)
    con = db.connect()
    cleared, sent = [], []
    bg = ["1 shell"]
    real = (orcamod.clear_context, orcamod.alive, orcamod.idle_now, orcamod.available,
            orcamod.send, orcamod.background_work, dmn_handoff._record_handoff)
    t = triggers.Trigger(True, "context", "context 206,000 >= 200,000")

    def fresh() -> None:
        for k in ("clear_owed:backend", "typed_at:backend"):
            db.clear_cursor(con, k)
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.set_handoff(con, "backend", None)
        con.execute("UPDATE agent_state SET throttle_count = 0 WHERE agent = 'backend'")
        con.execute("DELETE FROM handoff WHERE agent = 'backend'")
        del cleared[:], sent[:]
        bg[0] = "1 shell"

    try:
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (cleared.append(h), (True, "context cleared"))[1]
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        orcamod.background_work = lambda h: bg[0]
        dmn_handoff._record_handoff = lambda con_, agent, hid, log_: True     # the record itself is not under test
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        now = int(time.time())

        # (a) the cache has lapsed: nothing live to lose, cleared at once.
        fresh()
        db.add_event(con, "backend", "ask", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - dmn_states.PROMPT_CACHE_S - 60,))
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("owed (a): with its cache lapsed the clear is typed over the background work",
              cleared == ["term_x"] and not sent and row["state"] == dmn_states.PARKED, f"{cleared} {sent} {dict(row)}")
        check("owed (a): and it is no failure", row["throttle_count"] == 0, str(dict(row)))

        # (b) warm cache, work running: told once, clear owed, nothing else typed.
        fresh()
        db.add_event(con, "backend", "ask", {})
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        told = dmn_delivery.CLEAR_NOTICE.format(context="? of 200,000", minutes=5)
        check("owed (b): the agent is told, in the founder's words, and nothing is cleared yet",
              sent == [told] and not cleared and dmn_delivery.clear_owed(con, "backend"), f"{sent} {cleared}")
        check("owed (b): the wording is the founder's: what happens and when, no reply needed, the one command",
              told.startswith("ocp: your context is full (? of 200,000), so this session will be cleared in 5 minutes. "
                              "No reply is needed. Before then: 1. Stop your background shells. Anything still running "
                              "at that time is cut off. 2. If you were waiting on a result, write what was running and "
                              'what is left in: ocp progress "done: ... left: ..." - the next session starts from that '
                              "note. 3. Do not start new work or new background jobs. When your background work has "
                              "ended, the clear happens at once.") and "\n" not in told, told)
        check("owed (b): not parked, not degraded, no bump - a wait, not a failure",
              row["state"] == dmn_states.IDLE and row["throttle_count"] == 0
              and db.last_handoff(con, "backend")["state"] == "done", str(dict(row)))
        db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", "task", 0, None)
        check("owed (b): while the clear is owed no task and no message is typed at it",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
              and not dmn_messaging.can_type_now(con, "backend", "term_x")[0] and len(sent) == 1, str(sent))

        gone = db.get_request(con, db.add_request(con, "marina", "backend", "never mind", None,
                                                  "peer", "async", None, 0, None))
        check("owed (b): a cancelled request is refused with the hold's own reason, not the usage limit's",
              "about to be cleared" in dmn_delivery.notify_cancelled(con, gone, "withdrawn")[1])

        dmn_evaluate.supervise(con, "backend", lambda _m: None, now + 10, {}, [])
        check("owed (b): a tick with the work still running waits and tells it nothing more",
              not cleared and len(sent) == 1, f"{cleared} {sent}")

        bg[0] = ""
        con.execute("UPDATE cursors SET updated = 0 WHERE name = 'typed_at:backend'")
        db.clear_cursor(con, "typed_at:backend")
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now + 20, {}, [])
        row = db.get_agent_state(con, "backend")
        check("owed (b)-ends: once the work has ended the clear is typed and the agent parked",
              cleared == ["term_x"] and row["state"] == dmn_states.PARKED
              and not dmn_delivery.clear_owed(con, "backend") and row["throttle_count"] == 0,
              f"{cleared} {dict(row)}")

        # (b)-forced: the work is still there when the time is up.
        fresh()
        db.add_event(con, "backend", "ask", {})
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        db.set_cursor(con, "clear_owed:backend", now - dmn_delivery.CLEAR_WAIT_S - 1)
        db.clear_cursor(con, "typed_at:backend")
        dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        row = db.get_agent_state(con, "backend")
        check("owed (b)-forced: at the limit the clear is typed over the work, and it is no failure",
              cleared == ["term_x"] and row["state"] == dmn_states.PARKED and row["throttle_count"] == 0,
              f"{cleared} {dict(row)}")
        check("owed (b)-forced: and its record has the note once",
              (db.get_handoff(con, "backend") or "").count("Cut off by the clear") == 1)

        # A clear that has to wait for the agent's turn (it is writing its progress note) is
        # tried again, and the record is written once, when it lands.
        fresh()
        db.add_event(con, "backend", "ask", {})
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        db.set_cursor(con, "clear_owed:backend", now - dmn_delivery.CLEAR_WAIT_S - 1)
        tries = []
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (
            tries.append(h), (True, "context cleared") if len(tries) > 2 else (False, "agent is mid-turn; not clearing"))[1]
        for _ in range(3):
            db.clear_cursor(con, "typed_at:backend")
            dmn_evaluate.supervise(con, "backend", lambda _m: None, now, {}, [])
        check("owed (b)-forced: a clear that fails twice on the agent's turn then lands writes the note once",
              len(tries) == 3 and (db.get_handoff(con, "backend") or "").count("Cut off by the clear") == 1
              and db.get_agent_state(con, "backend")["state"] == dmn_states.PARKED,
              f"{len(tries)} {db.get_handoff(con, 'backend')}")
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (cleared.append(h), (True, "context cleared"))[1]

        check("owed (b)-forced: and the handoff record says what was cut off",
              "1 shell" in (db.get_handoff(con, "backend") or "") and "gone" in (db.get_handoff(con, "backend") or ""),
              str(db.get_handoff(con, "backend")))

        # A session that is gone takes the debt with it.
        fresh()
        db.set_cursor(con, "clear_owed:backend", now)
        db.set_closed(con, "backend", "sess")
        check("owed: and a closed session owes nothing",
              not dmn_handoff.settle_owed_clear(con, "backend", lambda _m: None)
              and not dmn_delivery.clear_owed(con, "backend"))
    finally:
        (orcamod.clear_context, orcamod.alive, orcamod.idle_now, orcamod.available,
         orcamod.send, orcamod.background_work, dmn_handoff._record_handoff) = real
        con.close()


def test_the_wait_for_sweep_tells_the_founder_once_of_a_stuck_wait(home: Path) -> None:
    """T296: a cycle in `task_blocks` and a wait on a blocker that was discarded or closed
    without being done are read from the db every tick and go to the founder once - a
    cycle `add_block` refused can still come from a merge or a reopen."""
    from ocp.daemon import waitfor
    _daemon_home(home, 20)
    con = db.connect()
    log: list[str] = []

    def raised() -> list:
        return con.execute("SELECT body FROM requests WHERE kind = 'task' AND target = 'human' AND body LIKE '[wait-for]%'"
                           ).fetchall()

    try:
        a, b, c = (db.new_task(con, t, "backend", "manager") for t in ("a", "b", "c"))
        db.add_block(con, a, b)
        db.add_block(con, b, c)
        waitfor.sweep(con, log.append, 0)
        check("wait-for: a chain is no cycle and nothing is told", not raised(), str(log))

        db.add_block(con, c, a)
        waitfor.sweep(con, log.append, 0)
        sent = raised()
        check("wait-for: a loop of three is told to the founder, naming all three",
              len(sent) == 1 and all(f"T{t}" in sent[0]["body"] for t in (a, b, c)), str([r["body"] for r in sent]))
        waitfor.sweep(con, log.append, 0)
        waitfor.sweep(con, log.append, 0)
        check("wait-for: and once, however many ticks it lasts", len(raised()) == 1)

        db.remove_block(con, c, a)
        waitfor.sweep(con, log.append, 0)
        check("wait-for: a loop that is broken is forgotten", not con.execute(
            "SELECT 1 FROM cursors WHERE name LIKE 'waitfor:%'").fetchone())
        db.add_block(con, c, a)
        waitfor.sweep(con, log.append, 0)
        check("wait-for: back while the first is still undecided, it is that decision, not a second",
              len(raised()) == 1)
        con.execute("UPDATE requests SET state = 'done' WHERE kind = 'task' AND target = 'human'")
        con.execute("UPDATE tasks SET done_evidence = 'seen' WHERE owner = 'human'")
        db.clear_cursor(con, next(r["name"] for r in con.execute("SELECT name FROM cursors WHERE name LIKE 'waitfor:%'")))
        waitfor.sweep(con, log.append, 0)
        check("wait-for: and told again once that was decided", len(raised()) == 2)
        db.remove_block(con, c, a)

        d = db.new_task(con, "waits on a task that goes away", "backend", "manager")
        e = db.new_task(con, "to be discarded", "backend", "manager")
        db.add_block(con, d, e)
        db.discard_task(con, e, "manager", "not needed")
        before = len(raised())
        waitfor.sweep(con, log.append, 0)
        sent = raised()[before:]
        check("wait-for: a wait on a discarded blocker is told once, saying so",
              len(sent) == 1 and f"T{e}" in sent[0]["body"] and "discarded" in sent[0]["body"],
              str([r["body"] for r in sent]))
        waitfor.sweep(con, log.append, 0)
        check("wait-for: and not again", len(raised()) == before + 1)

        f, g = (db.new_task(con, t, "backend", "manager") for t in ("f", "g"))
        db.add_block(con, f, g)
        db.mark_task_done(con, g, "sha abc", "reason")
        before = len(raised())
        waitfor.sweep(con, log.append, 0)
        check("wait-for: a blocker that finished is no stuck wait",
              len(raised()) == before)

        # Ids that do not run in wait order: T-p waits on T-r, T-r on T-q, T-q on T-p. The founder is
        # told the real order, or the `--unblock` he is pointed at names a wait that is not there.
        p_, q_, r_ = (db.new_task(con, t, "backend", "manager") for t in ("p", "q", "r"))
        db.add_block(con, p_, r_)
        db.add_block(con, r_, q_)
        db.add_block(con, q_, p_)
        before = len(raised())
        waitfor.sweep(con, log.append, 0)
        sent = raised()[before:]
        check("wait-for: a loop is named in the order its waits go, not in id order",
              len(sent) == 1 and f"T{p_} (backend) -> T{r_} (backend) -> T{q_} (backend) -> T{p_}" in sent[0]["body"],
              str([r["body"] for r in sent]))
        for w, b_ in ((p_, r_), (r_, q_), (q_, p_)):
            db.remove_block(con, w, b_)

        # A loop the founder owns and opened is filed under him: no agent's name is invented.
        m_, n_ = (db.new_task(con, t, db.HUMAN, db.HUMAN) for t in ("m", "n"))
        db.add_block(con, m_, n_)
        db.add_block(con, n_, m_)
        before = len(raised())
        waitfor.sweep(con, log.append, 0)
        row = con.execute("SELECT requester FROM requests WHERE kind = 'task' AND target = 'human' ORDER BY id DESC LIMIT 1").fetchone()
        check("wait-for: a loop only the founder owns is filed under him, not under an agent that may not exist",
              len(raised()) == before + 1 and row["requester"] == db.HUMAN, str(dict(row)))
        for w, b_ in ((m_, n_), (n_, m_)):
            db.remove_block(con, w, b_)

        # The other half: `--blocked-by` itself refuses to close a loop.
        _, _, x = actions.task_new("product", "backend", "x", "x")
        _, _, y = actions.task_new("product", "backend", "y", "y")
        _, _, z = actions.task_new("product", "backend", "z", "z")
        ok1, _ = actions.add_block(con, x, y, "product")
        ok1b, _ = actions.add_block(con, y, z, "product")
        ok2, why2 = actions.add_block(con, z, x, "product")
        ok3, why3 = actions.add_block(con, x, x, "product")
        check("wait-for: --blocked-by refuses a loop of three, naming it, and a task waiting on itself",
              ok1 and ok1b and not ok2 and f"T{z} -> T{x} -> T{y} -> T{z}" in why2
              and not ok3 and "itself" in why3, f"{why2} {why3}")
    finally:
        con.close()


def test_a_passed_window_is_asked_about_rather_than_guessed(home: Path) -> None:
    """After its window ends, an idle account's last figure describes a window
    that no longer exists, so the only way to learn the truth is to ask.

    This was `probe.due` - spend a turn on the account to find out. Since #1447
    it is `usage_due`, and the endpoint answers without spending anything, so
    the question is only *whether* to ask.
    """
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    con = db.connect()
    try:
        write_status(home, "beta", pct_5h=95, pct_7d=34, reset_5h=now + 1800,
                     reset_7d=now + 86400 * 2, rl_at=now, collected_at=now)
        check("window: an open window with a current reading is not asked about",
              dmn_usage_poll.usage_due(con, "beta", status.read("beta"), now) is None)
        write_status(home, "beta", pct_5h=95, pct_7d=34, reset_5h=now - 60,
                     reset_7d=now + 86400 * 2, rl_at=now, collected_at=now)
        check("window: a passed window is",
              dmn_usage_poll.usage_due(con, "beta", status.read("beta"), now)
              == "its 5h window has ended")
        write_status(home, "beta", pct_5h=95, pct_7d=34, reset_5h=now + 1800,
                     reset_7d=now + 86400 * 2, rl_at=now - dmn_states.USAGE_MAX_AGE_S - 1,
                     collected_at=now)
        check("window: and so is a figure older than the cadence, whatever its window",
              (dmn_usage_poll.usage_due(con, "beta", status.read("beta"), now) or "").startswith(
                  "last read"))
    finally:
        con.close()


def test_a_working_agent_is_not_handed_off_on_a_percentage(home: Path) -> None:
    """It runs until it hits the wall: nothing below 100% parks it, and an
    account nobody can read is not a wall."""
    for pct in (85, 92, 99):
        _daemon_home(home / f"p{pct}", pct)
        con = db.connect()
        try:
            dmn_evaluate.tick(con, lambda _m: None)
            row = db.get_agent_state(con, "backend")
            check(f"wall: {pct}% does not park a working agent",
                  row["state"] not in (dmn_states.PARKED, dmn_states.HANDOFF)
                  and not con.execute("SELECT 1 FROM handoff").fetchall(), str(dict(row)))
        finally:
            con.close()
    _daemon_home(home / "unread", 40)
    write_status(home / "unread", "alpha", pct_5h=40, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
    con = db.connect()
    try:
        dmn_evaluate.tick(con, lambda _m: None)
        check("wall: an unreadable account does not park or move anyone",
              db.get_agent_state(con, "backend")["state"] not in (dmn_states.PARKED, dmn_states.HANDOFF))
    finally:
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
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.idle_now, dmn_delivery.wait_for_session)
    try:
        orca_idle()
        orcamod.close_session = exit_ends_session("mover")
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
        orcamod.relaunch = relaunch_fails_but_late

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
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.idle_now, dmn_delivery.wait_for_session) = real
        con.close()


def test_wake_over_a_stall_asks_first_same_as_unstall_did(home: Path) -> None:
    """tui._needs_confirm(): w reached the FATAL row unconfirmed would clear a
    stall on a stray keypress - the one safety #1618/#1620 made a condition of
    folding unstall into wake. Proof: one check per branch, each failing when
    its own line is reverted."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="ran out")
        check("confirm: w over a stall asks first",
              tui._needs_confirm(con, "backend", "wake"))

        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 900,
                           detail="by hand; wakes at x")
        check("confirm: w over an ordinary park does not ask",
              not tui._needs_confirm(con, "backend", "wake"))

        for label in tui.CONFIRM:
            check(f"confirm: {label} always asks",
                  tui._needs_confirm(con, "backend", label))
    finally:
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
        check("identity: and told agents start via ocp launch", "ocp launch" in msg, msg)
        ok, _msg, rid = askmod.ask("backend", "do this", requester="reviewer")
        check("identity: saying so explicitly still works", ok and rid is not None, _msg)
        out = _capture(lambda: climod.main(["withdraw", str(rid)]))
        check("identity: withdraw from nobody is refused, not 'not yours'",
              "ocp launch" in out, out)
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


def test_one_agents_bad_tick_is_not_everyones(home: Path) -> None:
    """Supervising an agent shells out to Orca three times. An exception in any
    of it used to end the tick, so every agent after it in the list went
    unsupervised with nothing saying so."""
    _daemon_home(home, 100)
    agents.register("zzlater")
    con = db.connect()
    db.set_terminal(con, "zzlater", "term_z", "wt")
    db.set_account(con, "zzlater", "alpha")
    real = dmn_evaluate.supervise
    try:
        def explode(con_, agent, *a, **k):
            if agent == "backend":
                raise RuntimeError("orca fell over")
            return real(con_, agent, *a, **k)

        dmn_evaluate.supervise = explode
        t = dmn_evaluate.tick(con, lambda _m: None)
        check("tick: the failure is reported against the agent that had it",
              any("backend: error" in a for a in t.actions), str(t.actions))
        check("tick: and it counts as a failed attempt",
              db.get_agent_state(con, "backend")["failed_attempts"] == 1,
              str(dict(db.get_agent_state(con, "backend"))))
        check("tick: the agents after it are still supervised",
              db.get_agent_state(con, "zzlater") is not None
              and db.get_agent_state(con, "zzlater")["state"] == dmn_states.PARKED,
              str(dict(db.get_agent_state(con, "zzlater") or {})))
    finally:
        dmn_evaluate.supervise = real
        con.close()


def test_handoff_reclaims_context(home: Path) -> None:
    """A handoff must return the context, not just write a digest.

    Without this the context never drops, so a context trigger fires again on
    the next tick and the agent hands off forever, paying for a fold each time.
    """
    _daemon_home(home, 50)
    con = db.connect()
    cleared = []
    real_clear, real_alive, real_idle = orcamod.clear_context, orcamod.alive, orcamod.idle_now
    try:
        orca_idle()
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (
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
        orcamod.idle_now = lambda h: False
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
        orcamod.clear_context, orcamod.alive = real_clear, real_alive
        orcamod.idle_now = real_idle
        con.close()


def test_a_context_handoff_wakes_at_once_with_its_record(home: Path) -> None:
    """Founder #1903: a context handoff is not a usage wall, so once the
    clear is done there is no window left to wait out - its park's wake_at
    is immediate, not the account's reset_5h, whatever its usage window says.

    But it still has to be a park, not a shortcut straight to QUEUED/IDLE:
    waking is the one path (`wake_agent`/`wake_prompt`) that hands the agent
    its own record back. Skipping it left the agent with an empty
    conversation and no memory of what it was doing - auditor #1957's
    blocking finding on the first cut of this fix, which only asserted the
    state was not PARKED and so passed on exactly that regression.
    """
    _daemon_home(home, 20)
    con = db.connect()
    typed: list[str] = []
    real = (orcamod.clear_context, orcamod.alive, orcamod.idle_now,
           orcamod.resume_with, orcamod.typeable_now)
    try:
        # A recorded (non-assumed) reading on an account with room to spare -
        # the case the auditor hit sitting PARKED 09:25-11:30 at 19%.
        db.set_account(con, "backend", "alpha")
        write_status(home, "alpha", pct_5h=19, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 9000)
        orca_idle()
        orcamod.typeable_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        orcamod.resume_with = lambda h, text: (typed.append(text), (True, "typed"))[1]
        readable_agent(con, home, "backend")
        # Otherwise the wake reads this as a launch-recipe change and takes
        # the closed-agent relaunch path instead of typing at the live one.
        db.set_launch_fingerprint(con, "backend", launch.fingerprint("backend"))
        sessions_are("backend")

        t = triggers.Trigger(True, "context", "context 700,000 >= 120,000")
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        before = int(time.time())
        row = db.get_agent_state(con, "backend")
        check("context handoff: parks with an immediate wake, not the account's reset",
              row["state"] == dmn_states.PARKED and row["wake_at"] is not None
              and row["wake_at"] <= before + 5, str(dict(row)))
        check("context handoff: keeps its own record for the wake to deliver",
              bool(db.get_handoff(con, "backend")), "no handoff kept")

        for _ in range(3):
            dmn_evaluate.supervise(con, "backend", lambda _m: None, int(time.time()) + 1, {}, [])
        check("context handoff: its own record is typed into the session",
              any("fresh session" in support.pointed_record(t) for t in typed), str(typed))
        row = db.get_agent_state(con, "backend")
        check("context handoff: settles into QUEUED or IDLE once delivered, not PARKED",
              row["state"] in (dmn_states.IDLE, dmn_states.QUEUED) and not row["wake_at"],
              str(dict(row)))
        check("context handoff: the record is spent, not replayed on the next wake",
              db.get_handoff(con, "backend") is None)
    finally:
        (orcamod.clear_context, orcamod.alive, orcamod.idle_now,
         orcamod.resume_with, orcamod.typeable_now) = real
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
    real_resume, real_alive = orcamod.resume_with, orcamod.alive
    try:
        orca_idle()
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "handoff injected"))[1]
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
    finally:
        orcamod.resume_with, orcamod.alive = real_resume, real_alive
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
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.resume_with, dmn_delivery.wait_for_session)
    sent: list[str] = []
    closed: list[str] = []
    launched: list[str] = []
    table = sessions_are("backend")
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
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

        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        orcamod.close_session = lambda h, **kw: (closed.append(h), table.pop("backend", None),
                                                 (True, "session ended"))[2]
        orcamod.relaunch = lambda h, cmd, started=None: (
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
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.resume_with, dmn_delivery.wait_for_session) = real
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
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.resume_with, dmn_delivery.wait_for_session)
    sent: list[str] = []
    closed: list[str] = []
    launched: list[str] = []
    table = sessions_are("backend")
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
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

        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        orcamod.close_session = lambda h, **kw: (closed.append(h), table.pop("backend", None),
                                                 (True, "session ended"))[2]
        orcamod.relaunch = lambda h, cmd, started=None: (
            launched.append(cmd), table.update({"backend": 4242}), (True, "relaunched"))[2]
        dmn_delivery.wait_for_session = lambda agent, timeout=0: True

        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: an unrecorded fingerprint ends the session, not typed into",
              closed == ["term_x"], str(closed))
        check("wake: and relaunches with its own session id to come back to",
              launched and "--resume sess-old" in launched[0], str(launched))
        check("wake: the relaunch itself is reported as a success", ok, why)
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.resume_with, dmn_delivery.wait_for_session) = real
        con.close()


def test_a_launch_records_its_fingerprint_and_says_when_it_cannot(home: Path) -> None:
    """Drift is judged against what `_record_terminal` wrote: if that write
    never lands, the daemon relaunches the session at every wake, so a failed
    write has to be said, not swallowed."""
    _daemon_home(home, 20)
    os.environ["ORCA_TERMINAL_HANDLE"] = "term_rec"
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
        os.environ.pop("ORCA_TERMINAL_HANDLE", None)


def test_a_drifted_wake_that_cannot_relaunch_says_why(home: Path) -> None:
    """A drifted session that is neither relaunched nor left alone silently:
    with no session id to resume, or a session that will not end, the wake
    falls through to typing in place, and the log has to say that happened."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.resume_with,
            dmn_delivery.wait_for_session)
    table = sessions_are("backend")
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True     # typeable_now asks whether it is idle
        db.set_terminal(con, "backend", "term_x", "wt")
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        keep_handoff("backend", "# handoff · backend\n## next\n- go\n")
        db.set_launch_fingerprint(con, "backend", "stale-fingerprint")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 10,
                           detail="usage; wakes soon")
        orcamod.resume_with = lambda h, text: (True, "typed")
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
        orcamod.close_session = lambda h, **kw: (False, "would not close")
        logged.clear()
        dmn_lifecycle.wake_agent(con, "backend", logged.append)
        check("drift wake: a session that will not end is logged, not silent",
              any("would not end" in m and "in place" in m for m in logged), str(logged))
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.resume_with,
         dmn_delivery.wait_for_session) = real
        con.close()


def test_a_second_daemon_is_refused_by_the_lock_alone(home: Path) -> None:
    """Nothing but the lock decides who supervises."""
    import contextlib
    import io

    use_home(home)
    buf = io.StringIO()
    with lock.singleton(dmn_locks.DAEMON_LOCK):
        with contextlib.redirect_stdout(buf):
            rc = dmn_loop.run(once=True)
    check("daemon: a second one is refused", rc == 1)
    check("daemon: and the refusal names the holder",
          f"already running (pid {os.getpid()})" in buf.getvalue(), buf.getvalue())

    # And the lock is not left behind by the process that held it.
    with lock.singleton(dmn_locks.DAEMON_LOCK) as held:
        check("daemon: the lock is released when its holder is done", held)


def test_an_exiting_daemon_leaves_the_live_ones_name(home: Path) -> None:
    """A second daemon's refusal and exit must not disturb the live one's name."""
    use_home(home)
    env = {**os.environ, "OCP_HOME": str(home)}
    holder, pid = _hold_daemon_lock_in_another_process(env)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = dmn_loop.run(once=True)
        check("daemon: the second one is refused", rc == 1)
        check("daemon: and the live one is still the one named",
              dmn_locks.running_elsewhere() == pid, str(dmn_locks.running_elsewhere()))
    finally:
        holder.kill()
        holder.wait(timeout=30)


def test_one_daemon_is_counted_in_the_process_table(home: Path) -> None:
    """A supervisor nobody recorded exists only there. The words `ocp daemon`
    on a command line do not make one: a grep for them carries both, and so
    does an agent session whose system prompt quotes the command."""
    use_home(home)
    table = [
        (4707, "/usr/bin/python /Users/x/workspaces/orcapipe/bin/ocp daemon"),
        (4801, "grep -n ocp daemon"),
        (4802, "vim /Users/x/workspaces/orcapipe/ocp/daemon.py"),
        (4803, "/usr/bin/python /Users/x/.local/bin/ocp ps"),
        (4804, "claude --append-system-prompt Report with /Users/x/bin/ocp daemon"),
        (4806, "claude -p /Users/x/bin/ocp daemon"),
        (os.getpid(), "/usr/bin/python /Users/x/bin/ocp daemon"),
    ]
    orig_table, orig_env = obsmod.process_table, obsmod.process_env
    try:
        obsmod.process_table = lambda: table
        obsmod.process_env = lambda pid: f"OCP_HOME={home}"
        check("ps: the running daemon is found, and only it",
              dmn_locks.processes() == [4707], str(dmn_locks.processes()))
        check("ps: /ocp daemon quoted past the front (a session's own claude "
              "-p argv, not launchd's [ocp_executable(), 'daemon']) is not one",
              4806 not in dmn_locks.processes(), str(dmn_locks.processes()))

        obsmod.process_env = lambda pid: f"OCP_HOME={home}-elsewhere"
        check("ps: another installation's daemon is not this one's duplicate",
              dmn_locks.processes() == [], str(dmn_locks.processes()))

        # The same directory under its other name - /var and /private/var are
        # one place on macOS, and the test homes live there. Compared as
        # spellings, our own duplicate reads as somebody else's and nothing is
        # reported at all.
        other = os.path.realpath(home)
        check("ps: (the fixture has two names for one directory)",
              other != str(home), f"{other} == {home}")
        obsmod.process_env = lambda pid: f"OCP_HOME={other}"
        check("ps: the home is compared as a directory, not as a spelling",
              dmn_locks.processes() == [4707], str(dmn_locks.processes()))

        # A process we cannot place is the one worth naming.
        obsmod.process_env = lambda pid: None
        check("ps: an unplaceable daemon is reported, not dropped",
              dmn_locks.processes() == [4707], str(dmn_locks.processes()))

        # Read, but OCP_HOME simply is not set: that process runs against the
        # default home, not against ours just because we could not name one.
        obsmod.process_env = lambda pid: "SHELL=/bin/zsh"
        check("ps: an unset OCP_HOME reads as the default home, not as ours",
              dmn_locks.processes() == [], str(dmn_locks.processes()))

        obsmod.process_table = lambda: None
        check("ps: and a process table we could not read is not an empty one",
              dmn_locks.processes() == [], str(dmn_locks.processes()))
    finally:
        obsmod.process_table, obsmod.process_env = orig_table, orig_env


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


def test_two_processes_cannot_both_supervise_one_home(home: Path) -> None:
    """The failure this exists to prevent is two processes, not two calls: an
    in-process check would pass on a lock that never leaves the process."""
    use_home(home)
    env = {**os.environ, "OCP_HOME": str(home)}
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys, time; sys.path.insert(0, {str(ROOT)!r})\n"
         "from ocp.infra import lock\n"
         "held = lock.singleton('daemon')\n"
         "print(held.__enter__(), flush=True)\n"
         "time.sleep(120)\n"],
        env=env, stdout=subprocess.PIPE, text=True)
    try:
        check("lock: another process took the daemon lock",
              holder.stdout.readline().strip() == "True")
        out = subprocess.run([str(ROOT / "bin" / "ocp"), "daemon",
                              "--once"],
                             env=env, capture_output=True, text=True, timeout=120)
        check("daemon: the program itself refuses, not only a call to run()",
              out.returncode == 1, out.stdout + out.stderr)
        check("daemon: and the refusal reaches the log it is detected in",
              "already running" in out.stdout, out.stdout)
    finally:
        holder.kill()
        holder.wait(timeout=30)

    # The kernel takes the lock back from a process that dies holding it, so
    # there is no stale hold to reclaim the way a pidfile has to be.
    with lock.singleton(dmn_locks.DAEMON_LOCK) as free:
        check("daemon: a killed holder leaves the lock free", free)


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
        run(lambda: test_daemon_park(Path(td) / 'd1'))
        run(lambda: test_daemon_resume(Path(td) / 'd2'))
        run(lambda: test_daemon_kill_recovery(Path(td) / 'd3'))
        run(lambda: test_reconcile_stalls_one_agents_unreadable_config_not_the_others(Path(td) / 'reconbad'))
        run(lambda: test_the_daemon_marks_itself_before_its_socket_takes_a_verb(Path(td) / 'inddaemon'))
        run(lambda: test_a_hand_edited_control_character_stalls_one_agent_live_through_the_daemon(
            Path(td) / 'readguarddaemon'))
        run(lambda: test_dev_mode_config_round_trips(Path(td) / 'devmodecfg'))
        run(lambda: test_dev_mode_fingerprint_and_restart_decision(
            Path(td) / 'devmodefp_repo', Path(td) / 'devmodefp_home'))
        run(lambda: test_dev_mode_watches_subpackages(Path(td) / 'devmodesub'))
        run(lambda: test_dev_mode_fingerprint_skips_a_missing_watched_file(Path(td) / 'devmodefpmissing'))
        run(lambda: test_dev_mode_import_check_runs_the_real_entrypoint(Path(td) / 'devmodeimport'))
        run(lambda: test_dev_mode_restart_execs_after_the_lock_and_db_are_released(Path(td) / 'devmoderestart'))
        run(lambda: test_dev_mode_check_failing_degrades_not_crashes(Path(td) / 'devmodedegrade'))
        run(lambda: test_dev_mode_restart_yields_to_a_signal_that_arrived_during_the_check(
            Path(td) / 'devmodesignalrace'))
        run(lambda: test_reconcile_still_propagates_a_fault_that_is_not_one_agents(Path(td) / 'reconsys'))
        run(lambda: test_reconcile_stalls_one_due_agents_unreadable_config_at_wake_too(Path(td) / 'reconwake'))
        run(lambda: test_daemon_handoff_timeout(Path(td) / 'd4'))
        run(lambda: test_daemon_stall_decision(Path(td) / 'd5'))
        run(lambda: test_daemon_degraded_recovers(Path(td) / 'd5c'))
        run(lambda: test_a_stale_wake_at_does_not_claim_work_that_does_not_exist(
            Path(td) / 'stalewake'))
        run(lambda: test_the_other_wake_writers_also_read_the_queue(
            Path(td) / 'otherwake'))
        run(lambda: test_an_agent_waiting_on_a_reply_is_not_at_a_usage_wall(Path(td) / 'awaitwall'))
        run(lambda: test_a_park_on_a_reply_that_is_settled_is_dropped_on_the_next_tick(Path(td) / 'settledpark'))
        run(lambda: test_an_owed_clear_that_holds_everything_says_why_once(Path(td) / 'owedsaid'))
        run(lambda: test_daemon_tick_expires_handoff(Path(td) / 'd5d'))
        run(lambda: test_daemon_ignores_stale_status(Path(td) / 'd5e'))
        run(lambda: test_ocp_presses_only_the_key_that_declines(Path(td) / 'dcl0'))
        run(lambda: test_an_agent_with_background_work_is_not_idle_until_it_ends(Path(td) / 'bgw0'))
        run(lambda: test_background_work_is_tracked_while_running_too(Path(td) / 'bgw1'))
        run(lambda: test_a_screen_with_background_work_is_kept_as_a_sample(Path(td) / 'bgsample'))
        run(lambda: test_a_closed_session_shows_no_background_work(Path(td) / 'bgw2'))
        run(lambda: test_t52_idle_timeout_and_cache_expiry_overrides(Path(td) / 'idletimeout'))
        run(lambda: test_the_live_trust_screen_is_never_pressed(Path(td) / 'livetrust'))
        run(lambda: test_a_running_daemon_is_reported_whatever_daemon_pid_says(Path(td) / 'd6'))
        run(lambda: test_a_daemon_that_never_wrote_its_name_is_still_reported(Path(td) / 'd6b'))
        run(lambda: test_a_holder_this_process_cannot_signal_is_still_reported(Path(td) / 'd6b2'))
        run(lambda: test_the_lock_names_only_its_current_holder(Path(td) / 'd6c'))
        run(lambda: test_a_daemon_process_that_does_not_hold_the_lock_is_not_called_absent(Path(td) / 'd6d'))
        run(lambda: test_service_plist(Path(td) / 'svc1'))
        run(lambda: test_service_launchctl_gated(Path(td) / 'svc2'))
        run(lambda: test_service_status_when_not_installed(Path(td) / 'svc3'))
        run(lambda: test_service_status_when_the_plist_cannot_be_looked_at(Path(td) / 'svc3b'))
        run(lambda: test_service_supervisor_of(Path(td) / 'svc4'))
        run(lambda: test_service_crash_loop(Path(td) / 'svc5'))
        run(lambda: test_daemon_refusal_logs_through_the_same_path(Path(td) / 'svc5b'))
        run(lambda: test_the_daemon_logs_the_code_sha_it_started_on(Path(td) / 'codesha'))
        run(lambda: test_service_log_rotation(Path(td) / 'svc6'))
        run(lambda: test_a_config_that_will_not_parse_is_never_overwritten_by_a_setting(Path(td) / 'cfgguard'))
        run(lambda: test_a_running_agent_is_told_when_work_arrives(Path(td) / 'deliver'))
        run(lambda: test_an_agent_is_never_handed_another_agents_session(Path(td) / 'ident'))
        run(lambda: test_a_resume_woken_for_a_message_is_handed_that_message(Path(td) / 'woketick'))
        run(lambda: test_a_closed_session_closes_its_pane_and_the_wake_opens_a_new_terminal_that_resumes(Path(td) / 'panewake'))
        run(lambda: test_a_parked_closed_agent_without_a_pane_is_woken_at_its_deadline(Path(td) / 'panedue'))
        run(lambda: test_an_agent_idle_past_its_prompt_cache_comes_back_fresh_with_its_work(Path(td) / 'coldwake'))
        run(lambda: test_the_cache_clock_decides_a_resume_not_whether_a_handoff_exists(Path(td) / 'coldnone'))
        run(lambda: test_a_cold_wake_that_fails_leaves_no_handoff_behind(Path(td) / 'a_cold_wak'))
        run(lambda: test_a_wake_does_not_call_a_fresh_start_a_park(Path(td) / 'freshstart'))
        run(lambda: test_the_sweep_corrects_what_it_can_see(Path(td) / 'sweep'))
        run(lambda: test_sweep_does_not_close_a_live_session_on_an_unmappable_pid(Path(td) / 'sweepflaky'))
        run(lambda: test_an_agent_with_nowhere_to_run_is_not_handed_off(Path(td) / 'noterm'))
        run(lambda: test_a_wake_is_believed_only_when_a_session_appears(Path(td) / 'wakereceipt'))
        run(lambda: test_a_wake_does_not_claim_a_session_it_did_not_start(Path(td) / 'wakeforeign'))
        run(lambda: test_a_wake_refuses_to_type_when_the_process_table_is_unreadable(Path(td) / 'wakeunreadable'))
        run(lambda: test_a_refused_launch_leaves_its_prompt(Path(td) / 'launchrefused'))
        run(lambda: test_a_walled_wake_says_so_everywhere(Path(td) / 'walled'))
        run(lambda: test_a_walled_wake_is_not_shown_with_nothing_waiting(Path(td) / 'walledquiet'))
        run(lambda: test_a_walled_wake_with_no_reset_reads_unknown(Path(td) / 'walledunknown'))
        run(lambda: test_a_resumed_agent_is_rescued_from_a_7d_wall_too(Path(td) / 'walled7drescue'))
        run(lambda: test_a_park_that_cannot_be_woken_keeps_its_deadline(Path(td) / 'parkdue'))
        run(lambda: test_a_parked_agent_is_unparked_by_its_own_context_limit(
            Path(td) / 'parkctxlimit'))
        run(lambda: test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s(Path(td) / 'ctxthrash'))
        run(lambda: test_the_settle_window_actually_enforces_with_require_idle_false(
            Path(td) / 'settleenforce'))
        run(lambda: test_the_post_lock_idle_reread_actually_gates(
            Path(td) / 'idlereread'))
        run(lambda: test_every_call_site_actually_hands_supervise_the_per_agent_context_limit(
            Path(td) / 'ctxsites'))
        run(lambda: test_a_resumed_handoff_also_records_its_park(Path(td) / 'parklevel6'))
        run(lambda: test_a_resumed_hand_park_stays_forceable_too(Path(td) / 'parklevel6b'))
        run(lambda: test_the_whole_loop_runs_on_our_own_commands(Path(td) / 'loop'))
        run(lambda: test_an_agent_can_put_another_one_to_work(Path(td) / 'dispatch'))
        run(lambda: test_a_due_park_with_no_terminal_is_started_not_held(Path(td) / 'dueterm'))
        run(lambda: test_the_wait_for_sweep_tells_the_founder_once_of_a_stuck_wait(Path(td) / 'waitfor'))
        run(lambda: test_a_clear_held_by_background_work_is_owed_not_failed(Path(td) / 'owedclear'))
        run(lambda: test_the_daemon_starts_every_agent_with_waiting_work_before_its_first_tick(Path(td) / 'startall'))
        run(lambda: test_the_screens_say_a_closed_agent_is_woken_only_when_the_tick_wakes_it(Path(td) / 'toldwake'))
        run(lambda: test_a_passed_window_is_asked_about_rather_than_guessed(Path(td) / 'probedue'))
        run(lambda: test_a_working_agent_is_not_handed_off_on_a_percentage(Path(td) / 'ar3'))
        run(lambda: test_stalled_survives_a_hook_that_fires_after_the_third_failed_wake(
            Path(td) / 'movestalled'))
        run(lambda: test_wake_over_a_stall_asks_first_same_as_unstall_did(Path(td) / 'wakeconfirm'))
        run(lambda: test_who_is_asking_is_not_guessed(Path(td) / 'whoasks'))
        run(lambda: test_one_agents_bad_tick_is_not_everyones(Path(td) / 'badtick'))
        run(lambda: test_handoff_reclaims_context(Path(td) / 'reclaim'))
        run(lambda: test_a_context_handoff_wakes_at_once_with_its_record(Path(td) / 'ctxnopark'))
        run(lambda: test_a_resumed_context_handoff_wakes_at_once(Path(td) / 'resumectxwake'))
        run(lambda: test_wake_injects_handoff(Path(td) / 'wake'))
        run(lambda: test_a_drifted_launch_relaunches_instead_of_waking_in_place(Path(td) / 'driftwake'))
        run(lambda: test_a_parked_row_with_no_fingerprint_also_relaunches(Path(td) / 'nofp'))
        run(lambda: test_a_launch_records_its_fingerprint_and_says_when_it_cannot(Path(td) / 'recfp'))
        run(lambda: test_a_drifted_wake_that_cannot_relaunch_says_why(Path(td) / 'driftlog'))
        run(lambda: test_a_second_daemon_is_refused_by_the_lock_alone(Path(td) / 'onedmn'))
        run(lambda: test_an_exiting_daemon_leaves_the_live_ones_name(Path(td) / 'pidname'))
        run(lambda: test_one_daemon_is_counted_in_the_process_table(Path(td) / 'dmnps'))
        run(lambda: test_process_env_reads_a_gone_pid_as_unreadable(Path(td) / 'procenv'))
        run(lambda: test_live_sessions_fails_the_whole_reading_on_an_unmappable_pid())
        run(lambda: test_two_processes_cannot_both_supervise_one_home(Path(td) / 'twoproc'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
