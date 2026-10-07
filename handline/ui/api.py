"""The screens' one way to the daemon's data: a JSON-RPC call on a fresh connection (`daemon.client.call`).

A screen shows what the daemon says and never reads the database itself. `connect` is the one place a socket comes from, so a test
serves a socketpair in its place."""
from __future__ import annotations

import socket
from datetime import datetime, timezone

from ..daemon import client
from ..infra import sessiontoken

CLIENT = "ui"


def connect() -> socket.socket:
    return client.ensure_daemon()


def call(method: str, params: dict | None = None) -> dict:
    """The result of `method`. client.DaemonDown when the daemon is not there, client.RpcError when it refuses."""
    return client.call(connect(), method, params, CLIENT, sessiontoken.founder_key_held())


def epoch(ts: str | None) -> int | None:
    """Seconds since 1970 of a time the daemon states (`2026-10-07T10:20:51Z`)."""
    return None if ts is None else int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
