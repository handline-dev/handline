"""The commands' way to the daemon's methods: one JSON-RPC call on a fresh connection (`daemon.client.call`), as the
founder when this terminal holds the founder key, and as the agent when it runs in an agent's session (the daemon
tells which from the process, not from anything said here). `connect` is the one place a socket comes from, so a
test serves a socketpair in its place."""
from __future__ import annotations

import socket
import uuid
from datetime import datetime

from ..daemon import client
from ..infra import sessiontoken

CLIENT = "hl"


def connect() -> socket.socket:
    return client.ensure_daemon()


def call(method: str, params: dict | None = None) -> dict:
    """The result of `method`. client.DaemonDown when the daemon is not there, client.RpcError when it refuses."""
    return client.call(connect(), method, params, CLIENT, sessiontoken.founder_key_held())


def write(method: str, params: dict) -> tuple[bool, str]:
    """A write under a fresh request_id: (True, what it said) or (False, why it was refused or not made)."""
    ok, said, _got = written(method, params)
    return ok, said


def written(method: str, params: dict) -> tuple[bool, str, dict]:
    """`write`, and the whole result beside the words ({} when refused or not made)."""
    try:
        got = call(method, {**params, "request_id": uuid.uuid4().hex})
    except client.DaemonDown as e:
        return False, f"daemon not running, nothing done: {e}", {}
    except client.RpcError as e:
        return False, str(e), {}
    except OSError as e:
        return False, f"the daemon did not answer, so it is not known whether this was done: {e}", {}
    return True, got.get("said", ""), got


def said(ok: bool, text: str) -> int:
    """Print what a write said, and its exit code."""
    print(text)
    return 0 if ok else 1


def epoch(ts: str | None) -> int | None:
    """A wire Timestamp (`2026-10-07T12:00:00Z`) as seconds, for timefmt to print in local time."""
    return None if not ts else int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp())
