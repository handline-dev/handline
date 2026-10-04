"""T946: tools/test.sh is the one way tests run - the machine's venv python, unbuffered, OCP_AGENT
unset, OCP_HOME in a temp dir removed afterwards - and tools/suite.sh is that same path.

Run on its own: python3 tests/infra/test_test_sh.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

STUB = """#!/bin/sh
echo "ARGS $*"
echo "AGENT=${OCP_AGENT-unset}"
echo "ACCOUNT=${OCP_ACCOUNT-unset}"
echo "HOME=$OCP_HOME"
echo "UNBUF=$PYTHONUNBUFFERED"
[ -d "$OCP_HOME" ] && echo "HOME_EXISTS"
exit "${STUB_RC:-0}"
"""


def _run(script: str, home: Path, *args: str, venv: bool = True, rc: int = 0) -> subprocess.CompletedProcess:
    fake = home / "machine"
    if venv:
        (fake / "venv" / "bin").mkdir(parents=True, exist_ok=True)
        py = fake / "venv" / "bin" / "python"
        py.write_text(STUB)
        py.chmod(0o755)
    env = dict(os.environ, OCP_HOME=str(fake), OCP_AGENT="orcapipe.daemon", OCP_ACCOUNT="theta",
               STUB_RC=str(rc), PATH=str(home / "bin") + os.pathsep + os.environ["PATH"])
    return subprocess.run([str(ROOT / "tools" / script), *args], capture_output=True, text=True, env=env, timeout=60)


def _fields(r: subprocess.CompletedProcess) -> dict:
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def test_a_run_goes_through_the_venv_python_with_a_clean_unbuffered_environment(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    r = _run("test.sh", home, "-k", "reconcile")
    got = _fields(r)
    check("test.sh: no file means the whole-suite runner, with -k passed on, under the -u venv python",
          "ARGS -u tests/test_ocp.py -k reconcile" in r.stdout, r.stdout + r.stderr)
    check("test.sh: it says on stderr what it is running, so a slow run is never silent",
          "tools/test.sh:" in r.stderr and "venv/bin/python -u tests/test_ocp.py -k reconcile" in r.stderr, r.stderr)
    check("test.sh: OCP_AGENT and OCP_ACCOUNT are unset for the run", got.get("AGENT") == "unset"
          and got.get("ACCOUNT") == "unset", str(got))
    check("test.sh: output is unbuffered", got.get("UNBUF") == "1", str(got))
    scratch = got.get("HOME", "")
    check("test.sh: OCP_HOME is a temp dir, not the machine's, and it existed during the run",
          scratch and scratch != str(home / "machine") and "HOME_EXISTS" in r.stdout, str(got))
    check("test.sh: and it is removed afterwards", scratch and not Path(scratch).exists(), scratch)


def test_a_file_argument_runs_that_one_area_file_and_the_exit_code_comes_back(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    r = _run("test.sh", home, "tests/ui/test_screens.py", "-k", "one", rc=3)
    check("test.sh: an existing file is the target, the rest of the arguments follow it",
          "ARGS -u tests/ui/test_screens.py -k one" in r.stdout, r.stdout)
    check("test.sh: a failing run's exit code is the script's", r.returncode == 3, str(r.returncode))
    r = _run("test.sh", home)
    check("test.sh: no arguments is the whole suite, and a passing exit is 0",
          "ARGS -u tests/test_ocp.py" in r.stdout and r.returncode == 0, r.stdout)


def test_without_a_venv_it_says_so_and_uses_python3_and_suite_sh_is_the_same_path(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    r = _run("test.sh", home, "-k", "nothing_matches_this_zzz", venv=False)
    check("test.sh: no venv is named on stderr, the run falls back to python3 rather than stopping",
          "no venv at" in r.stderr and "hl venv install" in r.stderr and "python3 -u tests/test_ocp.py" in r.stderr,
          r.stderr)
    r = _run("suite.sh", home, "-k", "x")
    check("suite.sh: is test.sh - the same header, the same arguments",
          "tools/test.sh:" in r.stderr and "ARGS -u tests/test_ocp.py -k x" in r.stdout, r.stdout + r.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_a_run_goes_through_the_venv_python_with_a_clean_unbuffered_environment(Path(td) / "s1"))
        run(lambda: test_a_file_argument_runs_that_one_area_file_and_the_exit_code_comes_back(Path(td) / "s2"))
        run(lambda: test_without_a_venv_it_says_so_and_uses_python3_and_suite_sh_is_the_same_path(Path(td) / "s3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
