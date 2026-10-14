"""Tests: tools/go-env.sh drops, once a day, the build cache files no build used for two days.

Run on its own: tools/test.sh tests/infra/test_go_env.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
DAY = 86400


def _entry(cache: Path, name: str, days_old: float) -> Path:
    p = cache / name[:2] / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    at = time.time() - days_old * DAY
    os.utime(p, (at, at))
    return p


def _source(cache: Path) -> None:
    subprocess.run(["bash", "-c", f". {ROOT}/tools/go-env.sh"], check=True, timeout=60, env={**os.environ, "GOCACHE": str(cache)})


def _gone(p: Path) -> bool:
    """The prune runs in the background: wait for it."""
    for _ in range(100):
        if not p.exists():
            return True
        time.sleep(0.05)
    return False


def test_the_cache_is_pruned_once_a_day(home: Path) -> None:
    if not shutil.which("go"):
        check("go env: no go on PATH, nothing to run", True)
        return
    cache = home / "cache"
    old, fresh = _entry(cache, "aa-old", 3), _entry(cache, "bb-fresh", 1)
    top = cache / "trim.txt"                    # go's own file at the top is not an entry
    top.write_text("0")
    os.utime(top, (time.time() - 9 * DAY,) * 2)
    _source(cache)
    check("go env: an entry unused for more than two days is dropped", _gone(old))
    check("go env: one used since, and go's own files at the top, stay", fresh.exists() and top.exists())
    again = _entry(cache, "cc-old", 3)
    _source(cache)
    check("go env: a second run the same day prunes nothing", not _gone(again))
    stamp = cache / "handline-pruned"
    os.utime(stamp, (time.time() - 1.5 * DAY,) * 2)
    _source(cache)
    check("go env: a day later it prunes again", _gone(again))


def main() -> int:
    def tests(td):
        run(lambda: test_the_cache_is_pruned_once_a_day(Path(td) / "a"))
    return run_area(tests)


if __name__ == "__main__":
    raise SystemExit(main())
