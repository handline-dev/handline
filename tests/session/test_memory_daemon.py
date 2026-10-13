"""T230: an agent's memory is its own folder, and two hooks keep it small
(docs/memory-design.md), against core's real `handline.team.memory`."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from handline.session import handoff as handoffmod, launch as launchmod, memoryuse  # noqa: E402
from handline.team import memory as memorymod  # noqa: E402
from handline.vendors import claude_hooks  # noqa: E402
from handline.vendors.claude import Claude  # noqa: E402
from handline.vendors.opencode import Opencode  # noqa: E402


class _Status:
    """Makes core's `memory.status` fail (or answer) for the length of a with-block."""

    def __init__(self, fail=True):
        self.fail = fail

    def __enter__(self):
        self.before = memorymod.status
        if self.fail:
            def broken(agent, now=None):
                raise OSError("unreadable")
            memorymod.status = broken
        return self

    def __exit__(self, *exc):
        memorymod.status = self.before


def _topic(agent: str, rel: str, age_days: float = 0) -> Path:
    """A topic file in the agent's memory folder, last touched `age_days` ago."""
    f = memorymod.memory_dir(agent) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("a memory\n")
    then = time.time() - age_days * 86400
    os.utime(f, (then, then))
    return f


def _index(n: int, pad: int = 0) -> str:
    return "".join(f"- [Entry {i}](e{i}.md) - {'x' * pad}\n" for i in range(n))


def _hook(event: str, agent: str = "backend", **fields) -> str:
    """Run `hl hook <event>` for `agent` and return what it printed."""
    real_agent, out = os.environ.get("OCP_AGENT"), io.StringIO()
    os.environ["OCP_AGENT"] = agent
    try:
        with contextlib.redirect_stdout(out):
            support._fire_hook(event, **fields)
    finally:
        if real_agent is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = real_agent
    return out.getvalue().strip()


def test_the_claude_driver_points_one_settings_object_at_the_agents_memory(home: Path) -> None:
    drv, mem = Claude(), Path("/h/agents/backend/memory")
    argv = drv.launch_argv("/x/claude", None, "high", "Agent", [], None, memory_dir=mem)
    settings = [json.loads(a) for i, a in enumerate(argv) if argv[i - 1] == "--settings"]
    check("memory: the folder is passed as autoMemoryDirectory, an absolute path",
          len(settings) == 1 and settings[0]["autoMemoryDirectory"] == str(mem), str(argv))
    argv = drv.launch_argv("/x/claude", None, "high", "Agent", [], None, hide=["/a"], memory_dir=mem)
    settings = [json.loads(a) for i, a in enumerate(argv) if argv[i - 1] == "--settings"]
    check("memory: with hidden paths it is still ONE --settings object carrying both",
          len(settings) == 1 and settings[0]["autoMemoryDirectory"] == str(mem)
          and settings[0]["sandbox"]["filesystem"]["denyRead"] == ["/a"], str(argv))
    argv = drv.launch_argv("/x/claude", None, "high", "Agent", [], None)
    check("memory: no folder adds no autoMemoryDirectory",
          "autoMemoryDirectory" not in json.loads(argv[argv.index("--settings") + 1]), str(argv))
    check("memory: a vendor with no auto-memory has no setting for it", Opencode().memory_settings(mem) == {})


def test_launch_hides_every_other_agents_memory_and_creates_its_own(home: Path) -> None:
    _daemon_home(home, 20)
    agents.register("frontend")
    check("hide: every other agent's memory folder, never its own",
          launchmod.hidden_memories("backend") == [str(paths.agent_dir("frontend") / "memory")],
          str(launchmod.hidden_memories("backend")))


def test_a_large_memory_index_write_is_accepted(home: Path) -> None:
    _daemon_home(home, 20)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    index = mem / "MEMORY.md"
    index.write_text(_index(3))
    for name, content in {"bytes": _index(3, pad=2000), "entries": _index(40), "both": _index(40, pad=2000)}.items():
        check(f"hook: a Write that leaves the index far over the old budget in {name} is not refused",
              _hook("PreToolUse", tool_name="Write", tool_input={"file_path": str(index), "content": content}) == "{}")
    line = "- [Entry 0](e0.md) - \n"
    index.write_text(_index(25))
    check("hook: nor is an Edit that grows an index already over",
          _hook("PreToolUse", tool_name="Edit", tool_input={
              "file_path": str(index), "old_string": line, "new_string": line + line}) == "{}")


