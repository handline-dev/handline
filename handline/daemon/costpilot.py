"""The cost pilot: two ways to spend fewer cache tokens on an idle agent, each off until an
agent's own agent.yaml turns it on, so every other agent is the control.

Renewal (`cache_renewal: true`). An open idle session whose cache lives an hour is asked for its
handoff note a minute or two before the cache lapses. That turn reads the context at the cached
price and writes a response, which restarts the hour. At most `MAX_RENEWALS` per idle stretch,
and never over `RENEW_CTX_MAX` tokens of context: past that a lapse is cheaper to take than a
read, and the fold below is the answer. It replaces `handoff_note.at_cache_edge`'s single ask at
90% for this agent. A stretch ends at a message someone typed; the ask's own turn does not end it.

Fold (`idle_fold: true`). An open idle session that holds no task, has nothing queued, and has a
context of `idle_fold_tokens` (150k) or more is closed fresh after `idle_fold_after_s` (10 minutes)
of silence: its next wake is a new conversation that starts from the handoff record, with the note
the agent wrote while its cache was warm. The model fold (`ocp fold`) is not run here: the daemon
tick must not wait on it.

Every renewal and fold is an event (`cache_renewal`, `idle_fold`) carrying the figures it was
decided on, so `token_calls` can be cut by the interval between them. Decisions are levels read
from the record, never edges: a renewal count is read back from the events.
"""
from __future__ import annotations

from .. import db
from ..session import cachettl, events, idle_close
from . import delivery, handoff_note, lifecycle
from .states import IDLE

RENEWAL_KEY = "cache_renewal"
FOLD_KEY = "idle_fold"

# A renewal is asked in the last stretch of the life: no later than `handoff_note.ASK_MARGIN_S`
# before the end (a turn needs that long to reach its first request), and wide enough that one
# 30-second tick cannot step over it - with a one-hour cache, from 57.5 to 59 minutes.
RENEW_LEAD_S = 150
RENEW_MIN_LEFT_S = handoff_note.ASK_MARGIN_S
MAX_RENEWALS = 2
RENEW_CTX_MAX = 150_000

FOLD_CTX_MIN = 150_000
FOLD_IDLE_S = 600


def _own(agent: str, key: str):
    return idle_close._agent_cfg(agent).get(key)


def _flag(agent: str, key: str) -> bool:
    """An agent's own switch; there is no global one, and absent means off."""
    return _own(agent, key) is True


def renewal_on(agent: str) -> bool:
    return _flag(agent, RENEWAL_KEY)


def fold_on(agent: str) -> bool:
    return _flag(agent, FOLD_KEY)


