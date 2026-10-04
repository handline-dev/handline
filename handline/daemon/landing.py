"""T418: a request is delivered once the receiving session's transcript shows it, not
once it was typed.

`delivered_at` is the daemon's mark that it went to type a request. What decides the
word "delivered" (chat, `hl inbox`, the stuck list) is `landed_at`, set here from
the transcript, read every tick: a level, so a daemon that was down when the line
landed still finds it. Claude Code writes a line typed mid-turn as a
`queue-operation` entry and one typed while idle as a `user` entry, so a landing is
seen within seconds either way.

T766: one rule for a queued line, repeated every tick: the transcript first - there, it
is landed - and not there, typed unless this daemon typed it less than `LANDING_WAIT_S`
ago (kept in memory only, so a restart at worst types it once more). A line dropped by
the terminal, typed into a session that closed or was replaced, never submitted, is
owed again and typed afresh by the path that first told it, as often as it takes: there
is no count and no giving up - a line that never lands stays queued, on the stuck list.
A vendor with no transcript to read (`memory_root` is None) cannot be checked, so a
typed line is taken as landed there.

A retry can find the first line still unsent in the input box; it is then submitted
with the second, one line holding both, and that lands once.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .. import db, vendors
from ..session import handoff as handoffmod
from ..team import agents
from . import delivery

LANDING_WAIT_S = 90

# A landing is at the end of a transcript, seconds after the send.
_TAIL_BYTES = 4 << 20

# When this daemon last typed each request: what keeps a line from being typed again
# inside `LANDING_WAIT_S`. Not stored - a daemon that restarted knows nothing of it.
_typed_at: dict[int, int] = {}


def note_typed(rid: int, now: int | None = None) -> None:
    _typed_at[rid] = int(time.time()) if now is None else now


def typed_recently(rid: int, now: int) -> bool:
    return now - _typed_at.get(rid, -LANDING_WAIT_S) < LANDING_WAIT_S


def state(row, now: int | None = None) -> str:
    """Where a request is on its way into its target's session: "queued" (not typed),
    "typed" (typed, the transcript has not shown it yet) or "delivered"."""
    if row["landed_at"]:
        return "delivered"
    if not row["typed_at"]:
        return "queued"
    return "typed"


def _texts(entry: dict):
    """The text a transcript entry holds that a person or the daemon typed. Never a
    tool result: `hl inbox` prints the very header this looks for."""
    kind = entry.get("type")
    if kind == "queue-operation" and entry.get("operation") == "enqueue":
        yield entry.get("content")
    elif kind == "attachment" and isinstance(entry.get("attachment"), dict) \
            and entry["attachment"].get("type") == "queued_command":
        yield entry["attachment"].get("prompt")
    elif kind == "user" and isinstance(entry.get("message"), dict):
        content = entry["message"].get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    yield block.get("text")


def _holds(line: bytes, rid: int, header: re.Pattern) -> bool:
    if f"#{rid} from ".encode() not in line:
        return False
    try:
        entry = json.loads(line)
    except ValueError:
        return False
    return isinstance(entry, dict) and any(isinstance(t, str) and header.search(t) for t in _texts(entry))


def shown_in(transcript: Path, rid: int, whole: bool = False) -> bool:
    """Does the transcript hold the line that tells an agent about request `rid`? Its
    last `_TAIL_BYTES`, where a landing is; `whole` reads it all, for the one look
    that decides a line was never shown."""
    header = re.compile(rf"#{rid} from [^\n]*waiting for you:")
    try:
        with open(transcript, "rb") as f:
            if whole:
                return any(_holds(line, rid, header) for line in f)
            size = f.seek(0, 2)
            f.seek(max(0, size - _TAIL_BYTES))
            return any(_holds(line, rid, header) for line in f.read().split(b"\n"))
    except OSError:
        return False


def _keeps_transcripts(acct: str) -> bool:
    try:
        return vendors.for_account(acct).memory_root(vendors.config_dir(acct)) is not None
    except vendors.UnknownVendor:
        return False


def _owe_again(con, agent: str, rid: int) -> None:
    db.owe_again(con, rid)
    if db.get_cursor(con, f"notify:{agent}") == rid:
        db.forget_told(con, agent)      # a task head is told again only when the cursor is void


def landed_already(con, agent: str, rid: int) -> bool:
    """The transcript first (T766): is `rid` already in `agent`'s session? Then it is landed and
    typing it again would only repeat it. False when nothing says where to look."""
    acct = agents.current_account(agent, con)
    if not acct or not _keeps_transcripts(acct):
        return False
    row = db.get_request(con, rid)
    live = delivery._live_session(con, agent)
    sessions = [s for s in dict.fromkeys((row["typed_session"] if row else None, live)) if s]
    return any(shown_in(p, rid) for p in (handoffmod.find_transcript(s, acct) for s in sessions) if p)


def sweep(con, agent: str, log) -> None:
    """Confirm or owe again every request typed at `agent` and not yet shown in its
    transcript: shown, it is landed; not shown and not typed within `LANDING_WAIT_S` by
    this daemon (or its session gone), it is typed again - without end."""
    rows = db.awaiting_landing(con, agent)
    if not rows:
        return
    now = int(time.time())
    acct = agents.current_account(agent, con)
    if not acct:
        return          # nothing says where to look, so nothing is claimed either way
    if not _keeps_transcripts(acct):
        for row in rows:
            db.mark_landed(con, int(row["id"]))
        return
    live = delivery._live_session(con, agent)
    state_row = db.get_agent_state(con, agent)
    for row in rows:
        rid = int(row["id"])
        sessions = [s for s in dict.fromkeys((row["typed_session"], live)) if s]
        if not sessions:
            continue        # no session id is known yet: a missing line cannot be told from a missing id
        # Typed into a session that is gone is not worth waiting out: nothing will read it.
        gone = bool(row["typed_session"]) and (bool(state_row and state_row["closed_at"])
                                               or bool(live and live != row["typed_session"]))
        expired = gone or not typed_recently(rid, now)
        paths_ = [p for p in (handoffmod.find_transcript(s, acct) for s in sessions) if p]
        if any(shown_in(p, rid, whole=expired) for p in paths_):
            db.mark_landed(con, rid)
            db.add_event(con, agent, "landed", {"request_id": rid, "after_s": now - int(row["typed_at"])})
            log(f"  {agent}: #{rid} is in its transcript")
            continue
        if not expired:
            continue
        _owe_again(con, agent, rid)
        db.add_event(con, agent, "retyped", {"request_id": rid, "gone": gone})
        log(f"  {agent}: #{rid} never reached its transcript"
            f"{' (its session ended)' if gone else ''} - typed again")
