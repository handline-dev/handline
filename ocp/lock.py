"""One writer at a time per agent terminal.

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
