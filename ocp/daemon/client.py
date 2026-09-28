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

import json
import socket

from ..infra import paths

# A wake can legitimately take up to ~450s (control.py's own WAITS); shorter
# would misread one still in progress as the daemon being down.
TIMEOUT_S = 480.0


def send(payload: dict, timeout: float = TIMEOUT_S) -> dict | None:
    """POST one request to the daemon and return its `{ok, said}` reply, or
    None when nothing is listening (the daemon is down, or has not made its
    socket yet)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(paths.socket_file()))
            sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError:
        return None
    data = b"".join(chunks).strip()
    if not data:
        return None
    try:
        reply = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return reply if isinstance(reply, dict) else None
