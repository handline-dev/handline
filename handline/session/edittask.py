"""The edit gate (T295, auditor #1655/#1657): refuses Edit/Write/MultiEdit/
NotebookEdit while the agent holds no current task - a say (T293) grants
none, so it can never carry a change.

On unless the founder turns it off (T298, T480), the same protected-store
mechanism as T288's stop-wait gate (`ocp hooks gate edit-task on|off`).
"""
from __future__ import annotations

import os

from ..team import memory
from ..infra import paths, protected
from .. import db

_NS = "gates"
_NAME = "edit_task"

GUARDED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def enforced() -> bool:
    """Whether holding no current task refuses the call; only the founder's `off` makes it log."""
    return not protected.is_off(_NS, _NAME)


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
    """Memory, $TMPDIR, the plan-mode plan folder and the agent's own handoff note
    (T571: the ask comes to an idle agent, which holds no task) are working files,
    never the deliverable itself. The note is that one file, not its folder, and not
    through a link."""
    path = _path(tool_input)
    if path is None:
        return False
    real = os.path.realpath(path)
    note = paths.handoff_note_file(agent)
    if real == os.path.realpath(note) and not os.path.islink(path) and not note.is_symlink():
        return True
    for root in (memory.memory_dir(agent), os.environ.get("TMPDIR"), _plans_dir()):
        if not root:
            continue
        root = os.path.realpath(root)
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def _refusal(con, agent: str, tool: str) -> str:
    """Why the call is refused and the one command that fixes it: start the task the agent
    owns if it owns one, else get one opened."""
    mine = [t for t in db.open_tasks(con)
            if t["owner"] == agent and not t["done_evidence"]]
    head = f"{tool} refused: {agent} holds no current task, and every change must belong to one (T295)."
    if mine:
        t = mine[0]
        more = f" (or another of yours: {', '.join('T%d' % m['id'] for m in mine[1:4])})" if len(mine) > 1 else ""
        return f"{head} Run `ocp task start T{t['id']}` to make your task \"{t['title'][:60]}\" current{more}, then retry."
    return (f"{head} You own no open task. If the founder just told you to do this, run "
            f"`ocp task begin \"<title>\"` to open one; otherwise ask whoever gave you the work: "
            f"`ocp say <them> \"please open a task for <this work>\"`.")


def guard(agent: str, tool: str | None, tool_input: object) -> str | None:
    """The reason to refuse this call, or None to let it through - None
    whenever the founder has switched it off, even for a real hit."""
    if agent == db.HUMAN or tool not in GUARDED_TOOLS or _exempt(agent, tool_input):
        return None
    con = db.connect()
    try:
        if db.current_task(con, agent) is not None:
            return None
        on = enforced()
        db.add_event(con, agent, "edit_gate", {"tool": tool, "enforced": on})
        if not on:
            return None
        return _refusal(con, agent, tool)
    finally:
        con.close()
