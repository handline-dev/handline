"""Tests: the suite's own harness: run(), labels, name checks.

Run on its own: python3 tests/infra/test_harness.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




def test_the_guard_against_the_real_orca_names_the_test_and_cannot_be_swallowed() -> None:
    """A test that starts `orca` itself is stopped and named; one that catches the error still fails the run,
    because the attempt is kept; and a child process that reaches `orca` reaches the stand-in on PATH, which
    records the test that ran it. (Each is cleaned out here so it does not fail the run.)"""
    n = len(ORCA_ATTEMPTS)
    try:
        subprocess.run(["orca", "terminal", "list", "--json"], capture_output=True)
        said = ""
    except AssertionError as e:
        said = str(e)
    swallowed = ORCA_ATTEMPTS[n:]
    del ORCA_ATTEMPTS[n:]
    check("orca guard: an orca started in process raises, naming the test and the call",
          CURRENT_TEST["name"] in said and "terminal list --json" in said, said)
    check("orca guard: and the attempt is kept, so catching the error does not hide it",
          swallowed == [(CURRENT_TEST["name"], "orca terminal list --json")], str(swallowed))
    r = subprocess.run(["sh", "-c", "orca worktree list"], capture_output=True)
    mine = [c for t, c in calls_that_reached_the_stand_in_orca() if t == CURRENT_TEST["name"]]
    check("orca guard: a child that runs orca reaches the stand-in, which exits 1 and logs the test",
          r.returncode == 1 and mine == ["worktree list"], str((r.returncode, mine)))
    kept = [ln for ln in FAKE_ORCA_LOG.read_text().splitlines()
            if not ln.startswith(CURRENT_TEST["name"] + "\t")]
    FAKE_ORCA_LOG.write_text("".join(ln + "\n" for ln in kept))
    check("orca guard: which is the one place `orca` resolves to for this suite",
          shutil.which("orca") == str(FAKE_BIN_DIR / "orca"), str(shutil.which("orca")))


def test_the_guard_against_the_live_tmux_server_names_the_test_and_cannot_be_swallowed() -> None:
    """A test that starts a tmux aimed at the live server is stopped and named; one that catches the error
    still fails the run, because the attempt is kept; and a child process reaches the stand-in on PATH, which
    refuses it and records the test that ran it. (Each is cleaned out here so it does not fail the run.)"""
    n = len(TMUX_ATTEMPTS)
    try:
        subprocess.run(["tmux", "-L", "ocp", "list-panes", "-a"], capture_output=True)
        said = ""
    except AssertionError as e:
        said = str(e)
    swallowed = TMUX_ATTEMPTS[n:]
    del TMUX_ATTEMPTS[n:]
    check("tmux guard: a live-server tmux started in process raises, naming the test and the call",
          CURRENT_TEST["name"] in said and "-L ocp list-panes" in said, said)
    check("tmux guard: and the attempt is kept, so catching the error does not hide it",
          swallowed == [(CURRENT_TEST["name"], "tmux -L ocp list-panes -a")], str(swallowed))
    r = subprocess.run(["sh", "-c", "tmux list-panes -a"], capture_output=True)
    mine = [c for t, c in calls_that_reached_live_tmux() if t == CURRENT_TEST["name"]]
    check("tmux guard: a child that runs tmux with no socket reaches the stand-in, which refuses it and logs the test",
          r.returncode == 1 and mine == ["list-panes -a"], str((r.returncode, mine)))
    kept = [ln for ln in FAKE_TMUX_LOG.read_text().splitlines()
            if not ln.startswith(CURRENT_TEST["name"] + "\t")]
    FAKE_TMUX_LOG.write_text("".join(ln + "\n" for ln in kept))
    check("tmux guard: which is the one place `tmux` resolves to for this suite",
          shutil.which("tmux") == str(FAKE_BIN_DIR / "tmux"), str(shutil.which("tmux")))


def test_the_suite_and_the_package_name_only_what_exists() -> None:
    """A test that reads a name nothing defines raises when it runs, and a
    failure everyone has learned to read past is not a failure: this suite ended
    each run on `NameError: DIFFERENTIAL_CASES` for a day because the cases had
    been deleted and their loop had not. A function is only read for the names
    it uses when it runs, so this reads them all, and it reads the registration
    list too, since a test that is never called never fails."""
    here = Path(__file__).resolve()
    tests = here.parent.parent
    root = tests.parent
    areas = sorted(p for p in tests.glob("*/test_*.py")
                   if p.name != "test_bridge_textual.py")
    for path in (areas + [tests / "support.py", tests / "test_ocp.py"]
                 + sorted((root / "handline").rglob("*.py"))
                 + [root / "bin" / "_ocp_entry.py", root / "libexec" / "ptyrun.py"]):
        bad = sorted(_undefined_globals(path))
        check(f"names: {path.relative_to(root)} reads no name that is not defined",
              not bad, str(bad[:5]))
    everywhere: list[str] = []
    for path in areas:
        text = path.read_text()
        defined = re.findall(r"^def (test_\w+)\(", text, re.M)
        registered = set(re.findall(r"(test_\w+)\(", text[text.index("\ndef main()"):]))
        check(f"suite: every test function in {path.name} is in its main()'s run list",
              all(d in registered for d in defined),
              str([d for d in defined if d not in registered][:5]))
        everywhere += defined
    bridge = (tests / "ui" / "test_bridge_textual.py").read_text()
    bdefined = re.findall(r"^(?:async )?def (test_\w+)\(", bridge, re.M)
    bregistered = set(re.findall(r"(test_\w+)\(", bridge[bridge.index("async def _run_all"):]))
    check("suite: every test function in test_bridge_textual.py is in its _run_all list",
          all(d in bregistered for d in bdefined),
          str([d for d in bdefined if d not in bregistered][:5]))
    check("suite: no test function is defined twice, in one file or across them",
          len(everywhere) == len(set(everywhere)))


def test_run_recovers_from_an_uncaught_error() -> None:
    """Review of buddyron/test-suite-agent-independent: checked by hand is not
    checked by the suite. Both a plain exception and a `sys.exit` from code
    under test must be recorded as a failure, not left to end the run."""
    real_pass, real_fail, real_log = support.PASS, support.FAIL, support.LABEL_LOG
    support.PASS, support.FAIL, support.LABEL_LOG = [], [], []
    try:
        def test_synthetic_boom():
            raise ValueError("synthetic")

        def test_synthetic_quits():
            sys.exit(1)

        # These two are meant to fail - swallow their PASS/FAIL (and label)
        # lines so a clean run's console output, and its label file, have no
        # `FAIL` that is not a real one.
        with contextlib.redirect_stdout(io.StringIO()):
            run(lambda: test_synthetic_boom())
            run(lambda: test_synthetic_quits())
        caught = support.FAIL
    finally:
        support.PASS, support.FAIL, support.LABEL_LOG = real_pass, real_fail, real_log

    check("run(): an uncaught exception is caught, not propagated, and "
          "recorded naming the test and the error",
          len(caught) == 2 and "test_synthetic_boom" in caught[0]
          and "ValueError" in caught[0], str(caught))
    check("run(): sys.exit from code under test is caught the same way",
          "test_synthetic_quits" in caught[1] and "SystemExit" in caught[1], str(caught))
    # This is exactly `main`'s own final line - a non-empty FAIL, from either
    # test above, is what turns into the process's exit code.
    check("a failure recorded this way still drives the run's exit code to 1",
          (1 if caught else 0) == 1)


def test_restore_world_pops_orca_env_vars_a_real_terminal_would_leave_set() -> None:
    """Auditor #1662/#1670: on a machine with no real Orca terminal (the
    common case, unlike the account this suite is usually run from), the pop
    in restore_world() is otherwise covered by nothing - both variables are
    set and unset by nothing else in the suite. Set both to markers directly,
    the same shape a real terminal's environment would leave them in, so the
    check does not depend on which machine runs it."""
    os.environ["ORCA_TERMINAL_HANDLE"] = "marker_handle"
    os.environ["ORCA_WORKTREE_ID"] = "marker_worktree"
    restore_world()
    check("restore_world: ORCA_TERMINAL_HANDLE is popped, not just this test's own",
          "ORCA_TERMINAL_HANDLE" not in os.environ)
    check("restore_world: ORCA_WORKTREE_ID is popped too, same class of leak",
          "ORCA_WORKTREE_ID" not in os.environ)


def test_run_restores_current_test_after_its_own_nested_run_calls(home: Path) -> None:
    """Pins BLOCKING 2 (auditor #1647, re-flagged unpinned in #1658
    SHOULD-FIX 1) at the label file itself: a test that calls run() on
    synthetic cases of its own must have its OWN later checks recorded under
    its own name in write_labels()'s output, not the last nested case's."""
    home.mkdir(parents=True, exist_ok=True)
    real_pass, real_fail, real_log = support.PASS, support.FAIL, support.LABEL_LOG
    support.PASS, support.FAIL, support.LABEL_LOG = [], [], []
    try:
        ocp_test_after_nested = None

        def test_synthetic_outer():
            nonlocal ocp_test_after_nested

            def test_synthetic_inner():
                check("inner: ran", True)
            with contextlib.redirect_stdout(io.StringIO()):
                run(lambda: test_synthetic_inner())
            # OCP_TEST, not only CURRENT_TEST, must come back too: it is what
            # the fake `orca` on PATH logs a call under (see hook() below),
            # and a caller's own filter on that log reads its owner's name
            # from this variable - auditor #1658 NOTE 1, half of BLOCKING 2's
            # fix left unpinned. Read here, before this test's own finally
            # (which sets OCP_TEST again) could hide a real regression.
            ocp_test_after_nested = os.environ.get("OCP_TEST")
            check("outer: ran after its own nested run()", True)

        with contextlib.redirect_stdout(io.StringIO()):
            run(lambda: test_synthetic_outer())
        path = str(home / "nested.jsonl")
        write_labels(path)
        labels = _read_labels(path)
    finally:
        support.PASS, support.FAIL, support.LABEL_LOG = real_pass, real_fail, real_log

    check("run(): OCP_TEST is restored after a nested run() too, not only CURRENT_TEST",
          ocp_test_after_nested == "test_synthetic_outer", ocp_test_after_nested)
    check("run(): the nested case's own check carries its own name",
          labels.get(("test_synthetic_inner", "inner: ran", 1)) == "PASS", str(labels))
    check("run(): the enclosing test's check after its nested run() call "
          "carries its own name, not the nested one's",
          labels.get(("test_synthetic_outer",
                       "outer: ran after its own nested run()", 1)) == "PASS",
          str(labels))


def test_a_clean_runs_label_file_has_no_fail_record(home: Path) -> None:
    """Pins SHOULD-FIX 3 (auditor #1647, re-flagged unpinned in #1658
    SHOULD-FIX 1) at the label file itself: LABEL_LOG is swapped alongside
    PASS/FAIL in test_run_recovers_from_an_uncaught_error, so its two
    synthetic failures must not reach write_labels()'s output - measured end
    to end here, not only by re-reading that test's own PASS/FAIL lists."""
    home.mkdir(parents=True, exist_ok=True)
    real_pass, real_fail, real_log = support.PASS, support.FAIL, support.LABEL_LOG
    support.PASS, support.FAIL, support.LABEL_LOG = [], [], []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run(lambda: test_run_recovers_from_an_uncaught_error())
        path = str(home / "clean.jsonl")
        write_labels(path)
        fails = [ln for ln in Path(path).read_text().splitlines()
                 if json.loads(ln)["state"] == "FAIL"]
    finally:
        support.PASS, support.FAIL, support.LABEL_LOG = real_pass, real_fail, real_log
    check("write_labels: a run with nothing really failing writes no FAIL record",
          fails == [], str(fails))


