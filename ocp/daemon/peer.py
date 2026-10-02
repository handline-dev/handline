"""Who is on the other end of the control socket, by its real process rather than by what it says.

A request names a speaker, and any agent can name another. The process that opened the connection is
not up to it: the kernel gives its pid, and the session that launched it is one of its ancestors, an
agent's session being a process `observe.live_sessions` reads the agent's name from at launch. A
process that left its parent (a daemonized child) has no session above it, and is nobody.
"""
from __future__ import annotations

import socket
import struct
import subprocess
import sys

LOCAL_PEERPID = 0x002        # macOS: getsockopt(SOL_LOCAL, LOCAL_PEERPID)
MAX_DEPTH = 64


def peer_pid(conn: socket.socket) -> int | None:
    try:
        if sys.platform == "darwin":
            return conn.getsockopt(0, LOCAL_PEERPID)
        pid, _uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                              struct.calcsize("3i")))
        return pid
    except (OSError, AttributeError, struct.error):
        return None


def _darwin_parent_of(pid: int) -> int | None:
    """proc_pidinfo(PROC_PIDTBSDINFO): the parent pid, without running `ps`."""
    import ctypes
    import ctypes.util
    lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
    buf = ctypes.create_string_buffer(136)          # struct proc_bsdinfo
    if lib.proc_pidinfo(pid, 3, 0, buf, 136) != 136:
        return None
    return struct.unpack_from("I", buf, 16)[0]     # pbi_ppid follows flags, status, xstatus, pid


def parent_of(pid: int) -> int | None:
    if sys.platform == "darwin":
        try:
            return _darwin_parent_of(pid)
        except (OSError, AttributeError, struct.error):
            pass
    try:
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def agent_of(pid: int | None, sessions: dict[str, int] | None = None) -> str | None:
    """The agent whose session `pid` runs under (or is), else None."""
    if pid is None:
        return None
    if sessions is None:
        from ..session import observe
        sessions = observe.live_sessions()
    if not sessions:
        return None
    by_pid = {p: a for a, p in sessions.items()}
    for _ in range(MAX_DEPTH):
        if pid in by_pid:
            return by_pid[pid]
        pid = parent_of(pid)
        if pid is None or pid <= 1:
            return None
    return None
