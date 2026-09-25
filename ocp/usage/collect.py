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

from ..infra import paths
from .. import vendors

# Context window size per model. The transcript does not carry it, and
# `agent-context --json` has no field for it either (measured for D9: 236
# commands, 0 mentions), so it is a table - seeded from what the statusLine
# itself reported across the fleet on 2026-09-21, where every session on both
# models said 1,000,000.
#
# An unrecognised model gets **no** figure rather than a guessed one. Too small
# a guess parks every agent on the context trigger at a fifth of its real
# window; the trigger is a rare backstop (5 of 271 parks) and is better silent
# than wrong. Keyed by prefix, so a dated model id still matches.
CTX_SIZE = (
    ("claude-opus-5", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-haiku-4-5", 200_000),
)

# How much of the transcript's tail to read looking for the last assistant
# turn. Measured on the fleet: the record was within 13 lines of the end on
# every agent. But one line can be a multi-megabyte tool result, so a tail that
# finds nothing is doubled rather than believed - up to a ceiling, past which
# this reports no figure instead of reading a 10MB file every 30 seconds.
TAIL_BYTES = 256 * 1024
TAIL_MAX_BYTES = 8 * 1024 * 1024

# The four counts that make up a turn's context, and the whole of what the
# statusLine's `context_window.current_usage` carried. Named rather than summed
# over the record, because the transcript's `usage` block holds far more -
# `iterations`, `service_tier`, per-cache breakdowns - and a later version
# adding one more top-level integer would silently inflate a total that gates
# parking. Slimming it is also KICKOFF rule 5: only the fields with a reader.
USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")

# Claude Code's own name for a message it generated locally - an interrupt
# notice, an API error. It carries a `usage` block of zeros, which would read as
# "this session has no context" on an agent that has been running for hours.
SYNTHETIC_MODEL = "<synthetic>"


def slim_usage(usage: dict) -> dict:
    """The four counts, as the statusLine wrote them."""
    out = {}
    for k in USAGE_KEYS:
        v = usage.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out


def tokens(usage: dict | None) -> int:
    return sum(slim_usage(usage or {}).values())


def ctx_size(model_id: str | None) -> int | None:
    if not isinstance(model_id, str):
        return None
    for prefix, size in CTX_SIZE:
        if model_id.startswith(prefix):
            return size
    return None


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
    worktree, so nothing else writes here. `ocp fold`'s `claude -p` does not
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


def _tail(path: Path, nbytes: int) -> bytes:
    with path.open("rb") as fh:
        try:
            fh.seek(-nbytes, 2)
        except OSError:
            fh.seek(0)
        return fh.read()


@dataclass(frozen=True)
class Turn:
    """The last assistant turn a transcript records.

    `whole_file` separates the two ways there can be no turn, which have
    opposite right answers: a session that has not answered yet has spent
    nothing, and a search that gave up on a large file knows nothing.
    """
    usage: dict | None
    model: str | None
    whole_file: bool


def last_turn(path: Path) -> Turn:
    """The `usage` and model of the last real assistant turn in a transcript.

    Scans backwards from the end. A record that will not parse is skipped
    rather than trusted: the tail begins mid-line, and the line at the end may
    be half-written by a session that is mid-turn right now.

    One record, never a sum, which is what keeps this clear of the trap design
    01-infra.md section 4 warns about: one API response's content blocks each
    become a line and each **copies the same usage object**, so anything that
    adds them up overcounts (measured at 2.4x, in `burnrate`). Reading the last
    copy gives the same figure as reading any other.
    """
    size = TAIL_BYTES
    while True:
        try:
            blob = _tail(path, size)
            whole = size >= path.stat().st_size
        except OSError:
            return Turn(None, None, False)
        for raw in reversed(blob.splitlines()):
            if b'"usage"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            model = msg.get("model")
            # A synthetic turn, or any turn reporting nothing, is not the
            # session's context - keep looking for the last one that is.
            if tokens(usage) <= 0 or model == SYNTHETIC_MODEL:
                continue
            return Turn(slim_usage(usage),
                        model if isinstance(model, str) else None, whole)
        if whole or size >= TAIL_MAX_BYTES:
            return Turn(None, None, whole)
        size *= 2


def cost_state(path: Path) -> dict | None:
    """The session's own cost record, when it has written one.

    Display only (`status.live_cost`), and not every Claude Code version writes
    it, so its absence is not a failure of the pull.
    """
    try:
        blob = _tail(path, TAIL_BYTES)
    except OSError:
        return None
    for raw in reversed(blob.splitlines()):
        if b'"cost-state"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict) and rec.get("type") == "cost-state":
            return rec
    return None


def _pct(used: int, size: int | None) -> int | None:
    if not size or used <= 0:
        return None
    # The statusLine rounds: 28.58% was reported as 29 and 6.17% as 6. Matching
    # it is what lets the two be compared field for field.
    return max(0, min(100, int(used * 100 / size + 0.5)))


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
    turn = last_turn(found.path)
    usage, model = turn.usage, turn.model
    size = ctx_size(model)
    # A session with no turn in a file we read whole has answered nothing, so
    # its context is its preamble and nothing else - 0, not unknown. A session
    # whose file we could not search to the end is unknown, and says so. The
    # two are one `/clear` apart on a live agent: the cleared session reads 0,
    # which is the fact the clear was for.
    pct = (_pct(tokens(usage), size) if usage is not None
           else (0 if turn.whole_file else None))
    cost = cost_state(found.path) or {}
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
        "model_id": model,
        "cwd": str(cwd),
        "ctx_used": usage,
        "ctx_size": size,
        "ctx_pct": pct,
        "cost_usd": cost.get("totalCostUSD"),
        "lines_added": cost.get("totalLinesAdded"),
        "lines_removed": cost.get("totalLinesRemoved"),
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
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(p)
    except OSError:
        return False
    return True


def collect(agent: str, cwd: str | Path, now: int | None = None) -> dict | None:
    """Pull one agent's snapshot and write it. None when there was none to write."""
    snap = snapshot(agent, cwd, now=now)
    if snap is None:
        return None
    return snap if write(agent, snap) else None
