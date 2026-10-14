"""Tests: an agent's further worktrees (agent.yaml `extra_worktrees`): the config, making them once beside the first, the launch giving them to
that agent alone as writable working directories, the preamble saying which is which, and the lookups that map a path to its agent.

Run on its own: python3 tests/team/test_extra_worktrees.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.actions import details  # noqa: E402
from handline.session import backends  # noqa: E402
from handline.session.backends import tmux  # noqa: E402
from handline.session.backends.base import Backend  # noqa: E402
from handline.team import spawn  # noqa: E402
from handline.usage import stats  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)


class _Host(Backend):
    """A terminal host that makes worktrees as tmux's does (git), and starts nothing."""
    name = "tmux"

    def available(self) -> bool:
        return True

    def create_worktree(self, repo, name, base_ref):
        return tmux.TmuxBackend().create_worktree(repo, name, base_ref)


def _world(home: Path) -> tuple[Path, str]:
    """A repo with a main branch, an agent `auditor` with its first worktree recorded, and an account to launch on."""
    use_home(home)
    repo = home / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "first")
    agents.register("auditor", repo=str(repo))
    accounts.register("alpha")
    (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
    vendors.get("claude").binary = lambda: "/x/bin/claude"
    backends.use(_Host())
    first, why = termmod.create_worktree(str(repo), "auditor", "main")
    assert first, why
    con = db.connect()
    db.set_terminal(con, "auditor", "t-1", first)
    con.close()
    return repo, first


def _args(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, x in enumerate(argv) if x == flag]


def test_the_config_is_a_count_that_only_a_real_number_turns_on(home: Path) -> None:
    use_home(home)
    check("config: absent is none", agents.extra_worktrees({}) == 0)
    check("config: 1 and 2 are themselves, more is the most there is", (agents.extra_worktrees({"extra_worktrees": 1}), agents.extra_worktrees({"extra_worktrees": 2}),
                                                                       agents.extra_worktrees({"extra_worktrees": 9})) == (1, 2, agents.MAX_EXTRA_WORKTREES))
    for bad in (None, True, False, -1, 0, "1", 1.5, [1]):
        check(f"config: {bad!r} is none", agents.extra_worktrees({"extra_worktrees": bad}) == 0)
    agents.register("auditor", repo="/r")
    agents.register("auditor", extra_worktrees=1)
    check("config: register writes it", agents.read("auditor").get("extra_worktrees") == 1)
    agents.register("auditor", effort="high")
    check("config: a re-registration that omits it keeps it", agents.read("auditor").get("extra_worktrees") == 1)
    agents.register("auditor", extra_worktrees=0)
    check("config: 0 removes the key", "extra_worktrees" not in agents.read("auditor"))
    try:
        agents.register("auditor", extra_worktrees=7)
        refused = False
    except ValueError:
        refused = True
    check("config: more than the most is refused", refused)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        climod.main(["agent", "register", "auditor", "--extra-worktrees", "2"])
    check("config: the CLI sets it and says when it applies", agents.read("auditor").get("extra_worktrees") == 2 and "made when it is next started" in out.getvalue(), out.getvalue())


def test_a_missing_second_worktree_is_made_once_beside_the_first(home: Path) -> None:
    repo, first = _world(home)
    try:
        check("make: nothing is made when the config asks for none", spawn.ensure_extras("auditor", first, repo) == ([], []))
        agents.register("auditor", extra_worktrees=1)
        have, told = spawn.ensure_extras("auditor", first, repo)
        check("make: one is made, named for the agent with -2, beside the first", len(have) == 1 and Path(have[0]).name == "auditor-2"
              and Path(have[0]).parent == Path(first).parent and Path(have[0]).is_dir() and told and "made worktree" in told[0], str((have, told)))
        check("make: the first is where it was", Path(first).is_dir() and Path(first).name == "auditor")
        con = db.connect()
        row = db.get_agent_state(con, "auditor")
        con.close()
        check("make: it is recorded, and the first is still the row's worktree", spawn.extra_paths(row) == have and row["worktree"] == first)
        again, told2 = spawn.ensure_extras("auditor", first, repo)
        check("make: asked again it is kept and nothing is made", again == have and told2 == [] and len(list(Path(first).parent.glob("auditor*"))) == 2, str((again, told2)))
        con = db.connect()
        db.set_terminal(con, "auditor", None, first)        # the reconciler dropping the terminal
        row = db.get_agent_state(con, "auditor")
        con.close()
        check("make: the reconciler's drop of the terminal keeps it", spawn.extra_paths(row) == have)
        again, _ = spawn.ensure_extras("auditor", first)
        check("make: a recovery start (no repo given) finds the repo and makes no third", again == have and len(list(Path(first).parent.glob("auditor*"))) == 2)
        agents.register("auditor", extra_worktrees=2)
        more, _ = spawn.ensure_extras("auditor", first, repo)
        check("make: a count of 2 makes only the one that is missing", more[0] == have[0] and len(more) == 2 and Path(more[1]).name == "auditor-3", str(more))
        import shutil
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", more[1]], check=True, capture_output=True)
        agents.register("auditor", extra_worktrees=2)
        again, told3 = spawn.ensure_extras("auditor", first, repo)
        check("make: one that is gone is made again, and the other is left", len(again) == 2 and again[0] == have[0] and Path(again[1]).is_dir() and told3, str((again, told3)))
        text = dmn_spawn.spawn("auditor", repo, dry_run=True)[1]
        check("make: a dry run says nothing is made when both are there", "would make" not in text, text)
        agents.register("auditor", extra_worktrees=0)
        check("make: with the config back to none the recorded ones are neither used nor removed", spawn.ensure_extras("auditor", first, repo)[0] == []
              and Path(have[0]).is_dir())
    finally:
        backends.use(None)


def test_the_dry_run_says_what_it_would_make(home: Path) -> None:
    repo, first = _world(home)
    try:
        agents.register("auditor", extra_worktrees=1)
        text = dmn_spawn.spawn("auditor", repo, dry_run=True)[1]
        check("dry run: it names the second worktree it would make and makes none", "would make auditor-2" in text and not Path(first + "-2").exists(), text)
    finally:
        backends.use(None)


def test_a_launch_gives_the_further_worktrees_to_that_agent_alone(home: Path) -> None:
    repo, first = _world(home)
    try:
        agents.register("other", repo=str(repo))
        agents.register("auditor", extra_worktrees=1)
        argv, _ = launch.build("auditor")
        check("launch: with the config asking and none made yet, nothing is added", "--add-dir" not in argv)
        have, _ = spawn.ensure_extras("auditor", first, repo)
        argv, _ = launch.build("auditor")
        settings = json.loads(_args(argv, "--settings")[0])
        check("launch: the second is a working directory of the session", _args(argv, "--add-dir") == have, str(argv))
        check("launch: and Bash may write it (the sandbox's allowWrite names it, and the rest of the sandbox settings stay)",
              settings["sandbox"]["filesystem"]["allowWrite"] == have and "denyRead" in settings["sandbox"]["filesystem"], str(settings))
        check("launch: the preamble says which is which", f"{first} (where you were started) is for probes" in _args(argv, "--append-system-prompt")[0]
              and have[0] in _args(argv, "--append-system-prompt")[0] and "suite runs" in _args(argv, "--append-system-prompt")[0])
        other = launch.build("other")[0]
        check("launch: another agent gets neither the directory nor the line", "--add-dir" not in other and have[0] not in " ".join(other))
        agents.register("auditor", extra_worktrees=0)
        argv, _ = launch.build("auditor")
        check("launch: with the config at none a recorded worktree is not given", "--add-dir" not in argv and "suite runs" not in " ".join(argv))
        agents.register("auditor", extra_worktrees=1)
        hidden = launch.hidden_transcripts("other", vendors.get("claude"))
        want = [str(vendors.get("claude").transcript_dir(vendors.config_dir("alpha"), p)) for p in (first, have[0])]
        check("launch: the transcripts of its second worktree are hidden from the others as the first's are", all(w in hidden for w in want), str((want, hidden)))
    finally:
        backends.use(None)


def main() -> int:
    def tests(td):
        run(lambda: test_the_config_is_a_count_that_only_a_real_number_turns_on(Path(td) / "x1"))
        run(lambda: test_a_missing_second_worktree_is_made_once_beside_the_first(Path(td) / "x2"))
        run(lambda: test_the_dry_run_says_what_it_would_make(Path(td) / "x3"))
        run(lambda: test_a_launch_gives_the_further_worktrees_to_that_agent_alone(Path(td) / "x4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
