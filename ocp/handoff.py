"""handoff - what an agent is handed back when it comes back from a park or a move.

Three facts, read from what is on disk and in the database, with no model call:

  1. the request it was working on (an agent has one delivered at a time, #189),
  2. the path of the session's transcript,
  3. the worktree's branch, HEAD and git status.

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

import re
import subprocess
import time
from pathlib import Path

from . import agents, db, paths, spawn as spawnmod, status, textsafe, vendors

# Enough to see what is in flight; the rest is `git status` away.
_STATUS_LINES = 20


def _git(worktree: str, *args: str) -> str | None:
    """git's stdout, or None when it could not be run or exited non-zero."""
    try:
        r = subprocess.run(["git", *args], cwd=worktree, capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def _request_line(con, agent: str) -> str:
    """The delivered request. None delivered is a fact about the agent, not a
    failure to read one: an agent between requests has nothing in progress."""
    head = db.head_of_queue(con, agent)
    if head is None or not head["delivered_at"]:
        return "request: none in progress"
    first = ((head["body"] or "").splitlines() or [""])[0][:120]
    return f"request: #{head['id']} from {head['requester']} - {first}"


def find_transcript(session: str, account: str) -> Path | None:
    """Where a session's transcript is, found by its id rather than by a status
    snapshot: nothing refreshes a closed agent's snapshot, so by the time a
    closed session is looked at the snapshot no longer names it. The account it
    ran under is searched first."""
    if not session or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", session):
        return None
    # One directory level under the vendor's memory root is where a transcript
    # is written, so the search is that deep and no deeper.
    for acct in [account, *(a for a in paths.list_accounts() if a != account)]:
        try:
            root = vendors.for_account(acct).memory_root(vendors.config_dir(acct))
        except vendors.UnknownVendor:
            continue
        for f in root.glob(f"*/{session}.jsonl"):
            return f
    return None


def _session_lines(agent: str, account: str,
                   closed_session: str | None = None) -> tuple[list[str], str | None]:
    if closed_session:
        found = find_transcript(closed_session, account)
        session, path = closed_session, str(found) if found else None
    else:
        st = status.read_for_agent(agent, account)
        session, path = st.session_id, st.transcript_path
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


def capture(con, agent: str, closed_session: str | None = None) -> tuple[str, list[str]]:
    """The record, and what could not be read for it (empty when it is whole).

    `closed_session` is the id of a session that has already ended: its
    transcript is then looked up by id instead of read from the live snapshot."""
    row = db.get_agent_state(con, agent)
    worktree = spawnmod.worktree_path(row["worktree"] if row else None)
    lines = [f"# handoff · {agent}", f"captured: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             _request_line(con, agent)]
    missing: list[str] = []
    for got, gap in (_session_lines(agent, agents.current_account(agent, con), closed_session),
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
