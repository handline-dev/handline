"""A permission prompt an agent is stopped at, answered by the founder (T223).

`pending` reads what one agent's terminal asks, through the vendor's own
recognition (`Driver.confirmation`), and `pending_all` lists every agent the
permission hook says is waiting. Each is a plain dict, the same for every
vendor: {agent, vendor, tool, title, lines, command, reason, since, wait}. `lines` is
the panel's own text and is what the founder is shown; `command` and `reason`
are the hook's record of the tool call, kept only when the screen carries them.

`wait` is the hook record's event id, the identity of this prompt: `answer` sends
the founder's Yes or No, given the dict they were shown, and only while the
agent's record still names that same wait. Nothing here answers on its own: no
rule, no timer, no daemon caller.
"""
from __future__ import annotations

from .. import db
from ..infra import lock
from ..session import orca, waiting
from .dialogs import _handle, _vendor


def _on_screen(text: str, lines) -> bool:
    return bool(text) and " ".join(text.split()) in " ".join(" ".join(lines).split())


def pending(con, agent: str, row=None, held=None) -> dict | None:
    """What this agent's terminal asks now, or None: no terminal, a screen that
    cannot be read, or one that is not a permission prompt the vendor recognizes.
    `row` and `held` (its state row and wait record) are for a caller that has
    them already."""
    if row is None:
        row = db.get_agent_state(con, agent)
    handle = row["terminal"] if row and row["terminal"] and not row["closed_at"] else None
    found = orca.confirmation_on_screen(handle) if handle else None
    if found is None:
        return None
    if held is None:
        held = waiting.read(agent) or {}
    command = held.get("command") or ""
    reason = held.get("reason") or ""
    return {"agent": agent, "vendor": _vendor(agent), "tool": held.get("tool") or "",
            "title": found.title, "lines": list(found.lines),
            "command": command if _on_screen(command, found.lines) else (found.lines[0] if found.lines else ""),
            "reason": reason if _on_screen(reason, found.lines) else "",
            "since": held.get("since"), "wait": held.get("after_event_id")}


def pending_all(con) -> list[dict]:
    """Every pending confirmation, oldest first. Only an agent the permission
    hook recorded as waiting has its screen read, so a tick nobody is waiting
    on reads none."""
    out = []
    for row in db.all_agent_states(con):
        held = waiting.read(row["agent"])
        found = pending(con, row["agent"], row, held) if held else None
        if found:
            out.append(found)
    return sorted(out, key=lambda p: p["since"] or 0)


def answer(con, agent: str, yes: bool, seen: dict) -> tuple[bool, str]:
    """Send Yes or No to the prompt `seen` (a dict `pending` returned). The screen
    is read again first, and nothing is pressed unless it is that same prompt:
    the same panel text and the same wait record. A prompt with no record to pin
    it to (`wait` None) is not answered.

    Locked (T351) the same terminal the tick types into - see
    `dialogs.answer_dialog`."""
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"{agent}: something else is using its terminal; try again"
        handle = _handle(con, agent)
        if handle is None:
            return False, f"{agent}: it has no terminal to answer in"

        def same_wait() -> bool:
            held = waiting.read(agent) or {}
            return seen.get("wait") is not None and held.get("after_event_id") == seen.get("wait")
        outcome, _, why = orca.answer_confirmation(handle, yes, tuple(seen.get("lines") or ()), same_wait)
        if outcome == orca.CLEARED:
            return True, f"{agent}: answered {'Yes' if yes else 'No'}"
        return False, f"{agent}: not answered - {why or outcome}"
