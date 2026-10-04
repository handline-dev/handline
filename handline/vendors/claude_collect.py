"""Claude's collector: what a session's transcript says about its context.

The pull side of decision D11 (see usage/collect.py for where the transcript is
found and the snapshot written). Measured against the live statusLine collector
on 19 agents (#1402): on every one of the 16 that had a figure, the last-turn
sum reproduced the statusLine's `current_usage` token for token, and on the
three that had none the transcript has none either.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import Snapshot

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
# parking. Slimming it is also AGENTS.md rule 5: only the fields with a reader.
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


def context_window(model_id: str | None) -> int | None:
    if not isinstance(model_id, str):
        return None
    for prefix, size in CTX_SIZE:
        if model_id.startswith(prefix):
            return size
    return None


def _tail(path: Path, nbytes: int) -> bytes:
    with path.open("rb") as fh:
        try:
            fh.seek(-nbytes, 2)
        except OSError:
            fh.seek(0)
        return fh.read()


def last_turn(path: Path) -> tuple[dict | None, str | None, bool]:
    """(usage, model, whole_file) of the last real assistant turn in a transcript.

    `whole_file` separates the two ways there can be no turn, which have
    opposite right answers: a session that has not answered yet has spent
    nothing, and a search that gave up on a large file knows nothing.

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
            return None, None, False
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
            slim = slim_usage(usage)
            # A synthetic turn, or any turn reporting nothing, is not the
            # session's context - keep looking for the last one that is.
            if sum(slim.values()) <= 0 or model == SYNTHETIC_MODEL:
                continue
            return slim, model if isinstance(model, str) else None, whole
        if whole or size >= TAIL_MAX_BYTES:
            return None, None, whole
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


class ClaudeCollector:
    def collect(self, transcript: Path, config_dir: Path) -> Snapshot | None:
        usage, model, whole = last_turn(transcript)
        size = context_window(model)
        # A session with no turn in a file we read whole has answered nothing, so
        # its context is its preamble and nothing else - 0, not unknown. A session
        # whose file we could not search to the end is unknown, and says so. The
        # two are one `/clear` apart on a live agent: the cleared session reads 0,
        # which is the fact the clear was for.
        pct = (_pct(sum(usage.values()), size) if usage is not None
               else (0 if whole else None))
        cost = cost_state(transcript) or {}
        return Snapshot(
            model_id=model,
            ctx_used=usage,
            ctx_size=size,
            ctx_pct=pct,
            cost_usd=cost.get("totalCostUSD"),
            lines_added=cost.get("totalLinesAdded"),
            lines_removed=cost.get("totalLinesRemoved"),
        )
