"""A permission prompt an agent is stopped at, answered by the founder (T223).

`pending` reads what one agent's terminal asks, through the vendor's own
recognition (`Driver.confirmation`), and `pending_all` lists every agent the
permission hook says is waiting. Each is a plain dict, the same for every
vendor: {agent, vendor, tool, title, lines, command, reason, since}. `lines` is
the panel's own text and is what the founder is shown; `command` and `reason`
are the hook's record of the tool call, kept only when the screen carries them.

`answer` sends the founder's Yes or No, given the dict they were shown. Nothing
here answers on its own: no rule, no timer, no daemon caller.
"""
from __future__ import annotations

from .. import db
from ..session import orca, waiting
from .dialogs import _handle, _vendor


def _on_screen(text: str, lines) -> bool:
    return bool(text) and " ".join(text.split()) in " ".join(" ".join(lines).split())


def pending(con, agent: str) -> dict | None:
    """What this agent's terminal asks now, or None: no terminal, a screen that
    cannot be read, or one that is not a permission prompt the vendor recognizes."""
    handle = _handle(con, agent)
    found = orca.confirmation_on_screen(handle) if handle else None
    if found is None:
        return None
    held = waiting.read(agent) or {}
    command = held.get("command") or ""
    reason = held.get("reason") or ""
    return {"agent": agent, "vendor": _vendor(agent), "tool": held.get("tool") or "",
            "title": found.title, "lines": list(found.lines),
            "command": command if _on_screen(command, found.lines) else (found.lines[0] if found.lines else ""),
            "reason": reason if _on_screen(reason, found.lines) else "",
            "since": held.get("since")}


def pending_all(con) -> list[dict]:
    """Every pending confirmation, oldest first. Only an agent the permission
    hook recorded as waiting has its screen read, so a tick nobody is waiting
    on reads none."""
    out = [p for p in (pending(con, a["agent"]) for a in db.all_agent_states(con)
                       if waiting.read(a["agent"])) if p]
    return sorted(out, key=lambda p: p["since"] or 0)


def answer(con, agent: str, yes: bool, seen: dict) -> tuple[bool, str]:
    """Send Yes or No to the prompt `seen` (a dict `pending` returned). The screen
    is read again first and nothing is pressed unless it is that same prompt."""
    handle = _handle(con, agent)
    if handle is None:
        return False, f"{agent}: it has no terminal to answer in"
    outcome, _, why = orca.answer_confirmation(handle, yes, tuple(seen.get("lines") or ()))
    if outcome == orca.CLEARED:
        return True, f"{agent}: answered {'Yes' if yes else 'No'}"
    return False, f"{agent}: not answered - {why or outcome}"
