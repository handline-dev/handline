"""Tests: the edit gate (T295) - edittask.guard, its exemptions, the
founder-only enforce switch, and its PreToolUse wiring.

Run on its own: python3 tests/session/test_edittask.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.session import edittask, stopgate  # noqa: E402
from ocp.team import memory as memorymod  # noqa: E402
from ocp.infra import protected  # noqa: E402


def _hook(event: str, agent: str = "backend", **fields) -> str:
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


def test_no_current_task_is_a_reason_only_once_enforced(home: Path) -> None:
    _daemon_home(home, 20)
    call = {"file_path": "/repo/some/file.py"}
    check("warn-only by default: guard lets it through",
          edittask.guard("backend", "Write", call) is None)
    con = db.connect()
    try:
        rows = con.execute("SELECT payload FROM events WHERE agent = 'backend' "
                           "AND kind = 'edit_gate'").fetchall()
        check("but logs one edit_gate event, enforced=false",
              len(rows) == 1 and json.loads(rows[0]["payload"])["enforced"] is False,
              [dict(r) for r in rows])
    finally:
        con.close()

    edittask.set_enforced(True)
    why = edittask.guard("backend", "Edit", call)
    check("enforced: now a real refusal, naming the agent and the tool",
          why is not None and "backend" in why and "Edit" in why, why)
    check("T401 item 19: it says to ask whoever gave the work, not just to wait",
          "ask whoever gave you this work" in why and "ocp say" in why, why)

    ok, msg, tid = actions.task_new("product", "backend", "do the thing")
    check("setup: backend now holds a current task", ok, msg)
    check("holding a current task: never refused, enforced or not",
          edittask.guard("backend", "Write", call) is None)


def test_only_the_guarded_tools_are_looked_at(home: Path) -> None:
    _daemon_home(home, 20)
    edittask.set_enforced(True)
    call = {"file_path": "/repo/some/file.py"}
    check("Read is not a write", edittask.guard("backend", "Read", call) is None)
    check("Bash is out of scope (accepted gap - the merge gate covers commits)",
          edittask.guard("backend", "Bash", {"command": "rm -rf /"}) is None)
    check("a NotebookEdit is guarded, by notebook_path",
          edittask.guard("backend", "NotebookEdit",
                         {"notebook_path": "/repo/nb.ipynb"}) is not None)


def test_memory_and_tmpdir_are_exempt(home: Path) -> None:
    _daemon_home(home, 20)
    edittask.set_enforced(True)
    mem = memorymod.memory_dir("backend")
    mem.mkdir(parents=True, exist_ok=True)
    check("a write inside the agent's own memory folder is exempt",
          edittask.guard("backend", "Write", {"file_path": str(mem / "notes.md")}) is None)

    tmp = os.environ.get("TMPDIR")
    check("setup: TMPDIR is set for this sandboxed run", tmp)
    scratch = Path(tmp) / "edittask-test-scratch.txt"
    check("a write under $TMPDIR is exempt",
          edittask.guard("backend", "Write", {"file_path": str(scratch)}) is None)

    check("a write elsewhere is still guarded",
          edittask.guard("backend", "Write", {"file_path": "/repo/real/file.py"}) is not None)


def test_the_plan_mode_folder_is_exempt_but_no_name_match_elsewhere(home: Path) -> None:
    _daemon_home(home, 20)
    edittask.set_enforced(True)
    cfg = Path(home) / "cfgdir"
    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
    try:
        plans = cfg / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        check("a write under $CLAUDE_CONFIG_DIR/plans is exempt",
              edittask.guard("backend", "Write", {"file_path": str(plans / "plan.md")}) is None)
        check("a file elsewhere just named plan.md is not",
              edittask.guard("backend", "Write", {"file_path": "/repo/plan.md"}) is not None)
    finally:
        del os.environ["CLAUDE_CONFIG_DIR"]
    check("with no CLAUDE_CONFIG_DIR set, nothing matches and nothing breaks",
          edittask.guard("backend", "Write", {"file_path": "/repo/plan.md"}) is not None)


def test_the_edit_gate_enforce_switch_is_the_founders_to_flip(home: Path) -> None:
    _daemon_home(home, 20)
    check("off by default", edittask.enforced() is False)
    os.environ["OCP_AGENT"] = "backend"
    try:
        edittask.set_enforced(True)
        check("never set", False)
    except protected.NotTheFounder:
        pass
    finally:
        del os.environ["OCP_AGENT"]
    rc, out = _cli(["hooks", "gate", "edit-task"], as_agent=None)
    check("founder: show", rc == 0 and "off (warn-only)" in out, out)
    rc, out = _cli(["hooks", "gate", "edit-task", "on"], as_agent=None)
    check("founder: set on", rc == 0 and "edit-task gate: on" in out, out)
    check("enforced() now reads True", edittask.enforced() is True)
    check("the stop-wait gate is untouched by the edit-task one",
          stopgate.enforced() is False)


def test_record_from_stdin_denies_through_claudes_own_format(home: Path) -> None:
    _daemon_home(home, 20)
    edittask.set_enforced(True)
    call = {"tool_name": "Write", "tool_input": {"file_path": "/repo/real/file.py"}}
    out = json.loads(_hook("PreToolUse", **call))
    deny = out["hookSpecificOutput"]
    check("hook: no current task prints a deny decision with the reason",
          deny["hookEventName"] == "PreToolUse" and deny["permissionDecision"] == "deny"
          and "no current task" in deny["permissionDecisionReason"], str(out))
    ok, msg, tid = actions.task_new("product", "backend", "do the thing")
    check("setup: backend picks up a task", ok, msg)
    check("hook: holding one, the same call prints nothing to act on",
          _hook("PreToolUse", **call) == "{}")
    check("hook: an event outside an agent's session is ignored",
          _hook("PreToolUse", agent="", **call) == "{}")


def main() -> int:
    def tests(td):
        run(lambda: test_no_current_task_is_a_reason_only_once_enforced(Path(td) / 'et0'))
        run(lambda: test_only_the_guarded_tools_are_looked_at(Path(td) / 'et1'))
        run(lambda: test_memory_and_tmpdir_are_exempt(Path(td) / 'et2'))
        run(lambda: test_the_plan_mode_folder_is_exempt_but_no_name_match_elsewhere(Path(td) / 'et2b'))
        run(lambda: test_the_edit_gate_enforce_switch_is_the_founders_to_flip(Path(td) / 'et3'))
        run(lambda: test_record_from_stdin_denies_through_claudes_own_format(Path(td) / 'et4'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
