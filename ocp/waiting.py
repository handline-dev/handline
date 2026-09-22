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

from . import db, orca, paths

# What of a tool's input is worth showing, in the order it is looked for.
_INPUT_FIELDS = ("command", "file_path", "path", "pattern", "url", "query", "description")
SHOWN_CHARS = 120

# What proves the agent's own turn continued past the prompt that raised a
# wait - not merely that some other tool call finished. Deliberately narrower
# than db.AGENT_ACTIVITY_KINDS: SubagentStop looks the same shape as this
# module already refuses to trust PostToolUse for - the Agent tool can
# dispatch several subagents in one batch, and one of them finishing while a
# sibling is still stuck at a permission prompt fires SubagentStop the same
# way an ordinary sibling tool call fires PostToolUse (auditor #1654
# SHOULD-FIX 1, measured: 15 same-agent SubagentStop pairs under two seconds
# apart in the live db, which is what parallel subagents look like).
# TeammateIdle is not this agent's own turn either - it is another agent's
# status reaching this session (auditor #1654 NOTE 5). What is left is
# unimpeachable: an `ocp` verb only runs once the tool call that ran it has
# itself returned, and PostCompact is this session's own transcript growing,
# neither a side effect an unrelated tool call could produce.
MOVED_ON_KINDS = frozenset({
    "ask", "cancel", "commit", "escalate", "report", "resolve", "withdraw",
    "no_task", "PostCompact",
})


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


def _modal_still_there(handle: str | None, since: int) -> bool:
    """Is the terminal's own screen still showing a dialog - the authoritative
    check, since events can be entirely silent for as long as an agent is
    mid-turn (PostToolUse is deliberately never recorded, #1698: a founder
    incident where nothing at all was written for 26 minutes).

    Fail-closed, the same direction screen_blocker() itself already fails in:
    no terminal, or a screen that cannot be read right now, reads as the
    dialog is still there. Hiding a wait that is real is worse than showing
    one that is not - the founder's own words for the bug this answers.
    BACKGROUNDED (the session picker showing instead) and None (a plain
    composer) both mean no dialog is on screen; only MODAL and UNREADABLE
    keep the wait up.

    `since` (the wait's own timestamp) is passed to the cache as
    `not_before`: a read taken before this wait was even noted cannot prove
    it is gone (auditor #1721 BLOCKING - a cached "no dialog" from prompt A
    answered prompt B, which had not been raised yet when that read happened).
    """
    if not handle:
        return True
    blocker = orca.screen_blocker_cached(handle, not_before=since)
    return bool(blocker and blocker.kind in (orca.MODAL, orca.UNREADABLE))


def of(con, row) -> dict | None:
    """The wait to show for an agent row, or None.

    Only a session that is still running is waiting: a closed one, or one the
    daemon has since moved out of RUNNING, is not at that prompt any more
    whatever the file says.

    clear() only unlinks the file for the exact tool call that raised it -
    right when a denial or an interruption means that call never fires its own
    PostToolUse, so nothing else ever clears it. So this asks a question no
    sibling tool call can answer instead: has the agent recorded activity of
    one of MOVED_ON_KINDS - never a side effect of some other tool call
    finishing - after the prompt that raised this wait? Finding one proves
    the turn that hit this prompt has already gone on, so the wait is stale
    and is cleared here, not left for the next matching PostToolUse that may
    never come. The comparison is by `events.id`, not `since`: a whole-second
    clock ties too easily against activity a real agent produces moments
    later. A wait noted before this field existed has no id to compare and is
    left showing rather than guessed at.

    A third path beside that one and the exact-match PostToolUse clear in
    clear(): the agent's own terminal, read and only for an agent that already
    has a wait file - almost always zero or one agent, never the whole fleet,
    so this costs nothing on a tick nobody is waiting. Read through Orca's
    short cache (screen_blocker_cached), since this runs on every redraw of
    the dashboard for as long as a wait stays open.

    Unlike the other two, a clean screen does not delete the file - it only
    hides this call's answer (auditor #1721 BLOCKING: deleting on a cached
    "no dialog" could delete a *second* prompt's wait, raised after that read
    was taken, the moment its own screen read fell in the same cache window).
    The hooks and the activity check above still delete on the real thing
    they each answer for; whichever of those two clears first still wins.
    A wait the screen hides here is shown again the moment a fresh read finds
    the dialog still up - nothing here is lost, only not shown this call.
    """
    if not row or row["closed_at"] or row["state"] != "RUNNING":
        return None
    w = read(row["agent"])
    if not w:
        return None
    eid = w.get("after_event_id")
    if eid is not None:
        moved_on = db.last_activity_id(con, row["agent"], MOVED_ON_KINDS)
        if moved_on is not None and moved_on > eid:
            clear(row["agent"])
            return None
    if not _modal_still_there(row["terminal"], w["since"]):
        return None
    return w
