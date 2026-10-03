"""An agent's AskUserQuestion as the bridge shows it, instead of the raw JSON its tool call shows there
(T304/T322): `_as_dict`, the plain dict `prompt_blocks` builds a question block from (T603: the
separate pending/answer path this module once held is gone; a question is answered through its block).

`question_data` (the hook's own record of the tool call) is preferred for display over the screen's
own parse, since it is Claude's own words with nothing to mis-OCR, but only when its option count and
multiSelect flag still match the screen - a stale record would otherwise show the founder the wrong
words for the right buttons. The dict is the same for every vendor: {agent, vendor, header, question,
options, multi_select, other_number, other_text, lines}."""
from __future__ import annotations

from .dialogs import _vendor


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
