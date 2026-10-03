"""Runs every area test file, in parallel, and reports one count.

Run: python3 tests/test_ocp.py            (all areas)
     python3 tests/<group>/test_<area>.py         (one area on its own)
     python3 tests/test_ocp.py -k reconcile --write-labels before.txt
     python3 tests/test_ocp.py --diff-labels before.txt after.txt
     python3 tests/test_ocp.py -j 2       (at most two areas at once)

The tests live in tests/<group>/test_<area>.py over the shared tests/support.py, which
this module also re-exports for tests/ui/test_bridge_textual.py. That file needs
`textual`, so it runs under whichever python can import it (this one, else the
venv's) and is reported as skipped, loudly, when none can.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import *  # noqa: E402,F401,F403
from support import diff_labels  # noqa: E402

HERE = Path(__file__).resolve().parent
NOT_AREAS = {"test_ocp.py", "test_bridge_textual.py"}
BRIDGE = HERE / "ui" / "test_bridge_textual.py"


def area_files() -> list[Path]:
    """Every area file, the biggest first so the longest runs start earliest."""
    files = [p for p in HERE.glob("*/test_*.py") if p.name not in NOT_AREAS]
    return sorted(files, key=lambda p: -p.stat().st_size)


# A file that runs longer than this is a failure, not a wait: a background run
# of the suite must end on its own. Overridable for a slow machine.
FILE_TIMEOUT_S = int(os.environ.get("OCP_TEST_FILE_TIMEOUT", "1800"))
TIMED_OUT = 124


TIMES: dict[str, float] = {}     # file name -> seconds its process took


def _run_one(path: Path, args: list[str], python: str | None = None,
             timeout: float | None = None) -> tuple[Path, int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("OCP_AGENT", "OCP_ACCOUNT")}
    limit = FILE_TIMEOUT_S if timeout is None else timeout
    # Its own session, so a timeout ends whatever the test started as well, not
    # only the test's own python. No stdin: a test that reads it gets EOF at once
    # instead of waiting on whatever pipe the runner was started with.
    started = time.monotonic()
    proc = subprocess.Popen([python or sys.executable, "-u", str(path), *args], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=limit)
        TIMES[path.name] = time.monotonic() - started
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return path, TIMED_OUT, (out or "") + f"\n{path.name} timed out after {limit:g}s\n"
    return path, proc.returncode, out or ""


def missing_module(output: str) -> str | None:
    """The third-party module a file died on at import, when that is all that
    happened: it printed no PASS or FAIL, so it never ran a test. A module of
    this repo - handline, or a file beside the tests - is a broken import, a failure."""
    if re.search(r"^(PASS|FAIL)  ", output, re.M) or re.search(r"^\d+ passed, \d+ failed$", output, re.M):
        return None
    m = re.search(r"^ModuleNotFoundError: No module named '([^']+)'", output, re.M)
    if not m:
        return None
    top = m.group(1).split(".")[0]
    if top in ("ocp", "handline") or (HERE / f"{top}.py").exists() or any((d / f"{top}.py").exists() for d in HERE.glob("*/")):
        return None
    return m.group(1)


def run_all(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", dest="filter", default=None,
                        help="run only test_* functions whose name contains this substring")
    parser.add_argument("--write-labels", dest="write_labels", default=None,
                        help="write every PASS/FAIL label from this run to a file")
    parser.add_argument("--diff-labels", dest="diff_labels", nargs=2, default=None,
                        metavar=("A", "B"),
                        help="print only the labels whose state differs between two "
                             "--write-labels files; does not run the suite")
    parser.add_argument("-j", dest="jobs", type=int, default=4,
                        help="how many area files run at once (default 4)")
    args = parser.parse_args(argv)
    if args.diff_labels:
        return diff_labels(*args.diff_labels)
    files = area_files()
    with tempfile.TemporaryDirectory() as td:
        label_paths = {p: str(Path(td) / f"{p.stem}.labels") for p in files}

        def child_args(p: Path) -> list[str]:
            out: list[str] = []
            if args.filter is not None:
                out += ["-k", args.filter]
            if args.write_labels:
                out += ["--write-labels", label_paths[p]]
            return out

        bridge_py = _python_with_textual() if args.filter is None else None
        jobs = [(p, child_args(p), None) for p in files]
        if bridge_py:
            jobs.insert(0, (BRIDGE, [], bridge_py))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            results = list(pool.map(lambda j: _run_one(*j), jobs))
        skipped = ""
        if args.filter is not None:
            skipped = f"SKIPPED  {BRIDGE.name}: it has no -k filter; run the whole suite to include it"
        elif not bridge_py:
            skipped = (f"SKIPPED  {BRIDGE.name}: textual is importable from neither {sys.executable} "
                       f"nor the machine's venv ({venvmod.venv_python(MACHINE_VENV)}); "
                       "run `hl venv` to install it")
        if args.write_labels:
            with open(args.write_labels, "w") as out:
                for p in files:
                    if Path(label_paths[p]).exists():
                        out.write(Path(label_paths[p]).read_text())
    passed = failed = 0
    failures: list[str] = []
    crashed = False
    skips: list[str] = []
    for path, code, output in results:
        module = missing_module(output)
        if module:
            skips.append(f"SKIPPED  {path.name}: it needs the module {module}, which is not installed here")
            continue
        print(output, end="")
        counted = re.findall(r"^(\d+) passed, (\d+) failed$", output, re.M)
        if code == TIMED_OUT and not counted:
            crashed = True
            failures.append(f"{path.name} timed out (limit {FILE_TIMEOUT_S}s, OCP_TEST_FILE_TIMEOUT)")
            continue
        if counted:
            passed += int(counted[-1][0])
            failed += int(counted[-1][1])
        else:
            crashed = True
            failures.append(f"{path.name} did not finish (exit {code})")
        if code != 0 and counted and not int(counted[-1][1]):
            crashed = True
            failures.append(f"{path.name} exited {code} after reporting no failure")
        failures += re.findall(r"^  FAILED: (.*)$", output, re.M)
    if skipped:
        print(skipped)
    for line in skips:
        print(line)
    print("\nslowest files: " + ", ".join(f"{n} {t:.0f}s" for n, t in
                                         sorted(TIMES.items(), key=lambda kv: -kv[1])[:6]))
    print(f"\n{passed} passed, {failed} failed")
    for f in failures:
        print(f"  FAILED: {f}")
    return 1 if failed or crashed else 0


if __name__ == "__main__":
    sys.exit(run_all(sys.argv[1:]))
