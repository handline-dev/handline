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

from handline.session import launch as launchmod  # noqa: E402
from handline.vendors.claude import Claude  # noqa: E402
from handline.vendors.opencode import Opencode  # noqa: E402


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
    check("auto: the sandbox is said either way, off unless asked for",
          got[0]["sandbox"]["enabled"] is False, str(got))
    check("auto: nothing trusted adds no autoMode",
          "autoMode" not in _settings(drv.launch_argv("/x/claude", None, "high", "Agent", [], None))[0])


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


def test_the_sandbox_is_on_only_where_agent_yaml_turns_it_on(home: Path) -> None:
    _launch_world(home)
    got = _settings(launchmod.build("backend")[0])
    check("sandbox: off by default, and the launch says so in the one --settings object",
          len(got) == 1 and got[0]["sandbox"]["enabled"] is False, str(got))
    check("sandbox: and the read/edit denies stay", got[0].get("permissions", {}).get("deny"), str(got))
    off = launchmod.fingerprint("backend")
    agents.register("backend", sandbox=True)
    got = _settings(launchmod.build("backend")[0])
    check("sandbox: on writes enabled true", got[0]["sandbox"]["enabled"] is True, str(got))
    check("sandbox: it reads back on", agents.read("backend")["sandbox"] is True
          and agents.sandbox_on("backend", agents.read("backend")))
    check("sandbox: a changed sandbox is a changed launch", launchmod.fingerprint("backend") != off)
    agents.register("backend", sandbox=False)
    check("sandbox: off again takes the key out", "sandbox" not in agents.read("backend")
          and not agents.sandbox_on("backend", agents.read("backend")))
    check("sandbox: and the fingerprint is the old one", launchmod.fingerprint("backend") == off)
    cfg = agents.read("backend")
    cfg["sandbox"] = False
    agents.write("backend", cfg)
    check("sandbox: a hand-written false is off too", not agents.sandbox_on("backend", agents.read("backend")))
    check("sandbox: a vendor without the switch is passed nothing it would not take",
          Opencode().launch_argv("/x/o", None, "high", "A", [], None, sandbox=False) == ["/x/o"])


def test_the_sandbox_setting_holds_across_sessions_until_it_is_changed(home: Path) -> None:
    import importlib.util
    from handline.team import agent_settings
    _launch_world(home)
    con = db.connect()

    def record_launch() -> None:
        """What `hl launch` records for the session it starts."""
        db.set_launch_fingerprint(con, "backend", launchmod.fingerprint("backend"),
                                  json.dumps(launchmod.launch_record("backend")))
        if db.get_agent_state(con, "backend")["closed_at"]:
            db.clear_closed(con, "backend")

    def sandboxed() -> bool:
        return any(x.get("sandbox", {}).get("enabled") is True for x in _settings(launchmod.build("backend")[0]))

    def recorded() -> str:
        return json.loads(db.get_agent_state(con, "backend")["launch_parts"])["sandbox"]
    try:
        check("nothing in the daemon ends it with a session",
              importlib.util.find_spec("handline.daemon.sandboxlease") is None)
        agents.register("backend", sandbox=True)
        record_launch()
        check("on: a session launched with it is sandboxed", sandboxed())
        db.set_closed(con, "backend", "sess-1")
        check("on: the session ends and the next launch is still sandboxed",
              sandboxed() and agents.read("backend")["sandbox"] is True)
        record_launch()                      # the wake
        db.set_closed(con, "backend", "sess-2")
        db.clear_closed(con, "backend")
        check("on: and so is every one after, with the same recipe",
              sandboxed() and launchmod.fingerprint_parts("backend")["sandbox"] == recorded())
        ok, msg = agent_settings.set_values("backend", {"sandbox": "off"})
        check("off: the agent settings switch turns it off", ok and not sandboxed()
              and not agents.sandbox_on("backend", agents.read("backend")), msg)
        check("off: the running session's recipe now differs, so an idle sweep restarts it",
              launchmod.fingerprint_parts("backend")["sandbox"] != recorded())
        db.set_closed(con, "backend", "sess-3")
        ok, msg = agent_settings.set_values("backend", {"sandbox": "on"})
        check("on: given to a closed agent it is on for the launch that wakes it", ok and sandboxed(), msg)
    finally:
        con.close()


