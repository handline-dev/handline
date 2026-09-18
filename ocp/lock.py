"""Exclusive holds: one writer per agent terminal, one daemon per installation.

Both are flocks, and for the same reason - the kernel releases them when
the holder dies, so neither can be left behind by a process that was
killed.

Two processes type into an agent's terminal: the daemon on its tick, and `ocp
top` when a human presses a key. Waking blocks for up to WAKE_SESSION_TIMEOUT_S
waiting for a session to appear, and seven ticks fit inside that window - so a
second `ocp launch` gets typed into the terminal while the first is still
starting, and lands either beside the new session or inside its input box,
unsent. That is the one thing in this system two writers can spoil: everything
else is written to the database, where sqlite settles the order.

It does not nest. flock is held per open file description and this opens a
fresh one each time, so a second acquire inside the first fails rather than
nesting - quietly doing nothing, which is harder to find than a deadlock. A
path that runs under another's lock says so where it is defined.

Never waits. A tick that cannot have the lock leaves that agent alone and comes
back in two seconds; a keypress that cannot have it says so and the human
presses it again. Waiting would put the daemon's whole tick behind a human who
walked away mid-confirmation.
"""
from __future__ import annotations

import contextlib
import fcntl
import os

from . import paths


def _path(agent: str):
    d = paths.home() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{agent}.terminal"


@contextlib.contextmanager
def terminal(agent: str):
    """Yield True while this process holds the right to type at `agent`.

    Yields False immediately when someone else holds it. The lock lives on a
    file descriptor, so it is released by the kernel if the holder dies - a
    daemon killed mid-wake does not leave an agent unreachable.
    """
    try:
        fd = os.open(_path(agent), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        # Without a lock we cannot promise one writer, and the alternative to
        # promising it is two launches in one terminal.
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


@contextlib.contextmanager
def singleton(name: str):
    """Yield True while this process is the only holder of `name`.

    The daemon's guard against a second supervisor. A pidfile records who
    wrote it last and nothing else: a daemon started from another shell, or
    one orphaned when launchd reloaded the job under it, is not in the file
    and so is invisible to a check that reads it. Two supervisors on one
    database then notify, park and wake the same agent twice, and the older
    one runs whatever code it was started with.

    The kernel drops an flock when its holder dies, so unlike the pidfile
    there is nothing stale to reclaim and no boot time to compare.
    """
    d = paths.home() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(d / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        # The same reasoning as `terminal`: we cannot promise there is one of
        # us, and the alternative to promising it is two daemons.
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)
