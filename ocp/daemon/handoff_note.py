"""The agent's own part of its handoff (T571): a file it is asked to write before its
session closes, which the mechanical record (`session.handoff.capture`) carries.

An idle agent is asked once per stretch, while its prompt cache is still warm (a turn
then reads the context at the cached price). It writes `note_path` and stops; the
close or move follows at once. The ask is never waited on past `ASK_WAIT_S`, and an
agent that wrote nothing is closed on the mechanical record alone.

The ask is remembered in one cursor: its row id is the newest event when the ask
was typed, and its time is when. The turn that answers it is the one
`UserPromptSubmit` after that event; a second one is someone else typing, and a
`SessionStart` is a new session: either ends the stretch, and the note no longer
counts. A record that carries the note ends it too (`session.handoff.record`). The
file is removed before every ask, so one that exists was written after it.
"""
from __future__ import annotations

import time

from .. import db
from ..infra import paths, textsafe
from ..session import idle_close
from . import delivery
from .states import RESUME_WARM_S

# How long a typed ask may go without its turn starting before the close goes on.
ASK_WAIT_S = 300
# What the record carries of the note; the ask names it so the agent keeps to it.
NOTE_MAX = 3000

_ASK = ("This session is about to close. Write your handoff to {path} now, with one Write "
        "(replace what is there), then stop and run nothing else. The next session of you "
        "reads it first. In at most {max} characters, only what `ocp` and your memory cannot "
        "tell it: (1) what you are doing now and why - judgments in progress; (2) what you "
        "promised to whom and what you wait on, as T123/#1234 numbers only; (3) hunches not "
        "yet checked, and what to check next; (4) how you read the founder's latest intent: "
        "its # number and one line. Do not copy what `ocp` shows by a T or # number (task "
        "bodies, messages, decisions, team state). A lasting lesson or preference goes to "
        "your memory first, not here. If there is nothing, write the single word: nothing.")


def note_path(agent: str):
    return paths.agent_dir(agent) / "handoff.md"


def _key(agent: str) -> str:
    return f"handoff_ask:{agent}"


def _prompts_since(con, agent: str, event_id: int) -> int:
    return con.execute("SELECT COUNT(*) FROM events WHERE agent = ? AND kind = 'UserPromptSubmit' "
                       "AND id > ?", (agent, event_id)).fetchone()[0]


def asked(con, agent: str) -> tuple[int, int] | None:
    """(when, prompts typed since) for this stretch's ask, or None when there is none:
    never asked, someone else typed after the ask's own turn, or a new session began."""
    event_id = db.get_cursor(con, _key(agent))
    when = db.cursor_updated(con, _key(agent))
    if not event_id or when is None:
        return None
    if con.execute("SELECT 1 FROM events WHERE agent = ? AND kind = 'SessionStart' AND id > ? LIMIT 1",
                   (agent, event_id)).fetchone():
        return None
    n = _prompts_since(con, agent, event_id)
    return (when, n) if n <= 1 else None


def answered_turn(con, agent: str) -> bool:
    """Has the asked agent's turn come and gone? Idle is the caller's to know."""
    got = asked(con, agent)
    return bool(got and got[1] == 1)


def text(con, agent: str) -> str | None:
    """What the agent wrote for this stretch, cleaned and cut to `NOTE_MAX`, or None."""
    if asked(con, agent) is None:
        return None
    try:
        raw = note_path(agent).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    body = textsafe.text(raw).strip()
    if not body:
        return None
    return body if len(body) <= NOTE_MAX else body[:NOTE_MAX].rstrip() + f"\n(cut at {NOTE_MAX} characters)"


def clear(con, agent: str) -> None:
    """The note is in the record: drop it and the ask."""
    note_path(agent).unlink(missing_ok=True)
    db.clear_cursor(con, _key(agent))


def _eligible(con, agent: str, now: int) -> bool:
    last = db.last_activity(con, agent)
    return idle_close.config_flag("handoff_note", True) and not delivery.has_background_work(con, agent) \
        and last is not None and now - last < RESUME_WARM_S


def _ask(con, agent: str, log) -> bool:
    path = note_path(agent)
    path.unlink(missing_ok=True)
    last_event = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    db.set_cursor(con, _key(agent), last_event)
    if delivery.type_notice(con, agent, _ASK.format(path=path, max=NOTE_MAX)):
        log(f"  {agent}: asked to write its handoff note")
        return True
    db.clear_cursor(con, _key(agent))      # nothing was typed: no ask was made
    return False


def hold(con, agent: str, now: int, log) -> bool:
    """Should the close or move this agent is due wait for its note? Asks once per stretch
    and holds while the ask's turn has not begun, for at most `ASK_WAIT_S`. False means go
    on, with the note if it was written and without it otherwise - never a block."""
    got = asked(con, agent)
    if got is None:
        return _eligible(con, agent, now) and _ask(con, agent, log)
    when, prompts = got
    return prompts == 0 and now - when < ASK_WAIT_S