def test_diff_labels_prints_only_what_changed_and_nothing_else(home: Path) -> None:
    """diff_labels() is the tool a mutation check trusts to say which label a
    mutant killed - so it is exercised directly, on files it does not itself
    write, rather than only by hand through one real run's output."""
    home.mkdir(parents=True, exist_ok=True)
    a_path, b_path = str(home / "a.jsonl"), str(home / "b.jsonl")
    Path(a_path).write_text(
        '{"state": "PASS", "test": "t1", "name": "x", "occurrence": 1}\n'
        '{"state": "FAIL", "test": "t2", "name": "y", "occurrence": 1}\n')
    Path(b_path).write_text(
        '{"state": "FAIL", "test": "t1", "name": "x", "occurrence": 1}\n'
        '{"state": "FAIL", "test": "t2", "name": "y", "occurrence": 1}\n'
        '{"state": "PASS", "test": "t3", "name": "z", "occurrence": 1}\n')
    got: dict = {}
    out = _capture(lambda: got.__setitem__("rc", diff_labels(a_path, b_path)))
    lines = [ln for ln in out.splitlines() if ln]
    check("diff_labels: the flip is reported", "PASS -> FAIL  t1 :: x" in lines, out)
    check("diff_labels: the label only in b reads MISSING on a's side",
          "MISSING -> PASS  t3 :: z" in lines, out)
    check("diff_labels: the unchanged label is not reported at all",
          not any("t2" in ln for ln in lines), out)
    check("diff_labels: nothing else was printed", len(lines) == 2, out)
    check("diff_labels: a real change returns 1", got["rc"] == 1)

    c_path = str(home / "c.jsonl")
    Path(c_path).write_text(Path(a_path).read_text())
    out2 = _capture(lambda: got.__setitem__("rc", diff_labels(a_path, c_path)))
    check("diff_labels: two identical files print nothing and return 0",
          out2 == "" and got["rc"] == 0, out2)

    # A group whose length differs between two runs (one occurrence dropped
    # and the rest renumbered, not only a different -k) is the likelier real
    # cause of a MISSING past the first, and the ordinal it names is the
    # group's new tail, not whichever iteration actually vanished - true of
    # the mechanism, worth knowing when reading its output.
    d_path = str(home / "d.jsonl")
    Path(d_path).write_text(
        '{"state": "PASS", "test": "t4", "name": "w", "occurrence": 1}\n'
        '{"state": "PASS", "test": "t4", "name": "w", "occurrence": 2}\n'
        '{"state": "PASS", "test": "t4", "name": "w", "occurrence": 3}\n')
    e_path = str(home / "e.jsonl")
    Path(e_path).write_text(
        '{"state": "PASS", "test": "t4", "name": "w", "occurrence": 1}\n'
        '{"state": "PASS", "test": "t4", "name": "w", "occurrence": 2}\n')
    out3 = _capture(lambda: diff_labels(d_path, e_path))
    check("diff_labels: a shortened group is named by its tail ordinal, "
          "past the first, whichever member actually left",
          out3.strip() == "PASS -> MISSING  t4 :: w [3]", out3)