def test_only_mcp_servers_the_founder_approved_by_content_are_enabled_at_launch(home: Path) -> None:
    import os
    from handline.infra import protected
    from handline.team import trust
    _launch_world(home)
    declared, other = home / "proj", home / "elsewhere"
    entries = {"supabase": {"command": "npx", "args": ["supabase-mcp"]}, "gsc": {"command": "mcp-gsc"}}
    for d in (declared, other):
        d.mkdir()
        (d / ".mcp.json").write_text(json.dumps({"mcpServers": entries}))
    check("mcp: a driver given servers names them in --settings",
          _settings(Claude().launch_argv("/x/claude", None, "high", "A", [], None, mcp_approved=["b", "a"]))
          == [{"enabledMcpjsonServers": ["b", "a"], "sandbox": {"enabled": False}}])
    check("mcp: and given none passes no setting for it", "enabledMcpjsonServers" not in
          _settings(Claude().launch_argv("/x/claude", None, "high", "A", [], None, mcp_approved=[]))[0])
    protected.put("trust", "roots.json", json.dumps([{"root": str(declared)}]))
    check("mcp: a declared folder whose servers nobody approved enables none: claude still asks",
          trust.mcp_approved(declared) == [] and trust.mcp_unapproved(declared) == ["gsc", "supabase"])
    check("mcp: approving a folder outside every root is refused, and writes nothing",
          not trust.approve_mcp(other)[0] and trust.mcp_approved(declared) == [])
    ok, said = trust.approve_mcp(declared)
    check("mcp: the founder approves a declared folder's servers, and the message names them",
          ok and said == "approved: gsc, supabase" and trust.mcp_approved(declared) == ["gsc", "supabase"], said)
    check("mcp: the same entries in a folder outside every root are still not approved",
          trust.mcp_approved(other) == [] and trust.mcp_unapproved(other) == ["gsc", "supabase"])
    # What an agent can do to its own worktree's file: add a server, or change an approved one's command.
    (declared / ".mcp.json").write_text(json.dumps({"mcpServers": {
        **entries, "evil": {"command": "sh", "args": ["-c", "x"]}}}))
    check("mcp: a server the agent added is not approved", trust.mcp_approved(declared) == ["gsc", "supabase"]
          and trust.mcp_unapproved(declared) == ["evil"])
    (declared / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "supabase": {"command": "sh", "args": ["-c", "x"]}, "gsc": entries["gsc"]}}))
    check("mcp: an approved name with another command is not approved either",
          trust.mcp_approved(declared) == ["gsc"] and trust.mcp_unapproved(declared) == ["supabase"])
    (declared / ".mcp.json").write_text("{not json")
    check("mcp: an unreadable file names none", trust.mcp_approved(declared) == [] and trust.mcp_unapproved(declared) == [])
    (declared / ".mcp.json").write_text(json.dumps({"mcpServers": entries}))
    here = os.getcwd()
    try:
        for folder, want in ((declared, ["gsc", "supabase"]), (other, None)):
            os.chdir(folder)
            got = [x.get("enabledMcpjsonServers") for x in _settings(launchmod.build("backend")[0])]
            check(f"mcp: a launch in {folder.name} carries {want}", [g for g in got if g] == ([want] if want else []), str(got))
        (declared / ".mcp.json").write_text(json.dumps({"mcpServers": {**entries, "evil": {"command": "sh"}}}))
        os.chdir(declared)
        got = [x.get("enabledMcpjsonServers") for x in _settings(launchmod.build("backend")[0])]
        check("mcp: and a launch after the agent added a server carries only the approved ones",
              [g for g in got if g] == [["gsc", "supabase"]], str(got))
    finally:
        os.chdir(here)


def main() -> int:
    def tests(td):
        run(lambda: test_only_mcp_servers_the_founder_approved_by_content_are_enabled_at_launch(Path(td) / 'a6'))
        run(lambda: test_the_sandbox_setting_holds_across_sessions_until_it_is_changed(Path(td) / 'a5'))
        run(lambda: test_an_agent_starts_in_the_mode_it_is_set_to(Path(td) / 'a3'))
        run(lambda: test_the_sandbox_is_on_only_where_agent_yaml_turns_it_on(Path(td) / 'a4'))
        run(lambda: test_the_claude_driver_asks_for_auto_mode_and_names_what_is_trusted(Path(td) / 'a0'))
        run(lambda: test_the_trusted_environment_names_the_worktree_its_repo_and_its_github_remotes(Path(td) / 'a1'))
        run(lambda: test_a_launch_starts_in_auto_mode_unless_the_agent_skips_permissions(Path(td) / 'a2'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
