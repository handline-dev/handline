"""The daemon's JSON-RPC 2.0 connection (contract/protocol.md, "Framing", "Handshake" and "Errors"):
one object per line, many calls on one connection, `hello` first, every failure a typed error.

A line with `verb` and no `jsonrpc` is a legacy request and keeps its one-shot `{ok, said}` exchange: the
adapter answers it and hangs up, as the old server did, until its last caller has moved.
"""
from __future__ import annotations

import getpass
import json
import socket

from .. import db
from ..infra import sessiontoken

PROTOCOL = 1
MIN_CLIENT = 1
MAX_LINE_BYTES = 1 << 20
ROLES = ("anonymous", "agent", "member", "founder")     # lowest first

BAD_REQUEST, NOT_FOUND, REFUSED, INTERNAL = -32600, -32001, -32002, -32603
PARSE_ERROR, INVALID_PARAMS = -32700, -32602


class RpcError(Exception):
    """A call that failed, as the wire says it: the JSON-RPC code, the stable `data.code`, the rest of `data`."""

    def __init__(self, rpc_code: int, code: str, message: str, **data) -> None:
        super().__init__(message)
        self.rpc_code, self.code, self.message, self.data = rpc_code, code, message, data

    def wire(self) -> dict:
        return {"code": self.rpc_code, "message": self.message, "data": {"code": self.code, **self.data}}


def bad_request(message: str, field: str | None = None, rpc_code: int = BAD_REQUEST) -> RpcError:
    return RpcError(rpc_code, "bad_request", message, **({"field": field} if field else {}))


def refused(reason: str, gate: str | None = None, hint: str | None = None) -> RpcError:
    extra = {k: v for k, v in (("gate", gate), ("hint", hint)) if v}
    return RpcError(REFUSED, "refused", reason, reason=reason, **extra)


class Caller:
    """Who is on the other end of this connection, from the kernel's word about the peer process."""

    def __init__(self, peer: int | None) -> None:
        agent = sessiontoken.agent_of(peer)
        if agent:
            self.role, self.info = "agent", {"kind": "agent", "name": agent}
        else:       # the local user on the local socket is the founder
            self.role, self.info = "founder", {"kind": "person", "name": getpass.getuser()}


def _hello(ctx: "Connection", params: dict) -> dict:
    client, protocol = params.get("client"), params.get("protocol")
    if not isinstance(client, str) or not client:
        raise bad_request("hello needs client, the name/version of the caller", "client", INVALID_PARAMS)
    if isinstance(protocol, bool) or not isinstance(protocol, int):
        raise bad_request("hello needs protocol, an integer", "protocol", INVALID_PARAMS)
    if protocol < MIN_CLIENT:
        raise refused(f"this daemon serves clients of protocol {MIN_CLIENT} or newer; yours speaks {protocol}",
                      gate="protocol", hint="hl service install")
    from .loop import _loaded_sha       # loop imports this module's server, so not at the top
    caller = ctx.caller
    return {"protocol": PROTOCOL, "min_client": MIN_CLIENT, "daemon": _loaded_sha(), "schema": db.LATEST_VERSION,
            "role": caller.role, "caller": caller.info,
            "capabilities": {"chat": False, "review": False, "input_ask": False, "terminal_format": []},
            "transport": "local",
            "methods": [{"name": name, "since": 1, "role": role, "allowed": ROLES.index(caller.role) >= ROLES.index(role)}
                        for name, (_, role) in METHODS.items()]}


# name -> (handler, lowest role that may call it). Every method a later task adds is a line here.
METHODS = {"hello": (_hello, "anonymous")}


class Connection:
    def __init__(self, conn: socket.socket, log, handle_legacy) -> None:
        self.conn, self.log, self.handle_legacy = conn, log, handle_legacy
        self.peer = sessiontoken.peer_pid(conn)
        self.caller = Caller(self.peer)
        self.said_hello = False
        self._buf = b""

    def _line(self) -> bytes | None:
        """The next line, or what is left at EOF; None at a clean EOF. A line past the limit is bad_request."""
        while True:
            nl = self._buf.find(b"\n")
            if nl != -1:
                line, self._buf = self._buf[:nl], self._buf[nl + 1:]
                return line
            if len(self._buf) > MAX_LINE_BYTES:
                raise bad_request(f"a line is at most {MAX_LINE_BYTES} bytes")
            chunk = self.conn.recv(65536)
            if not chunk:       # a client that shuts its write side after one object needs no newline
                line, self._buf = self._buf, b""
                return line or None
            self._buf += chunk

    def _send(self, obj: dict) -> bool:
        try:
            self.conn.sendall(json.dumps(obj).encode("utf-8") + b"\n")
            return True
        except OSError:
            return False        # the client is gone; nothing left to tell it

    def serve(self) -> None:
        while True:
            try:
                raw = self._line()
            except RpcError as e:
                self._send({"jsonrpc": "2.0", "id": None, "error": e.wire()})
                return          # the rest of the stream cannot be framed
            if raw is None:
                return
            if not raw.strip():
                continue
            if not self._one(raw):
                return

    def _one(self, raw: bytes) -> bool:
        """Answer one line; False when the connection is done."""
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return self._send({"jsonrpc": "2.0", "id": None, "error": bad_request(f"not JSON: {e}", None, PARSE_ERROR).wire()})
        if isinstance(msg, dict) and "verb" in msg and "jsonrpc" not in msg:
            self._send(self.handle_legacy(msg, self.log, self.peer))
            return False
        rid = msg.get("id") if isinstance(msg, dict) else None
        if isinstance(rid, bool) or not isinstance(rid, (int, str, type(None))):
            rid = None
        try:
            result = self._dispatch(msg)
        except RpcError as e:
            error = e
        except Exception as e:  # noqa: BLE001 - a call that breaks must reply, not kill the connection
            self.log(f"  rpc: {type(e).__name__}: {e}")
            error = RpcError(INTERNAL, "internal", "internal error")
        else:
            if isinstance(msg, dict) and "id" not in msg:
                return True     # a notification is never answered
            return self._send({"jsonrpc": "2.0", "id": rid, "result": result})
        return self._send({"jsonrpc": "2.0", "id": rid, "error": error.wire()})

    def _dispatch(self, msg) -> dict:
        if isinstance(msg, list):
            raise bad_request("batches are not accepted")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            raise bad_request("a request is a JSON-RPC 2.0 object")
        if "id" in msg and (isinstance(msg["id"], bool) or not isinstance(msg["id"], (int, str))):
            raise bad_request("id is an integer or a string", "id")
        method, params = msg.get("method"), msg.get("params", {})
        if not isinstance(method, str):
            raise bad_request("method is a string", "method")
        if not isinstance(params, dict):
            raise bad_request("params go by name, as an object", "params", INVALID_PARAMS)
        if method != "hello" and not self.said_hello:
            raise bad_request("the first call is hello", "method")
        entry = METHODS.get(method)
        if entry is None:
            raise bad_request(f"no such method: {method}", "method")
        handler, role = entry
        if ROLES.index(self.caller.role) < ROLES.index(role):
            raise refused(f"{method} is for a {role} or above; this caller is {self.caller.role}", gate="role")
        result = handler(self, params)
        if method == "hello":
            self.said_hello = True
        return result
