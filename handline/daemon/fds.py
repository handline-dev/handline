"""The daemon's open-descriptor count: logged each tick, named by type when it nears the limit, and EMFILE said once a minute.

A daemon that ran out of descriptors answered nothing and said nothing for 18 minutes; this is how the next one names what holds them.
"""
from __future__ import annotations

import errno
import fcntl
import os
import resource
import stat
import time
from collections import Counter

NEAR = 0.8              # of the soft limit: the inventory is logged once per crossing
EMFILE_EVERY_S = 60     # EMFILE is said at most this often

_state = {"near": False, "said": -EMFILE_EVERY_S, "last": None}


def is_exhaustion(e: BaseException) -> bool:
    return isinstance(e, OSError) and e.errno in (errno.EMFILE, errno.ENFILE)


def count() -> int:
    """Descriptors open in this process (the listing's own descriptor is not counted)."""
    return max(len(os.listdir("/dev/fd")) - 1, 0)


def _path(fd: int) -> str:
    """The file a descriptor is open on: macOS answers F_GETPATH (its /dev/fd cannot be read as a link), Linux the link."""
    getpath = getattr(fcntl, "F_GETPATH", None)
    try:
        if getpath is not None:
            return os.fsdecode(fcntl.fcntl(fd, getpath, bytes(1024)).split(b"\0", 1)[0])
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return ""


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
    name = os.path.basename(_path(fd))
    return "db" if any(m in name for m in (".sqlite", ".db")) else "file"


def inventory() -> dict[str, int]:
    fds = [int(n) for n in os.listdir("/dev/fd") if n.isdigit()]
    return dict(Counter(kind(fd) for fd in fds))


def soft_limit() -> int:
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    return soft if soft != resource.RLIM_INFINITY else 1 << 20


def check(log) -> int:
    """Run once per tick. The count is logged when it first reads and whenever it moves by a twentieth of the limit; the inventory the
    first tick it is past NEAR of the soft limit (again after it falls back). Never raises: it runs when descriptors are scarcest."""
    try:
        n, limit = count(), soft_limit()
    except OSError as e:
        say_exhaustion(log, e, "counting descriptors")
        return -1
    if _state["last"] is None or abs(n - _state["last"]) >= max(limit // 20, 1):
        _state["last"] = n
        log(f"  fds: {n}/{limit}")
    if n < NEAR * limit:
        _state["near"] = False
    elif not _state["near"]:
        try:
            inv = inventory()
        except OSError as e:
            say_exhaustion(log, e, "taking the descriptor inventory")  # tried again next tick
        else:
            _state["near"] = True
            log("  fds: near the limit - " + ", ".join(f"{k} {v}" for k, v in sorted(inv.items(), key=lambda kv: -kv[1])))
    return n


def say_exhaustion(log, e: BaseException, doing: str, now: float | None = None) -> None:
    """Out of descriptors while `doing`: said at most once a minute, so a spell of it does not become the log."""
    now = time.monotonic() if now is None else now
    if now - _state["said"] >= EMFILE_EVERY_S:
        _state["said"] = now
        log(f"out of file descriptors while {doing}: {e}")
