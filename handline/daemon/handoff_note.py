"""The agent's own part of its handoff: a file it is asked to write while its prompt cache is
still warm (a turn then reads the context at the cached price), which the mechanical record
(`session.handoff.capture`) carries into the next session if that one starts fresh.

It is asked at two moments. Just before a close or a move, once per stretch. And, for a session
that stays open and idle, at 90% of its cache's real life (`cachettl`: an hour for a subscription,
read off its last response): the cache is about to be paid for in full, so the turn that writes
the note is the cheap moment to write it. A cache whose life is five minutes gets no ask at all;
the record is then the facts alone. An agent turns it off for itself with `handoff_note: false` in
its agent.yaml (config.yaml's `handoff_note` is the global switch).

It writes `note_path` and stops. The ask is never waited on past `ASK_WAIT_S`, and an agent that
wrote nothing, or could not (the wall, an error), is handed the mechanical record alone.

The ask is remembered in one cursor: its row id is the newest event when the ask
was typed, and its time is when. The turn that answers it is the one
`UserPromptSubmit` after that event; a second one is someone else typing, and a
`SessionStart` is a new session: either ends the stretch, and the note no longer
counts. A record that carries the note ends it too (`session.handoff.record`). The
file is removed before every ask, so one that exists was written after it.

No repeats: the stretch is also what keeps a second note from being asked for. While no one has
typed since the ask's own turn, the stretch stands, so an idle session is asked once however long
it sits; a delivered message or a working turn ends it, and the next idle stretch asks again.
"""
from __future__ import annotations

import time

from .. import db
from ..infra import paths, textsafe
from ..session import cachettl, events, idle_close
from . import delivery
from .states import IDLE, PROMPT_CACHE_S, RESUME_WARM_S

# The share of its cache's life an open idle session is asked at, and how close to the end the ask
# is still worth making (the turn has to reach its first request before the cache is gone).
ASK_AT = 0.9
ASK_MARGIN_S = 60

# How long a typed ask may go without its turn starting before the close goes on.
ASK_WAIT_S = 300
# What the record carries of the note; the ask names it so the agent keeps to it.
NOTE_MAX = 3000

_ASK = ("Write your handoff to {path} now, while your prompt cache is warm, with one Write "
        "(replace what is there), then stop and run nothing else. The next session of you, if "
        "it starts fresh, reads it first: copy yourself into it. At most {max} characters, about "
        "30 lines, only what `hl` and your memory cannot tell it: (1) what you were deciding "
        "and why; (2) what you meant to do next; (3) promises or doubts not yet on any task; "
        "(4) the founder's preferences and the pitfalls you learned this session; (5) who you "
        "wait on, as T<n>/#<n> numbers only. Do not restate what a task or message holds; cite "
        "its T or # number. A lasting lesson or preference goes to your memory first, not here. "
        "If there is nothing, write the single word: nothing.")


def note_path(agent: str):
    return paths.handoff_note_file(agent)


def _key(agent: str) -> str:
    return f"handoff_ask:{agent}"


def _kind_key(agent: str) -> str:
    return f"handoff_ask_kind:{agent}"


KEY = "handoff_note"


def default_enabled() -> bool:
    """The global switch: what an agent with no `handoff_note` of its own follows. On unless
    config.yaml says off."""
    return idle_close.config_flag(KEY, True)


def set_default_enabled(on: bool) -> None:
    idle_close.set_config_flag(KEY, on)


def enabled(agent: str) -> bool:
    """Whether this agent writes a handoff note at all: its own `handoff_note` in agent.yaml,
    else the global switch."""
    mine = idle_close._agent_cfg(agent).get(KEY)
    return mine if isinstance(mine, bool) else default_enabled()


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
    """Has the turn answering a close-time ask come and gone? Idle is the caller's to know. An ask
    made because the cache was about to expire is not a reason to close: the session stays open."""
    got = asked(con, agent)
    return bool(got and got[1] == 1 and db.get_meta(con, _kind_key(agent)) != "cache")


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
    db.set_meta(con, _kind_key(agent), None)


