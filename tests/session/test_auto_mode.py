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


def _launch_world(home: Path) -> None:
    use_home(home)
    accounts.register("alpha")
    (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
    agents.register("backend")
    vendors.get("claude").binary = lambda: "/x/bin/claude"


def _mode_flag(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--permission-mode"]


def test_an_agent_starts_in_the_mode_it_is_set_to(home: Path) -> None:
    _launch_world(home)
    check("mode: unset is auto, as every launch was", _mode_flag(launchmod.build("backend")[0]) == ["auto"])
    for mode in ("default", "acceptEdits", "plan"):
        agents.register("backend", mode=mode)
        argv = launchmod.build("backend")[0]
        check(f"mode: {mode} is the one --permission-mode, with no auto beside it",
              _mode_flag(argv) == [mode] and "--dangerously-skip-permissions" not in argv, str(argv))
        check(f"mode: {mode} gives the classifier nothing to be told",
              all("autoMode" not in x for x in _settings(argv)), str(_settings(argv)))
        check(f"mode: {mode} reads back", agents.read("backend")["mode"] == mode and agents.mode_of("backend", agents.read("backend")) == mode)
    agents.register("backend", mode="bypass")
    argv = launchmod.build("backend")[0]
    check("mode: bypass is the old switch: the flag, no mode, and skip_permissions in the file",
          "--dangerously-skip-permissions" in argv and _mode_flag(argv) == []
          and agents.skip_permissions(agents.read("backend")) and "mode" not in agents.read("backend"), str(argv))
    agents.register("backend", skip_permissions=False)
    check("mode: turning the old switch off returns it to auto",
          _mode_flag(launchmod.build("backend")[0]) == ["auto"])
    agents.register("backend", skip_permissions=True)
    check("mode: the old flag alone still shows as bypass", agents.mode_of("backend", agents.read("backend")) == "bypass")
    agents.register("backend", mode="plan")
    check("mode: a mode turns the old switch off and revokes its grant",
          not agents.skip_permissions(agents.read("backend")) and not agents.skip_permissions_granted("backend"))
    agents.register("backend", mode="auto")
    check("mode: auto is written as nothing", "mode" not in agents.read("backend")
          and _mode_flag(launchmod.build("backend")[0]) == ["auto"])
    try:
        agents.register("backend", mode="yolo")
        check("mode: an unknown one is refused", False, "it went through")
    except ValueError:
        check("mode: an unknown one is refused", True)
    f = agents.agent_file("backend")
    f.write_text(f.read_text() + "mode: bypass\n")
    reseal(f)
    check("mode: a hand-written bypass with no grant is not bypass",
          agents.mode_of("backend", agents.read("backend")) == "auto")


def test_the_sandbox_is_off_only_where_the_founder_turned_it_off(home: Path) -> None:
    _launch_world(home)
    argv = launchmod.build("backend")[0]
    check("sandbox: on by default, the launch never says enabled", all("enabled" not in x.get("sandbox", {}) for x in _settings(argv)))
    agents.register("backend", sandbox=False)
    argv = launchmod.build("backend")[0]
    got = _settings(argv)
    check("sandbox: off writes enabled false into the one --settings object",
          len(got) == 1 and got[0]["sandbox"]["enabled"] is False, str(got))
    check("sandbox: and the read/edit denies stay", got[0].get("permissions", {}).get("deny"), str(got))
    check("sandbox: it reads back off", agents.read("backend")["sandbox"] is False
          and not agents.sandbox_on("backend", agents.read("backend")))
    check("sandbox: a changed sandbox is a changed launch",
          "sandbox" in launchmod.fingerprint_parts("backend"))
    off = launchmod.fingerprint
    agents.register("backend", sandbox=True)
    check("sandbox: on again removes the key and the grant", "sandbox" not in agents.read("backend")
          and agents.sandbox_on("backend", agents.read("backend")))
    check("sandbox: and the fingerprint is the old one", launchmod.fingerprint("backend") != "")
    cfg = agents.read("backend")
    cfg["sandbox"] = False
    agents.write("backend", cfg)
    check("sandbox: a hand-written off with no grant is still on", agents.sandbox_on("backend", agents.read("backend")))
    argv = launchmod.build("backend")[0]
    check("sandbox: and that launch leaves it on", all("enabled" not in x.get("sandbox", {}) for x in _settings(argv)))
    check("sandbox: a vendor without the switch is passed nothing it would not take",
          Opencode().launch_argv("/x/o", None, "high", "A", [], None, sandbox=False) == ["/x/o"])


def test_the_sandbox_leave_lasts_one_session_and_can_be_ended_at_once(home: Path) -> None:
    from ocp.daemon import sandboxlease
    from ocp import actions
    _launch_world(home)
    con = db.connect()
    said = []

    def record_launch() -> None:
        """What `ocp launch` records for the session it starts."""
        db.set_launch_fingerprint(con, "backend", launchmod.fingerprint("backend"),
                                  json.dumps(launchmod.launch_record("backend")))
        time.sleep(0.01)                     # the next launch has its own stamp
        if db.get_agent_state(con, "backend")["closed_at"]:
            db.clear_closed(con, "backend")

    def off() -> bool:
        return not launchmod.sandbox_on("backend", agents.read("backend"), con)
    try:
        record_launch()                      # a session running with the sandbox on
        agents.register("backend", sandbox=False)
        sandboxlease.sweep(con, said.append)
        check("lease: given while a session runs sandboxed it waits for the session that takes it",
              agents.sandbox_lease("backend") == "granted" and off(), str(said))
        record_launch()                      # the restart that applies it: launched with it off
        sandboxlease.sweep(con, said.append)
        check("lease: once a session is up with it off, the lease is active",
              agents.sandbox_lease("backend") == "active" and off() and said, str(said))
        sandboxlease.sweep(con, said.append)
        check("lease: it stays for the whole session", agents.sandbox_lease("backend") == "active"
              and agents.read("backend").get("sandbox") is False)
        db.set_closed(con, "backend", "sess-1")
        check("lease: the moment the session is closed a launch is sandboxed again, before any sweep",
              not off() and all("enabled" not in x.get("sandbox", {}) for x in _settings(launchmod.build("backend")[0])))
        sandboxlease.sweep(con, said.append)
        check("lease: and the sweep ends it: no key, no lease",
              agents.sandbox_lease("backend") is None and "sandbox" not in agents.read("backend"))
        # The order that let a lease outlive its session: active, closed, woken, and only then the sweep.
        agents.register("backend", sandbox=True)
        agents.register("backend", sandbox=False)
        db.clear_closed(con, "backend")
        record_launch()
        sandboxlease.sweep(con, said.append)
        check("lease: a second lease is active on its own launch", agents.sandbox_lease("backend") == "active")
        db.set_closed(con, "backend", "sess-2")
        argv = launchmod.build("backend")[0]        # the wake: launched while the close is recorded
        check("lease: the wake is sandboxed", all("enabled" not in x.get("sandbox", {}) for x in _settings(argv)))
        record_launch()                              # ...its launch is recorded, and the close cleared
        before = launchmod.fingerprint_parts("backend")["sandbox"]
        sandboxlease.sweep(con, said.append)
        check("lease: the sweep that comes after the wake still ends it",
              agents.sandbox_lease("backend") is None and "sandbox" not in agents.read("backend"), str(said[-2:]))
        check("lease: and the new session's recipe is the sandboxed one, so nothing restarts it",
              launchmod.fingerprint_parts("backend")["sandbox"] == before != launchmod.SANDBOX_OFF_PART
              and json.loads(db.get_agent_state(con, "backend")["launch_parts"])["sandbox"] == before)
        agents.register("backend", sandbox=False)
        sandboxlease.sweep(con, said.append)
        check("lease: and the next launch asked for off starts from a fresh grant",
              agents.sandbox_lease("backend") == "granted")
        agents.register("backend", sandbox=True)
        # Given while the agent is closed, it waits for the session that comes next.
        agents.register("backend", sandbox=False)
        sandboxlease.sweep(con, said.append)
        check("lease: given to a closed agent it is kept for its next session",
              agents.sandbox_lease("backend") == "granted" and off())
        check("lease: and that launch is the one without a sandbox",
              any(x.get("sandbox", {}).get("enabled") is False for x in _settings(launchmod.build("backend")[0])))
        record_launch()
        sandboxlease.sweep(con, said.append)
        ok, msg = actions.sandbox_back_on("backend")
        check("lease: turning it back on is at once: key and lease gone",
              ok and agents.sandbox_lease("backend") is None and "sandbox" not in agents.read("backend"), msg)
        ok, msg = actions.sandbox_back_on("backend")
        check("lease: and asking again says it is already on", ok and "already on" in msg, msg)
        check("lease: the running session's recipe now differs, so an idle sweep restarts it sandboxed",
              launchmod.fingerprint_parts("backend")["sandbox"] != launchmod.SANDBOX_OFF_PART)
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_sandbox_leave_lasts_one_session_and_can_be_ended_at_once(Path(td) / 'a5'))
        run(lambda: test_an_agent_starts_in_the_mode_it_is_set_to(Path(td) / 'a3'))
        run(lambda: test_the_sandbox_is_off_only_where_the_founder_turned_it_off(Path(td) / 'a4'))
        run(lambda: test_the_claude_driver_asks_for_auto_mode_and_names_what_is_trusted(Path(td) / 'a0'))
        run(lambda: test_the_trusted_environment_names_the_worktree_its_repo_and_its_github_remotes(Path(td) / 'a1'))
        run(lambda: test_a_launch_starts_in_auto_mode_unless_the_agent_skips_permissions(Path(td) / 'a2'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
