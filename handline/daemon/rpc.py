"""The daemon's JSON-RPC 2.0 connection (contract/protocol.md, "Framing", "Handshake" and "Errors"):
one object per line, many calls on one connection, `hello` first, every failure a typed error.

A line with `verb` and no `jsonrpc` is a legacy request and keeps its one-shot `{ok, said}` exchange: the
adapter answers it and hangs up, as the old server did, until its last caller has moved.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import socket
import threading
import time
from typing import Callable, NamedTuple

from .. import db
from ..infra import sessiontoken

PROTOCOL = 1
MIN_CLIENT = 1
MAX_LINE_BYTES = 1 << 20
ROLES = ("anonymous", "agent", "member", "founder")     # lowest first

BAD_REQUEST, NOT_FOUND, REFUSED, INTERNAL = -32600, -32001, -32002, -32603
PARSE_ERROR, INVALID_PARAMS, NO_SUCH_METHOD = -32700, -32602, -32601
# A line that has started must finish within RECV_TIMEOUT_S; a connection past its hello may wait between
# calls up to IDLE_TIMEOUT_S. Before hello it gets the short limit, as a legacy request always did.
RECV_TIMEOUT_S = 30
IDLE_TIMEOUT_S = 3600
REPLY_KEEP_S = 24 * 3600
REQUEST_ID_MAX = 128


class RpcError(Exception):
    """A call that failed, as the wire says it: the JSON-RPC code, the stable `data.code`, the rest of `data`."""

    def __init__(self, rpc_code: int, code: str, message: str, **data) -> None:
        super().__init__(message)
        self.rpc_code, self.code, self.message, self.data = rpc_code, code, message, data

    def wire(self) -> dict:
        return {"code": self.rpc_code, "message": self.message, "data": {"code": self.code, **self.data}}


def schema_said(e: "db.SchemaError") -> str:
    """What to tell a caller whose request the daemon cannot serve for the database's sake: which side is old,
    and the one command that fixes it."""
    if e.kind == "newer":
        return (f"the database schema is newer than this daemon ({e.have} vs {e.latest}): the daemon is running old "
                f"code, restart it with `{db.schema.RESTART_COMMAND}`")
    if e.kind == "older":
        return (f"the database schema is older than this daemon needs ({e.have} vs {e.latest}): it is migrated when "
                f"the daemon starts; restart it with `{db.schema.RESTART_COMMAND}`")
    return str(e)


def bad_request(message: str, field: str | None = None, rpc_code: int = BAD_REQUEST) -> RpcError:
    return RpcError(rpc_code, "bad_request", message, **({"field": field} if field else {}))


def refused(reason: str, gate: str | None = None, hint: str | None = None) -> RpcError:
    extra = {k: v for k, v in (("gate", gate), ("hint", hint)) if v}
    return RpcError(REFUSED, "refused", reason, reason=reason, **extra)


class Caller:
    """Who is on the other end, and only on positive proof: an agent when the peer process descends from a
    session `hl launch` recorded, the founder when `hello` carried the founder key, anyone else anonymous.
    A peer nothing can be traced for (a double-forked child, a failed pid lookup) proves nothing."""

    def __init__(self, peer: int | None, key=None) -> None:
        agent = sessiontoken.agent_of(peer)
        if agent:       # wins over a key: a session that held the founder key would be a leak, not the founder
            self.role, self.info = "agent", {"kind": "agent", "name": agent}
        elif sessiontoken.founder_key_ok(key):
            self.role, self.info = "founder", {"kind": "person", "name": getpass.getuser()}
        else:
            self.role, self.info = "anonymous", {"kind": "anonymous", "name": "anonymous"}

    @property
    def actor(self) -> str:
        """The name a write is made under: an agent's own, else `human` (the founder and any device)."""
        return self.info["name"] if self.role == "agent" else db.HUMAN

    @property
    def actor_info(self) -> dict:
        """The spec's `Actor` for an event: a person is a `user` there."""
        return {**self.info, "kind": "user" if self.info["kind"] == "person" else self.info["kind"]}

    def allows(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


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
    ctx.caller = caller = Caller(ctx.peer, params.get("key"))
    return {"protocol": PROTOCOL, "min_client": MIN_CLIENT, "daemon": _loaded_sha(), "schema": db.LATEST_VERSION,
            "role": caller.role, "caller": caller.info,
            "capabilities": {"chat": False, "review": False, "input_ask": False, "terminal_format": []},
            "transport": "local",
            "methods": _methods(caller)}


def _whoami(ctx: "Connection", params: dict) -> dict:
    return {"caller": ctx.caller.info, "role": ctx.caller.role, "methods": _methods(ctx.caller)}


class Method(NamedTuple):
    handler: Callable
    role: str               # the lowest role that may call it
    write: bool = False     # takes a request_id, keeps its reply a day, and is logged under its actor
    own_event: bool = False  # it writes its own event, under its actor, so no generic one is added


# Every method a later task adds is a line here.
METHODS = {
    "hello": Method(_hello, "anonymous"),
    "whoami": Method(_whoami, "agent"),
}


def _methods(caller: Caller) -> list[dict]:
    return [{"name": name, "since": 1, "role": m.role, "allowed": caller.allows(m.role)} for name, m in METHODS.items()]


_RETRY_LOCKS = [threading.Lock() for _ in range(64)]      # striped by request_id: a few unrelated writes may wait, none run twice


def _params_key(params: dict) -> str:
    return json.dumps({k: v for k, v in params.items() if k != "request_id"}, sort_keys=True, ensure_ascii=False)


def _write(ctx: "Connection", name: str, m: Method, params: dict) -> dict:
    """Run a write once per request_id. The reply of a success is kept a day; a retry with the same id and
    params gets it back, marked `replayed`, and repeats nothing. Refusals and errors are not kept, so a retry
    is judged again. Retries of one id wait for each other, so two at once cannot both run."""
    rid = params.get("request_id")
    if not isinstance(rid, str) or not rid or len(rid) > REQUEST_ID_MAX:
        raise bad_request(f"{name} is a write: it takes a request_id, a string of 1 to {REQUEST_ID_MAX} characters", "request_id")
    key = (ctx.caller.actor, rid)       # the founder and every device share `human`: key by device id when devices land
    with _RETRY_LOCKS[hash(key) % len(_RETRY_LOCKS)]:
        con = db.connect()
        try:
            row = con.execute("SELECT method, params, reply FROM rpc_replies WHERE caller = ? AND request_id = ? AND ts > ?",
                              (*key, int(time.time()) - REPLY_KEEP_S)).fetchone()
            pkey = _params_key(params)
            if row:
                if row["method"] != name or row["params"] != pkey:
                    raise bad_request(f"request_id {rid!r} was used for a different call", "request_id")
                return {**json.loads(row["reply"]), "replayed": True}
            fx = con.execute("SELECT method, result_json FROM write_effects WHERE caller = ? AND request_id = ? AND created_at > ?",
                             (*key, int(time.time()) - REPLY_KEEP_S)).fetchone()
            if fx:      # the write landed and the daemon died before its reply was kept: answer, repeat nothing
                if fx["method"] != name:
                    raise bad_request(f"request_id {rid!r} was used for a different call", "request_id")
                return {**json.loads(fx["result_json"]), "replayed": True}
            result = m.handler(ctx, params)
            if not m.own_event:
                db.add_event(con, ctx.caller.actor, "rpc_write", {"method": name, "request_id": rid, "actor": ctx.caller.actor_info})
            now = int(time.time())
            con.execute("DELETE FROM rpc_replies WHERE ts <= ?", (now - REPLY_KEEP_S,))
            con.execute("DELETE FROM write_effects WHERE created_at <= ?", (now - REPLY_KEEP_S,))
            con.execute("INSERT OR REPLACE INTO rpc_replies(caller, request_id, method, params, reply, ts) VALUES(?,?,?,?,?,?)",
                        (*key, name, pkey, json.dumps(result), now))
            return result
        finally:
            con.close()


class Connection:
    def __init__(self, conn: socket.socket, log, handle_legacy) -> None:
        self.conn, self.log, self.handle_legacy = conn, log, handle_legacy
        self.peer = sessiontoken.peer_pid(conn)
        self.caller = Caller(self.peer)
        self.said_hello = False
        self._buf = b""
        self._send_lock = threading.Lock()      # replies and event notices are written by different threads
        self.subs: list = []
        self.on_close: list = []
        self.after_reply: list = []             # run once the reply to the call in hand is sent

    def _line(self) -> bytes | None:
        """The next line, or what is left at EOF; None at a clean EOF. A line past the limit is bad_request."""
        while True:
            nl = self._buf.find(b"\n")
            if nl != -1:
                line, self._buf = self._buf[:nl], self._buf[nl + 1:]
                return line
            if len(self._buf) > MAX_LINE_BYTES:
                raise bad_request(f"a line is at most {MAX_LINE_BYTES} bytes")
            self.conn.settimeout(IDLE_TIMEOUT_S if self.said_hello and not self._buf else RECV_TIMEOUT_S)
            chunk = self.conn.recv(65536)
            if not chunk:       # a client that shuts its write side after one object needs no newline
                line, self._buf = self._buf, b""
                return line or None
            self._buf += chunk

    def _send(self, obj: dict) -> bool:
        try:
            self.send_line(obj)
            return True
        except OSError:
            return False        # the client is gone; nothing left to tell it

    def send_line(self, obj: dict) -> None:
        with self._send_lock:
            self.conn.sendall(json.dumps(obj).encode("utf-8") + b"\n")

    def hang_up(self) -> None:
        """End the connection from another thread: the read in `serve` then returns."""
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def serve(self) -> None:
        try:
            self._serve()
        finally:
            for fn in self.on_close:
                fn()

    def _serve(self) -> None:
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
        except db.SchemaError as e:
            error = refused(schema_said(e), gate="schema", hint=db.schema.RESTART_COMMAND)
        except Exception as e:  # noqa: BLE001 - a call that breaks must reply, not kill the connection
            self.log(f"  rpc: {type(e).__name__}: {e}")
            error = RpcError(INTERNAL, "internal", "internal error")
        else:
            if isinstance(msg, dict) and "id" not in msg:
                return True     # a notification is never answered
            sent = self._send({"jsonrpc": "2.0", "id": rid, "result": result})
            todo, self.after_reply = self.after_reply, []
            for fn in todo:
                fn()
            return sent
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
            raise bad_request(f"no such method: {method}", "method", NO_SUCH_METHOD)
        if not self.caller.allows(entry.role):
            raise refused(f"{method} is for a {entry.role} or above; this caller is {self.caller.role}", gate="role")
        result = _write(self, method, entry, params) if entry.write else entry.handler(self, params)
        if method == "hello":
            self.said_hello = True
        return result

    def require_self(self, agent) -> None:
        """For the methods that act as the caller's own session: an `agent` that is not the caller is refused."""
        if self.caller.role == "agent" and agent != self.caller.info["name"]:
            raise refused(f"{agent!r} is not this session's agent", gate="not_self")
