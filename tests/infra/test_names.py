"""Tests: every setting is read under its Handline name and its ocp name.

Run on its own: python3 tests/infra/test_names.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.infra import names  # noqa: E402


def _set(**kw):
    saved = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return saved


def _restore(saved) -> None:
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_a_setting_is_read_under_both_names_new_first() -> None:
    saved = _set(HANDLINE_AGENT=None, OCP_AGENT=None)
    try:
        check("names: unset under both is the default", names.getenv("OCP_AGENT") is None
              and names.getenv("OCP_AGENT", "x") == "x")
        os.environ["OCP_AGENT"] = "old"
        check("names: the old name alone is read", names.getenv("OCP_AGENT") == "old")
        os.environ["HANDLINE_AGENT"] = "new"
        check("names: the new name wins when both are set", names.getenv("OCP_AGENT") == "new")
        os.environ["HANDLINE_AGENT"] = ""
        check("names: set but empty counts as set (the reader decides what empty means)",
              names.getenv("OCP_AGENT") == "")
        check("names: asking by the new spelling reads the same",
              names.getenv("HANDLINE_AGENT") == "")
        check("names: both() gives every entry a twin and keeps what is already there",
              names.both({"OCP_HOME": "/a", "HANDLINE_AGENT": "n", "OCP_AGENT": "o", "PATH": "/p"})
              == {"OCP_HOME": "/a", "HANDLINE_HOME": "/a", "HANDLINE_AGENT": "n", "OCP_AGENT": "o", "PATH": "/p"})
    finally:
        _restore(saved)


def test_the_home_is_found_under_either_name_and_an_existing_dot_handline() -> None:
    saved = _set(HANDLINE_HOME=None, OCP_HOME=None, OCP_TEST_ROOT=None, HANDLINE_TEST_ROOT=None)
    try:
        os.environ["OCP_TEST_ROOT"] = "/nonexistent-root"
        check("home: OCP_HOME alone", (os.environ.__setitem__("OCP_HOME", "/tmp/a") or paths.home()) == Path("/tmp/a"))
        os.environ["HANDLINE_HOME"] = "/tmp/b"
        check("home: HANDLINE_HOME wins over OCP_HOME", paths.home() == Path("/tmp/b"))
        os.environ["HANDLINE_HOME"] = " "
        try:
            paths.home()
            said = ""
        except SystemExit as e:
            said = str(e)
        check("home: an empty HANDLINE_HOME is refused too", "set but empty" in said, said)
        os.environ.pop("HANDLINE_HOME")
        os.environ.pop("OCP_HOME")
    finally:
        _restore(saved)


def test_the_default_home_is_dot_handline_only_once_it_exists(home: Path) -> None:
    saved = _set(HANDLINE_HOME=None, OCP_HOME=None, OCP_TEST_ROOT=str(home), HANDLINE_TEST_ROOT=None)
    try:
        home.mkdir(parents=True, exist_ok=True)
        check("default home: ~/.orcapipe while there is no ~/.handline", paths.default_home() == home / ".orcapipe")
        (home / ".handline").mkdir()
        check("default home: ~/.handline once it exists", paths.default_home() == home / ".handline")
    finally:
        _restore(saved)


def test_what_a_session_is_started_with_carries_both_names(home: Path) -> None:
    use_home(home)
    accounts.register("alpha")
    env = accounts.env_for("alpha", "backend")
    check("env: a launch gets OCP_ and HANDLINE_ for home, account and agent",
          all(env.get(f"{p}{k}") == v for p in ("OCP_", "HANDLINE_")
              for k, v in (("HOME", str(paths.home())), ("ACCOUNT", "alpha"), ("AGENT", "backend"))),
          str({k: v for k, v in env.items() if k.startswith(("OCP_", "HANDLINE_"))}))


def test_the_session_scan_reads_either_name_of_a_process_environment() -> None:
    from ocp.session import observe
    check("scan: a process started with the new name is found", observe._extract("PATH=/x HANDLINE_AGENT=a", "HANDLINE_AGENT") == "a")
    src = (ROOT / "ocp" / "session" / "observe.py").read_text()
    check("scan: live_sessions asks for the new name before the old",
          '_extract(env, "HANDLINE_AGENT") or _extract(env, "OCP_AGENT")' in src)
    lock_src = (ROOT / "ocp" / "daemon" / "locks.py").read_text()
    check("scan: the daemon-lock check knows both home spellings", '"HANDLINE_HOME="' in lock_src)


def test_the_handline_command_runs_the_same_program_as_ocp(home: Path) -> None:
    exe = ROOT / "bin" / "handline"
    check("command: bin/handline is a link to bin/ocp", exe.is_symlink() and os.readlink(exe) == "ocp")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OCP_", "HANDLINE_"))}
    out = {n: subprocess.run([str(ROOT / "bin" / n), "--version"], capture_output=True, text=True, env=env,
                             timeout=60) for n in ("ocp", "handline")}
    check("command: both answer --version the same", out["ocp"].returncode == 0
          and out["ocp"].stdout == out["handline"].stdout, str({k: (v.returncode, v.stdout, v.stderr[-200:]) for k, v in out.items()}))
    env["HANDLINE_HOME"] = str(home)
    r = subprocess.run([str(ROOT / "bin" / "handline"), "doctor"], capture_output=True, text=True, env=env, timeout=60)
    check("command: HANDLINE_HOME picks the home the command runs in", str(home) in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-300:])


def main() -> int:
    def tests(td):
        run(lambda: test_a_setting_is_read_under_both_names_new_first())
        run(lambda: test_the_home_is_found_under_either_name_and_an_existing_dot_handline())
        run(lambda: test_the_default_home_is_dot_handline_only_once_it_exists(Path(td) / "dflt"))
        run(lambda: test_what_a_session_is_started_with_carries_both_names(Path(td) / "envboth"))
        run(lambda: test_the_session_scan_reads_either_name_of_a_process_environment())
        run(lambda: test_the_handline_command_runs_the_same_program_as_ocp(Path(td) / "cmd"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