def test_the_pre_tool_hook_refuses_an_ocp_message_the_shell_would_run(home: Path) -> None:
    _daemon_home(home, 20)

    def out(command: str) -> str:
        return _hook("PreToolUse", tool_name="Bash", tool_input={"command": command})

    bad = out('ocp ask orcapipe.auditor "found[\'idle_s\'] is `idle`"')
    deny = json.loads(bad)["hookSpecificOutput"]
    check("shell: a backtick in a double-quoted ocp body is denied, with the fix in the reason",
          deny["permissionDecision"] == "deny" and "--file" in deny["permissionDecisionReason"], bad)
    check("shell: so is $( in one", "deny" in out('ocp resolve 5 "ran $(date)"'))
    check("shell: and an unquoted heredoc, which expands too", "deny" in out("ocp ask a <<EOF\nsee `x`\nEOF"))
    for name, cmd in {
        "single quotes": "ocp ask a 'see `x` and $(y)'",
        "an escaped backtick": 'ocp ask a "see \\`x\\`"',
        "--file": "ocp ask a --file /tmp/body.txt",
        "a quoted heredoc": "ocp ask a <<'EOF'\nsee `x` and $(y)\nEOF",
        "a plain double quote": 'ocp ask a "no substitution here"',
        "not ocp": 'git commit -m "`x`"',
    }.items():
        check(f"shell: {name} is left alone", out(cmd) == "{}", out(cmd))


def test_reading_or_writing_a_memory_file_is_noted_for_the_month_check(home: Path) -> None:
    _daemon_home(home, 20)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    side = paths.agent_dir("backend") / "memory-use.json"
    topic = str(mem / "feedback" / "testing.md")

    memoryuse.note_use("backend", "Read", {"file_path": topic}, now=1000)
    check("use: a Read of a memory file is recorded under its path relative to the folder",
          json.loads(side.read_text()) == {"feedback/testing.md": 1000}, side.read_text())
    memoryuse.note_use("backend", "Read", {"file_path": topic}, now=1000 + 60)
    check("use: noted again within the day changes nothing",
          json.loads(side.read_text()) == {"feedback/testing.md": 1000})
    memoryuse.note_use("backend", "Edit", {"file_path": topic}, now=1000 + memoryuse.RENOTE_S)
    check("use: later it is updated, by an Edit as well",
          json.loads(side.read_text()) == {"feedback/testing.md": 1000 + memoryuse.RENOTE_S})
    memoryuse.note_use("backend", "Read", {"file_path": str(mem / "MEMORY.md")}, now=5000)
    memoryuse.note_use("backend", "Read", {"file_path": str(home / "elsewhere.md")}, now=5000)
    memoryuse.note_use("backend", "Bash", {"file_path": topic}, now=9_000_000)
    check("use: the index itself, a file elsewhere and another tool are not a use",
          list(json.loads(side.read_text())) == ["feedback/testing.md"], side.read_text())

    side.unlink()
    out = _hook("PostToolUse", tool_name="Read", tool_input={"file_path": topic})
    check("use: the PostToolUse hook does it, and still only answers {}",
          out == "{}" and list(json.loads(side.read_text())) == ["feedback/testing.md"], out)


def test_two_concurrent_hook_calls_do_not_drop_one_anothers_use(home: Path) -> None:
    """T247 (merger #1343 finding 3): note_use's read-modify-write of
    memory-use.json had no lock, so two PostToolUse hooks racing on it could
    both read the same old dict and the later write clobber the earlier
    entry. a's write is paused mid-flight, still holding the lock, while b's
    whole call runs concurrently: without the lock b would read the file
    before a's entry lands and its own write would drop a's; with it, b's
    read cannot happen until a's write (and the lock) is released."""
    _daemon_home(home, 20)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    side = paths.agent_dir("backend") / "memory-use.json"
    a, b = str(mem / "a.md"), str(mem / "b.md")

    a_writing = threading.Event()
    let_a_finish = threading.Event()
    real_write = memoryuse.atomic.write_text

    def slow_write(path, text):
        a_writing.set()
        let_a_finish.wait(timeout=5)
        real_write(path, text)

    memoryuse.atomic.write_text = slow_write
    try:
        ta = threading.Thread(target=lambda: memoryuse.note_use(
            "backend", "Read", {"file_path": a}, now=1000))
        ta.start()
        assert a_writing.wait(timeout=5)
        tb = threading.Thread(target=lambda: memoryuse.note_use(
            "backend", "Read", {"file_path": b}, now=2000))
        tb.start()
        tb.join(timeout=0.2)
        assert tb.is_alive(), "b finished without ever blocking on a's lock - the test proved nothing"
        let_a_finish.set()
        ta.join(timeout=5)
        tb.join(timeout=5)
    finally:
        memoryuse.atomic.write_text = real_write

    check("lock: b's note is not dropped by a's concurrent write",
          json.loads(side.read_text()) == {"a.md": 1000, "b.md": 2000}, side.read_text())


