"""An agent's pending AskUserQuestion, answered by the founder from the bridge
instead of the raw JSON its tool call shows there (T304/T322).

`pending` reads what one agent's terminal asks, through the vendor's own
recognition (`Driver.ask_question`) - the screen read is the only thing that
says this prompt is on screen *now*, at all, and is what `answer` later
re-checks before pressing anything. `question_data` (the hook's own record of
the tool call) is preferred for display over the screen's own parse, since it
is Claude's own words with nothing to mis-OCR, but only when its option count
and multiSelect flag still match the screen - a stale record would otherwise
show the founder the wrong words for the right buttons. Each is a plain dict,
the same for every vendor: {agent, vendor, header, question, options,
multi_select, other_number, other_text, lines}. `answer` sends the founder's
picks or free text, given the dict they were shown."""
from __future__ import annotations

from .. import db
from ..session import orca, waiting
from .dialogs import _handle, _vendor

TOOL = "AskUserQuestion"


def _question_data_of(held: dict | None, q) -> dict | None:
    data = (held or {}).get("question_data")
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list) or len(questions) != 1:
        return None
    q0 = questions[0]
    options = q0.get("options") if isinstance(q0, dict) else None
    if not isinstance(options, list) or len(options) != len(q.options):
        return None
    if bool(q0.get("multiSelect")) != q.multi_select:
        return None
    return q0


def _as_dict(agent: str, q, held: dict | None = None) -> dict:
    q0 = _question_data_of(held, q)
    options = q0["options"] if q0 else [{"label": o.label, "description": o.description} for o in q.options]
    return {"agent": agent, "vendor": _vendor(agent),
            "header": (q0.get("header") if q0 else None) or q.header,
            "question": (q0.get("question") if q0 else None) or q.question,
            "options": [{"label": str(o.get("label") or ""), "description": str(o.get("description") or ""),
                        "checked": q.options[i].checked} for i, o in enumerate(options)],
            "multi_select": q.multi_select, "other_number": q.other_number,
            "other_text": q.other_text, "lines": list(q.lines)}


def pending(con, agent: str, row=None, held=None) -> dict | None:
    """What this agent's terminal asks now, or None: no terminal, a screen
    that cannot be read, one that is not an AskUserQuestion prompt the
    vendor recognizes, or one `confirmations.pending` already claims - a
    permission prompt wins when both read the same screen (T336: they once
    agreed on a plain Bash prompt and showed both modals for it). `row` and
    `held` are for a caller that has them already."""
    if row is None:
        row = db.get_agent_state(con, agent)
    handle = row["terminal"] if row and row["terminal"] and not row["closed_at"] else None
    if handle is None:
        return None
    if orca.confirmation_on_screen(handle) is not None:
        return None
    q = orca.question_on_screen(handle)
    if q is None:
        return None
    if held is None:
        held = waiting.read(agent)
    return _as_dict(agent, q, held)


def pending_all(con) -> list[dict]:
    """Every pending question, oldest first. Only an agent the permission hook
    recorded as waiting on AskUserQuestion has its screen read."""
    out = []
    for row in db.all_agent_states(con):
        held = waiting.read(row["agent"])
        if held and held.get("tool") == TOOL:
            found = pending(con, row["agent"], row, held)
            if found:
                out.append((held.get("since") or 0, found))
    return [found for _, found in sorted(out, key=lambda pair: pair[0])]


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
        # T336 diagnostics (temporary, founder #2013): the boxes+Other combo's
        # own trail, when there is one, rides along on a plain success too -
        # a log line worth having while this path is still new.
        return True, f"{agent}: answered" + (f" ({why})" if why else "")
    return False, f"{agent}: not answered - {why or outcome}"
