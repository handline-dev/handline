"""The one prompt an agent's screen shows, as a row the bridge reads and answers (T395).

The daemon's screen watch (`daemon.prompts`) judges each agent's screen once and
keeps a `prompt_blocks` row for what it shows: a permission prompt, an
AskUserQuestion, or a plain dialog - never two at once, in that priority. The
bridge only reads those rows (`open_blocks`) and asks for an answer
(`answer`); the screen is not judged anywhere else. What is pressed is what a
fresh read of the screen shows *now*: a prompt whose panel text, or (for a
permission prompt) hook wait id, no longer matches the row is refused."""
from __future__ import annotations

import hashlib
import json
import time

from .. import db
from ..infra import daemonclient as client, lock
from ..session import term, waiting
from . import ask_question, confirmations, dialogs

PERMISSION, QUESTION, DIALOG = term.PROMPT_PERMISSION, term.PROMPT_QUESTION, term.PROMPT_DIALOG
# How old a row's last_seen may be before it no longer counts as a prompt on screen: a
# watch that stopped, or a screen it cannot read, must not leave a prompt showing for good.
STALE_S = 10


def fingerprint(kind: str, panel: list[str]) -> str:
    return hashlib.sha1("\n".join([kind, *panel]).encode()).hexdigest()


def describe(agent: str, kind: str, payload, held: dict | None) -> dict:
    """{kind, title, panel, detail, wait_id, answerable} for what `term.prompt_on_screen` read."""
    if kind == PERMISSION:
        detail = confirmations.describe(agent, payload, held)
        wait = detail["wait"]
        # A permission prompt with no hook record has nothing to pin its identity to,
        # so it cannot be answered from here (T223): the terminal is where it is answered.
        return {"kind": kind, "title": payload.title, "panel": list(payload.lines), "detail": detail,
                "wait_id": wait, "answerable": wait is not None}
    if kind == QUESTION:
        detail = ask_question._as_dict(agent, payload, held)
        return {"kind": kind, "title": payload.question or payload.header, "panel": list(payload.lines),
                "detail": detail, "wait_id": None, "answerable": True}
    detail = {"agent": agent, "options": [list(o) for o in payload.options],
              "body": dialogs._context(payload), "truncated": payload.title == term.TRUNCATED_TITLE}
    return {"kind": kind, "title": payload.title, "panel": list(payload.body), "detail": detail,
            "wait_id": None, "answerable": True}


def _row_dict(row) -> dict:
    out = dict(row)
    out["panel"], out["detail"] = json.loads(row["panel"]), json.loads(row["detail"])
    return out


def of_row(row) -> dict:
    """A `prompt_blocks` row as the dict the rest of this module hands out."""
    return _row_dict(row)


def open_blocks(con, now: float | None = None) -> list[dict]:
    """Every prompt on an agent's screen now, oldest first, for the screen to show: an open
    row the watch has refreshed within `STALE_S`."""
    now = time.time() if now is None else now
    return [_row_dict(r) for r in db.open_prompts(con) if now - r["last_seen"] <= STALE_S]


def open_block_of(con, agent: str, now: float | None = None) -> dict | None:
    """`agent`'s open block, when the watch has refreshed it within `STALE_S`."""
    row = db.open_prompt(con, agent)
    if row is None or (time.time() if now is None else now) - row["last_seen"] > STALE_S:
        return None
    return _row_dict(row)


def headline(block: dict) -> str:
    """What a block is, in one line, for the places that count what waits on the founder."""
    if block["kind"] == PERMISSION:
        d = block["detail"]
        return " ".join(x for x in (f"{d.get('tool') or 'tool'}:", d.get("command")) if x)
    return block["title"] or block["kind"]


def request_answer(block_id: int, choice: dict) -> tuple[bool, str]:
    """Ask the daemon to answer block `block_id` (`answer` says how); the screen sends nothing itself."""
    reply = client.send({"verb": "answer_block", "block": block_id, "choice": choice})
    if reply is None:
        return False, "the daemon is not running - nothing sent"
    return bool(reply.get("ok")), str(reply.get("said", ""))


