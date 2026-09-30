"""handoff - what an agent is handed back when it comes back from a park or a move.

Four facts, read from what is on disk and in the database, with no model call:

  1. the request it was working on (an agent has one delivered at a time, #189),
  2. the progress note the agent itself wrote on that request (`ocp progress`),
  3. the path of the session's transcript,
  4. the worktree's branch, HEAD and git status.

Nothing else. Everything a summary would add is in the transcript, and a summary
is a model call, which fails when every account is walled - the moment a
handoff is most needed. This one cannot fail on that: a fact that could not be
read is named in the record, and the handoff is degraded only for that reason.

The record is taken while the session is still live, because the agent's own
status snapshot goes stale once it is closed and its transcript path with it. A
close that failed to take one leaves none; the wake then takes it from the
closed session's id, finding the transcript by name.
It is stored on the agent's row (`db.get_handoff`). A wake that types it to the
agent clears it (`db.set_handoff(con, agent, None)`), and so does a launch,
which injects it into the system prompt: a record on the row is one no session
has been handed.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..team import agents, spawn as spawnmod
from .. import db, vendors
from ..infra import gitrun, paths, textsafe
from ..usage import status

# Enough to see what is in flight; the rest is `git status` away.
_STATUS_LINES = 20


def _git(worktree: str, *args: str) -> str | None:
    """git's stdout, or None when it could not be run or exited non-zero."""
    rc, out, _ = gitrun.run(worktree, *args)
    return out.rstrip("\n") if rc == 0 else None


def _request_line(con, agent: str) -> str:
    """The delivered request - the agent's effective head (T337, auditor
    #2028: not `db.head_of_queue` directly, which a blocked head would name
    even though the agent was never told it and is working on whatever
    stood in for it instead). None delivered is a fact about the agent, not
    a failure to read one: an agent between requests has nothing in
    progress."""
    from ..daemon import delivery  # local: daemon.delivery imports daemon.handoff, which imports this module
    head = delivery.effective_head(con, agent)
    if head is None or not head["delivered_at"]:
        return "request: none in progress"
    first = ((head["body"] or "").splitlines() or [""])[0][:120]
    return f"request: #{head['id']} from {head['requester']} - {first}"


# The note is the agent's own words about what is done and what is left; the
# handoff is typed into a fresh session, so it has to stay short.
PROGRESS_MAX = 1500


def _progress_key(request_id: int) -> str:
    return f"progress:{request_id}"


def _agent_progress_key(agent: str) -> str:
    return f"progress-agent:{agent}"


def set_progress(con, agent: str, note: str) -> tuple[bool, str]:
    """Keep `note` as the progress on the agent's delivered request (its
    effective head, T337 - the one it was actually told, not a blocked true
    head it never saw), replacing the last one. With no request in progress it
    is kept on the agent instead, so a deferred agent can still leave a handoff
    note (T366). An empty note clears it."""
    from ..daemon import delivery  # local: see _request_line
    head = delivery.effective_head(con, agent)
    delivered = head is not None and bool(head["delivered_at"])
    note = textsafe.text(note).strip()
    if len(note) > PROGRESS_MAX:
        return False, (f"{len(note)} characters; the handoff carries at most "
                       f"{PROGRESS_MAX} - keep what is done and what is left")
    if not delivered:
        db.set_meta(con, _agent_progress_key(agent), note or None)
        return True, "you (no request in progress)"
    db.set_meta(con, _agent_progress_key(agent), None)
    db.set_meta(con, _progress_key(int(head["id"])), note or None)
    return True, f"#{head['id']}"


def progress_of(con, request_id: int) -> str | None:
    """The progress note kept on this request, whether or not it is the head."""
    return db.get_meta(con, _progress_key(request_id)) or None


def get_progress(con, agent: str) -> str | None:
    """The note kept on the agent's effective head (T337 - see _request_line), or
    on the agent when it has no request in progress (T366)."""
    from ..daemon import delivery  # local: see _request_line
    head = delivery.effective_head(con, agent)
    if head is None or not head["delivered_at"]:
        return db.get_meta(con, _agent_progress_key(agent)) or None
    return db.get_meta(con, _progress_key(int(head["id"]))) or None


def _progress_lines(con, agent: str) -> list[str]:
    note = get_progress(con, agent)
    if not note:
        return []
    return ["progress (written by the agent):"] + ["  " + l for l in note.splitlines()]


def find_transcript(session: str, account: str | None) -> Path | None:
    """Where a session's transcript is, found by its id rather than by a status
    snapshot: nothing refreshes a closed agent's snapshot, so by the time a
    closed session is looked at the snapshot no longer names it. The account it
    ran under is searched first."""
    if not session or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", session):
        return None
    # One directory level under the vendor's memory root is where a transcript
    # is written, so the search is that deep and no deeper.
    for acct in [*([account] if account else []), *(a for a in paths.list_accounts() if a != account)]:
        try:
            root = vendors.for_account(acct).memory_root(vendors.config_dir(acct))
        except vendors.UnknownVendor:
            continue
        if root is None:
            continue
        for f in root.glob(f"*/{session}.jsonl"):
            return f
    return None


# The events a session that has actually been talked to leaves behind. A fresh
# or just-cleared session has only its SessionStart, so it is never mistaken for
# the one a restart came from.
_TALKED_EVENTS = ("UserPromptSubmit", "Stop", "StopFailure")


