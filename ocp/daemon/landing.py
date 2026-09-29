"""T418: a request is delivered once the receiving session's transcript shows it, not
once it was typed.

`delivered_at` is the daemon's mark that it went to type a request. What decides the
word "delivered" (chat, `ocp inbox`, the stuck list) is `landed_at`, set here from
the transcript, read every tick: a level, so a daemon that was down when the line
landed still finds it. Claude Code writes a line typed mid-turn as a
`queue-operation` entry and one typed while idle as a `user` entry, so a landing is
seen within seconds either way.

A line the transcript does not show `LANDING_WAIT_S` after it was typed - dropped by
the terminal, typed into a session that closed or was replaced, never submitted - is
owed again and typed afresh by the path that first told it, up to `MAX_TRIES` times
in all; after that it is "lost", said once to its asker and kept on the stuck list.
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
from . import delivery, timebox

LANDING_WAIT_S = 90
MAX_TRIES = 3

# A landing is at the end of a transcript, seconds after the send.
_TAIL_BYTES = 4 << 20
# A request given up on is looked for again this seldom: a daemon that was down while
# the line landed, and then more than the tail was written, finds it here.
LOST_RECHECK_S = 600
# What a lost request says of itself, wherever it is shown.
LOST_TEXT = f"typed {MAX_TRIES} times, never in its transcript"


def state(row, now: int | None = None) -> str:
    """Where a request is on its way into its target's session: "queued" (not typed),
    "typed" (typed, the transcript has not shown it yet), "lost" (typed `MAX_TRIES`
    times and never shown) or "delivered"."""
    if row["landed_at"]:
        return "delivered"
    if not row["typed_at"]:
        return "queued"
    now = int(time.time()) if now is None else now
    if int(row["landing_tries"] or 0) + 1 >= MAX_TRIES and now - int(row["typed_at"]) >= LANDING_WAIT_S:
        return "lost"
    return "typed"


def _texts(entry: dict):
    """The text a transcript entry holds that a person or the daemon typed. Never a
    tool result: `ocp inbox` prints the very header this looks for."""
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


def _owe_again(con, agent: str, rid: int, counted: bool) -> None:
    db.owe_again(con, rid, counted)
    if db.get_cursor(con, f"notify:{agent}") == rid:
        db.forget_told(con, agent)      # a task head is told again only when the cursor is void


def _report_lost(con, row, log, now: int) -> None:
    rid, agent = int(row["id"]), row["target"]
    if db.get_cursor(con, f"landing_lost:{rid}"):
        return
    text = (f"#{rid} to {agent}: {LOST_TEXT}, so {agent} has not read it. `ocp read {rid}` shows it; a message to {agent} is how to reach it.")
    if timebox._tell(con, row["requester"], text, now):
        db.set_cursor(con, f"landing_lost:{rid}", now)
        db.add_event(con, agent, "landing_lost", {"request_id": rid, "tries": MAX_TRIES})
        log(f"  #{rid}: to {agent}, {LOST_TEXT} - {row['requester']} told")


def sweep(con, agent: str, log) -> None:
    """Confirm, retry or give up on every request typed at `agent` and not yet
    shown in its transcript."""
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
        expired = gone or now - int(row["typed_at"]) >= LANDING_WAIT_S
        given_up = expired and not gone and int(row["landing_tries"] or 0) + 1 >= MAX_TRIES
        if given_up and db.get_cursor(con, f"landing_lost:{rid}") \
                and now - db.get_cursor(con, f"landing_recheck:{rid}") < LOST_RECHECK_S:
            continue        # said already: looked at again only now and then, not every tick
        paths_ = [p for p in (handoffmod.find_transcript(s, acct) for s in sessions) if p]
        if any(shown_in(p, rid, whole=expired) for p in paths_):
            db.mark_landed(con, rid)
            db.add_event(con, agent, "landed", {"request_id": rid, "after_s": now - int(row["typed_at"]),
                                                "tries": int(row["landing_tries"] or 0) + 1})
            log(f"  {agent}: #{rid} is in its transcript")
            continue
        if not expired:
            continue
        if given_up:
            db.set_cursor(con, f"landing_recheck:{rid}", now)
            _report_lost(con, row, log, now)
            continue
        # A session that ended is no evidence against the terminal, so it uses up no try.
        _owe_again(con, agent, rid, counted=not gone)
        db.add_event(con, agent, "retyped", {"request_id": rid, "gone": gone})
        log(f"  {agent}: #{rid} never reached its transcript"
            f"{' (its session ended)' if gone else ''} - typed again")
