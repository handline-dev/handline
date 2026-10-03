"""Say: the look-up rules for `ocp say` (T293, auditor #1655/#1657). The verb itself,
`team.ask.say`, lives with `ask`; a say needs no answer, so nothing here is worded
for one - only the look-up test a daemon pass uses to flag a say that was work.
"""
from __future__ import annotations

import re

# T293/#1657: the read-only Bash commands a say may be answered from without
# counting as work - shared with the daemon's own tool-tracking and this
# module's tests. No "git branch": its write forms (-d/-D/-m/-c) share the
# same prefix as its listing form.
READONLY_BASH_ALLOWLIST = (
    "git show", "git log", "git diff", "git status",
    "ls", "cat", "head",
    "hl read", "hl inbox", "hl task list", "hl team", "hl task board",
)

# A shell operator anywhere in the command means it is not just the listed
# command running - a pipe, redirect or chain can turn a look-up into a
# write (`cat > x`, `git log | sh`, `head -1 a && make`).
_SHELL_OPERATORS = re.compile(r"[;&|<>`\n]|\$\(")

# #1657: up to this many look-ups (Read/Grep/Glob, or the allowlist above)
# in the reply window is still a say; past it, the daemon flags it whatever
# the tools were - a long look-up session usually means the question was
# bigger than it read.
LOOKUP_BUDGET = 5


def is_lookup_bash(command: str) -> bool:
    """Is this Bash `command` a look-up (`READONLY_BASH_ALLOWLIST`), not
    work? A shell operator refuses it outright (auditor #1742: a pipe or
    redirect turns a look-up into a write); otherwise matched by prefix -
    `git log --oneline` is still `git log`, an unfamiliar command is not."""
    said = command.strip()
    if _SHELL_OPERATORS.search(said):
        return False
    return any(said == cmd or said.startswith(cmd + " ") for cmd in READONLY_BASH_ALLOWLIST)


__all__ = ["READONLY_BASH_ALLOWLIST", "LOOKUP_BUDGET", "is_lookup_bash"]
