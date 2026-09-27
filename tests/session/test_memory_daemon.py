"""T230: an agent's memory is its own folder, and two hooks keep it small
(docs/memory-design.md), against core's real `ocp.team.memory`."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from ocp.session import handoff as handoffmod, launch as launchmod, memoryguard  # noqa: E402
from ocp.team import memory as memorymod  # noqa: E402
from ocp.vendors import claude_hooks  # noqa: E402
from ocp.vendors.claude import Claude  # noqa: E402
from ocp.vendors.opencode import Opencode  # noqa: E402


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
    """Run `ocp hook <event>` for `agent` and return what it printed."""
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
          settings == [{"autoMemoryDirectory": str(mem)}], str(argv))
    argv = drv.launch_argv("/x/claude", None, "high", "Agent", [], None, hide=["/a"], memory_dir=mem)
    settings = [json.loads(a) for i, a in enumerate(argv) if argv[i - 1] == "--settings"]
    check("memory: with hidden paths it is still ONE --settings object carrying both",
          len(settings) == 1 and settings[0]["autoMemoryDirectory"] == str(mem)
          and settings[0]["sandbox"]["filesystem"]["denyRead"] == ["/a"], str(argv))
    check("memory: no folder and nothing hidden adds no --settings",
          "--settings" not in drv.launch_argv("/x/claude", None, "high", "Agent", [], None))
    check("memory: a vendor with no auto-memory has no setting for it", Opencode().memory_settings(mem) == {})


def test_launch_hides_every_other_agents_memory_and_creates_its_own(home: Path) -> None:
    _daemon_home(home, 20)
    agents.register("frontend")
    check("hide: every other agent's memory folder, never its own",
          launchmod.hidden_memories("backend") == [str(paths.agent_dir("frontend") / "memory")],
          str(launchmod.hidden_memories("backend")))


def test_a_write_that_pushes_the_index_over_budget_is_refused_and_says_why(home: Path) -> None:
    _daemon_home(home, 20)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    index = mem / "MEMORY.md"
    _topic("backend", "old.md", age_days=40)
    _topic("backend", "older.md", age_days=90)
    _topic("backend", "fresh.md")
    index.write_text(_index(3))
    target = {"file_path": str(index)}
    bytes_over = _index(3, pad=800)
    why = memoryguard.guard("backend", "Write", {**target, "content": bytes_over})
    check("guard: a Write that grows the index past its bytes is refused, with the numbers",
          why and "over its budget" in why and "/2048 bytes" in why, str(why))
    check("guard: ...and names what has not been used for 28 days",
          why and "28+ days: old.md, older.md" in why, str(why))
    check("guard: more than 20 entries is refused too",
          memoryguard.guard("backend", "Write", {**target, "content": _index(21)}) is not None)
    check("guard: within the budget passes",
          memoryguard.guard("backend", "Write", {**target, "content": _index(5)}) is None)

    index.write_text(_index(25))
    check("guard: an index already over budget can still shrink",
          memoryguard.guard("backend", "Write", {**target, "content": _index(22)}) is None)
    check("guard: ...but not grow",
          memoryguard.guard("backend", "Write", {**target, "content": _index(26)}) is not None)
    index.write_text(_index(3, pad=900))
    check("guard: the same holds for bytes: shrinking while over passes, growing is refused",
          memoryguard.guard("backend", "Write", {**target, "content": _index(3, pad=800)}) is None
          and memoryguard.guard("backend", "Write", {**target, "content": _index(3, pad=1000)}) is not None)
    index.write_text(_index(25))
    line = "- [Entry 0](e0.md) - \n"
    check("guard: an Edit is judged on the index it would leave",
          memoryguard.guard("backend", "Edit", {**target, "old_string": line, "new_string": line + line}) is not None
          and memoryguard.guard("backend", "Edit", {**target, "old_string": line, "new_string": ""}) is None)
    check("guard: a MultiEdit is applied in order",
          memoryguard.guard("backend", "MultiEdit", {**target, "edits": [
              {"old_string": line, "new_string": ""}, {"old_string": "- [Entry 1]", "new_string": "- [One]"}]}) is None
          and memoryguard.guard("backend", "MultiEdit", {**target, "edits": [
              {"old_string": line, "new_string": line * 3}]}) is not None)
    check("guard: an edit that does not apply is left to the tool to fail",
          memoryguard.guard("backend", "Edit", {**target, "old_string": "nope", "new_string": "x" * 5000}) is None)

    check("guard: a topic file is not the index",
          memoryguard.guard("backend", "Write", {"file_path": str(mem / "topic.md"), "content": "x" * 9000}) is None)
    check("guard: nor is a path outside the folder, however it climbs out of it",
          memoryguard.guard("backend", "Write", {"file_path": str(mem / ".." / "MEMORY.md"),
                                                "content": _index(30)}) is None)
    check("guard: nor another tool", memoryguard.guard("backend", "Read", {**target}) is None
          and memoryguard.guard("backend", "Bash", {"command": "echo hi >> MEMORY.md"}) is None)


def test_the_pre_tool_hook_denies_through_claudes_own_format_and_never_breaks_a_session(home: Path) -> None:
    _daemon_home(home, 20)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text(_index(3))
    call = {"tool_name": "Write", "tool_input": {"file_path": str(mem / "MEMORY.md"), "content": _index(25)}}
    out = json.loads(_hook("PreToolUse", **call))
    deny = out["hookSpecificOutput"]
    check("hook: over budget prints a deny decision with the reason",
          deny["hookEventName"] == "PreToolUse" and deny["permissionDecision"] == "deny"
          and "over its budget" in deny["permissionDecisionReason"], str(out))
    check("hook: a call that fits prints nothing to act on",
          _hook("PreToolUse", tool_name="Write", tool_input={
              "file_path": str(mem / "MEMORY.md"), "content": _index(4)}) == "{}")
    with _Status():
        check("hook: a memory that cannot be read lets the call through", _hook("PreToolUse", **call) == "{}")
    check("hook: an event outside an agent's session is ignored",
          _hook("PreToolUse", agent="", **call) == "{}")


def test_reading_or_writing_a_memory_file_is_noted_for_the_month_check(home: Path) -> None:
    _daemon_home(home, 20)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    side = paths.agent_dir("backend") / "memory-use.json"
    topic = str(mem / "feedback" / "testing.md")

    memoryguard.note_use("backend", "Read", {"file_path": topic}, now=1000)
    check("use: a Read of a memory file is recorded under its path relative to the folder",
          json.loads(side.read_text()) == {"feedback/testing.md": 1000}, side.read_text())
    memoryguard.note_use("backend", "Read", {"file_path": topic}, now=1000 + 60)
    check("use: noted again within the hour changes nothing",
          json.loads(side.read_text()) == {"feedback/testing.md": 1000})
    memoryguard.note_use("backend", "Edit", {"file_path": topic}, now=1000 + memoryguard.RENOTE_S)
    check("use: later it is updated, by an Edit as well",
          json.loads(side.read_text()) == {"feedback/testing.md": 1000 + memoryguard.RENOTE_S})
    memoryguard.note_use("backend", "Read", {"file_path": str(mem / "MEMORY.md")}, now=5000)
    memoryguard.note_use("backend", "Read", {"file_path": str(home / "elsewhere.md")}, now=5000)
    memoryguard.note_use("backend", "Bash", {"file_path": topic}, now=9_000_000)
    check("use: the index itself, a file elsewhere and another tool are not a use",
          list(json.loads(side.read_text())) == ["feedback/testing.md"], side.read_text())

    side.unlink()
    out = _hook("PostToolUse", tool_name="Read", tool_input={"file_path": topic})
    check("use: the PostToolUse hook does it, and still only answers {}",
          out == "{}" and list(json.loads(side.read_text())) == ["feedback/testing.md"], out)


def test_the_pre_tool_hook_is_installed_for_file_writes_only(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)
    added, _ = claude_hooks.install("/x/ocp", {"PreToolUse", "Stop"})
    groups = json.loads(claude_hooks.target_settings().read_text())["hooks"]
    check("install: PreToolUse is added, matched to the write tools",
          "PreToolUse" in added and groups["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit", str(groups))
    check("install: an event that fires on every call carries no matcher of ours",
          "matcher" not in groups["Stop"][0])
    added, pruned = claude_hooks.install("/x/ocp", {"PreToolUse", "Stop"})
    check("install: and again adds nothing", not added and not pruned, str((added, pruned)))


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
        run(lambda: test_a_write_that_pushes_the_index_over_budget_is_refused_and_says_why(Path(td) / 'm2'))
        run(lambda: test_the_pre_tool_hook_denies_through_claudes_own_format_and_never_breaks_a_session(Path(td) / 'm3'))
        run(lambda: test_reading_or_writing_a_memory_file_is_noted_for_the_month_check(Path(td) / 'm4'))
        run(lambda: test_the_pre_tool_hook_is_installed_for_file_writes_only(Path(td) / 'm5'))
        run(lambda: test_the_handoff_and_the_wake_say_what_the_memory_needs(Path(td) / 'm6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