def cache_clock(con, agent: str, session: str | None = None) -> cachettl.Clock | None:
    """The prompt-cache clock of this agent's session (`session`, if it is a closed one's id), read
    from its transcript; None when there is no transcript to read."""
    from ..session import handoff as handoffmod  # local: that module reaches this one
    from ..team import agents
    _session, path = handoffmod.previous_session(con, agent, agents.current_account(agent, con), session)
    return cachettl.read(path)


def cache_left(con, agent: str, now: float) -> float | None:
    """Seconds until this agent's cache lapses, negative once it has, or None with nothing to go
    by: the real TTL off its last response when the transcript says, else an hour from its last
    activity - the clock `lifecycle.cache_expired` has always used."""
    clock = cache_clock(con, agent)
    if clock is not None and clock.ttl is not None:
        return clock.left(now)
    last = db.last_activity(con, agent)
    return None if last is None else last + PROMPT_CACHE_S - now


def _eligible(con, agent: str, now: int) -> bool:
    """May a close or move ask now: the agent writes notes, nothing runs behind its prompt, and a turn
    would still be cheap - its cache has a life over five minutes and it is not nearly over."""
    if not enabled(agent) or delivery.has_background_work(con, agent):
        return False
    clock = cache_clock(con, agent)
    if clock is not None and clock.ttl is not None:
        return clock.ttl >= cachettl.LONG_TTL_S and now - clock.at < clock.ttl - cachettl.margin(clock.ttl)
    last = db.last_activity(con, agent)
    return last is not None and now - last < RESUME_WARM_S


def _ask(con, agent: str, log, kind: str = "close") -> bool:
    path = note_path(agent)
    path.unlink(missing_ok=True)
    last_event = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    db.set_cursor(con, _key(agent), last_event)
    db.set_meta(con, _kind_key(agent), kind)
    if delivery.type_notice(con, agent, _ASK.format(path=path, max=NOTE_MAX)):
        log(f"  {agent}: asked to write its handoff note" + (" before its cache expires" if kind == "cache" else ""))
        return True
    db.clear_cursor(con, _key(agent))      # nothing was typed: no ask was made
    db.set_meta(con, _kind_key(agent), None)
    return False


def at_cache_edge(con, agent: str, now: int, st, log) -> bool:
    """An open, idle session whose cache is nearly spent is asked for its note, once.

    Only a cache with a life of an hour or more: at five minutes there is no warm moment worth a turn.
    Only once per stretch (`asked`), and only inside the last tenth of the life, which a session that
    was working never reaches - each response restarts the clock. A session that has since closed,
    or whose line cannot be typed now (a turn running, a dialog open, the wall), is simply asked on
    a later tick while the window lasts; one the ask cannot reach is left to the facts. A closed
    session, one that is not idle, and one at the usage wall are not asked."""
    row = db.get_agent_state(con, agent)
    if not row or row["closed_at"] or row["state"] != IDLE or events.at_limit(con, agent):
        return False                      # not an open, idle session that can take a turn
    if not enabled(agent) or asked(con, agent) is not None or delivery.has_background_work(con, agent):
        return False
    clock = cachettl.read(getattr(st, "transcript_path", None))
    if clock is None or clock.ttl is None or clock.ttl < cachettl.LONG_TTL_S:
        return False
    elapsed = now - clock.at
    if not (ASK_AT * clock.ttl <= elapsed < clock.ttl - ASK_MARGIN_S):
        return False
    return _ask(con, agent, log, "cache")


def hold(con, agent: str, now: int, log) -> bool:
    """Should the close or move this agent is due wait for its note? Asks once per stretch
    and holds while the ask's turn has not begun, for at most `ASK_WAIT_S`. False means go
    on, with the note if it was written and without it otherwise - never a block."""
    got = asked(con, agent)
    if got is None:
        return _eligible(con, agent, now) and _ask(con, agent, log)
    when, prompts = got
    return prompts == 0 and now - when < ASK_WAIT_S
