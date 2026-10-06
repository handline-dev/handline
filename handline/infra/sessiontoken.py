"""The proof that a request comes from an agent's own session.

There is no secret. `hl launch` records its own process id and start time for the agent it becomes
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
import hmac
import os
import secrets
import socket
import struct
import sys

from . import protected

NS = "session-tokens"      # the store folder keeps its name; renaming it would orphan the records
KEY_NS = "hook-keys"
FOUNDER_NS = "founder-keys"     # the daemon's key for the founder's own callers; hidden from sessions like KEY_NS
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


def _exe(pid: int) -> str | None:
    """The path of the program `pid` is running, from the kernel; None when it cannot be told."""
    if sys.platform == "darwin":
        libproc = ctypes.CDLL(ctypes.util.find_library("proc"))
        buf = ctypes.create_string_buffer(4096)
        n = libproc.proc_pidpath(pid, buf, 4096)
        return buf.value.decode("utf-8", "replace") if n > 0 else None
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def is_claude(path: str | None) -> bool:
    """Whether a program path is Claude Code: the `claude` command, or its versioned binary in the
    folder the installer keeps them in. A copy run through `node` shows up as node and is not told."""
    if not path:
        return False
    return (os.path.basename(path) == "claude" or "/claude/versions/" in path
            or "/claude-code/" in path or "/@anthropic-ai/claude" in path)


def claude_ancestors(pid: int | None = None) -> int:
    """How many Claude Code processes sit above `pid` (default: this process) in the process tree. The
    session an agent was launched as is one, and every command it runs and every hook it fires has just
    that one above it; a second means a Claude Code another process started inside it, which inherited
    the agent's environment and its hooks without being the agent."""
    pid = os.getpid() if pid is None else pid
    count = 0
    for _ in range(_MAX_DEPTH):
        info = _proc(pid)
        if info is None or info[0] <= 1:
            break
        pid = info[0]
        if is_claude(_exe(pid)):
            count += 1
    return count


def chain(pid: int | None = None) -> list[tuple[int, str]]:
    """(pid, program) of each process above `pid`, for a record of who issued a command."""
    pid = os.getpid() if pid is None else pid
    out: list[tuple[int, str]] = []
    for _ in range(_MAX_DEPTH):
        info = _proc(pid)
        if info is None or info[0] <= 1:
            break
        pid = info[0]
        out.append((pid, os.path.basename(_exe(pid) or "?")))
    return out


def nested(pid: int | None = None) -> bool:
    """Whether this process runs under a Claude Code that itself runs under another."""
    return claude_ancestors(pid) >= 2


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


def agent_of(pid: int | None) -> str | None:
    """The agent whose recorded session process `pid` is, or descends from; None for any other process."""
    if not pid:
        return None
    folder = protected.root() / NS
    try:
        names = sorted(p.name for p in folder.iterdir())
    except OSError:
        return None
    return next((n for n in names if verify(n, pid)), None)


def key_folder():
    return protected.root() / KEY_NS


def hidden_folders() -> list:
    """The key folders a session must not read."""
    return [key_folder(), protected.root() / FOUNDER_NS]


def ensure_key_folder() -> None:
    """The folder exists before a launch hides it: the first launch on a machine would otherwise name a
    path the sandbox rule then does not cover once it appears. Quietly nothing when the store is unwritable."""
    try:
        protected.ensure(KEY_NS)
        protected.ensure(FOUNDER_NS)
    except OSError:
        pass


def mint_key(agent: str) -> bool:
    """A new key for `agent`'s hook (see session/landgate.py), kept where the session's sandbox cannot read
    it. False when the store cannot be written, which is what an agent's session gets."""
    try:
        protected.put(KEY_NS, agent, secrets.token_urlsafe(32))
    except (OSError, ValueError):
        return False
    return True


def key_held(agent: str) -> str | None:
    """The key, for the hook, which the harness runs outside the sandbox. None inside it."""
    try:
        return (protected.get(KEY_NS, agent) or "").strip() or None
    except ValueError:
        return None


def key_ok(agent: str | None, key: str | None) -> bool:
    kept = key_held(agent) if agent else None
    return bool(kept and key) and hmac.compare_digest(kept, str(key))


def ensure_founder_key() -> bool:
    """The founder key exists: the daemon makes one at start when there is none. False when the store
    cannot be written, which is what an agent's session gets."""
    if founder_key_held():
        return True
    try:
        protected.put(FOUNDER_NS, "key", secrets.token_urlsafe(32))
    except (OSError, ValueError):
        return False
    return True


def founder_key_held() -> str | None:
    """The founder key, for the founder's own client. None inside a session's sandbox."""
    try:
        return (protected.get(FOUNDER_NS, "key") or "").strip() or None
    except ValueError:
        return None


def founder_key_ok(key) -> bool:
    kept = founder_key_held()
    return bool(kept and isinstance(key, str) and key) and hmac.compare_digest(kept, key)