def _last_talked_session(con, agent: str) -> tuple[str | None, str | None]:
    """(session id, transcript path) from the agent's newest hook event of a
    session that was actually used, for when the status snapshot names neither."""
    rows = con.execute(
        "SELECT payload FROM events WHERE agent = ? AND kind IN (?, ?, ?) "
        "ORDER BY id DESC LIMIT 20", (agent, *_TALKED_EVENTS)).fetchall()
    for r in rows:
        try:
            data = json.loads(r["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("session_id"), str):
            path = data.get("transcript_path")
            return data["session_id"], path if isinstance(path, str) and path else None
    return None, None


def previous_session(con, agent: str, account: str | None,
                     closed_session: str | None = None) -> tuple[str | None, str | None]:
    """(session id, transcript path) of the session an agent is restarting
    from, as much of it as can be found: the status snapshot, then the agent's
    own hook events for an id, then the id looked up by name for a path."""
    if closed_session:
        session, path = closed_session, None
    else:
        st = status.read_for_agent(agent, account)
        session, path = st.session_id, st.transcript_path
        if not session or not path:
            ev_session, ev_path = _last_talked_session(con, agent)
            session = session or ev_session
            path = path or (ev_path if ev_session == session else None)
    if session and not path:
        found = find_transcript(session, account or "")
        path = str(found) if found else None
    return session, path


def _session_lines(con, agent: str, account: str | None,
                   closed_session: str | None = None) -> tuple[list[str], str | None]:
    session, path = previous_session(con, agent, account, closed_session)
    if not path:
        return [], "the session's transcript path"
    return [f"previous session: {session}, recorded at {path}",
            "Grep it for what you need - do not read it whole, it is a JSONL "
            "transcript with every tool result in it."], None


def _worktree_lines(worktree: str | None) -> tuple[list[str], str | None]:
    if not worktree or not Path(worktree).is_dir():
        return [], "the worktree (none recorded, or gone)"
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(worktree, "rev-parse", "--short", "HEAD")
    dirty = _git(worktree, "status", "--porcelain")
    if branch is None or head is None or dirty is None:
        return [], "the worktree's branch, HEAD or git status"
    lines = [f"worktree: {worktree}",
            f"branch: {textsafe.oneline(branch)} @ {textsafe.oneline(head)}"]
    # A status line names a path the agent's own worktree chose - the same
    # class of text a filename in `ocp drift` is, and not one git quotes for
    # us here (`status --porcelain` is meant for scripts to parse, not shown
    # to a terminal, so it never has git's own quoting to lean on).
    changed = dirty.splitlines()
    lines.append("git status: clean" if not changed else "git status:")
    lines += [f"  {textsafe.oneline(c)}" for c in changed[:_STATUS_LINES]]
    if len(changed) > _STATUS_LINES:
        lines.append(f"  ... and {len(changed) - _STATUS_LINES} more")
    return lines, None


def memory_line(agent: str) -> str:
    """One line for the agent's next session when its memory needs tending -
    over budget, files the index does not list, files unused for a month - and
    "" when it does not. Never an edit: the agent merges or deletes, and ocp
    only says what it found (`team/memory.py`)."""
    try:
        from ..team import memory  # core's
        st = memory.status(agent)
    except Exception:  # noqa: BLE001 - a memory that cannot be read is not worth a handoff
        return ""
    over, unindexed, stale = bool(st.get("over")), list(st.get("unindexed") or []), list(st.get("stale") or [])
    if not (over or unindexed or stale):
        return ""
    parts = [f"memory: index {st.get('bytes')}/{memory.INDEX_BYTES} bytes, "
             f"{st.get('entries')}/{memory.INDEX_ENTRIES} entries"
             + (" - over budget, so writes that grow it are refused until you merge or delete entries"
                if over else "")]
    if unindexed:
        parts.append(f"not in the index (never loaded): {', '.join(unindexed[:6])}")
    if stale:
        parts.append(f"unused for {memory.STALE_DAYS}+ days: {', '.join(stale[:6])}")
    parts.append("merge, promote or delete these before you finish")
    return "; ".join(parts)


def capture(con, agent: str, closed_session: str | None = None) -> tuple[str, list[str]]:
    """The record, and what could not be read for it (empty when it is whole).

    `closed_session` is the id of a session that has already ended: its
    transcript is then looked up by id instead of read from the live snapshot."""
    row = db.get_agent_state(con, agent)
    worktree = spawnmod.worktree_path(row["worktree"] if row else None)
    lines = [f"# handoff · {agent}", f"captured: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             _request_line(con, agent), *_progress_lines(con, agent)]
    if note := memory_line(agent):
        lines.append(note)
    missing: list[str] = []
    for got, gap in (_session_lines(con, agent, agents.current_account(agent, con), closed_session),
                     _worktree_lines(worktree)):
        lines += got
        if gap:
            missing.append(gap)
            lines.append(f"(could not read {gap})")
    return "\n".join(lines), missing


def record(con, agent: str, closed_session: str | None = None) -> list[str]:
    """Capture and keep it for the agent's next start. Returns what was missing."""
    text, missing = capture(con, agent, closed_session)
    db.set_handoff(con, agent, text)
    return missing
