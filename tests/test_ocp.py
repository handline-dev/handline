"""Runs every area test file, in parallel, and reports one count.

Run: python3 tests/test_ocp.py            (all areas)
     python3 tests/test_<area>.py         (one area on its own)
     python3 tests/test_ocp.py -k reconcile --write-labels before.txt
     python3 tests/test_ocp.py --diff-labels before.txt after.txt
     python3 tests/test_ocp.py -j 2       (at most two areas at once)

The tests live in tests/test_<area>.py over the shared tests/support.py, which
this module also re-exports for tests/test_bridge_textual.py.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import *  # noqa: E402,F401,F403
from support import diff_labels  # noqa: E402

HERE = Path(__file__).resolve().parent
NOT_AREAS = {"test_ocp.py", "test_bridge_textual.py"}


def area_files() -> list[Path]:
    """Every area file, the biggest first so the longest runs start earliest."""
    files = [p for p in HERE.glob("test_*.py") if p.name not in NOT_AREAS]
    return sorted(files, key=lambda p: -p.stat().st_size)


def _run_one(path: Path, args: list[str]) -> tuple[Path, int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("OCP_AGENT", "OCP_ACCOUNT")}
    done = subprocess.run([sys.executable, str(path), *args], capture_output=True,
                          text=True, env=env)
    return path, done.returncode, done.stdout + done.stderr


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

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            results = list(pool.map(lambda p: _run_one(p, child_args(p)), files))
        if args.write_labels:
            with open(args.write_labels, "w") as out:
                for p in files:
                    if Path(label_paths[p]).exists():
                        out.write(Path(label_paths[p]).read_text())
    passed = failed = 0
    failures: list[str] = []
    crashed = False
    for path, code, output in results:
        print(output, end="")
        counted = re.findall(r"^(\d+) passed, (\d+) failed$", output, re.M)
        if counted:
            passed += int(counted[-1][0])
            failed += int(counted[-1][1])
        else:
            crashed = True
            failures.append(f"{path.name} did not finish (exit {code})")
        failures += re.findall(r"^  FAILED: (.*)$", output, re.M)
    print(f"\n{passed} passed, {failed} failed")
    for f in failures:
        print(f"  FAILED: {f}")
    return 1 if failed or crashed else 0


if __name__ == "__main__":
    sys.exit(run_all(sys.argv[1:]))
