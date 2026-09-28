"""The edit gate (T295, auditor #1655/#1657): refuses Edit/Write/MultiEdit/
NotebookEdit while the agent holds no current task - a say (T293) grants
none, so it can never carry a change.

Warn-only until the founder switches it on (T298), the same protected-store
mechanism as T288's stop-wait gate (`ocp hooks gate edit-task on|off`).
"""
from __future__ import annotations

import os

from ..team import memory
from ..infra import protected
from .. import db

_NS = "gates"
_NAME = "edit_task"

GUARDED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def enforced() -> bool:
    """Whether holding no current task actually refuses the call, or only logs one."""
    return protected.get(_NS, _NAME) == "on"


def set_enforced(on: bool) -> None:
    """Founder-only - `protected.put` itself refuses an agent session."""
    protected.put(_NS, _NAME, "on" if on else "off")


def _path(tool_input: object) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    p = tool_input.get("file_path") or tool_input.get("notebook_path")
    return p if isinstance(p, str) and p else None


def _plans_dir() -> str | None:
    """$CLAUDE_CONFIG_DIR/plans - where recent Claude Code versions save a
    plan-mode plan (orcapipe #1917). Nothing else named 'plan' is exempt: a
    free-standing name match would be a way to write real files around the
    gate."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(cfg, "plans") if cfg else None


def _exempt(agent: str, tool_input: object) -> bool:
    """Memory, $TMPDIR and the plan-mode plan folder are working files,
    never the deliverable itself."""
    path = _path(tool_input)
    if path is None:
        return False
    real = os.path.realpath(path)
    for root in (memory.memory_dir(agent), os.environ.get("TMPDIR"), _plans_dir()):
        if not root:
            continue
        root = os.path.realpath(root)
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def guard(agent: str, tool: str | None, tool_input: object) -> str | None:
    """The reason to refuse this call, or None to let it through - None
    whenever it is not enforced() yet, even for a real hit (T298)."""
    if tool not in GUARDED_TOOLS or _exempt(agent, tool_input):
        return None
    con = db.connect()
    try:
        if db.current_task(con, agent) is not None:
            return None
        on = enforced()
        db.add_event(con, agent, "edit_gate", {"tool": tool, "enforced": on})
        if not on:
            return None
        return (f"{agent} holds no current task, so this {tool} is refused (T295). "
                f"`ocp task start T<n>` if you already hold one, otherwise whoever "
                f"assigned this work opens one for you.")
    finally:
        con.close()
