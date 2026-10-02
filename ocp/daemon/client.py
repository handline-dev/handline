"""The thin client every caller (core, ui) reaches the daemon's socket
through (T351): one request in, one {ok, said} reply out, or None when the
daemon is not there to answer.

A refused or missing socket is not raised as an error - it is the one signal
`send` exists to carry: with the daemon down there is no fallback path
(#2073 decision A), so a caller sees None and prints its own "daemon not
running, nothing sent" rather than this module guessing at wording for
every kind of caller.
"""
from __future__ import annotations

import errno
import json
import socket

from ..infra import paths

# A wake can legitimately take up to ~450s (control.py's own WAITS); shorter
# would misread one still in progress as the daemon being down.
TIMEOUT_S = 480.0


def _exchange(sock: socket.socket, payload: dict) -> bytes:
    """One request over a connected socket, and everything the peer sends back.

    The daemon answers as soon as it has read the line and then hangs up, so a reply can be waiting
    before the write side is shut: macOS then refuses the shutdown with ENOTCONN. That is the daemon
    having already answered, not a daemon that is not there - read as the latter it told the caller
    nothing was sent about a message that had been typed, and a retry typed it twice."""
    sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
    try:
        sock.shutdown(socket.SHUT_WR)
    except OSError as e:
        if e.errno != errno.ENOTCONN:
            raise
    chunks = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def send(payload: dict, timeout: float = TIMEOUT_S) -> dict | None:
    """POST one request to the daemon and return its `{ok, said}` reply, or
    None when nothing is listening (the daemon is down, or has not made its
    socket yet)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(paths.socket_file()))
            raw = _exchange(sock, payload)
    except OSError:
        return None
    data = raw.strip()
    if not data:
        return None
    try:
        reply = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return reply if isinstance(reply, dict) else None
