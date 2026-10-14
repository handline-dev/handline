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
    without a readable pid reads as None.
    """
    return lock.holder(DAEMON_LOCK) or None
