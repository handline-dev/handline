"""An agent's own memory folder, seen from its PostToolUse hook (docs/memory-design.md).

`note_use` runs after a tool call and records which memory files the agent has
read or written, for `hl memory status` to find the ones it has not touched for
a month. It calls no model. Nothing refuses a write to the memory index.

The folder and the sidecar `agents/<name>/memory-use.json` ({path relative to
the folder: unix ts}) are core's (`team/memory.py`); this module writes the
sidecar and `hl memory status` alone reads it.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time

from ..infra import atomic
from ..team import memory

USE_TOOLS = ("Write", "Edit", "MultiEdit", "Read")
# A file already noted this recently is not rewritten: a day's granularity is
# all the 28-day rule needs, and every tool call runs this.
RENOTE_S = 24 * 3600


def _relative(agent: str, tool_input: object) -> str | None:
    """The path a tool call names, relative to this agent's memory folder, or
    None when it names something else."""
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path:
        return None
    root = os.path.realpath(memory.memory_dir(agent))
    target = os.path.realpath(path)
    return os.path.relpath(target, root) if target.startswith(root + os.sep) else None


@contextlib.contextmanager
def _use_file_lock(f):
    """Held across the read-modify-write below: two PostToolUse hooks from
    calls in the same turn can run concurrently, and without this the second
    to write clobbers the first's timestamp with its own read, dropping it
    (T247, merger #1343 finding 3). Unlike infra/lock.py's holds this one
    waits - the file is tiny and the hold is brief, and giving up would drop
    the update anyway, the very bug this exists to fix."""
    try:
        fd = os.open(str(f) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield  # no lock is the pre-existing race, not a new failure
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the flock too


def note_use(agent: str, tool: str | None, tool_input: object, now: int | None = None) -> None:
    """Record that the agent touched a memory file - the index itself is not a use."""
    if tool not in USE_TOOLS:
        return
    rel = _relative(agent, tool_input)
    if rel is None or rel == memory.INDEX:
        return
    now = int(time.time()) if now is None else now
    f = memory.use_file(agent)
    with _use_file_lock(f):
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
