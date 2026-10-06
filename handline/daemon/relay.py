"""The relay socket: how the gateway, the process that authenticates paired devices, calls this daemon for them.

One connection per device connection, JSON-RPC lines as on daemon.sock. The first line is `relay.open` with the device's
id, name and role; after that every line is the device's own call and every line sent back (replies and notices) goes to the
device. The end of the connection is the gateway's word that the device left or was revoked: its subscriptions and terminal
attaches go with it.

Who may connect is the point. The gateway writes its pid in the protected store (no sandbox can write it); a peer is accepted
only when it is that process and no session `hl launch` recorded sits above it. Anything else, and every case where either
cannot be read, is closed without a byte. The device's role is taken as given up to member, never above.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading

from ..infra import paths, protected, sessiontoken, textsafe
from . import rpc

NS, NAME = "gateway", "pid"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def gateway_pid() -> int | None:
    """The pid the gateway recorded, or None when there is none or it cannot be read."""
    try:
        text = (protected.get(NS, NAME) or "").strip()
    except ValueError:
        return None
    return int(text) if text.isdigit() and int(text) > 1 else None


def trusted(peer: int | None) -> bool:
    """Whether `peer` is the gateway: the recorded pid, and not a process of any agent's session."""
    if not peer or peer != gateway_pid():
        return False
    try:
        list((protected.root() / sessiontoken.NS).iterdir())
    except FileNotFoundError:
        pass            # no session was ever recorded: nothing to descend from
    except OSError:
        return False    # the table of sessions cannot be read: nothing proves this is not one
    return sessiontoken.agent_of(peer) is None


class RelayConnection(rpc.Connection):
    """A device's calls, relayed. It starts past `hello` (the gateway did the device's) and has no idle limit."""

    def __init__(self, conn: socket.socket, log) -> None:
        super().__init__(conn, log, lambda msg, log, peer: {"ok": False, "said": "not a relay call"})
        self.idle_s = None

    def caller_for(self, key) -> rpc.Caller:
        return self.caller       # a relayed hello is the device's, never the gateway's

    def _open(self) -> bool:
        """The first line: relay.open with the device. False, with nothing sent, when it is anything else."""
        raw = self._line()
        try:
            msg = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            return False
        params = msg.get("params") if isinstance(msg, dict) else None
        if not (isinstance(msg, dict) and msg.get("jsonrpc") == "2.0" and msg.get("method") == "relay.open" and "id" in msg
                and isinstance(params, dict)):
            return False
        device, name = params.get("device_id"), params.get("name")
        if (not isinstance(device, str) or not _ID.fullmatch(device) or params.get("role") != "member"
                or not isinstance(name, str) or not name.strip() or len(name) > 64 or textsafe.has_unsafe(name)):
            return False
        self.caller = rpc.DeviceCaller(device, name.strip())
        self.said_hello = True
        return self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}})

    def _serve(self) -> None:
        try:
            opened = self._open()
        except (rpc.RpcError, OSError):
            return
        if opened:
            super()._serve()


def _serve_one(conn: socket.socket, log) -> None:
    try:
        peer = sessiontoken.peer_pid(conn)
        if trusted(peer):
            RelayConnection(conn, log).serve()
        else:
            log(f"  relay: refused a connection from pid {peer}")
    except OSError:
        pass
    finally:
        conn.close()


class RelayServer:
    """The relay socket's accept loop, one thread per connection."""

    def __init__(self, log) -> None:
        self._log = log
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        path = paths.relay_socket_file()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)
        try:
            sock.bind(str(path))
        finally:
            os.umask(old_umask)
        sock.listen(64)
        sock.settimeout(1.0)
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="ocp-daemon-relay")
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
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
            os.unlink(paths.relay_socket_file())
        except FileNotFoundError:
            pass
