"""A permission prompt an agent is stopped at, as the bridge shows it (T223): `describe`, the plain dict
`prompt_blocks` builds a confirmation block from (T603: the separate pending/answer path this module
once held is gone; a prompt is answered through its block).

The dict is the same for every vendor: {agent, vendor, tool, title, lines, command, reason, since,
wait}. `lines` is the panel's own text and is what the founder is shown; `command` and `reason` are the
hook's record of the tool call when the screen carries it, else what `session.promptparse` reads off the screen. `wait` is the hook record's
event id, the identity of this prompt."""
from __future__ import annotations

from ..session import promptparse, waiting
from .dialogs import _vendor


def _on_screen(text: str, lines) -> bool:
    return bool(text) and " ".join(text.split()) in " ".join(" ".join(lines).split())


def describe(agent: str, found, held: dict | None) -> dict:
    """The plain dict for a permission prompt `found` (a vendor `Confirmation`) with the hook's record `held`."""
    held = held if held is not None else (waiting.read(agent) or {})
    command = held.get("command") or ""
    reason = held.get("reason") or ""
    seen = promptparse.permission_fields(found.title, found.lines)
    return {"agent": agent, "vendor": _vendor(agent), "tool": held.get("tool") or seen["tool"] or "",
            "title": found.title, "lines": list(found.lines),
            "command": command if _on_screen(command, found.lines) else seen["command"] or "",
            "reason": reason if _on_screen(reason, found.lines) else seen["reason"] or "",
            "since": held.get("since"), "wait": held.get("after_event_id")}
