"""An agent's pending AskUserQuestion, answered by the founder from the bridge
instead of the raw JSON its tool call shows there today (T304).

`pending` reads what one agent's terminal asks, through the vendor's own
recognition (`Driver.ask_question`), gated the same way `confirmations.pending`
is: only an agent the permission hook already recorded as waiting on this
exact tool has its screen read. Each is a plain dict, the same for every
vendor: {agent, vendor, header, question, options, multi_select, other_number,
other_text, lines}. `answer` sends the founder's picks or free text, given the
dict they were shown."""
from __future__ import annotations

from .. import db
from ..session import orca, waiting
from .dialogs import _handle, _vendor

TOOL = "AskUserQuestion"


def _as_dict(agent: str, q) -> dict:
    return {"agent": agent, "vendor": _vendor(agent), "header": q.header, "question": q.question,
            "options": [{"label": o.label, "description": o.description, "checked": o.checked}
                       for o in q.options],
            "multi_select": q.multi_select, "other_number": q.other_number,
            "other_text": q.other_text, "lines": list(q.lines)}


def pending(con, agent: str, row=None, held=None) -> dict | None:
    """What this agent's terminal asks now, or None: no terminal, a screen
    that cannot be read, or one that is not an AskUserQuestion prompt the
    vendor recognizes. `row` and `held` are for a caller that has them
    already."""
    if row is None:
        row = db.get_agent_state(con, agent)
    handle = row["terminal"] if row and row["terminal"] and not row["closed_at"] else None
    if handle is None:
        return None
    q = orca.question_on_screen(handle)
    return _as_dict(agent, q) if q is not None else None


def pending_all(con) -> list[dict]:
    """Every pending question, oldest first. Only an agent the permission hook
    recorded as waiting on AskUserQuestion has its screen read."""
    out = []
    for row in db.all_agent_states(con):
        held = waiting.read(row["agent"])
        if held and held.get("tool") == TOOL:
            found = pending(con, row["agent"], row, held)
            if found:
                out.append(found)
    return sorted(out, key=lambda p: (waiting.read(p["agent"]) or {}).get("since") or 0)


def answer(con, agent: str, seen: dict, choices: tuple[int, ...] = (),
          text: str | None = None) -> tuple[bool, str]:
    """Send the founder's answer to the question `seen` (a dict `pending`
    returned): `choices` (0-based indexes into `seen["options"]`) or `text`
    for its "Type something" control - never both."""
    handle = _handle(con, agent)
    if handle is None:
        return False, f"{agent}: it has no terminal to answer in"
    outcome, _, why = orca.answer_question(handle, tuple(seen.get("lines") or ()), choices, text)
    if outcome == orca.CLEARED:
        return True, f"{agent}: answered"
    return False, f"{agent}: not answered - {why or outcome}"
