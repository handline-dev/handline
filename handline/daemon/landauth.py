"""What the session's own `ocp land` command vouched for, held for a minute and used once.

`session/landgate.py` records a grant when the session's PreToolUse hook sees the exact command; the
daemon takes it when the matching verb arrives (`socket_server._handle_request`). The sha is the text
typed in the command, which is also what `ocp land` sends.
"""
from __future__ import annotations

import threading
import time

TTL_S = 60.0
_lock = threading.Lock()
_grants: dict[tuple[str, str, str], float] = {}


def grant(agent: str, verb: str, sha: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        for k in [k for k, exp in _grants.items() if exp <= now]:
            del _grants[k]
        _grants[(agent, verb, sha)] = now + TTL_S


def take(agent: str, verb: str, sha: str, now: float | None = None) -> bool:
    """Whether a live grant for exactly this agent, verb and sha existed; it is spent either way."""
    now = time.time() if now is None else now
    with _lock:
        exp = _grants.pop((agent, verb, sha), 0.0)
    return exp > now
