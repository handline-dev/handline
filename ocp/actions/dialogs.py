"""A dialog on an agent's terminal, answered by the founder from the bridge (T187): what the
screen asks and offers (`dialog_of`), the pick pressed on it (`answer_dialog`), and the standing
answers the founder saved with "always" - a trusted folder for the folder-trust screen, a
`dialog_rules` rule for any other dialog."""
from __future__ import annotations

import re

from ..infra import lock, textsafe
from ..session import orca
from ..team import agents, trust
from .. import db

TRUST = "trust"
TRUST_LABEL = "Trust this folder"


def _handle(con, agent: str) -> str | None:
    row = db.get_agent_state(con, agent)
    return row["terminal"] if row and row["terminal"] and not row["closed_at"] else None


def _vendor(agent: str) -> str:
    try:
        return str(agents.read(agent).get("vendor") or agents.DEFAULT_VENDOR)
    except Exception:  # noqa: BLE001 - an unreadable agent file is not a dialog
        return agents.DEFAULT_VENDOR


def dialog_of(con, agent: str) -> dict | None:
    """What this agent's terminal is asking, or None: {agent, vendor, kind, title, options,
    folder, body}. `kind` is `trust` for the folder-trust screen (one option: trust the folder shown)
    and `dialog` for a numbered dialog, whose options are (number, label)."""
    handle = _handle(con, agent)
    if handle is None:
        return None
    vendor = _vendor(agent)
    folder = orca.trust_prompt_folder(handle)
    if folder:
        return {"agent": agent, "vendor": vendor, "kind": TRUST, "title": f"Trust {textsafe.oneline(folder)}?",
                "options": [(1, TRUST_LABEL)], "folder": textsafe.oneline(folder), "body": _trust_text(handle)}
    dialog = orca.dialog_on_screen(handle)
    if dialog is None:
        return None
    return {"agent": agent, "vendor": vendor, "kind": "dialog", "title": dialog.title,
            "options": list(dialog.options), "folder": "", "body": _context(dialog)}


def _trust_text(handle: str) -> list[str]:
    """The trust screen's own words, under its last rule line and without its two choices or its key hint (the
    modal's button is the trust one; declining is left to the terminal)."""
    text = orca.screen_text(handle)
    if text is None:
        return []
    lines = text.split("\n")
    rules = [i for i, l in enumerate(lines) if l.strip() and set(l.strip()) <= {"\u2500"}]
    keep = lines[rules[-1] + 1:] if rules else lines
    return [textsafe.oneline(l).strip() for l in keep if l.strip()
            and l.strip().lstrip("\u276f ") not in _TRUST_CHOICES and not l.strip().startswith("Enter to confirm")]


_TRUST_CHOICES = {"No, exit", "Yes, I trust this folder"}
_NUMBERED = re.compile(r"^(?:\u276f\s*)?\d+\.\s")


def _context(dialog) -> list[str]:
    """The panel's own lines the modal does not already show as title or option buttons."""
    shown = {dialog.title.strip()} | {label.strip() for _, label in dialog.options}
    return [textsafe.oneline(l) for l in dialog.body
            if l.strip() not in shown and not _NUMBERED.match(l.strip())]


def answer_dialog(con, agent: str, title: str, number: int, always: bool = False) -> tuple[bool, str]:
    """Press option `number` of the dialog titled `title`, after reading the screen again: what
    is pressed is what is on screen now, not what the modal showed. `always` saves the choice
    first-class: the folder becomes a trusted root, any other dialog becomes a rule on its exact
    title and the chosen label - remembered only once the press itself lands, never ahead of it:
    a rule saved before a press that then fails would stand for a choice nothing confirmed was
    ever made, and under a truncated dialog's shared placeholder title (T383) would wrongly
    answer every other long dialog like it too.

    Locked (T351) the same terminal the tick types into - a key pressed here
    and a line the tick sends can otherwise land in the same input box."""
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"{agent}: something else is using its terminal; try again"
        return press_choice(con, agent, title, number, always)


def press_choice(con, agent: str, title: str, number: int, always: bool = False) -> tuple[bool, str]:
    """`answer_dialog` without taking the terminal lock: for a caller that already holds it (`prompt_blocks.answer`)."""
    now = dialog_of(con, agent)
    if now is None or now["title"] != title:
        return False, f"{agent}: the dialog is gone or changed - nothing pressed"
    label = next((lab for n, lab in now["options"] if n == number), None)
    if label is None:
        return False, f"{agent}: the dialog has no option {number}"
    handle = _handle(con, agent)
    if now["kind"] == TRUST:
        ok, said = orca.confirm_trust(handle)
        if not ok:
            return False, f"{agent}: {said}"
    else:
        outcome, _, why = orca.press_option(handle, number)
        if outcome != orca.CLEARED:
            return False, f"{agent}: {why or outcome}"
    if always:
        ok, remember_said = _remember(now, label)
        if not ok:
            return True, f"{agent}: chose {number}. {label} - but not remembered: {remember_said}"
    if now["kind"] == TRUST:
        return True, f"{agent}: {said}"
    return True, f"{agent}: chose {number}. {label}" + (" - and will again" if always else "")


def _remember(dialog: dict, label: str) -> tuple[bool, str]:
    if dialog["kind"] == TRUST:
        if trust.is_declared(dialog["folder"]):
            return True, "already trusted"
        return trust.enact(dialog["folder"], "chosen from the agent's dialog")
    if dialog["title"] == orca.TRUNCATED_TITLE:
        # every long command shares this title, so a rule on it would answer them all
        return False, "a long command's title is not shown, so no rule can be kept for it"
    from ..team import dialog_rules      # the founder-only store; agents cannot write it
    try:
        return dialog_rules.add(dialog["vendor"], dialog["title"], label)
    except PermissionError as e:
        return False, str(e)


def standing_answers() -> list[dict]:
    """Every answer the founder saved, for the Home settings: {kind, key, text}. `key` is what
    `remove_answer` takes."""
    out = [{"kind": TRUST, "key": r["root"], "text": f"trust {r['root']}"} for r in trust.list_roots()]
    from ..team import dialog_rules
    for r in dialog_rules.list_rules():
        out.append({"kind": "dialog", "key": (r["vendor"], r["title"], r["label"]),
                    "text": f"{r['vendor']}: \"{r['title']}\" -> {r['label']}"})
    return out


def remove_answer(kind: str, key) -> tuple[bool, str]:
    if kind == TRUST:
        return trust.remove(key)
    from ..team import dialog_rules
    return dialog_rules.remove(*key)
