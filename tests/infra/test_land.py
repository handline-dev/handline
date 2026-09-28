"""Tests: tools/land.sh lands a branch without main's checkout holding a half-merged tree.

Run on its own: python3 tests/infra/test_land.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

LAND = ROOT / "tools" / "land.sh"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@x", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=30)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _land(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(LAND), *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=60)


def _repo(td: Path) -> Path:
    """A main checkout with a module and a suite that is green unless RED exists."""
    main = td / "main"
    main.mkdir(parents=True)
    _git(main, "init", "-q", "-b", "main")
    (main / "a.py").write_text("x = 1\ny = 2\n")
    (main / "tools").mkdir()
    suite = main / "tools" / "suite.sh"
    suite.write_text('#!/usr/bin/env bash\n[ ! -e "$(dirname "$0")/../RED" ]\n')
    suite.chmod(0o755)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    return main


def _branch(main: Path, name: str, filename: str, text: str) -> str:
    _git(main, "checkout", "-q", "-b", name, "main")
    (main / filename).write_text(text)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", name)
    sha = _git(main, "rev-parse", "HEAD")
    _git(main, "checkout", "-q", "main")
    return sha


def test_a_branch_lands_by_fast_forward_and_a_conflict_never_touches_the_checkout(home: Path) -> None:
    """T390: every agent's ocp runs from main's checkout, so a merge in it that
    stops at conflict markers breaks ocp for everyone until it is resolved."""
    main = _repo(home)
    before = _git(main, "rev-parse", "HEAD")
    clean = _branch(main, "clean", "b.py", "z = 3\n")
    r = _land(main, clean, "Merge clean")
    merged = _git(main, "rev-parse", "HEAD")
    check("land: a clean branch lands and prints the merge sha", r.returncode == 0
          and r.stdout.strip() == merged and merged != before, r.stdout + r.stderr)
    check("land: it is a merge commit with the message given, and the file is in main's checkout",
          _git(main, "log", "-1", "--format=%s") == "Merge clean" and (main / "b.py").read_text() == "z = 3\n"
          and len(_git(main, "log", "-1", "--format=%P").split()) == 2)
    check("land: the scratch worktree is gone", len(_git(main, "worktree", "list").splitlines()) == 1,
          _git(main, "worktree", "list"))

    theirs = _branch(main, "clash", "a.py", "x = 1\ny = 30\n")
    (main / "a.py").write_text("x = 1\ny = 20\n")
    _git(main, "commit", "-qam", "main edits a.py")
    head, text = _git(main, "rev-parse", "HEAD"), (main / "a.py").read_bytes()
    r = _land(main, theirs, "Merge clash")
    check("land: a conflict exits 1 and names the file", r.returncode == 1 and "a.py" in r.stderr, r.stderr)
    check("land: main's checkout is exactly as it was - same commit, same bytes, no markers, no merge state",
          _git(main, "rev-parse", "HEAD") == head and (main / "a.py").read_bytes() == text
          and b"<<<<<<<" not in (main / "a.py").read_bytes() and not (main / ".git" / "MERGE_HEAD").exists()
          and _git(main, "status", "--porcelain") == "", _git(main, "status", "--porcelain"))
    check("land: and the scratch worktree is gone after a conflict too",
          len(_git(main, "worktree", "list").splitlines()) == 1)


def test_a_red_suite_or_a_dirty_checkout_lands_nothing(home: Path) -> None:
    main = _repo(home)
    red = _branch(main, "red", "RED", "")   # a branch that turns the suite red once merged
    head = _git(main, "rev-parse", "HEAD")
    r = _land(main, "--test", red, "Merge red")
    check("land: --test with a red suite exits 1 and lands nothing",
          r.returncode == 1 and "red" in r.stderr and _git(main, "rev-parse", "HEAD") == head
          and not (main / "RED").exists(), r.stderr)
    sha = _branch(main, "feat", "b.py", "z = 3\n")
    r = _land(main, "--test", sha, "Merge feat")
    check("land: --test with a green suite lands", r.returncode == 0 and (main / "b.py").exists(), r.stderr)

    other = _branch(main, "other", "c.py", "w = 4\n")
    head = _git(main, "rev-parse", "HEAD")
    (main / "b.py").write_text("dirty\n")
    _git(main, "add", "b.py")
    (main / "c.py").write_text("in the way\n")
    r = _land(main, other, "Merge other")
    check("land: a main checkout the fast-forward cannot update is refused, and left as it was",
          r.returncode == 1 and _git(main, "rev-parse", "HEAD") == head
          and (main / "c.py").read_text() == "in the way\n", r.stdout + r.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_a_branch_lands_by_fast_forward_and_a_conflict_never_touches_the_checkout(Path(td) / "l1"))
        run(lambda: test_a_red_suite_or_a_dirty_checkout_lands_nothing(Path(td) / "l2"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
