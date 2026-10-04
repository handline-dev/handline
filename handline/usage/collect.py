"""Pull what the statusLine used to push (decision D11).

The statusLine collector is a **push**: Claude Code runs it once per turn and
hands it a payload, so it is free, but it only speaks while a session speaks,
it only exists on Claude, and it occupies the one slot the founder can use for
something a human reads. This module is the **pull** side, and it takes the
same fields out of things the session writes anyway:

    session identity   the transcript file is both the id and the path
    context window     the last assistant turn's `usage`, over the model's size
    cost / lines       the `cost-state` record, when the session has written one
    which account      the transcript's own path: accounts/<acct>/.claude/...

Measured against the live collector on 19 agents (#1402): on every one of the
16 that had a figure at all, the last-turn sum reproduced the statusLine's
`current_usage` **token for token** (285,796 / 175,999 / 469,196 ... ), and on
the three that had none the transcript has none either - they are sessions that
have not taken a turn yet. So this is the same number, not an estimate of it.

Two things the pull does better. It reads the account out of the path the
session is actually writing to, which is as first-hand as the collector's own
`CLAUDE_CONFIG_DIR`; and it needs nothing installed in the vendor, which is
what makes it the mechanism for codex, whose rollout JSONL carries the same
three groups (D9).

One thing it does worse, and the reason it runs only for a live session: the
collector's `collected_at` is a heartbeat - a rendered status line means a
session is alive right now - and no pull can reproduce a heartbeat. So the
daemon collects only for agents whose session it has not closed, and a closed
agent's snapshot ages into `stale` exactly as it does today.

Rate limits are not here: they are the one group the transcript does not carry
(only a 429 once the wall is hit), and `usage_api` already pulls them from the
account's own OAuth credential.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..infra import atomic, paths
from .. import vendors


def tokens(usage: dict | None) -> int:
    """A snapshot's context in tokens: the sum of the counts its collector kept."""
    return sum(v for v in (usage or {}).values()
               if isinstance(v, int) and not isinstance(v, bool))


def ctx_size(model_id: str | None) -> int | None:
    return vendors.context_window(model_id)


def encode_cwd(account: str, cwd: str | Path) -> str:
    """A working directory as the vendor names its project directory.

    Claude Code's answer, which this was written against, maps every character
    outside [A-Za-z0-9] to `-`: `/Users/ron/orca/workspaces/orcapipe` becomes
    `-Users-ron-orca-workspaces-orcapipe`, the dot going the same way as the
    slashes. Read off the live directories rather than guessed: they keep case
    (`-Users-ron-Library-Application-Support-...`) and collapse `/.claude` to
    `--claude`.
    """
    return vendors.for_account(account).memory_project_dir(str(cwd))


def project_dir(account: str, cwd: str | Path) -> Path | None:
    root = vendors.for_account(account).memory_root(vendors.config_dir(account))
    return None if root is None else root / encode_cwd(account, cwd)


@dataclass(frozen=True)
class Located:
    """Which file a session is writing, and what that tells us for free."""
    account: str
    session_id: str
    path: Path
    at: int  # when the session last wrote, which is not when we looked


def locate(cwd: str | Path, accounts: list[str] | None = None) -> Located | None:
    """The newest transcript for this working directory, across every account.

    Searching all accounts is what makes the account first-hand: the session
    chose the directory it writes into, so its path *is* the account, the same
    ground truth the collector took from `CLAUDE_CONFIG_DIR`. It also follows a
    move between accounts for nothing - the transcript left under the old
    account is simply older.

    Newest-wins is what follows a **cleared** session, which is the case that
    made it the rule rather than a convenience: `/clear` starts a new transcript
    file, and the one the daemon just cleared is dead. Measured on
    `orcapipe.chat` (#1402): eleven lines, a `/clear` command record, no turn -
    and the live session, while the statusLine was still reporting the 20%
    context of the session before the clear.

    A project directory is keyed by working directory and each agent has its own
    worktree, so nothing else writes here. `hl fold`'s `claude -p` does not
    land in it either: it runs from the daemon's directory, not the agent's.
    """
    best: Located | None = None
    for acct in (accounts if accounts is not None else paths.list_accounts()):
        try:
            d = project_dir(acct, cwd)
            entries = list(d.glob("*.jsonl")) if d else []
        except (OSError, vendors.UnknownVendor):
            continue
        for f in entries:
            try:
                at = int(f.stat().st_mtime)
            except OSError:
                continue
            if best is None or at > best.at:
                best = Located(account=acct, session_id=f.stem, path=f, at=at)
    return best


def snapshot(agent: str | None, cwd: str | Path, now: int | None = None,
             accounts: list[str] | None = None) -> dict | None:
    """One session's snapshot, in the shape every reader already expects.

    None when no transcript can be found for this working directory: nothing to
    say is written as nothing at all, never as a snapshot of zeros.
    """
    found = locate(cwd, accounts)
    if found is None:
        return None
    now = int(time.time()) if now is None else now
    drv = vendors.for_account(found.account)
    collector = drv.collector()
    read = (collector.collect(found.path, vendors.config_dir(found.account))
            if collector else None)
    if read is None:
        return None
    return {
        "account": found.account,
        "agent": agent,
        # What the session last wrote, not when we read it. The collector's
        # field means the same thing for the same reason: it is the figure
        # `status.stale` judges, and a pull that stamped its own clock here
        # would report a dead session as fresh forever.
        "collected_at": found.at,
        "pulled_at": now,
        "session_id": found.session_id,
        "transcript_path": str(found.path),
        "model_id": read.model_id,
        "cwd": str(cwd),
        "ctx_used": read.ctx_used,
        "ctx_size": read.ctx_size,
        "ctx_pct": read.ctx_pct,
        "cost_usd": read.cost_usd,
        "lines_added": read.lines_added,
        "lines_removed": read.lines_removed,
        "source": "pull",
    }


def write(agent: str, snap: dict, path: Path | None = None) -> bool:
    """Leave the snapshot where `status.read_for_agent` already looks.

    The account file is not written here. Its rate limits are the account's and
    `usage_api.record` owns them; its context and session id were only ever
    whichever session happened to render last, and `read_for_agent` overrides
    both from this file.
    """
    p = path or paths.agent_status_file(agent)
    if not p.parent.is_dir():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    try:
        atomic.write_text(p, json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def collect(agent: str, cwd: str | Path, now: int | None = None) -> dict | None:
    """Pull one agent's snapshot and write it. None when there was none to write."""
    snap = snapshot(agent, cwd, now=now)
    if snap is None:
        return None
    return snap if write(agent, snap) else None
