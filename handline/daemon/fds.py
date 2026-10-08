"""The daemon's open-descriptor count: logged each tick, named by type when it nears the limit, and EMFILE said once a minute.

A daemon that ran out of descriptors answered nothing and said nothing for 18 minutes; this is how the next one names what holds them.
"""
from __future__ import annotations

import errno
import os
import resource
import stat
import time
from collections import Counter

NEAR = 0.8              # of the soft limit: the inventory is logged once per crossing
EMFILE_EVERY_S = 60     # EMFILE is said at most this often

_state = {"near": False, "said": -EMFILE_EVERY_S}


def is_exhaustion(e: BaseException) -> bool:
    return isinstance(e, OSError) and e.errno in (errno.EMFILE, errno.ENFILE)


def count() -> int:
    """Descriptors open in this process (the listing's own descriptor is not counted)."""
    return max(len(os.listdir("/dev/fd")) - 1, 0)


def kind(fd: int) -> str:
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        return "closed"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISFIFO(mode):
        return "pipe"
    if stat.S_ISCHR(mode):
        return "pty" if os.isatty(fd) else "char"
    if stat.S_ISDIR(mode):
        return "dir"
    try:
        name = os.readlink(f"/dev/fd/{fd}")
    except OSError:
        name = ""
    return "db" if name.endswith((".db", ".db-wal", ".db-shm", ".sqlite")) else "file"


def inventory() -> dict[str, int]:
    fds = [int(n) for n in os.listdir("/dev/fd") if n.isdigit()]
    return dict(Counter(kind(fd) for fd in fds))


def soft_limit() -> int:
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    return soft if soft != resource.RLIM_INFINITY else 1 << 20


def check(log) -> int:
    """Run once per tick: logs the count, and the inventory the first tick it is past NEAR of the soft limit (again after it falls back)."""
    try:
        n, limit = count(), soft_limit()
    except OSError as e:
        say_exhaustion(log, e, "counting descriptors")
        return -1
    log(f"  fds: {n}/{limit}")
    if n >= NEAR * limit:
        if not _state["near"]:
            _state["near"] = True
            inv = inventory()
            log("  fds: near the limit - " + ", ".join(f"{k} {v}" for k, v in sorted(inv.items(), key=lambda kv: -kv[1])))
    else:
        _state["near"] = False
    return n


def say_exhaustion(log, e: BaseException, doing: str, now: float | None = None) -> None:
    """Out of descriptors while `doing`: said at most once a minute, so a spell of it does not become the log."""
    now = time.monotonic() if now is None else now
    if now - _state["said"] >= EMFILE_EVERY_S:
        _state["said"] = now
        log(f"out of file descriptors while {doing}: {e}")