def test_the_pre_tool_hook_is_installed_for_file_writes_only(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)
    added, _ = claude_hooks.install("/x/ocp", {"PreToolUse", "Stop"})
    groups = json.loads(claude_hooks.target_settings().read_text())["hooks"]
    check("install: PreToolUse is added, matched to the write tools",
          "PreToolUse" in added
          and groups["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit|NotebookEdit|Bash", str(groups))
    check("install: an event that fires on every call carries no matcher of ours",
          "matcher" not in groups["Stop"][0])
    added, pruned = claude_hooks.install("/x/ocp", {"PreToolUse", "Stop"})
    check("install: and again adds nothing", not added and not pruned, str((added, pruned)))


def test_install_reconciles_an_existing_groups_matcher(home: Path) -> None:
    """A machine installed before NotebookEdit joined MATCHERS still has the old
    matcher on disk. Re-running install must widen it, or NotebookEdit never
    reaches the edit gate (T295, auditor #1928)."""
    os.environ["OCP_HOME"] = str(home)
    target = claude_hooks.target_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    claude_hooks.write_settings(target, {"hooks": {"PreToolUse": [{
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [{"type": "command", "command": "/x/ocp hook PreToolUse", "timeout": 10}],
    }]}})
    added, pruned = claude_hooks.install("/x/ocp", {"PreToolUse"})
    groups = json.loads(target.read_text())["hooks"]
    check("install: an existing group's matcher is widened to match MATCHERS",
          groups["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit|NotebookEdit|Bash", str(groups))
    check("install: the existing group is kept, not added again",
          not added and not pruned, str((added, pruned)))


def test_the_handoff_and_the_wake_say_what_the_memory_needs(home: Path) -> None:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        mem = memorymod.memory_dir("backend")
        mem.mkdir(parents=True, exist_ok=True)
        (mem / "MEMORY.md").write_text(_index(3))
        for i in range(3):
            _topic("backend", f"e{i}.md")
        check("handoff: a memory in order adds nothing", handoffmod.memory_line("backend") == "")
        (mem / "MEMORY.md").write_text(_index(22, pad=40))
        _topic("backend", "a.md")
        _topic("backend", "b.md", age_days=60)
        line = handoffmod.memory_line("backend")
        check("handoff: over budget, unindexed and unused files are named, with what to do",
              "22/20 entries" in line and "over budget" in line
              and "not in the index (never loaded): " in line and "a.md" in line and "b.md" in line
              and "unused for 28+ days: b.md" in line and "merge, promote or delete" in line, line)
        text, _ = handoffmod.capture(con, "backend")
        check("handoff: the record carries it", line in text.splitlines(), text)
        woken = dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("wake: a fresh session with no handoff is told the same", line in woken, woken)
        (mem / "MEMORY.md").write_text(_index(3))
        for f in ("a.md", "b.md"):
            (mem / f).unlink()
        check("wake: nothing to tend, nothing said",
              "memory:" not in dmn_lifecycle.wake_prompt("backend", resumed=False, was_closed=True, con=con))
        (mem / "MEMORY.md").write_text(_index(30))
        with _Status():
            check("handoff: a memory that cannot be read is not worth a line", handoffmod.memory_line("backend") == "")
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_claude_driver_points_one_settings_object_at_the_agents_memory(Path(td) / 'm0'))
        run(lambda: test_launch_hides_every_other_agents_memory_and_creates_its_own(Path(td) / 'm1'))
        run(lambda: test_a_large_memory_index_write_is_accepted(Path(td) / 'm2'))
        run(lambda: test_the_pre_tool_hook_refuses_an_ocp_message_the_shell_would_run(Path(td) / 'm3s'))
        run(lambda: test_reading_or_writing_a_memory_file_is_noted_for_the_month_check(Path(td) / 'm4'))
        run(lambda: test_two_concurrent_hook_calls_do_not_drop_one_anothers_use(Path(td) / 'm4b'))
        run(lambda: test_the_pre_tool_hook_is_installed_for_file_writes_only(Path(td) / 'm5'))
        run(lambda: test_install_reconciles_an_existing_groups_matcher(Path(td) / 'm5b'))
        run(lambda: test_the_handoff_and_the_wake_say_what_the_memory_needs(Path(td) / 'm6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
