"""T935: agents see and branch from `main`, not from a stale ref: a launch points origin/HEAD at
origin/main (Claude Code names a session's main branch from it), and a handoff from a branch forked
far before main's tip says so.

Run on its own: python3 tests/infra/test_git_base.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import gitrun  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@x", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=30)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _commit(repo: Path, n: int) -> None:
    for i in range(n):
        (repo / "f.txt").write_text(f"{repo.name} {i} {_git(repo, 'rev-parse', 'HEAD') if i else ''}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"c{i}")


def _clone_whose_head_is_orcapipe(td: Path) -> Path:
    """What this repo looked like: a clone made while the remote's default branch was `orcapipe`."""
    seed = td / "seed"
    seed.mkdir(parents=True)
    _git(seed, "init", "-q", "-b", "orcapipe")
    _commit(seed, 1)
    _git(seed, "branch", "main")
    bare = td / "origin.git"
    _git(td, "clone", "-q", "--bare", str(seed), str(bare))
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/orcapipe")
    work = td / "work"
    _git(td, "clone", "-q", str(bare), str(work))
    return work


def test_a_launch_points_origin_head_at_main_and_only_when_it_must(home: Path) -> None:
    work = _clone_whose_head_is_orcapipe(home)
    head = lambda: _git(work, "symbolic-ref", "refs/remotes/origin/HEAD")      # noqa: E731
    check("setup: the clone's origin/HEAD names orcapipe, as the real one did", head() == "refs/remotes/origin/orcapipe",
          head())
    check("origin/HEAD: a launch moves it to main and says it changed it",
          gitrun.point_origin_head_at_main(work) is True and head() == "refs/remotes/origin/main", head())
    check("origin/HEAD: already on main, a launch changes nothing and says so",
          gitrun.point_origin_head_at_main(work) is False and head() == "refs/remotes/origin/main")
    _git(work, "update-ref", "-d", "refs/remotes/origin/main")
    _git(work, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/orcapipe")
    check("origin/HEAD: with no origin/main to point at it is left alone, not broken",
          gitrun.point_origin_head_at_main(work) is False and head() == "refs/remotes/origin/orcapipe", head())
    check("origin/HEAD: outside a repository it is a quiet False",
          gitrun.point_origin_head_at_main(home / "nowhere") is False)


def test_commits_behind_main_counts_from_where_the_branch_forked(home: Path) -> None:
    work = _clone_whose_head_is_orcapipe(home)
    _git(work, "checkout", "-q", "-b", "main", "origin/main")
    _git(work, "checkout", "-q", "-b", "feature", "main")
    check("behind: a branch at main's tip is 0 behind", gitrun.commits_behind_main(work) == 0)
    _git(work, "checkout", "-q", "main")
    _commit(work, 5)
    _git(work, "checkout", "-q", "feature")
    check("behind: five commits on main since the fork is 5", gitrun.commits_behind_main(work) == 5,
          str(gitrun.commits_behind_main(work)))
    _git(work, "branch", "-m", "main", "main-old")
    check("behind: with no main branch there is nothing to count", gitrun.commits_behind_main(work) is None)
    check("behind: outside a repository, None", gitrun.commits_behind_main(home / "nowhere") is None)


def test_exec_agent_points_origin_head_at_main_before_it_becomes_the_agent(home: Path) -> None:
    from handline.session import launch
    _daemon_home(home, 20)
    work = _clone_whose_head_is_orcapipe(home / "g")
    here = Path.cwd()
    real = (launch.build, launch._record_terminal, launch.fingerprint, os.execve)
    execd = []
    launch.build = lambda *a, **k: (["claude"], {})
    launch._record_terminal = lambda *a, **k: None
    launch.fingerprint = lambda agent: "fp"
    os.execve = lambda *a: execd.append(a)
    try:
        os.chdir(work)
        con = db.connect()
        try:
            launch.exec_agent("worker", [], False, "")
        finally:
            con.close()
    finally:
        os.chdir(here)
        launch.build, launch._record_terminal, launch.fingerprint, os.execve = real
    check("launch: the agent is exec'd with origin/HEAD already on main", execd
          and _git(work, "symbolic-ref", "refs/remotes/origin/HEAD") == "refs/remotes/origin/main", str(execd))


def test_a_handoff_from_a_far_behind_branch_warns_and_a_near_one_does_not(home: Path) -> None:
    _daemon_home(home, 20)
    hand_edit_team("""
agents:
  manager: {}
  worker: {}
  helper: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
""")
    for a in ("manager", "worker", "helper"):
        agents.register(a)
    work = _clone_whose_head_is_orcapipe(home / "g")
    _git(work, "checkout", "-q", "-b", "main", "origin/main")
    _git(work, "checkout", "-q", "-b", "feature", "main")
    _git(work, "checkout", "-q", "main")
    _commit(work, 3)
    _git(work, "checkout", "-q", "feature")
    ok, _, t1 = actions.task_new("manager", "worker", "one", "w")
    ok, _, t2 = actions.task_new("manager", "worker", "two", "w")
    here = Path.cwd()
    saved = gitrun.BEHIND_WARN
    try:
        os.chdir(work)
        gitrun.BEHIND_WARN = 100
        rc, out = _cli(["task", "handoff", f"T{t1}", "helper", "note"], as_agent="worker")
        check("handoff: three commits behind main is not worth a warning", rc == 0 and "warning" not in out, out)
        gitrun.BEHIND_WARN = 2
        rc, out = _cli(["task", "handoff", f"T{t2}", "helper", "note"], as_agent="worker")
        check("handoff: past the limit it still goes, and says the branch was forked 3 commits before main",
              rc == 0 and "forked 3 commits before main" in out and "`main`" in out, out)
    finally:
        os.chdir(here)
        gitrun.BEHIND_WARN = saved


def main() -> int:
    def tests(td):
        run(lambda: test_a_launch_points_origin_head_at_main_and_only_when_it_must(Path(td) / "b1"))
        run(lambda: test_commits_behind_main_counts_from_where_the_branch_forked(Path(td) / "b2"))
        run(lambda: test_a_handoff_from_a_far_behind_branch_warns_and_a_near_one_does_not(Path(td) / "b3"))
        run(lambda: test_exec_agent_points_origin_head_at_main_before_it_becomes_the_agent(Path(td) / "b4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
