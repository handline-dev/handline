"""The daemon's control socket (T351): a local Unix socket, mode 0600, the
only way in to type at an agent's terminal or touch its session. One JSON
request per connection, one JSON `{ok, said}` reply, then the connection
closes - no polling, no second message on the same connection.

Every verb here runs on a connection of its own (`db.connect()` per request,
the same rule `control.perform` already follows) and, where it touches an
agent's terminal, under that agent's `lock.terminal` - the same lock the
tick holds, so a request arriving mid-tick loses the race cleanly instead of
landing beside what the tick just typed. A connection handled in its own
thread: `wake` alone can hold the socket open for `WAKE_SESSION_TIMEOUT_S`
waiting for a session to appear, and a slow verb for one agent must not hold
up a `message` to another.

A refused connection (no socket file, or nothing listening) is the caller's
only sign the daemon is down (#2073 decision A) - `client.send` returns None
for it, and prints nothing here: what to do about it is the caller's.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

from .. import db
from ..actions import confirmations, control, details, dialogs, prompt_blocks
from ..infra import paths

from . import messaging, sends

# A request this old with nothing read yet is not going to finish reading;
# closed rather than left to hold a thread forever on a client that connected
# and stalled.
RECV_TIMEOUT_S = 30
# One request line, bounded so a runaway or hostile client cannot grow a
# thread's buffer without limit - every real payload here is well under 64K.
MAX_REQUEST_BYTES = 1 << 20


def _wake(con, log, **kw) -> tuple[bool, str]:
    return True, control.wake(con, kw["agent"])


def _close(con, log, **kw) -> tuple[bool, str]:
    return True, control.close(con, kw["agent"])


def _park(con, log, **kw) -> tuple[bool, str]:
    return True, control.park(con, kw["agent"])


def _fold(con, log, **kw) -> tuple[bool, str]:
    return True, control.fold(con, kw["agent"])


def _move(con, log, **kw) -> tuple[bool, str]:
    return True, control.move(con, kw["agent"], kw["account"])


def _start(con, log, **kw) -> tuple[bool, str]:
    return True, control.start(con, kw["agent"], kw.get("repo"), kw.get("base", "main"))


def _message(con, log, **kw) -> tuple[bool, str]:
    return messaging.send_message(con, kw["agent"], kw["text"], log)


def _answer_dialog(con, log, **kw) -> tuple[bool, str]:
    return dialogs.answer_dialog(con, kw["agent"], kw["title"], int(kw["number"]),
                                 bool(kw.get("always", False)))


def _confirm(con, log, **kw) -> tuple[bool, str]:
    return confirmations.answer(con, kw["agent"], bool(kw["yes"]), kw["seen"])


def _answer_block(con, log, **kw) -> tuple[bool, str]:
    return prompt_blocks.answer(con, int(kw["block"]), dict(kw.get("choice") or {}))


def _trust(con, log, **kw) -> tuple[bool, str]:
    worktrees = [(a, w) for a, w in kw["worktrees"]]
    said = details.trust_worktrees(kw["account"], worktrees)
    return True, "; ".join(said)


VERBS = {
    "message": _message,
    "ask": sends.ask_verb,
    "say": sends.say_verb,
    "wake": _wake,
    "close": _close,
    "park": _park,
    "fold": _fold,
    "move": _move,
    "start": _start,
    "answer_dialog": _answer_dialog,
    "confirm": _confirm,
    "answer_block": _answer_block,
    "trust": _trust,
}


def _handle_request(payload: dict, log) -> dict:
    verb = payload.get("verb")
    fn = VERBS.get(verb)
    if fn is None:
        return {"ok": False, "said": f"not a socket verb: {verb!r}"}
    kw = {k: v for k, v in payload.items() if k != "verb"}
    con = db.connect()
    try:
        ok, said = fn(con, log, **kw)
        return {"ok": bool(ok), "said": str(said)}
    except Exception as e:  # noqa: BLE001 - a bad request must reply, not kill the thread
        return {"ok": False, "said": f"{verb}: {e}"}
    finally:
        con.close()


def _read_line(conn: socket.socket) -> bytes | None:
    """One request: bytes up to the first newline, or up to
    MAX_REQUEST_BYTES/EOF without one - a client that shuts its write side
    after one JSON object needs no trailing newline."""
    buf = bytearray()
    while len(buf) < MAX_REQUEST_BYTES:
        chunk = conn.recv(4096)
        if not chunk:
            break
        nl = chunk.find(b"\n")
        if nl != -1:
            buf += chunk[:nl]
            return bytes(buf)
        buf += chunk
    return bytes(buf) if buf else None


def _serve_one(conn: socket.socket, log) -> None:
    try:
        conn.settimeout(RECV_TIMEOUT_S)
        raw = _read_line(conn)
        if not raw:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            reply = {"ok": False, "said": f"bad request: {e}"}
        else:
            reply = _handle_request(payload, log) if isinstance(payload, dict) \
                else {"ok": False, "said": "a request is a JSON object"}
        try:
            conn.sendall(json.dumps(reply).encode("utf-8") + b"\n")
        except OSError:
            pass  # the client is gone; nothing left to tell it
    finally:
        conn.close()


class SocketServer:
    """Runs the accept loop in a background thread, one further thread per
    connection - see the module docstring for why neither is inline with the
    tick."""

    def __init__(self, log) -> None:
        self._log = log
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        path = paths.socket_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # The daemon singleton lock already guarantees we are the only
        # daemon for this OCP_HOME, so a file left at this path is a stale
        # one from a process that did not exit cleanly, not a live listener.
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Created private via umask, not just chmod after: chmod alone leaves
        # a window where the file is briefly world-writable.
        old_umask = os.umask(0o177)
        try:
            sock.bind(str(path))
        finally:
            os.umask(old_umask)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # the umask above already narrowed it; this is only extra insurance
        sock.listen(64)
        sock.settimeout(1.0)  # so `stop()` is noticed instead of blocking accept() forever
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                        name="ocp-daemon-socket")
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # the socket was closed under us by stop()
            threading.Thread(target=_serve_one, args=(conn, self._log), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            os.unlink(paths.socket_file())
        except FileNotFoundError:
            pass
