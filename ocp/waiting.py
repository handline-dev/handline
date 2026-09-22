"""An agent stopped at a permission prompt only the founder can answer.

Such an agent emits no Stop, so its state stays RUNNING and nothing else says a
person is wanted. The hook that sees the prompt writes one small file per agent;
the hooks that see it answered take it away. A file, not a row: the clearing
hook is PostToolUse, which fires on every tool call, so what it does when
nothing is waiting has to be one `stat` and no database. That only clears the
one tool call it names, so `of()` below also asks the database, once, at
display time, for the case that leaves - a prompt answered without ever
firing its own PostToolUse.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import db, paths

# What of a tool's input is worth showing, in the order it is looked for.
_INPUT_FIELDS = ("command", "file_path", "path", "pattern", "url", "query", "description")
SHOWN_CHARS = 120


def _file(agent: str) -> Path:
    return paths.home() / "waiting" / f"{agent}.json"


def summary(tool_input: object) -> str:
    """The one line that says what the tool was asked to do, control characters
    flattened so it cannot draw over the screen it is shown on."""
    text = ""
    if isinstance(tool_input, dict):
        for k in _INPUT_FIELDS:
            v = tool_input.get(k)
            if isinstance(v, str) and v.strip():
                text = v
                break
        else:
            text = json.dumps(tool_input, sort_keys=True, default=str)
    elif tool_input is not None:
        text = str(tool_input)
    return "".join(c if c.isprintable() else " " for c in text)[:SHOWN_CHARS]


def note(agent: str, tool: str | None, tool_input: object,
         after_event_id: int | None = None) -> None:
    """The agent is at a permission prompt for this tool call.

    `after_event_id` is that PermissionRequest's own row id in `events` -
    what `of()` later asks whether anything has happened after, since a
    second-resolution clock ties too often to order two calls a real agent
    can make moments apart (see `of()`).
    """
    f = _file(agent)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tool": tool or "", "command": summary(tool_input),
                               "since": int(time.time()), "after_event_id": after_event_id}))
    tmp.replace(f)


def read(agent: str) -> dict | None:
    try:
        data = json.loads(_file(agent).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("since") else None


def clear(agent: str, tool: str | None = None, tool_input: object = None) -> None:
    """The prompt is answered. With a tool call given, only that call's prompt:
    another tool finishing while this one still waits is not an answer."""
    f = _file(agent)
    if not f.exists():
        return
    if tool is not None:
        held = read(agent)
        if held and (held.get("tool") != tool or held.get("command") != summary(tool_input)):
            return
    try:
        os.unlink(f)
    except OSError:
        pass


def of(con, row) -> dict | None:
    """The wait to show for an agent row, or None.

    Only a session that is still running is waiting: a closed one, or one the
    daemon has since moved out of RUNNING, is not at that prompt any more
    whatever the file says.

    clear() only unlinks the file for the exact tool call that raised it -
    right when a denial or an interruption means that call never fires its own
    PostToolUse, so nothing else ever clears it. A mismatched tool call is not
    evidence the prompt is over: a sibling dispatched in the same batch
    finishes the same way while the noted one is still genuinely pending, and
    PostToolUse is deliberately never written to `events` (see events.py) for
    exactly that noise. So this asks a question no sibling tool call can
    answer instead: has the agent's *own* recorded activity - a captured hook
    event, or an `ocp` verb it ran itself - moved past the prompt that raised
    this wait? Every kind `last_activity_id` counts can only be produced by
    the agent's own turn continuing; none of them is a side effect of some
    other tool call finishing. Finding one proves the turn that hit this
    prompt has already gone on - through however it went on - so the wait is
    stale and is cleared here, not left for the next matching PostToolUse that
    may never come. PermissionRequest itself must be excluded, or the very
    event that raised this wait would always satisfy "something happened after
    it" - which is also why the comparison is by `events.id`, not `since`: a
    whole-second clock ties too easily against activity a real agent produces
    moments later. A wait noted before this field existed has no id to compare
    and is left showing rather than guessed at.
    """
    if not row or row["closed_at"] or row["state"] != "RUNNING":
        return None
    w = read(row["agent"])
    if not w:
        return None
    eid = w.get("after_event_id")
    if eid is not None:
        moved_on = db.last_activity_id(con, row["agent"], exclude=frozenset({"PermissionRequest"}))
        if moved_on is not None and moved_on > eid:
            clear(row["agent"])
            return None
    return w
