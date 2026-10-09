"""Tests: the daemon's sweep of stale repo copies in the shared scratch root.

Run on its own: tools/test.sh tests/infra/test_scratch.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import scratch  # noqa: E402

NOW = 2_000_000_000.0
OLD = NOW - scratch.STALE_S - 60
NEW = NOW - 60


def _age(p: Path, t: float) -> None:
    os.utime(p, (t, t), follow_symlinks=False)


def _copy(base: Path, name: str, t: float) -> Path:
    d = base / name
    (d / "node_modules" / "x").mkdir(parents=True)
    (d / "AGENTS.md").write_text("# agents\n")
    for p in (d / "node_modules" / "x", d / "node_modules", d / "AGENTS.md", d):
        _age(p, t)
    return d


def test_a_copy_untouched_for_a_day_goes_and_a_recent_one_stays(td: Path) -> None:
    base = td / "root"
    stale, fresh = _copy(base, "xm3", OLD), _copy(base, "wb", NEW)
    removed = scratch.sweep(NOW, base)
    check("scratch: the copy untouched for a day is deleted", removed == [stale] and not stale.exists(), removed)
    check("scratch: a copy touched within the day stays", fresh.exists())


def test_a_write_at_the_top_of_an_old_copy_keeps_it(td: Path) -> None:
    base = td / "root"
    d = _copy(base, "busy", OLD)
    (d / "__pycache__").mkdir()
    _age(d / "__pycache__", NEW)
    _age(d, OLD)     # the mkdir moved the copy's own mtime; only the new entry is recent
    check("scratch: a copy with a recently written top-level entry is in use, not stale",
          scratch.sweep(NOW, base) == [] and d.exists())


def test_nothing_but_a_repo_copy_is_touched(td: Path) -> None:
    base = td / "root"
    session = base / "-Users-someone-workspaces-repo"
    (session / "scratchpad").mkdir(parents=True)
    no_modules = base / "plain"
    no_modules.mkdir()
    (no_modules / "AGENTS.md").write_text("x")
    afile = base / "1f3f71f7.out"
    afile.write_text("x")
    target = _copy(td, "elsewhere", OLD)
    link = base / "link"
    link.symlink_to(target)
    for p in (session / "scratchpad", session, no_modules / "AGENTS.md", no_modules, afile):
        _age(p, OLD)
    _age(link, OLD)
    check("scratch: a session's scratch dir, a dir with no node_modules, a file and a symlink all stay",
          scratch.sweep(NOW, base) == [] and session.exists() and no_modules.exists() and afile.exists()
          and link.is_symlink() and target.exists())


def test_a_missing_root_is_no_error(td: Path) -> None:
    check("scratch: a root that does not exist deletes nothing and raises nothing",
          scratch.sweep(NOW, td / "absent") == [])


def _swept(state: dict, lines: list[str], now: float) -> bool:
    t = scratch.maybe_sweep(state, lines.append, now)
    if t:
        t.join(10)
    return t is not None


def test_the_loop_call_runs_once_an_hour_and_logs_what_it_deleted(td: Path) -> None:
    base = td / "root"
    _copy(base, "xm3", OLD)
    lines: list[str] = []
    real_root = scratch.root
    scratch.root = lambda: base
    try:
        state: dict = {}
        check("scratch: the first call sweeps and names what it deleted",
              _swept(state, lines, NOW) and len(lines) == 1 and "1 repo copy" in lines[0] and "xm3" in lines[0],
              str(lines))
        _copy(base, "u1", OLD)
        check("scratch: a call within the hour does not sweep again",
              not _swept(state, lines, NOW + scratch.EVERY_S - 1) and (base / "u1").exists() and len(lines) == 1)
        check("scratch: a call an hour later sweeps",
              _swept(state, lines, NOW + scratch.EVERY_S) and not (base / "u1").exists() and len(lines) == 2,
              str(lines))
    finally:
        scratch.root = real_root


def test_a_sweep_runs_off_the_callers_thread_and_one_at_a_time(td: Path) -> None:
    """Deleting one copy (~48k files) takes seconds, and the daemon's tick must not wait on it."""
    import threading
    release, started = threading.Event(), []
    real_sweep = scratch.sweep

    def slow(now, base=None):
        started.append(now)
        release.wait(10)
        return []

    scratch.sweep = slow
    try:
        state: dict = {}
        t = scratch.maybe_sweep(state, lambda line: None, NOW)
        check("scratch: maybe_sweep returns while its sweep is still deleting",
              t is not None and t.is_alive() and not release.is_set())
        check("scratch: no second sweep starts while one is still running, even an hour on",
              scratch.maybe_sweep(state, lambda line: None, NOW + scratch.EVERY_S) is None and len(started) == 1)
        release.set()
        t.join(10)
        check("scratch: once it is done, the next hour's sweep starts",
              _swept(state, [], NOW + scratch.EVERY_S) and len(started) == 2)
    finally:
        release.set()
        scratch.sweep = real_sweep


def test_a_failing_sweep_is_logged_not_raised(td: Path) -> None:
    real_sweep = scratch.sweep

    def broken(now, base=None):
        raise RuntimeError("stub: broke")

    scratch.sweep = broken
    lines: list[str] = []
    try:
        _swept({}, lines, NOW)
        check("scratch: a sweep that raises says so in the log", any("stub: broke" in x for x in lines), str(lines))
    finally:
        scratch.sweep = real_sweep


def main() -> int:
    def tests(td):
        run(lambda: test_a_copy_untouched_for_a_day_goes_and_a_recent_one_stays(Path(td) / 's1'))
        run(lambda: test_a_write_at_the_top_of_an_old_copy_keeps_it(Path(td) / 's2'))
        run(lambda: test_nothing_but_a_repo_copy_is_touched(Path(td) / 's3'))
        run(lambda: test_a_missing_root_is_no_error(Path(td) / 's4'))
        run(lambda: test_the_loop_call_runs_once_an_hour_and_logs_what_it_deleted(Path(td) / 's5'))
        run(lambda: test_a_sweep_runs_off_the_callers_thread_and_one_at_a_time(Path(td) / 's6'))
        run(lambda: test_a_failing_sweep_is_logged_not_raised(Path(td) / 's7'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
