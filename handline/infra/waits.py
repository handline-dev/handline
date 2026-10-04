"""Bounds that both the daemon and the actions that wait on it derive from.

They live outside `handline.daemon` so `handline.actions` can read them at import time without importing the
daemon package, which imports `handline.actions` in turn.
"""
from __future__ import annotations

# How long a session told `/exit` has to leave the process table. SessionEnd
# fires at once and the process can outlive it by up to a sweep tick (gone by the
# next sweep, 31s later, in the one case measured), and the sweep reads a closed
# row over a live process as "running again" and clears the mark.
END_WAIT_S = 60

# How long a relaunched session has to show up in the process table. `ocp
# launch` execs claude, so it appears at once when it appears at all; this is
# slack for a slow start, not a wait for the TUI to be usable.
WAKE_SESSION_TIMEOUT_S = 15
