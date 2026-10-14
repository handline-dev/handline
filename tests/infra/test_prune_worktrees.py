"""Tests: tools/prune_worktrees.py removes only a worktree whose branch is merged into main and that is clean, never
one an agent works in, and nothing without --remove.

Run on its own: tools/test.sh tests/infra/test_prune_worktrees.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "prune_worktrees.py"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "user@example.com", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "user@example.com"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=ENV)


def _rig(root: Path) -> tuple[Path, Path, dict[str, Path]]:
    """A repository on main, a home, and one worktree of each kind."""
    root.mkdir(parents=True)
    root = root.resolve()           # git names a worktree by its real path
    repo, home = root / "repo", root / "home"
    repo.mkdir()
    home.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "first")
    wts = {}
    for name in ("merged", "dirty", "modified", "unmerged", "detached", "locked", "agent", "extra", "here"):
        wts[name] = root / "wt" / name
        _git(repo, "worktree", "add", "-q", "-b", name, str(wts[name]))
    (wts["dirty"] / "note.txt").write_text("not committed")
    for name in ("modified", "unmerged"):
        (wts[name] / "f").write_text(name)      # each its own commit: the same content in the same second is one sha
        _git(wts[name], "add", "f")
        _git(wts[name], "commit", "-q", "-m", "work")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "modified")
    (wts["modified"] / "f").write_text("2")
    _git(wts["detached"], "checkout", "-q", "--detach")
    _git(repo, "worktree", "lock", str(wts["locked"]))
    con = sqlite3.connect(home / "db.sqlite")
    con.execute("CREATE TABLE agent_state(agent TEXT, worktree TEXT, extra_worktrees TEXT)")
    con.execute("INSERT INTO agent_state VALUES('a', ?, ?)", (f"repo-id::{wts['agent']}", json.dumps([str(wts["extra"])])))
    con.execute("INSERT INTO agent_state VALUES('b', NULL, NULL)")
    con.commit()
    con.close()
    return repo, home, wts


def _prune(cwd: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in ENV.items() if k not in ("HANDLINE_HOME", "OCP_HOME")}
    return subprocess.run([sys.executable, str(SCRIPT), "--home", str(home), *args], cwd=cwd, capture_output=True, text=True, env=env)


def test_only_a_merged_clean_worktree_no_agent_works_in_is_removed(home: Path) -> None:
    repo, hl_home, wts = _rig(home)
    kept = [n for n in wts if n != "merged"]
    dry = _prune(wts["here"], hl_home)
    check("prune worktrees: a dry run names the one to remove and removes nothing",
          dry.returncode == 0 and dry.stdout.count("remove  ") == 1 and f"remove  {wts['merged']}" in dry.stdout
          and all(p.is_dir() for p in wts.values()), dry.stdout + dry.stderr)
    for name, why in (("dirty", "untracked"), ("modified", "uncommitted"), ("unmerged", "not merged"), ("detached", "no branch"),
                      ("locked", "locked"), ("agent", "an agent"), ("extra", "an agent"), ("here", "run from it")):
        line = next((ln for ln in dry.stdout.splitlines() if ln.startswith(f"keep    {wts[name]}:")), "")
        check(f"prune worktrees: the {name} one stays, and why is said", why in line, dry.stdout)
    done = _prune(wts["here"], hl_home, "--remove")
    check("prune worktrees: --remove removes that one and no other",
          done.returncode == 0 and not wts["merged"].exists() and all(wts[n].is_dir() for n in kept), done.stdout + done.stderr)
    check("prune worktrees: the main checkout is never listed", str(repo) + ":" not in dry.stdout and f" {repo}\n" not in dry.stdout)


def test_nothing_is_removed_when_the_agents_cannot_be_read(home: Path) -> None:
    _, hl_home, wts = _rig(home)
    (hl_home / "db.sqlite").unlink()
    res = _prune(wts["here"], hl_home, "--remove")
    check("prune worktrees: with no database it exits 1, says why and removes nothing",
          res.returncode == 1 and "nothing is removed" in res.stderr and wts["merged"].is_dir(), res.stdout + res.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_only_a_merged_clean_worktree_no_agent_works_in_is_removed(Path(td) / "a"))
        run(lambda: test_nothing_is_removed_when_the_agents_cannot_be_read(Path(td) / "b"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