def test_write_labels_disambiguates_a_name_repeated_inside_one_test(home: Path) -> None:
    """A name is not unique even inside the one test that emits it - a loop
    that calls check() with the same literal string several times, as several
    real tests in this suite do, must not collapse into one label that only
    the last iteration can answer for."""
    home.mkdir(parents=True, exist_ok=True)
    real_pass, real_fail, real_log = support.PASS, support.FAIL, support.LABEL_LOG
    support.PASS, support.FAIL, support.LABEL_LOG = [], [], []
    try:
        def test_synthetic_loop():
            for ok in (True, True, False, True):
                check("loop: still parked - the reading is still at the wall", ok)

        with contextlib.redirect_stdout(io.StringIO()):
            run(lambda: test_synthetic_loop())
        path = str(home / "loop.jsonl")
        write_labels(path)
        labels = _read_labels(path)
    finally:
        support.PASS, support.FAIL, support.LABEL_LOG = real_pass, real_fail, real_log

    name = "loop: still parked - the reading is still at the wall"
    keys = [("test_synthetic_loop", name, i) for i in range(1, 5)]
    check("write_labels: all four occurrences are kept, not collapsed to one",
          set(labels) == set(keys), str(labels))
    check("write_labels: each occurrence carries its own call's state, in order",
          [labels.get(k) for k in keys] == ["PASS", "PASS", "FAIL", "PASS"], str(labels))

    # A mutation that flips only the first, still-real occurrence must be
    # visible on its own - the bug this test answers is that qualifying by
    # (test, name) alone made every occurrence but the last invisible to a
    # flip, because the dict write for occurrence 1 was overwritten by 2, 3
    # and 4 before diff_labels ever saw it.
    mutated = str(home / "loop_mutated.jsonl")
    lines = Path(path).read_text().splitlines()
    first = json.loads(lines[0])
    check("write_labels: setup - the first line is occurrence 1", first["occurrence"] == 1)
    first["state"] = "FAIL"
    lines[0] = json.dumps(first)
    Path(mutated).write_text("\n".join(lines) + "\n")
    out = _capture(lambda: diff_labels(path, mutated))
    check("write_labels: diff_labels catches a flip in the first occurrence alone",
          out.strip() == f"PASS -> FAIL  test_synthetic_loop :: {name}", out)


