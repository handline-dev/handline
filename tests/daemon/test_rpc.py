"""Tests: the daemon's JSON-RPC connection (contract/protocol.md): framing, hello first, typed errors,
roles, and the legacy `{verb}` adapter that still answers once and hangs up.

Run on its own: python3 tests/daemon/test_rpc.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc, socket_server  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402


class Wire:
    """A client end of a socket pair whose other end the daemon serves on a thread of its own."""

    def __init__(self, log=lambda m: None) -> None:
        self.a, b = socket.socketpair()
        self.a.settimeout(10)
        self.thread = threading.Thread(target=socket_server._serve_one, args=(b, log))
        self.thread.start()
        self.buf = b""

    def send(self, obj) -> None:
        self.a.sendall((obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n")

    def read(self) -> dict | None:
        while b"\n" not in self.buf:
            chunk = self.a.recv(65536)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def call(self, method, params=None, id=1, **extra):
        self.send({"jsonrpc": "2.0", "id": id, "method": method, **({"params": params} if params is not None else {}), **extra})
        return self.read()

    def close(self) -> None:
        self.a.close()
        self.thread.join(timeout=10)


def _code(reply) -> str:
    return reply["error"]["data"]["code"]


def test_hello_names_the_protocol_the_caller_and_what_it_may_call(home: Path) -> None:
    use_home(home)
    w = Wire()
    r = w.call("hello", {"client": "test/1", "protocol": 1})
    res = r["result"]
    check("hello: a result, the client's id echoed", r["jsonrpc"] == "2.0" and r["id"] == 1 and "error" not in r, str(r))
    check("hello: protocol, min_client, daemon, schema",
          res["protocol"] == rpc.PROTOCOL and res["min_client"] == rpc.MIN_CLIENT and res["daemon"]
          and res["schema"] == db.LATEST_VERSION, str(res))
    check("hello: the local user on the local socket is the founder", res["role"] == "founder"
          and res["caller"]["kind"] == "person" and res["transport"] == "local", str(res))
    check("hello: the methods are listed with their role and whether this caller may call them",
          {"name": "hello", "since": 1, "role": "anonymous", "allowed": True} in res["methods"], str(res["methods"]))
    check("hello: capabilities say what is not there yet", res["capabilities"]["chat"] is False)
    w.close()


def test_a_newer_client_is_served_and_an_older_one_is_refused_with_the_fix(home: Path) -> None:
    use_home(home)
    w = Wire()
    check("hello: a client newer than the daemon gets a normal reply",
          "result" in w.call("hello", {"client": "t", "protocol": rpc.PROTOCOL + 5}))
    w.close()
    real = rpc.MIN_CLIENT
    rpc.MIN_CLIENT = 3
    try:
        w = Wire()
        r = w.call("hello", {"client": "t", "protocol": 2})
        w.close()
    finally:
        rpc.MIN_CLIENT = real
    d = r["error"]["data"]
    check("hello: a client below min_client is refused, gate protocol, with the command that fixes it",
          r["error"]["code"] == -32002 and d["code"] == "refused" and d["gate"] == "protocol"
          and d["hint"] == "hl service install" and d["reason"], str(r))


def test_one_connection_carries_many_calls_and_a_one_shot_works(home: Path) -> None:
    use_home(home)
    w = Wire()
    w.call("hello", {"client": "t", "protocol": 1})
    ids = [w.call("hello", {"client": "t", "protocol": 1}, id=i)["id"] for i in (7, "x", 9)]
    check("framing: many calls, each answered under its own id", ids == [7, "x", 9], str(ids))
    w.close()
    w = Wire()
    w.send({"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "t", "protocol": 1}})
    w.a.shutdown(socket.SHUT_WR)
    check("framing: a one-shot call that shuts its write side still gets its reply", "result" in w.read())
    check("framing: and then the connection ends", w.read() is None)
    w.close()


def test_every_failure_is_a_typed_error(home: Path) -> None:
    use_home(home)
    w = Wire()
    r = w.call("session.wake", {"agent": "x"})
    check("errors: a call before hello is bad_request on the method", _code(r) == "bad_request"
          and r["error"]["code"] == -32600 and r["error"]["data"]["field"] == "method", str(r))
    w.call("hello", {"client": "t", "protocol": 1})
    r = w.call("no.such.method", {})
    check("errors: an unknown method is bad_request", _code(r) == "bad_request" and r["id"] == 1, str(r))
    r = w.call("hello", ["t", 1])
    check("errors: params that are not an object are bad_request with the invalid-params code",
          _code(r) == "bad_request" and r["error"]["code"] == -32602, str(r))
    r = w.call("hello", {"client": "t"})
    check("errors: a missing field is named", _code(r) == "bad_request" and r["error"]["data"]["field"] == "protocol", str(r))
    w.send(b"[1, 2]")
    r = w.read()
    check("errors: a batch is bad_request", _code(r) == "bad_request" and "batch" in r["error"]["message"], str(r))
    w.send(b"{not json")
    r = w.read()
    check("errors: a line that is not JSON is the parse-error code, id null",
          r["error"]["code"] == -32700 and _code(r) == "bad_request" and r["id"] is None, str(r))
    w.send({"jsonrpc": "2.0", "id": True, "method": "hello", "params": {"client": "t", "protocol": 1}})
    check("errors: an id that is not an integer or a string is bad_request", _code(w.read()) == "bad_request")
    w.send({"jsonrpc": "2.0", "method": "hello", "params": {"client": "t", "protocol": 1}})
    check("framing: a notification (no id) is not answered", w.call("hello", {"client": "t", "protocol": 1}, id=5)["id"] == 5)
    w.close()


def test_a_line_past_the_limit_ends_the_connection_with_bad_request(home: Path) -> None:
    use_home(home)
    w = Wire()
    def flood():
        try:
            w.a.sendall(b"x" * (rpc.MAX_LINE_BYTES + 70000))
        except OSError:
            pass        # the daemon hung up on it, as it should
    threading.Thread(target=flood, daemon=True).start()
    r = w.read()
    check("framing: an oversize line is bad_request and the connection is closed",
          r is not None and _code(r) == "bad_request" and w.read() is None, str(r))
    w.close()


def test_a_handler_that_breaks_is_internal_and_the_connection_goes_on(home: Path) -> None:
    use_home(home)
    def boom(ctx, params):
        raise RuntimeError("secret detail")
    rpc.METHODS["boom"] = (boom, "anonymous")
    try:
        w = Wire()
        w.call("hello", {"client": "t", "protocol": 1})
        r = w.call("boom", {})
        check("errors: internal, without the exception text", r["error"]["code"] == -32603 and _code(r) == "internal"
              and "secret" not in json.dumps(r), str(r))
        check("errors: and the next call is served", "result" in w.call("hello", {"client": "t", "protocol": 1}))
        w.close()
    finally:
        del rpc.METHODS["boom"]


def test_a_call_below_its_role_is_refused_at_the_role_gate(home: Path) -> None:
    use_home(home)
    rpc.METHODS["founder.only"] = (lambda ctx, params: {}, "founder")
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "backend"
    try:
        w = Wire()
        hello = w.call("hello", {"client": "t", "protocol": 1})["result"]
        rpc_methods = w.call("hello", {"client": "t", "protocol": 1})["result"]["methods"]
        r = w.call("founder.only", {})
        w.close()
    finally:
        sessiontoken.agent_of = real
        del rpc.METHODS["founder.only"]
    check("roles: a process in an agent's session is the agent", hello["role"] == "agent"
          and hello["caller"] == {"kind": "agent", "name": "backend"}, str(hello))
    check("roles: and hello says what it may not call",
          {"name": "founder.only", "since": 1, "role": "founder", "allowed": False} in rpc_methods)
    check("roles: a call below its role is refused, gate role", _code(r) == "refused"
          and r["error"]["data"]["gate"] == "role" and r["error"]["code"] == -32002, str(r))


def test_a_legacy_verb_gets_one_reply_and_a_hangup(home: Path) -> None:
    use_home(home)
    w = Wire()
    w.send({"verb": "not-a-real-verb", "agent": "backend"})
    r = w.read()
    check("legacy: a {verb} line is answered {ok, said}", r["ok"] is False and "not a socket verb" in r["said"], str(r))
    check("legacy: and the connection is closed after it", w.read() is None)
    w.close()


def main() -> int:
    def tests(td):
        run(lambda: test_hello_names_the_protocol_the_caller_and_what_it_may_call(Path(td) / "r1"))
        run(lambda: test_a_newer_client_is_served_and_an_older_one_is_refused_with_the_fix(Path(td) / "r2"))
        run(lambda: test_one_connection_carries_many_calls_and_a_one_shot_works(Path(td) / "r3"))
        run(lambda: test_every_failure_is_a_typed_error(Path(td) / "r4"))
        run(lambda: test_a_line_past_the_limit_ends_the_connection_with_bad_request(Path(td) / "r5"))
        run(lambda: test_a_handler_that_breaks_is_internal_and_the_connection_goes_on(Path(td) / "r6"))
        run(lambda: test_a_call_below_its_role_is_refused_at_the_role_gate(Path(td) / "r7"))
        run(lambda: test_a_legacy_verb_gets_one_reply_and_a_hangup(Path(td) / "r8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
