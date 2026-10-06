"""Tests: the differential tick harness (contract/tick): fixtures are current, the Python daemon gives the same
output on repeated runs, every input is scripted, and the comparer reports differences deterministically.

Run on its own: python3 tests/infra/test_tick_harness.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
TICK = ROOT / "contract" / "tick"
FIXTURES = sorted((TICK / "fixtures").glob("*.json"))
sys.path.insert(0, str(TICK))
import compare  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("OCP_AGENT", "HANDLINE_AGENT")}


def _pyrun(fx: Path) -> dict:
    r = subprocess.run([sys.executable, str(TICK / "pyrun.py"), str(fx)], capture_output=True, text=True, env=CLEAN_ENV)
    return json.loads(r.stdout) if r.returncode == 0 else {"error": r.stderr[-400:]}


def test_the_fixtures_are_current() -> None:
    r = subprocess.run([sys.executable, str(TICK / "scenarios.py"), "--check"], capture_output=True, text=True, env=CLEAN_ENV)
    check("tick: fixtures match what scenarios.py builds", r.returncode == 0, r.stderr[-400:])
    check("tick: there are thirty scenarios", len(FIXTURES) == 30, str(len(FIXTURES)))
    readme = (TICK / "README.md").read_text()
    check("tick: the README lists every scenario with its source", all(f.stem in readme and json.loads(f.read_text())["source"] in readme for f in FIXTURES))


def test_every_scenario_runs_the_same_twice_and_scripts_every_input() -> None:
    bad, loose = [], []
    for fx in FIXTURES:
        a, b = _pyrun(fx), _pyrun(fx)
        if a != b or "error" in a:
            bad.append(fx.stem)
        if a.get("unscripted"):
            loose.append(fx.stem)
    check("tick: repeated runs of every scenario give identical output", not bad, str(bad))
    check("tick: no scenario reaches for an input it did not script", not loose, str(loose))


def test_the_comparer_is_deterministic_and_finds_a_difference(td) -> None:
    fx = FIXTURES[1]
    py = f"{sys.executable} {TICK / 'pyrun.py'}"
    same = subprocess.run([sys.executable, str(TICK / "compare.py"), py, py, str(fx)], capture_output=True, text=True, env=CLEAN_ENV)
    check("tick: the same daemon twice is SAME, exit 0", same.returncode == 0 and f"SAME  {fx.stem}" in same.stdout, same.stdout)
    script = Path(td) / "other.py"
    script.write_text("import json,sys,subprocess\n"
                      f"o = json.loads(subprocess.run([sys.executable, {str(TICK / 'pyrun.py')!r}, sys.argv[1]], capture_output=True, text=True).stdout)\n"
                      "o['ticks'][0]['actions'] = ['changed']\nprint(json.dumps(o))\n")
    other = f"{sys.executable} {script}"
    runs = [subprocess.run([sys.executable, str(TICK / "compare.py"), py, other, str(fx)], capture_output=True, text=True, env=CLEAN_ENV) for _ in range(2)]
    check("tick: a differing daemon is DIFF with the path, exit 1", runs[0].returncode == 1 and f"DIFF  {fx.stem}" in runs[0].stdout and ".ticks[0].actions" in runs[0].stdout, runs[0].stdout)
    check("tick: the report is the same on every run", runs[0].stdout == runs[1].stdout)
    d = compare.diff({"a": [1, 2], "b": 1}, {"a": [1], "c": 1})
    check("tick: diff names length, missing and changed", d == [".a: length 2 != 1", ".b: A has 1, missing from B", ".c: missing from A, B has 1"], str(d))


def main() -> int:
    def tests(td):
        run(lambda: test_the_fixtures_are_current())
        run(lambda: test_every_scenario_runs_the_same_twice_and_scripts_every_input())
        run(lambda: test_the_comparer_is_deterministic_and_finds_a_difference(td))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
