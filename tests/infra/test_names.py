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
from handline.infra import names  # noqa: E402


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
    from handline.session import observe
    check("scan: a process started with the new name is found", observe._extract("PATH=/x HANDLINE_AGENT=a", "HANDLINE_AGENT") == "a")
    src = (ROOT / "handline" / "session" / "observe.py").read_text()
    check("scan: live_sessions asks for the new name before the old",
          '_extract(env, "HANDLINE_AGENT") or _extract(env, "OCP_AGENT")' in src)
    lock_src = (ROOT / "handline" / "daemon" / "locks.py").read_text()
    check("scan: the daemon-lock check knows both home spellings", '"HANDLINE_HOME="' in lock_src)


def test_a_name_set_for_a_child_wins_over_one_inherited_from_the_parent(home: Path) -> None:
    """A session exports HANDLINE_ names, which are read first: a login, fold or launch started from
    inside it must carry the new identity under both spellings, not the parent's."""
    use_home(home)
    accounts.register("alpha")
    saved = _set(HANDLINE_AGENT="parent.agent", HANDLINE_ACCOUNT="omega", HANDLINE_HOME="/parent/home",
                 OCP_AGENT="parent.agent", OCP_ACCOUNT="omega")
    try:
        env = accounts.env_for("alpha", "child.agent")
        os.environ.update({k: v for k, v in env.items() if k.startswith(("OCP_", "HANDLINE_"))})
        check("child env: the new agent and account are read, not the parent's",
              names.getenv("OCP_AGENT") == "child.agent" and names.getenv("OCP_ACCOUNT") == "alpha",
              str((names.getenv("OCP_AGENT"), names.getenv("OCP_ACCOUNT"))))
        check("child env: so is the home, under both spellings",
              env["HANDLINE_HOME"] == env["OCP_HOME"] == str(paths.home()), str(env))
        e2 = {"OCP_X": "old", "HANDLINE_X": "kept"}
        names.setenv(e2, "OCP_X", "new")
        check("setenv: sets both spellings", e2 == {"OCP_X": "new", "HANDLINE_X": "new"}, str(e2))
    finally:
        _restore(saved)
        for k in ("HANDLINE_HOME",):
            os.environ.pop(k, None)


def test_a_test_reaching_either_real_home_is_caught(home: Path) -> None:
    real = home / "realuser"
    (real / ".handline").mkdir(parents=True)
    saved = _set(OCP_TEST_ROOT=str(home), HANDLINE_HOME=None, OCP_HOME=None)
    old = paths._real_user_home
    paths._real_user_home = lambda: real
    try:
        for d in (".handline", ".orcapipe"):
            os.environ["OCP_HOME"] = str(real / d)
            try:
                paths.home()
                caught = False
            except paths.RealHomeInTest:
                caught = True
            check(f"guard: a test pointed at the real ~/{d} is caught", caught)
    finally:
        paths._real_user_home = old
        _restore(saved)


def test_the_handline_command_runs_the_same_program_as_ocp(home: Path) -> None:
    for old in ("handline", "ocp"):
        exe = ROOT / "bin" / old
        check(f"command: bin/{old} is a link to bin/hl", exe.is_symlink() and os.readlink(exe) == "hl")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OCP_", "HANDLINE_"))}
    out = {n: subprocess.run([str(ROOT / "bin" / n), "--version"], capture_output=True, text=True, env=env,
                             timeout=60) for n in ("hl", "ocp", "handline")}
    check("command: all three names answer --version the same", out["hl"].returncode == 0
          and out["hl"].stdout == out["ocp"].stdout == out["handline"].stdout and out["hl"].stdout.startswith("hl "), str({k: (v.returncode, v.stdout, v.stderr[-200:]) for k, v in out.items()}))
    env["HANDLINE_HOME"] = str(home)
    r = subprocess.run([str(ROOT / "bin" / "handline"), "doctor"], capture_output=True, text=True, env=env, timeout=60)
    check("command: HANDLINE_HOME picks the home the command runs in", str(home) in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-300:])


def test_the_old_package_name_still_imports_the_same_modules() -> None:
    r = subprocess.run([sys.executable, "-c",
                        f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ocp.infra.textsafe as a; "
                        "import handline.infra.textsafe as b; from ocp import db; import handline.db as d; "
                        "print(a is b, db is d)"], capture_output=True, text=True)
    check("shim: import ocp.<module> gives the handline module itself, not a copy",
          r.stdout.strip() == "True True", r.stdout + r.stderr[-300:])
    r = subprocess.run([sys.executable, "-m", "ocp.ui.actrun"], input="{}", capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=str(ROOT)))
    check("shim: python -m ocp.<module> runs the handline module (a bridge started before the rename)",
          "get_code" not in r.stderr and "No module named" not in r.stderr and "AttributeError" not in r.stderr,
          r.stdout + r.stderr[-300:])


def test_hl_is_the_main_command_and_the_old_names_are_recognised(home: Path) -> None:
    from handline.session import landgate, shellguard
    from handline.vendors import claude_hooks
    check("names: hl first, the two old names after it", names.COMMANDS == ("hl", "handline", "ocp"))
    for n in names.COMMANDS:
        check(f"names: `{n} land --pass` is read as a land verb",
              landgate.parse("Bash", {"command": f"{n} land --pass abc1234"}) == ("land_pass", "abc1234"))
        check(f"names: a hook entry written with {n} is ours",
              claude_hooks._is_our_hook(f"/x/bin/{n} hook Stop", "Stop"))
        check(f"names: a double-quoted backtick after `{n} say` is refused",
              shellguard.guard("Bash", {"command": f'{n} say a "`x`"'}) is not None)
    check("names: permissions allow the safe verbs under every name",
          all(f"Bash({n} say:*)" in claude_hooks.SAFE_COMMANDS for n in names.COMMANDS))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OCP_", "HANDLINE_"))}
    env["HANDLINE_HOME"] = str(home)
    r = subprocess.run([str(ROOT / "bin" / "hl")], capture_output=True, text=True, env=env, timeout=60)
    check("command: hl alone opens the screen (its text snapshot when piped), not a usage error",
          r.returncode == 0 and "usage:" not in r.stderr and "bridge" not in r.stdout.lower(), r.stdout[-200:] + r.stderr[-200:])


def main() -> int:
    def tests(td):
        run(lambda: test_a_setting_is_read_under_both_names_new_first())
        run(lambda: test_the_home_is_found_under_either_name_and_an_existing_dot_handline())
        run(lambda: test_the_default_home_is_dot_handline_only_once_it_exists(Path(td) / "dflt"))
        run(lambda: test_what_a_session_is_started_with_carries_both_names(Path(td) / "envboth"))
        run(lambda: test_a_name_set_for_a_child_wins_over_one_inherited_from_the_parent(Path(td) / "childenv"))
        run(lambda: test_a_test_reaching_either_real_home_is_caught(Path(td) / "guard"))
        run(lambda: test_the_session_scan_reads_either_name_of_a_process_environment())
        run(lambda: test_the_handline_command_runs_the_same_program_as_ocp(Path(td) / "cmd"))
        run(lambda: test_the_old_package_name_still_imports_the_same_modules())
        run(lambda: test_hl_is_the_main_command_and_the_old_names_are_recognised(Path(td) / "hlmain"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
