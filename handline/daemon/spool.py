"""The hook spool: where a hook leaves an event when the daemon is down (plan, "Agent hooks and the record
path"). A hook sends its event to the daemon; if it cannot, it appends one line here, and the daemon reads the
file and removes it at start. It only records: nothing in it decides or acts, and the daemon applies each line
the same way it applies a call, once, by its request id."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
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


# What is not the line's fault, and so keeps it: the database busy or failing (sqlite3.Error: a lock that timed
# out, a full disk, a corrupt page), the file system (OSError: unreadable, no space), and memory. Everything
# else raised while parsing or applying one line (bad JSON, a missing key, a value of the wrong type) is that
# line's own and it is skipped, logged, and the rest go on.
TRANSIENT = (sqlite3.Error, OSError, MemoryError)


def ingest(con, log) -> int:
    """Apply every spooled line, then remove the file; the number applied. The file is renamed first, so a hook
    writing meanwhile starts a new one. A line that cannot be parsed or applied is skipped; on a failure that is
    not the line's fault the lines not yet applied are left in the file for the next try (the next tick or
    start), and the ones already applied are safe to see again: each applies once, by its request id."""
    from . import rpc_hooks
    work = path().with_suffix(".ingesting")
    if path().exists() and not work.exists():
        os.rename(path(), work)
    if not work.exists():
        return 0
    lines = work.read_text(encoding="utf-8", errors="replace").splitlines()
    n = 0
    for i, raw in enumerate(lines):
        try:
            line = json.loads(raw)
            args = (line["agent"], line["kind"], line["data"], str(line["request_id"]), int(line["at"]))
        except (ValueError, KeyError, TypeError) as e:
            log(f"  spool: skipped a line that is not a hook event ({type(e).__name__}: {e})")
            continue
        try:
            rpc_hooks.apply(con, *args)
            n += 1
        except TRANSIENT as e:
            log(f"  spool: stopped at a line the database could not take ({type(e).__name__}: {e}); "
                f"{len(lines) - i} line(s) left for the next try")
            _keep(work, lines[i:])
            return n
        except Exception as e:  # noqa: BLE001 - this line cannot be applied: it is skipped, the rest go on
            log(f"  spool: skipped a line that cannot be applied ({type(e).__name__}: {e})")
    work.unlink()
    return n


def _keep(work, rest: list[str]) -> None:
    """Leave only `rest` in the file being ingested: written whole beside it and swapped in."""
    tmp = work.with_suffix(".keeping")
    tmp.write_text("".join(ln + "\n" for ln in rest), encoding="utf-8")
    os.replace(tmp, work)


def pending() -> bool:
    """Whether a file left by an ingest that did not finish is waiting: events must keep the ids it may replay."""
    return path().with_suffix(".ingesting").exists()