def _own_int(agent: str, key: str, default: int) -> int:
    v = _own(agent, key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else default


def renewals_in_stretch(rows: list[tuple[int, str]]) -> int:
    """How many renewals this idle stretch has had. `rows` are the agent's UserPromptSubmit,
    SessionStart and cache_renewal events as (id, kind), newest first. The stretch reaches back to
    the last message someone typed or the last session start; a prompt straight after a renewal is
    that renewal's own turn and does not end it."""
    n = 0
    for i, (_id, kind) in enumerate(rows):
        if kind == "cache_renewal":
            n += 1
        elif kind == "SessionStart":
            break
        elif kind == "UserPromptSubmit":
            older = rows[i + 1][1] if i + 1 < len(rows) else None
            if older != "cache_renewal":
                break
    return n


def _renewals(con, agent: str) -> int:
    rows = con.execute("SELECT id, kind FROM events WHERE agent = ? AND kind IN "
                       "('UserPromptSubmit', 'SessionStart', 'cache_renewal') ORDER BY id DESC LIMIT 20",
                       (agent,)).fetchall()
    return renewals_in_stretch([(r["id"], r["kind"]) for r in rows])


def _open_idle(con, agent: str, row) -> bool:
    """An open session that can take a turn now and runs nothing behind its prompt."""
    return bool(row and not row["closed_at"] and row["state"] == IDLE and not events.at_limit(con, agent)
                and not delivery.has_background_work(con, agent))


def renewal_due(con, agent: str, row, now: int, st) -> bool:
    """Is this session due a renewal now: the switch is on, it is open and idle, its cache is an
    hour's and in its last stretch, its context is known and not over `RENEW_CTX_MAX`, no ask of
    its is still waiting to be taken, and the stretch has had fewer than `MAX_RENEWALS`."""
    if not renewal_on(agent) or not handoff_note.enabled(agent) or not _open_idle(con, agent, row):
        return False
    ctx = st.trusted_ctx_used
    if ctx is None or ctx > RENEW_CTX_MAX:
        return False                      # unreadable is not small: fail closed
    clock = cachettl.read(getattr(st, "transcript_path", None))
    if clock is None or clock.ttl is None or clock.ttl < cachettl.LONG_TTL_S:
        return False
    left = clock.left(now)
    if left is None or not (RENEW_MIN_LEFT_S <= left < RENEW_LEAD_S):
        return False
    pending = handoff_note.asked(con, agent)
    if pending is not None and pending[1] == 0:
        return False                      # asked, and the turn has not begun
    return _renewals(con, agent) < MAX_RENEWALS


def renew(con, agent: str, row, now: int, st, log) -> bool:
    """Ask for the note if a renewal is due, and record it. True when the ask was typed."""
    if not renewal_due(con, agent, row, now, st):
        return False
    count = _renewals(con, agent) + 1
    if not handoff_note.ask(con, agent, log, "cache"):
        return False
    clock = cachettl.read(getattr(st, "transcript_path", None))
    db.add_event(con, agent, "cache_renewal",
                 {"n": count, "of": MAX_RENEWALS, "ctx": st.trusted_ctx_used,
                  "cache_left_s": int(clock.left(now)) if clock else None})
    log(f"  {agent}: cache renewal {count} of {MAX_RENEWALS}")
    return True


def fold_due(con, agent: str, row, now: int, st) -> bool:
    """Is this session due a fold now: the switch is on, it is open and idle past its fold wait, it
    holds no task and has nothing queued or waiting to be told, its context is known and at the
    fold size, there is a session to take the record from, and no close of it failed lately."""
    if not fold_on(agent) or not _open_idle(con, agent, row) or not st.session_id:
        return False
    ctx = st.trusted_ctx_used
    if ctx is None or ctx < _own_int(agent, "idle_fold_tokens", FOLD_CTX_MIN):
        return False
    if db.current_task(con, agent) is not None or db.queued_for(con, agent):
        return False                      # not at a task boundary
    if lifecycle.waiting_for(con, agent):
        return False
    wait = _own_int(agent, "idle_fold_after_s", FOLD_IDLE_S)
    last = delivery.idle_since(con, agent)
    # The note's own turn restarts the idle clock; it is the answer this fold was held for.
    if not handoff_note.answered_turn(con, agent) and (last is None or now - last < wait):
        return False
    failed = db.last_event_ts(con, agent, "close_failed")
    return failed is None or now - failed >= wait


def fold(con, agent: str, now: int, st, log) -> tuple[bool, str]:
    """Close the session fresh and record it. The caller has held the close for the note
    (`handoff_note.hold`) and checked `fold_due`."""
    ctx = st.trusted_ctx_used
    had_note = handoff_note.text(con, agent) is not None
    idle = max(0, now - (delivery.idle_since(con, agent) or now))
    ok, why = lifecycle.close_fresh(con, agent, log, st)
    if ok:
        db.add_event(con, agent, "idle_fold", {"ctx": ctx, "idle_s": idle, "note": had_note})
        log(f"  {agent}: folded - idle {idle // 60}m at {ctx:,} tokens, closed fresh"
            + ("" if had_note else " (no note)"))
    return ok, why
