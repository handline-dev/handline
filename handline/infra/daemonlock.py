"""The single-instance hold on this OCP_HOME: its name, and who holds it."""
from __future__ import annotations

from . import lock


# The name of the hold that makes this a single-instance program. One per
# OCP_HOME, so two installations on one machine still supervise their own.
DAEMON_LOCK = "daemon"


def running_elsewhere() -> int | None:
    """PID of the daemon supervising this OCP_HOME, or None when none is.

    Asked of the lock that refuses a second daemon (`lock.holder`), so a daemon
    is reported exactly when it is the one holding it. One that holds the lock
    without having written its pid - started before the lock recorded it - is
    named from the process table instead; only one that is in neither reads as
    None.
    """
    pid = lock.holder(DAEMON_LOCK)
    if pid is None:
        return None
    if pid:
        return pid
    from ..daemon import locks       # the process table knows only the Python daemon; it goes with it
    return next(iter(locks.processes()), None)
