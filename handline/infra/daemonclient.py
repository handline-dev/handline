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
import time

from . import paths, service

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


START_WAIT_S = 15.0         # how long a caller waits for a daemon launchd was asked to start
START_POLL_S = 0.2


class DaemonDown(Exception):
    """The daemon is not there and could not be started. Nothing was done."""

    def __init__(self, why: str) -> None:
        self.log_path = service.err_log()
        self.why = why
        super().__init__(f"the daemon is not running: {why} (log: {self.log_path}); nothing was done")


def connect(timeout: float = TIMEOUT_S) -> socket.socket | None:
    """A connected socket, or None when nothing is listening."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(paths.socket_file()))
    except OSError:
        sock.close()
        return None
    return sock


def ensure_daemon(timeout: float = TIMEOUT_S, wait_s: float = START_WAIT_S) -> socket.socket:
    """A connected socket. When nothing is listening, ask launchd to start the service and retry for
    `wait_s`; this never starts a daemon itself, so there is never a second one. DaemonDown when it does not
    come up, with nothing done."""
    sock = connect(timeout)
    if sock:
        return sock
    if service._plist_installed() is not True:
        raise DaemonDown("no service is installed to start it (`hl service install`)")
    res = service.run_launchctl(["kickstart", service.service_target()])
    if res.returncode != 0 and "could not find service" in f"{res.stderr} {res.stdout}".lower():
        # Installed and not loaded (a bootout, a login that did not load it): load what is installed, and start it. Nothing is
        # rewritten and no daemon is started here; a service that is loaded is never bootstrapped.
        loaded, said = service.load()
        if not loaded:
            raise DaemonDown(f"the service is installed but not loaded, and launchd would not load it: {said[:200]} (`hl service install`)")
        res = service.run_launchctl(["kickstart", service.service_target()])
    if res.returncode != 0:
        raise DaemonDown(f"launchd would not start it: {(res.stderr or res.stdout).strip()[:200] or 'no reason given'}")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        sock = connect(timeout)
        if sock:
            return sock
        time.sleep(START_POLL_S)
    raise DaemonDown(f"launchd started it but it did not answer within {wait_s:.0f}s")


class RpcError(Exception):
    def __init__(self, error: dict) -> None:
        self.code = (error.get("data") or {}).get("code", "internal")
        self.data = error.get("data") or {}
        super().__init__(error.get("message") or self.code)


def call(sock: socket.socket, method: str, params: dict | None = None, client_name: str = "hl", key: str | None = None) -> dict:
    """One JSON-RPC call on a fresh connection: hello, the call, and the reply to it. RpcError carries the
    daemon's typed error."""
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "hello",
              "params": {"client": client_name, "protocol": 1, **({"key": key} if key else {})}},
             {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}]
    with sock:
        sock.sendall(b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in lines))
        buf = b""
        while buf.count(b"\n") < 2:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    replies = [json.loads(ln) for ln in buf.split(b"\n") if ln.strip()]
    for r in replies:
        if r.get("id") == 1 and "error" in r:
            raise RpcError(r["error"])
    reply = next((r for r in replies if r.get("id") == 2), None)
    if reply is None:
        raise OSError("the daemon closed the connection before it replied")
    if "error" in reply:
        raise RpcError(reply["error"])
    return reply["result"]


def send(payload: dict, timeout: float = TIMEOUT_S, start: bool = True) -> dict | None:
    """POST one request to the daemon and return its `{ok, said}` reply, or
    None when nothing is listening and it could not be started (`ensure_daemon`
    is tried first when a service is installed; `start=False` skips it)."""
    try:
        sock = ensure_daemon(timeout) if start else connect(timeout)
    except DaemonDown:
        return None
    if sock is None:
        return None
    try:
        with sock:
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