def answer(con, block_id: int, choice: dict) -> tuple[bool, str]:
    """Answer block `block_id` with `choice` - permission: {"yes": bool}; question:
    {"choices": [0-based indexes], "text": str | None}; dialog: {"number": int, "always": bool};
    any kind: {"cancel": True}, which sends Esc (a permission prompt reads it as No).

    Refused, and recorded as refused, unless the screen read again now shows the very
    prompt the row names: same kind, same panel text and, for a permission prompt, the
    hook still waiting on the same wait id. Locked (T351) the same terminal the tick types into."""
    row = db.get_prompt(con, block_id)
    if row is None:
        return False, f"no prompt #{block_id}"
    agent = row["agent"]
    if row["gone_at"] is not None:
        return _record(con, row, False, f"{agent}: the prompt is gone - nothing sent")
    if not row["answerable"]:
        return _record(con, row, False, f"{agent}: this prompt has no hook record to pin it to - answer it in its terminal")
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"{agent}: something else is using its terminal; try again"
        handle = dialogs._handle(con, agent)
        if handle is None:
            return _record(con, row, False, f"{agent}: it has no terminal to answer in")
        kind, payload = term.prompt_on_screen(handle)
        if kind in (term.PROMPT_NONE, term.PROMPT_UNREADABLE):
            return _record(con, row, False, f"{agent}: the prompt is gone or its screen cannot be read - nothing sent")
        now = describe(agent, kind, payload, waiting.read(agent))
        if now["kind"] != row["kind"] or fingerprint(now["kind"], now["panel"]) != row["fingerprint"]:
            return _record(con, row, False, f"{agent}: the prompt on screen is not the one shown - nothing sent")
        if choice.get("cancel"):
            ok, said = _cancel(agent, handle, row)
        elif row["kind"] == PERMISSION:
            ok, said = _permission(agent, handle, row, now, bool(choice.get("yes")))
        elif row["kind"] == QUESTION:
            ok, said = _question(agent, handle, now, tuple(choice.get("choices") or ()), choice.get("text"))
        else:
            ok, said = dialogs.press_choice(con, agent, now["title"], int(choice["number"]),
                                            bool(choice.get("always", False)))
    return _record(con, row, ok, said)


def _cancel(agent: str, handle: str, row) -> tuple[bool, str]:
    """Esc, once the prompt was just confirmed to be the row's. A permission prompt or question
    reads Esc as No or cancel. A plain dialog gets it only where `term.declining_key` says the
    screen shows Esc only declines - not one whose Esc leaves the session or stops tasks."""
    if row["kind"] == DIALOG:
        outcome, _, why = term.decline_dialog(handle)
        if outcome == term.CLEARED:
            return True, f"{agent}: cancelled (Esc)"
        return False, f"{agent}: not cancelled - {why or outcome}"
    ok, out = term.send(handle, term.ESCAPE, enter=False)
    if not ok:
        return False, f"{agent}: the key could not be sent: {out[:120]}"
    for _ in range(term.PRESS_SETTLE_READS):
        time.sleep(term.PRESS_SETTLE_S)
        kind, payload = term.prompt_on_screen(handle)
        if kind == term.PROMPT_UNREADABLE:
            continue        # not evidence either way
        if kind == term.PROMPT_NONE or fingerprint(kind, describe(agent, kind, payload, None)["panel"]) != row["fingerprint"]:
            return True, f"{agent}: " + ("answered No (Esc)" if row["kind"] == PERMISSION else "cancelled (Esc)")
    return False, f"{agent}: the prompt is still on screen after Esc"


def _permission(agent: str, handle: str, row, now: dict, yes: bool) -> tuple[bool, str]:
    def same_wait() -> bool:
        return (waiting.read(agent) or {}).get("after_event_id") == row["wait_id"]
    outcome, _, why = term.answer_confirmation(handle, yes, tuple(now["panel"]), same_wait)
    if outcome == term.CLEARED:
        return True, f"{agent}: answered {'Yes' if yes else 'No'}"
    return False, f"{agent}: not answered - {why or outcome}"


def _question(agent: str, handle: str, now: dict, choices: tuple[int, ...], text) -> tuple[bool, str]:
    outcome, _, why = term.answer_question(handle, tuple(now["panel"]), choices, text)
    if outcome == term.CLEARED:
        return True, f"{agent}: answered" + (f" ({why})" if why else "")
    return False, f"{agent}: not answered - {why or outcome}"


def _record(con, row, ok: bool, said: str) -> tuple[bool, str]:
    db.prompt_answered(con, row["id"], ok, said, int(time.time()))
    return ok, said
