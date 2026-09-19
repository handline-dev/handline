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
status snapshot goes stale once it is closed and its transcript path with it.
It is stored on the agent's row (`db.get_handoff`) and cleared once handed over
(`db.set_handoff(con, agent, None)`), so a later, unrelated start never gets it.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import agents, db, spawn as spawnmod, status

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


def _session_lines(agent: str, account: str) -> tuple[list[str], str | None]:
    st = status.read_for_agent(agent, account)
    if not st.transcript_path:
        return [], "the session's transcript path"
    return [f"previous session: {st.session_id}, recorded at {st.transcript_path}",
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
    lines = [f"worktree: {worktree}", f"branch: {branch} @ {head}"]
    changed = dirty.splitlines()
    lines.append("git status: clean" if not changed else "git status:")
    lines += [f"  {c}" for c in changed[:_STATUS_LINES]]
    if len(changed) > _STATUS_LINES:
        lines.append(f"  ... and {len(changed) - _STATUS_LINES} more")
    return lines, None


def capture(con, agent: str) -> tuple[str, list[str]]:
    """The record, and what could not be read for it (empty when it is whole)."""
    row = db.get_agent_state(con, agent)
    worktree = spawnmod.worktree_path(row["worktree"] if row else None)
    lines = [f"# handoff · {agent}", f"captured: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             _request_line(con, agent)]
    missing: list[str] = []
    for got, gap in (_session_lines(agent, agents.current_account(agent, con)),
                     _worktree_lines(worktree)):
        lines += got
        if gap:
            missing.append(gap)
            lines.append(f"(could not read {gap})")
    return "\n".join(lines), missing


def record(con, agent: str) -> list[str]:
    """Capture and keep it for the agent's next start. Returns what was missing."""
    text, missing = capture(con, agent)
    db.set_handoff(con, agent, text)
    return missing
