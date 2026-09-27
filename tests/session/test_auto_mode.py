"""T239: a claude session starts in auto mode, and its classifier is told what
the team's own ground is, in the one --settings object."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from ocp.session import launch as launchmod  # noqa: E402
from ocp.vendors.claude import Claude  # noqa: E402
from ocp.vendors.opencode import Opencode  # noqa: E402


def _settings(argv: list[str]) -> list[dict]:
    return [json.loads(a) for i, a in enumerate(argv) if argv[i - 1] == "--settings"]


def test_the_claude_driver_asks_for_auto_mode_and_names_what_is_trusted(home: Path) -> None:
    drv = Claude()
    check("auto: the flag, not a defaultMode setting",
          drv.auto_mode_argv() == ["--permission-mode", "auto"], str(drv.auto_mode_argv()))
    check("auto: a vendor with no such mode passes nothing", Opencode().auto_mode_argv() == [])
    argv = drv.launch_argv("/x/claude", None, "high", "Agent", [], None,
                           hide=["/a"], memory_dir=Path("/m"), trusted=["one", "two"])
    got = _settings(argv)
    check("auto: ONE --settings object carries the memory, the hide rules and autoMode",
          len(got) == 1 and got[0]["autoMemoryDirectory"] == "/m"
          and got[0]["sandbox"]["filesystem"]["denyRead"] == ["/a"], str(got))
    check("auto: the environment keeps the built-in entries and adds ours after them",
          got[0]["autoMode"] == {"environment": ["$defaults", "one", "two"]}, str(got))
    check("auto: the sandbox is left as configured, never switched here",
          "enabled" not in got[0]["sandbox"], str(got))
    check("auto: nothing trusted adds no autoMode",
          "--settings" not in drv.launch_argv("/x/claude", None, "high", "Agent", [], None))


def test_the_trusted_environment_names_the_worktree_its_repo_and_its_github_remotes(home: Path) -> None:
    repo, wt = home / "repo", home / "wts" / "backend"
    repo.mkdir(parents=True)
    git = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("remote", "add", "origin", "https://github.com/acme/widgets.git")
    git("remote", "add", "ssh", "git@github.com:acme/tools")
    git("remote", "add", "other", "https://example.com/x/y.git")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
    git("worktree", "add", "-q", str(wt), "-b", "b")
    text = "\n".join(launchmod.trusted_environment(wt))
    check("trusted: the state folder", str(paths.home()) in text, text)
    check("trusted: the worktree, and what is routine in it",
          str(wt) in text and "sed -i" in text, text)
    check("trusted: its repository and the siblings' folder",
          f"repository is {repo.resolve()}," in text and f"under {wt.parent}" in text, text)
    check("trusted: the GitHub remotes, both spellings, once each, and nothing else",
          "github.com/acme/widgets" in text and "github.com/acme/tools" in text
          and "example.com" not in text and text.count("acme/widgets") == 1, text)
    check("trusted: no worktree, only the state folder",
          len(launchmod.trusted_environment(None)) == 1)
    check("trusted: a folder that is no repository names no repository or remote",
          "Source control" not in "\n".join(launchmod.trusted_environment(home)))


def test_a_launch_starts_in_auto_mode_unless_the_agent_skips_permissions(home: Path) -> None:
    use_home(home)
    accounts.register("alpha")
    (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
    agents.register("backend")
    cdrv = vendors.get("claude")
    cdrv.binary = lambda: "/x/bin/claude"
    flag = ["--permission-mode", "auto"]
    def has(argv):  # the pair, in order
        return any(argv[i:i + 2] == flag for i in range(len(argv)))
    argv = launchmod.build("backend")[0]
    check("launch: --permission-mode auto is in the argv", has(argv), str(argv))
    got = _settings(argv)
    check("launch: autoMode.environment is in --settings, defaults first",
          len(got) == 1 and got[0]["autoMode"]["environment"][0] == "$defaults"
          and len(got[0]["autoMode"]["environment"]) > 1, str(got))
    for spelling in (["--permission-mode", "plan"], ["--permission-mode=plan"]):
        argv = launchmod.build("backend", spelling)[0]
        check(f"launch: a caller's {spelling} is the only mode, no stray 'auto' beside it",
              "auto" not in argv and not has(argv) and argv[-len(spelling):] == spelling, str(argv))
    agents.register("backend", skip_permissions=True)
    check("launch: a granted skip-permissions agent does not also ask for auto",
          not has(launchmod.build("backend")[0]))


def main() -> int:
    def tests(td):
        run(lambda: test_the_claude_driver_asks_for_auto_mode_and_names_what_is_trusted(Path(td) / 'a0'))
        run(lambda: test_the_trusted_environment_names_the_worktree_its_repo_and_its_github_remotes(Path(td) / 'a1'))
        run(lambda: test_a_launch_starts_in_auto_mode_unless_the_agent_skips_permissions(Path(td) / 'a2'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
