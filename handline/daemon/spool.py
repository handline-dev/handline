"""The hook spool: where a hook leaves an event when the daemon is down (plan, "Agent hooks and the record
path"). A hook sends its event to the daemon; if it cannot, it appends one line here, and the daemon reads the
file and removes it at start. It only records: nothing in it decides or acts, and the daemon applies each line
the same way it applies a call, once, by its request id."""
from __future__ import annotations

import fcntl
import json
import os
import time

from ..infra import paths

NAME = "hook-spool.jsonl"


def path():
    return paths.home() / NAME


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


def ingest(con, log) -> int:
    """Apply every spooled line, then remove the file; the number applied. The file is renamed first, so a
    hook writing meanwhile starts a new one, and a file left by a crash is read again: a line already applied
    is applied once, by its request id."""
    from . import rpc_hooks
    work = path().with_suffix(".ingesting")
    if path().exists() and not work.exists():
        os.rename(path(), work)
    if not work.exists():
        return 0
    n = 0
    for raw in work.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            line = json.loads(raw)
            rpc_hooks.apply(con, line["agent"], line["kind"], line["data"], str(line["request_id"]), int(line["at"]))
            n += 1
        except Exception as e:  # noqa: BLE001 - one bad line is not the others' loss
            log(f"  spool: skipped a line ({type(e).__name__}: {e})")
    work.unlink()
    return n