def test_timefmt_stamp_reads_a_missing_time_as_empty_or_the_epoch() -> None:
    """T62: `empty=` is what a missing time reads as; without it a missing
    time is the epoch, not now."""
    from handline.infra import timefmt
    check("stamp: a missing time reads as empty= when one is given",
          timefmt.stamp(None, timefmt.DATE_TIME, "-") == "-"
          and timefmt.stamp(0, timefmt.DATE_TIME, "an unknown date") == "an unknown date")
    check("stamp: a real time ignores empty=",
          timefmt.stamp(86400 * 400, "%Y", "-") == time.strftime("%Y", time.localtime(86400 * 400)))
    check("stamp: None without empty= is 1970 (localtime(0)), never now",
          timefmt.stamp(None, "%Y") in ("1969", "1970")
          and timefmt.stamp(None, "%Y") == timefmt.stamp(0, "%Y"))


def test_the_runner_skips_a_file_missing_a_module_and_fails_one_that_hangs() -> None:
    """A run of the suite must end on its own: a file that dies on a module that
    is not installed is reported SKIPPED with the module's name, and one that
    hangs is killed at its limit and fails with a message that says so."""
    import contextlib
    import io
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import test_ocp as runner
    with tempfile.TemporaryDirectory() as td:
        hang, nomod, fine = (Path(td) / n for n in ("test_hang.py", "test_nomod.py", "test_fine.py"))
        hang.write_text("import time\ntime.sleep(60)\n")
        nomod.write_text("import surely_not_installed_anywhere\n")
        fine.write_text('print("1 passed, 0 failed")\n')

        path, code, out = runner._run_one(hang, [], timeout=1)
        check("runner: a hanging file is stopped at its limit, with the code and the words",
              code == runner.TIMED_OUT and "timed out after 1s" in out, out)
        check("runner: a missing module is found from the crash",
              runner.missing_module(subprocess.run([sys.executable, str(nomod)], capture_output=True,
                                                   text=True).stderr) == "surely_not_installed_anywhere")
        check("runner: a file that ran and counted is never a skip",
              runner.missing_module("No module named 'x'\n1 passed, 0 failed\n") is None)

        own = Path(td) / "test_own.py"
        own.write_text("import handline.daemon.no_such_module_left_behind\n")
        _, _, out = runner._run_one(own, [], timeout=30)
        check("runner: a module of this repo that does not exist is not a skip - it is a broken import",
              runner.missing_module(out) is None and "no_such_module_left_behind" in out, out)
        mid = Path(td) / "test_mid.py"
        mid.write_text('print("FAIL  something broke")\nimport surely_not_installed_anywhere\n')
        _, _, out = runner._run_one(mid, [], timeout=30)
        check("runner: a file that already ran a test is never skipped for a later import",
              runner.missing_module(out) is None and "FAIL  something broke" in out, out)
        reader = Path(td) / "test_reads_stdin.py"
        reader.write_text("import sys\nsys.stdin.read()\nprint('reached eof')\n")
        held_r, held_w = os.pipe()      # a stdin pipe nobody ever writes to or closes
        saved_fd = os.dup(0)
        os.dup2(held_r, 0)
        began = time.time()
        try:
            _, code, out = runner._run_one(reader, [], timeout=20)
        finally:
            os.dup2(saved_fd, 0)
            for fd in (saved_fd, held_r, held_w):
                os.close(fd)
        check("runner: a file that reads stdin gets EOF at once, though the runner's own stdin is an open pipe",
              code == 0 and "reached eof" in out and time.time() - began < 15,
              f"code={code} took={time.time() - began:.1f}s {out}")
        kid = Path(td) / "test_kid.py"
        pidfile = Path(td) / "kid.pid"
        kid.write_text("import subprocess, sys, time\n"
                       "p = subprocess.Popen(['sleep', '600'], stdout=subprocess.DEVNULL, "
                       f"stderr=subprocess.DEVNULL); open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
                       "print('started', flush=True); sys.stderr.write('stderr so far\\n'); sys.stderr.flush()\n"
                       "time.sleep(600)\n")
        began = time.time()
        _, code, out = runner._run_one(kid, [], timeout=2)
        took = time.time() - began
        time.sleep(0.5)
        child = int(pidfile.read_text())
        try:
            os.kill(child, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        if alive:
            os.kill(child, signal.SIGKILL)      # what a failing run must not leave behind
        check("runner: a timeout keeps what the file printed, stderr too", "started" in out and "stderr so far" in out, out)
        check("runner: and ends what the test started, not only the test itself - a child that "
              "holds no pipe is still gone", code == runner.TIMED_OUT and not alive and took < 30,
              f"code={code} alive={alive} took={took:.1f}s")

        saved = (runner.area_files, runner.FILE_TIMEOUT_S, runner._python_with_textual)
        runner.area_files, runner.FILE_TIMEOUT_S = (lambda: [hang, nomod, fine]), 1
        runner._python_with_textual = lambda: None
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = runner.run_all([])
        finally:
            runner.area_files, runner.FILE_TIMEOUT_S, runner._python_with_textual = saved
        said = buf.getvalue()
        check("runner: the skip names the file and the module and is not a failure",
              "SKIPPED  test_nomod.py: it needs the module surely_not_installed_anywhere" in said
              and "test_nomod.py did not finish" not in said, said)
        check("runner: the hang is a failure that names the file and the limit",
              rc == 1 and "test_hang.py timed out (limit 1s" in said, said)
        check("runner: the file that ran is still counted", "1 passed, 0 failed" in said, said)


def test_an_empty_ocp_home_is_refused_not_read_as_the_current_directory() -> None:
    """`Path("")` is the current directory, so an OCP_HOME set but empty made `hl init` set up
    wherever it was run. Empty or blank is refused with the way out; unset still means ~/.orcapipe."""
    from handline.infra import paths
    saved = os.environ.get("OCP_HOME")
    try:
        for value in ("", "   "):
            os.environ["OCP_HOME"] = value
            try:
                paths.home()
                said = ""
            except SystemExit as e:
                said = str(e)
            check(f"empty home: {value!r} is refused and says how to fix it",
                  "set but empty" in said and "unset it" in said, said)
        os.environ.pop("OCP_HOME")
        check("empty home: unset is still the default home", paths.home() == paths.default_home())
    finally:
        if saved is None:
            os.environ.pop("OCP_HOME", None)
        else:
            os.environ["OCP_HOME"] = saved


def test_every_daemon_actions_and_team_module_imports_on_its_own() -> None:
    """The daemon, actions and team packages import each other; a module imported first in a fresh
    interpreter (as tools/migrate_home.py does with handline.daemon.locks) must not hit a cycle."""
    from concurrent.futures import ThreadPoolExecutor
    mods = sorted(f"ocp.{pkg}.{f.stem}" for pkg in ("daemon", "actions", "team")
                  for f in (ROOT / "handline" / pkg).glob("*.py") if f.stem != "__init__")

    def alone(mod: str) -> str:
        r = subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, text=True, cwd=ROOT)
        return "" if r.returncode == 0 else f"{mod}: {r.stderr.strip().splitlines()[-1][:160]}"
    with ThreadPoolExecutor(8) as ex:
        broken = [b for b in ex.map(alone, mods) if b]
    check("imports: each daemon, actions and team module imports alone", not broken, "; ".join(broken[:3]))


