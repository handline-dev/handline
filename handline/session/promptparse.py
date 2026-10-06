"""What an open prompt says about itself, read from its screen text and nothing else: the tool, command and reason of a permission
prompt, and which dialog an unnumbered-title dialog is. A field the screen does not state is None, never a guess; a prompt this
cannot read is still listed, with its raw lines.

Measured on the captures in contract/prompts/: a permission prompt's title names its tool ("Bash command"), the command follows,
boxed with "│" or bare, then a description; a boxed run after the description is the reason the call is not allowed outright. Only a
title seen in a capture is mapped to a tool, so an edit approval, whose title was never captured, has no tool and is returned raw."""
from __future__ import annotations

TOOLS = {"Bash command": "Bash"}
BOX = "│"
EXIT_OPTIONS = ("Exit and stop tasks", "Exit now")


def _unbox(line: str) -> str:
    return line.strip().removeprefix(BOX).strip()


def permission_fields(title: str, lines) -> dict:
    """{tool, command, reason} of a permission prompt with this `title` and the `lines` between it and its question."""
    lines = [ln for ln in lines if ln.strip()]
    boxed = [ln.strip().startswith(BOX) for ln in lines]
    command = reason = None
    if lines and boxed[0]:
        n = 0
        while n < len(lines) and boxed[n]:
            n += 1
        command = "\n".join(_unbox(ln) for ln in lines[:n])
        rest = boxed[n:]
        if len(rest) >= 2 and not rest[0] and rest[-1]:
            m = len(lines) - 1
            while m > n and boxed[m - 1]:
                m -= 1
            reason = "\n".join(_unbox(ln) for ln in lines[m:])
    elif lines:
        command = lines[0].strip()
    return {"tool": TOOLS.get(title.strip()), "command": command or None, "reason": reason or None}


def dialog_kind(options) -> str:
    """`exit_confirm` for the exit confirmation (an option that exits, measured as "Exit and stop tasks" and "Exit now"), else `dialog`."""
    labels = [str(label).strip() for _, label in options]
    return "exit_confirm" if any(lb in EXIT_OPTIONS for lb in labels) else "dialog"
