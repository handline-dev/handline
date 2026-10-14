"""Where a hook leaves an event when the daemon does not answer: one JSON line per event, which the daemon reads
and removes at start (`daemon/spool.ingest`). It only records: nothing here decides or acts."""
from __future__ import annotations

import fcntl
import json
import os
import time

from . import paths


def path():
    return paths.home() / paths.HOOK_SPOOL


def append(agent: str, kind: str, data: dict, request_id: str, at: int | None = None) -> None:
    """One line, written whole under an exclusive lock, so lines from hooks running at once never mix."""
    line = json.dumps({"agent": agent, "kind": kind, "at": int(time.time()) if at is None else at,
                       "data": data, "request_id": request_id}) + "\n"
    fd = os.open(path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
