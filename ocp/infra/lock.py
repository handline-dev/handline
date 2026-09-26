"""Exclusive holds, each one flock: one writer per agent terminal, and one
daemon per installation.

Two processes type into an agent's terminal: the daemon on its tick, and the
screen when a human presses a key. Waking blocks for up to
WAKE_SESSION_TIMEOUT_S waiting for a session to appear, and seven ticks fit
inside that window - so a second `ocp launch` gets typed into the terminal
while the first is still starting, and lands either beside the new session or
inside its input box, unsent. That is the one thing in this system two writers
can spoil: everything else is written to the database, where sqlite settles the
order.

Two daemons spoil more. They wake, park and notify every agent twice, and the
older one runs whatever code it was started with. The lock is also the only
record of whether one is running: the holder writes its pid into the lock file,
and `holder` reads it only while the lock is really held, so what a screen
reports and what refuses a second daemon are one fact.

The kernel releases an flock when its holder dies, so neither hold survives the
process that took it - there is nothing stale to reclaim.

Neither nests. flock is held per open file description and each acquire opens a
fresh one, so a second acquire inside the first fails rather than nesting -
quietly doing nothing, which is harder to find than a deadlock. A path that
runs under another's lock says so where it is defined.

Neither waits. A tick that cannot have a terminal leaves that agent alone and
comes back in two seconds; a keypress that cannot have it says so and the human
presses it again; a daemon that cannot have the daemon lock exits, because the
one holding it is already doing the work. Waiting would put the daemon's whole
tick behind a human who walked away mid-confirmation.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os

from . import paths

# The two kinds of hold. The suffix is what keeps them apart on disk, so an
# agent named `daemon` cannot take the daemon's lock by being typed at.
TERMINAL, SINGLETON = "terminal", "lock"
_PID_WIDTH = 16


@contextlib.contextmanager
def _hold(name: str, kind: str, record_pid: bool = False):
    """Yield True while this process holds `name`, False if someone else does.
    With `record_pid`, the holder's pid is written into the file once held.

    An flock we cannot even attempt is not the same as one another process
    holds, and saying so is the difference between a daemon that exits because
    its work is being done and one that exits every restart for a reason that
    is not true.
    """
    try:
        d = paths.home() / "locks"
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(d / f"{name}.{kind}", os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        # Without a lock we cannot promise there is one of us, and the
        # alternative to promising it is two.
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            yield False
            return
        if record_pid:
            # Over the old bytes, at a fixed width and without truncating, so a
            # reader never sees an empty file where a name was.
            os.pwrite(fd, f"{os.getpid():<{_PID_WIDTH}d}\n".encode(), 0)
        yield True
    finally:
        os.close(fd)


def terminal(agent: str):
    """The right to type at `agent`. Yields False immediately when someone
    else holds it; the lock lives on a file descriptor, so the kernel releases
    it if the holder dies - a daemon killed mid-wake does not leave an agent
    unreachable."""
    return _hold(agent, TERMINAL)


def singleton(name: str):
    """The right to be the only `name` running against this OCP_HOME."""
    return _hold(name, SINGLETON, record_pid=True)


def holder(name: str) -> int | None:
    """PID of whoever holds singleton `name`; None when nobody does.

    0 when it is held by a process that has not written its name: the first
    instant of a file's life, or a holder from before names were recorded, or
    one whose recorded pid is unreadable garbage.

    Asked of the lock itself, so the answer cannot outlive the holder - a name
    left in the file by a dead process is never read as a live one, and no boot
    time or pid check has to guess. The probe's shared hold is momentary, and
    a daemon starting in that instant is refused as if it had a rival, which
    the supervisor's next start undoes.

    Only ESRCH from `kill(pid, 0)` makes the name stale; EPERM (another user,
    or a sandbox such as an agent's Bash tool) means we could not ask, and the
    flock already proved it is held.
    """
    try:
        fd = os.open(paths.home() / "locks" / f"{name}.{SINGLETON}", os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                return None
        else:
            return None
        try:
            pid = int(os.pread(fd, _PID_WIDTH + 1, 0).split()[0])
        except (OSError, ValueError, IndexError):
            return 0
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return 0
        except OSError:
            pass  # not ESRCH - see the docstring
        return pid
    finally:
        os.close(fd)
