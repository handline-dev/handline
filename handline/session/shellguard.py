"""A Bash call whose double-quoted ocp argument would run part of the message.

A backtick or `$(` inside double quotes is command substitution: the shell runs
the text, Claude Code stops the agent on an approval prompt only the human can
answer, and approving would send a garbled message anyway. `guard` refuses such
an `ocp ...` call up front, with the fix in the reason, so the agent resends it
by itself. The command is never rewritten.
"""
from __future__ import annotations

import re

_OCP = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?ocp\s")
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")

REASON = ("Refused: nothing in this command ran, including any heredoc or file write in it. "
          "A backtick or $( inside double quotes is shell substitution. Resend the body in "
          "single quotes, or written to a file first in a separate step and passed with "
          "--file PATH (ocp say, ocp task new, ocp task handoff, ocp task memo add), or on "
          "stdin with - through a quoted heredoc (<<'EOF').")


def _expands(text: str) -> bool:
    """Does this text, read as the inside of double quotes, hold a substitution?"""
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "`" or text.startswith("$(", i):
            return True
        i += 1
    return False


def _heredoc(command: str, start: int, dash: bool, word: str) -> tuple[str, int]:
    """(body, index of the newline ending it) for a heredoc whose body starts at `start`."""
    lines, pos = [], start
    while pos < len(command):
        stop = command.find("\n", pos)
        stop = len(command) if stop < 0 else stop
        line = command[pos:stop]
        if (line.lstrip("\t") if dash else line) == word:
            return "\n".join(lines), stop
        lines.append(line)
        pos = stop + 1
    return "\n".join(lines), len(command)


def substitutes(command: str) -> bool:
    """Would the shell substitute inside a double-quoted string or an unquoted
    heredoc body of this command?"""
    i, n, pending = 0, len(command), []
    quote = ""
    while i < n:
        c = command[i]
        if quote == "'":
            quote = "" if c == "'" else quote
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = ""
            elif c == "`" or command.startswith("$(", i):
                return True
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "\n" and pending:
            for dash, quoted, word in pending:
                body, i = _heredoc(command, i + 1, dash, word)
                if not quoted and _expands(body):
                    return True
            pending = []
        elif command.startswith("<<", i) and not command.startswith("<<<", i):
            m = _HEREDOC.match(command, i)
            if m:
                pending.append((bool(m.group(1)), bool(m.group(2)), m.group(3)))
                i = m.end() - 1
        i += 1
    return False


def guard(tool: str | None, tool_input: object) -> str | None:
    """The reason to refuse this tool call, or None to let it through."""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool != "Bash" or not isinstance(command, str) or not _OCP.search(command):
        return None
    return REASON if substitutes(command) else None
