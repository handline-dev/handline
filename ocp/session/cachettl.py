"""A session's prompt-cache clock, read from its transcript.

Claude Code writes the cache with a one-hour or a five-minute life, and every response says which:
`usage.cache_creation` splits what it wrote into `ephemeral_1h_input_tokens` and
`ephemeral_5m_input_tokens`. A subscription is on the hour; once an account draws on usage credits
the main conversation drops to five minutes, so reading the TTL off the response follows that by
itself. A response that only read the cache wrote nothing and says nothing, so the TTL is the
nearest earlier response that did. The clock itself, though, is the last response: every read
restarts the life.

Only Claude's transcript is read here. For a session whose transcript is not Claude's (or is
unreadable) there is no clock, and the callers keep what they did before there was one.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime

LONG_TTL_S = 3600
SHORT_TTL_S = 300

# The tail of a transcript read for the last responses: a turn's tool results can be large, so the read grows.
_CHUNKS = (256 * 1024, 2 * 1024 * 1024, 16 * 1024 * 1024)
# How many responses back to look for one that wrote the cache, when the last ones only read it.
_LOOK_BACK = 60


@dataclass(frozen=True)
class Clock:
    at: float               # when the last response of the main conversation was written (epoch seconds)
    ttl: int | None         # that cache's life: LONG_TTL_S, SHORT_TTL_S, or None when no response says

    def left(self, now: float) -> float | None:
        """Seconds until the cache lapses (negative once it has), or None when the TTL is not known."""
        return None if self.ttl is None else self.at + self.ttl - now


def margin(ttl: int) -> int:
    """What a resume needs under the TTL to reach its first request before the cache is gone:
    five minutes of an hour (what it always was), scaled down for a short life."""
    return min(300, ttl // 12)


def _ttl_of(usage: dict) -> int | None:
    made = usage.get("cache_creation")
    if not isinstance(made, dict):
        return None
    one_h, five_m = made.get("ephemeral_1h_input_tokens"), made.get("ephemeral_5m_input_tokens")
    if isinstance(one_h, int) and one_h > 0:
        return LONG_TTL_S
    if isinstance(five_m, int) and five_m > 0:
        return SHORT_TTL_S
    return None


def _when(stamp) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tail_lines(path: str, size: int, want: int) -> list[str]:
    with open(path, "rb") as f:
        f.seek(max(0, size - want))
        data = f.read()
    lines = data.decode("utf-8", errors="replace").split("\n")
    return lines[1:] if size > want else lines          # the first line of a cut read is partial


def _scan(lines: list[str]) -> tuple[Clock | None, bool]:
    """(the clock, whether it is whole): the last response, and the TTL of it or of the nearest
    earlier one that wrote the cache. Not whole when the lines ran out before either was found."""
    at: float | None = None
    seen = 0
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") if isinstance(d, dict) else None
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if d.get("type") != "assistant" or d.get("isSidechain") or not isinstance(usage, dict):
            continue
        when = _when(d.get("timestamp"))
        if when is None:
            continue
        if at is None:
            at = when
        ttl = _ttl_of(usage)
        seen += 1
        if ttl is not None:
            return Clock(at, ttl), True
        if seen >= _LOOK_BACK:
            return Clock(at, None), True
    return (Clock(at, None) if at is not None else None), False


_memo: dict[str, tuple[int, int, Clock | None]] = {}


def read(path: str | None) -> Clock | None:
    """The clock of the session whose transcript is at `path`, or None when there is none to read.
    Answered from memory while the file is unchanged, so asking every tick costs a stat."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _memo.get(path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    clock: Clock | None = None
    try:
        for want in _CHUNKS:
            clock, whole = _scan(_tail_lines(path, st.st_size, want))
            if whole or want >= st.st_size:
                break
    except OSError:
        return None
    if len(_memo) > 256:
        _memo.clear()
    _memo[path] = (st.st_mtime_ns, st.st_size, clock)
    return clock
