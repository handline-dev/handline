"""An agent's own memory folder, seen from two of its hooks (docs/memory-design.md).

`guard` runs before a Write or Edit and refuses one that would push the memory
index over its budget; `note_use` runs after a tool call and records which memory
files the agent has read or written, for `ocp memory status` to find the ones it
has not touched for a month. Neither calls a model, and neither is reached by a
Bash write: a `>>` onto MEMORY.md is not a tool call this hook sees.

The folder and the budget are core's contract (`team/memory.py`); the sidecar
`agents/<name>/memory-use.json` ({path relative to the folder: unix ts}) is this
module's to write and `ocp memory status` alone reads.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..infra import atomic, paths

INDEX = "MEMORY.md"
GUARDED_TOOLS = ("Write", "Edit", "MultiEdit")
USE_TOOLS = GUARDED_TOOLS + ("Read",)
# A file already noted this recently is not rewritten: a day's granularity is
# all the 28-day rule needs, and every tool call runs this.
RENOTE_S = 3600


def memory_dir(agent: str) -> Path:
    return paths.agent_dir(agent) / "memory"


def _relative(agent: str, tool_input: object) -> str | None:
    """The path a tool call names, relative to this agent's memory folder, or
    None when it names something else."""
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path:
        return None
    root = os.path.realpath(memory_dir(agent))
    target = os.path.realpath(path)
    return os.path.relpath(target, root) if target.startswith(root + os.sep) else None


def _sidecar(agent: str) -> Path:
    return paths.agent_dir(agent) / "memory-use.json"


def note_use(agent: str, tool: str | None, tool_input: object, now: int | None = None) -> None:
    """Record that the agent touched a memory file - the index itself is not a use."""
    if tool not in USE_TOOLS:
        return
    rel = _relative(agent, tool_input)
    if rel is None or rel == INDEX:
        return
    now = int(time.time()) if now is None else now
    f = _sidecar(agent)
    try:
        used = json.loads(f.read_text())
    except (OSError, ValueError):
        used = {}
    if not isinstance(used, dict):
        used = {}
    if rel in used and now - int(used[rel] or 0) < RENOTE_S:
        return
    used[rel] = now
    atomic.write_text(f, json.dumps(used, sort_keys=True, indent=1) + "\n")


def _entries(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.startswith("- ["))


def _would_be(current: str, tool: str, tool_input: dict) -> str | None:
    """The index as this call would leave it, or None when that cannot be
    worked out (the call is malformed, or its edit does not apply - the tool
    will fail it itself)."""
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    text = current
    for e in edits if isinstance(edits, list) else []:
        old, new = (e.get("old_string"), e.get("new_string")) if isinstance(e, dict) else (None, None)
        if not isinstance(old, str) or not isinstance(new, str) or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text if edits else None


def guard(agent: str, tool: str | None, tool_input: object) -> str | None:
    """The reason to refuse this tool call, or None to let it through.

    Only a call on the memory index is looked at, and only one that leaves it
    over its byte or entry budget in a dimension where it also grew: a call that
    shrinks the index passes even while it is over, since that is how it gets
    back under."""
    from ..team import memory  # core's: the budget and the agent's status
    if tool not in GUARDED_TOOLS or not isinstance(tool_input, dict) \
            or _relative(agent, tool_input) != INDEX:
        return None
    try:
        current = (memory_dir(agent) / INDEX).read_text()
    except OSError:
        current = ""
    result = _would_be(current, tool, tool_input)
    if result is None:
        return None
    was_b, now_b = len(current.encode()), len(result.encode())
    was_n, now_n = _entries(current), _entries(result)
    if not ((now_b > memory.INDEX_BYTES and now_b > was_b)
            or (now_n > memory.INDEX_ENTRIES and now_n > was_n)):
        return None
    st = memory.status(agent)
    why = (f"Your memory index would be {now_b}/{memory.INDEX_BYTES} bytes and "
           f"{now_n}/{memory.INDEX_ENTRIES} entries, over its budget, so this write is refused. "
           f"Merge or delete entries first; an edit that shrinks the index is allowed.")
    stale = list(st.get("stale") or [])
    if stale:
        why += f" Not used for {memory.STALE_DAYS}+ days: {', '.join(stale[:8])}."
    return why
