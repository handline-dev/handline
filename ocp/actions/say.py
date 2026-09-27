"""Say: the shape rules and delivery text for `ocp say` (T293, auditor
#1655/#1657). The verb itself, `team.ask.say`, lives with `ask` since
delivery is `ask`'s job; what belongs here is what is not about routing or
delivery - the wording shown to both sides, and the look-up test a future
daemon pass uses to flag a say that should have been a task.
"""
from __future__ import annotations

import shlex

from ..infra import textsafe
from .requests import first_line

# T293/#1657: a say is answered from a look-up, never from work. These are
# the read-only Bash commands that count as a look-up rather than work - kept
# here, not in the daemon, so the daemon's own tool-tracking and this
# module's tests read the same list. An unlisted Bash command counts as
# work: a false positive there costs nothing but a notice, where missing a
# real one would hide real work as a say.
READONLY_BASH_ALLOWLIST = (
    "git show", "git log", "git diff", "git status", "git branch",
    "ls", "cat", "head",
    "ocp read", "ocp inbox", "ocp task list", "ocp team", "ocp task board",
)

# #1657: up to this many look-ups (Read/Grep/Glob, or the allowlist above)
# in the reply window is still a say; past it, the daemon flags it whatever
# the tools were - a long look-up session usually means the question was
# bigger than it read.
LOOKUP_BUDGET = 5


def is_lookup_bash(command: str) -> bool:
    """Is this Bash `command` a look-up (`READONLY_BASH_ALLOWLIST`), not
    work? Matched by prefix - `git log --oneline` is still `git log` - so an
    unfamiliar flag on a listed command is still a look-up; an unfamiliar
    command is not."""
    said = command.strip()
    return any(said == cmd or said.startswith(cmd + " ") for cmd in READONLY_BASH_ALLOWLIST)


def delivery_line(rid: int) -> str:
    """T293/#1657: shown with every say, so its receiver has its own way out
    before the daemon ever has to flag anything. `ocp say` itself prints
    this to the sender, and `ocp read` prints it again for an unanswered
    say's receiver."""
    return ("answerable from what you know or a quick look? reply with `ocp say`. "
            f"needs a change, check, run or investigation? `ocp say --needs-task {rid}`")


def needs_task_command(owner: str, body: str) -> str:
    """The ready-made `task new` command `say --needs-task` hands back to the
    original sender - #1657's "the one who wants the work opens the task",
    with the say's own text as the title so nothing is retyped. `shlex.quote`
    so a title with its own quote or apostrophe still pastes as one shell
    word, not a syntax error."""
    title = textsafe.oneline(first_line(body))
    return f"ocp task new --owner {owner} {shlex.quote(title)}"


def flag_notice(rid: int, command: str) -> str:
    """#1657: what a sender is told when a say it sent should have been a
    task - a `say --needs-task` reply and, later, the daemon's own flag both
    end here so the two read the same. Never a refusal, never undoes the
    answer already given."""
    return f"#{rid} should have been a task - open one: `{command}`"


def needs_task_refusal(row, me: str) -> str | None:
    """Why `me` may not `say --needs-task` on this row, or None. Only its
    receiver decides a say was really work - the sender already made that
    call once by not opening a task, and the row must still be a live say,
    not something else read back through the wrong id."""
    if row is None:
        return "no such request"
    if row["kind"] != "say":
        return f"#{row['id']} is not a say - --needs-task only replies to one"
    if row["target"] != me:
        return f"#{row['id']} was not sent to you - only its receiver may flag it"
    return None


__all__ = [
    "READONLY_BASH_ALLOWLIST", "LOOKUP_BUDGET", "is_lookup_bash", "delivery_line",
    "needs_task_command", "flag_notice", "needs_task_refusal",
]
