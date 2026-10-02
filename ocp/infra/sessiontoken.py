"""The proof that a request comes from an agent's own session.

There is no secret. `ocp launch` records its own process id and start time for the agent it becomes
(`record`) in the protected store, which no agent session can write; the process it execs keeps that
id, so every process the session starts descends from it. The daemon asks the kernel who is on the
other end of a socket request (`peer_pid`) and accepts a verb only when that process descends from the
recorded one (`verify`). A name in the request proves nothing, and nothing an agent holds can be copied
out of its environment, `ps` or a file: a token in the environment was readable by every other session
through `sysctl`. A process that does not descend from the session, such as a daemonized
child, is refused. A session launched before this holds no record and is refused until relaunched.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import socket
import struct
import sys

from . import protected

NS = "session-tokens"      # the store folder keeps its name; renaming it would orphan the records
_MAX_DEPTH = 64


def _proc(pid: int) -> tuple[int, str] | None:
    """(parent pid, start time) of `pid`, or None when no such process is running. Read from the
    kernel, not from `ps`, which a session's sandbox forbids."""
    if sys.platform == "darwin":
        libproc = ctypes.CDLL(ctypes.util.find_library("proc"))
        buf = ctypes.create_string_buffer(136)
        if libproc.proc_pidinfo(pid, 3, 0, buf, 136) != 136:        # PROC_PIDTBSDINFO
            return None
        ppid = struct.unpack_from("I", buf, 16)[0]
        start = struct.unpack_from("QQ", buf, 120)                  # pbi_start_tvsec, pbi_start_tvusec
        return ppid, f"{start[0]}.{start[1]:06d}"
    try:
        with open(f"/proc/{pid}/stat") as f:
            rest = f.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return int(rest[1]), rest[19]                                   # ppid, starttime in ticks


def record(agent: str, pid: int | None = None) -> bool:
    """Remember `pid` (default: this process) as `agent`'s session. False when the store cannot be
    written, which is what an agent's session gets."""
    pid = os.getpid() if pid is None else pid
    info = _proc(pid)
    if info is None:
        return False
    try:
        protected.put(NS, agent, f"{pid} {info[1]}")
    except (OSError, ValueError):       # PermissionError, NotTheFounder and a bad name all say no
        return False
    return True


def peer_pid(conn: socket.socket) -> int | None:
    """The process id of the other end of a unix socket, as the kernel reports it."""
    try:
        if sys.platform == "darwin":
            return conn.getsockopt(0, 2) or None            # SOL_LOCAL, LOCAL_PEERPID
        return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0] or None
    except (OSError, AttributeError):
        return None


def verify(agent: str | None, pid: int | None) -> bool:
    """Whether process `pid` is, or descends from, the session recorded for `agent`."""
    if not agent or not pid:
        return False
    try:
        kept = protected.get(NS, agent)
    except ValueError:
        return False
    if not kept:
        return False
    head, _, started = kept.strip().partition(" ")
    if not head.isdigit():
        return False
    for _ in range(_MAX_DEPTH):
        info = _proc(pid)
        if info is None or pid <= 1:
            return False
        if pid == int(head):
            return info[1] == started       # a reused pid has another start time
        pid = info[0]
    return False