def test_every_tools_file_with_a_shebang_is_executable_in_the_index() -> None:
    """A tool run by its own path ("tools/x.py check") must land as mode 100755."""
    out = subprocess.run(["git", "ls-files", "-s", "tools"], cwd=ROOT, capture_output=True, text=True).stdout
    bad = []
    for line in out.splitlines():
        mode, _, _, path = line.replace("\t", " ").split(None, 3)
        if mode != "100755" and (ROOT / path).read_bytes().startswith(b"#!"):
            bad.append(f"{path} is {mode}")
    check("tools: every file with a shebang is 100755 in the index", out and not bad, "; ".join(bad))


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_every_daemon_actions_and_team_module_imports_on_its_own())
        run(lambda: test_every_tools_file_with_a_shebang_is_executable_in_the_index())
        run(lambda: test_timefmt_stamp_reads_a_missing_time_as_empty_or_the_epoch())
        run(lambda: test_the_runner_skips_a_file_missing_a_module_and_fails_one_that_hangs())
        run(lambda: test_the_guard_against_the_real_orca_names_the_test_and_cannot_be_swallowed())
        run(lambda: test_the_guard_against_the_live_tmux_server_names_the_test_and_cannot_be_swallowed())
        run(lambda: test_the_suite_and_the_package_name_only_what_exists())
        run(lambda: test_an_empty_ocp_home_is_refused_not_read_as_the_current_directory())
        run(lambda: test_run_recovers_from_an_uncaught_error())
        run(lambda: test_restore_world_pops_orca_env_vars_a_real_terminal_would_leave_set())
        run(lambda: test_run_restores_current_test_after_its_own_nested_run_calls(Path(td) / 'nestedrun'))
        run(lambda: test_a_clean_runs_label_file_has_no_fail_record(Path(td) / 'cleanrun'))
        run(lambda: test_diff_labels_prints_only_what_changed_and_nothing_else(Path(td) / 'difflabels'))
        run(lambda: test_write_labels_disambiguates_a_name_repeated_inside_one_test(
            Path(td) / 'looplabels'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
