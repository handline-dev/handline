"""An agent stopped at a permission prompt only the founder can answer.

Such an agent emits no Stop, so its state stays RUNNING and nothing else says a
person is wanted. The hook that sees the prompt writes one small file per agent;
the hooks that see it answered take it away. A file, not a row: the clearing
hook is PostToolUse, which fires on every tool call, so what it does when
nothing is waiting has to be one `stat` and no database.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths

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


def note(agent: str, tool: str | None, tool_input: object) -> None:
    """The agent is at a permission prompt for this tool call."""
    f = _file(agent)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tool": tool or "", "command": summary(tool_input),
                               "since": int(time.time())}))
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


def of(row) -> dict | None:
    """The wait to show for an agent row, or None.

    Only a session that is still running is waiting: a closed one, or one the
    daemon has since moved out of RUNNING, is not at that prompt any more
    whatever the file says.
    """
    if not row or row["closed_at"] or row["state"] != "RUNNING":
        return None
    return read(row["agent"])
