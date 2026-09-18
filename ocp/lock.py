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
older one runs whatever code it was started with. Only the lock can see one:
the pidfile names whoever wrote it last.

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


@contextlib.contextmanager
def _hold(name: str, kind: str):
    """Yield True while this process holds `name`, False if someone else does.

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
    return _hold(name, SINGLETON)
