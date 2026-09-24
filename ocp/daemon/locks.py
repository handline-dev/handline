"""The single-instance hold on this OCP_HOME, and finding another daemon in the process table."""
from __future__ import annotations

import os
from pathlib import Path

from .. import lock
from .. import observe as obs
from .. import paths


# ---------------------------------------------------------------- the lock
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
    return pid or next(iter(processes()), None)


def _supervises_this_home(pid: int) -> bool:
    """Is that daemon ours, or another installation's?

    The lock is per OCP_HOME, so two homes on one machine each run their own
    daemon and neither is a duplicate. Compared as directories, not as
    spellings: /tmp and /private/tmp are one place on macOS, and an unresolved
    comparison answers "somebody else's" about our own duplicate.
    """
    env = obs.process_env(pid)
    if env is None:
        # Unreadable is the one worth naming: a supervisor we cannot place is
        # exactly the one silence let run twice.
        return True
    # Read, but not set: `ocp launch` (and launchd) set OCP_HOME, but a daemon
    # started by hand usually does not, and that process still runs against
    # the default home - not against ours just because we could not name one.
    home = None
    for token in env.split():
        if token.startswith("OCP_HOME="):
            home = token.split("=", 1)[1]
            break
    home_path = Path(home).expanduser() if home else paths.default_home()
    return os.path.realpath(home_path) == os.path.realpath(paths.home())


def processes() -> list[int]:
    """PIDs of every `ocp daemon` supervising this OCP_HOME, this one aside.

    The lock refuses the next duplicate; it cannot end one that was already
    running when it arrived, and the lock names only its holder. The process
    table is the only place both of them appear.
    """
    mine = os.getpid()
    found = []
    for pid, cmd in obs.process_table() or []:
        argv = cmd.split()
        # The program being run: our own path, `daemon` next, and both at the
        # front of the command line. Anywhere else in it is somebody talking
        # about us - a `grep -n ocp daemon`, an editor on this file, a session
        # whose system prompt quotes the command. `service.build_plist` sets
        # ProgramArguments to exactly [ocp_executable(), "daemon"], so
        # launchd's own line never needs more than this.
        if not any(argv[i].endswith("/ocp") and argv[i + 1] == "daemon"
                   for i in range(min(2, len(argv) - 1))):
            continue
        if pid != mine and _supervises_this_home(pid):
            found.append(pid)
    return found
