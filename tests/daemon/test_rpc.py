"""Tests: the daemon's JSON-RPC connection (contract/protocol.md): framing, hello first, typed errors,
roles, and the legacy `{verb}` adapter that still answers once and hangs up.

Run on its own: python3 tests/daemon/test_rpc.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc, socket_server  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402


def _hp(**extra) -> dict:
    """hello params from the founder: the key only the founder's own process can read."""
    sessiontoken.ensure_founder_key()
    return {"client": "t", "protocol": 1, "key": sessiontoken.founder_key_held(), **extra}


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
    r = w.call("hello", _hp(client="test/1"))
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
          "result" in w.call("hello", _hp(protocol=rpc.PROTOCOL + 5)))
    w.close()
    real = rpc.MIN_CLIENT
    rpc.MIN_CLIENT = 3
    try:
        w = Wire()
        r = w.call("hello", _hp(protocol=2))
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
    w.call("hello", _hp())
    ids = [w.call("hello", _hp(), id=i)["id"] for i in (7, "x", 9)]
    check("framing: many calls, each answered under its own id", ids == [7, "x", 9], str(ids))
    w.close()
    w = Wire()
    w.send({"jsonrpc": "2.0", "id": 1, "method": "hello", "params": _hp()})
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
    w.call("hello", _hp())
    r = w.call("no.such.method", {})
    check("errors: an unknown method is bad_request with the method-not-found code",
          _code(r) == "bad_request" and r["error"]["code"] == -32601 and r["id"] == 1, str(r))
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
    w.send({"jsonrpc": "2.0", "id": True, "method": "hello", "params": _hp()})
    check("errors: an id that is not an integer or a string is bad_request", _code(w.read()) == "bad_request")
    w.send({"jsonrpc": "2.0", "method": "hello", "params": _hp()})
    check("framing: a notification (no id) is not answered", w.call("hello", _hp(), id=5)["id"] == 5)
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
    rpc.METHODS["boom"] = rpc.Method(boom, "anonymous")
    try:
        w = Wire()
        w.call("hello", _hp())
        r = w.call("boom", {})
        check("errors: internal, without the exception text", r["error"]["code"] == -32603 and _code(r) == "internal"
              and "secret" not in json.dumps(r), str(r))
        check("errors: and the next call is served", "result" in w.call("hello", _hp()))
        w.close()
    finally:
        del rpc.METHODS["boom"]


def test_a_call_below_its_role_is_refused_at_the_role_gate(home: Path) -> None:
    use_home(home)
    rpc.METHODS["founder.only"] = rpc.Method(lambda ctx, params: {}, "founder")
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "backend"
    try:
        w = Wire()
        hello = w.call("hello", _hp())["result"]
        rpc_methods = w.call("hello", _hp())["result"]["methods"]
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


def test_a_call_before_hello_is_refused_whatever_it_is(home: Path) -> None:
    use_home(home)
    ran = []
    rpc.METHODS["t.plain"] = rpc.Method(lambda ctx, params: ran.append(1) or {}, "anonymous")
    try:
        w = Wire()
        r = w.call("t.plain", {})
        check("hello first: a method called before hello is bad_request saying the first call is hello",
              _code(r) == "bad_request" and "first call is hello" in r["error"]["message"] and not ran, str(r))
        w.call("hello", _hp())
        check("hello first: after hello it is served", "result" in w.call("t.plain", {}) and ran == [1])
        w.close()
    finally:
        del rpc.METHODS["t.plain"]


def test_the_founder_is_known_only_by_the_founder_key(home: Path) -> None:
    use_home(home)
    sessiontoken.ensure_founder_key()
    key = sessiontoken.founder_key_held()
    roles = {}
    for label, params in (("right key", {"key": key}), ("no key", {}), ("wrong key", {"key": key + "x"}), ("empty key", {"key": ""}),
                          ("key of the wrong type", {"key": 7})):
        w = Wire()
        roles[label] = w.call("hello", {"client": "t", "protocol": 1, **params})["result"]
        w.close()
    check("founder key: the right one gives founder", roles["right key"]["role"] == "founder", str(roles["right key"]))
    check("founder key: none, a wrong one, an empty one and a mistyped one give anonymous, never founder",
          all(roles[k]["role"] == "anonymous" and roles[k]["caller"]["kind"] == "anonymous"
              for k in ("no key", "wrong key", "empty key", "key of the wrong type")), str(roles))
    check("founder key: an anonymous caller is told it may not call whoami",
          {m["name"]: m["allowed"] for m in roles["no key"]["methods"]}["whoami"] is False)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "backend"
    try:
        w = Wire()
        got = w.call("hello", {"client": "t", "protocol": 1, "key": key})["result"]
        w.close()
    finally:
        sessiontoken.agent_of = real
    check("founder key: a session's process is an agent even holding the key", got["role"] == "agent", str(got))
    real = sessiontoken.peer_pid
    sessiontoken.peer_pid = lambda conn: None
    try:
        w = Wire()
        got = w.call("hello", {"client": "t", "protocol": 1})["result"]
        w.close()
    finally:
        sessiontoken.peer_pid = real
    check("founder key: a peer that cannot be found is anonymous", got["role"] == "anonymous", str(got))


def test_the_founder_key_is_made_private_in_a_folder_sessions_cannot_read(home: Path) -> None:
    use_home(home)
    check("founder key: none before the daemon makes one", sessiontoken.founder_key_held() is None)
    check("founder key: the daemon makes it", sessiontoken.ensure_founder_key())
    key = sessiontoken.founder_key_held()
    file = sessiontoken.protected.root() / sessiontoken.FOUNDER_NS / "key"
    check("founder key: the file is 0600 and the folder 0700", oct(file.stat().st_mode & 0o777) == "0o600"
          and oct(file.parent.stat().st_mode & 0o777) == "0o700", oct(file.stat().st_mode))
    check("founder key: a second start keeps it", sessiontoken.ensure_founder_key() and sessiontoken.founder_key_held() == key)
    check("founder key: its folder is one a launch hides", file.parent in sessiontoken.hidden_folders())


def test_a_connection_may_wait_between_calls_but_not_over_half_a_line(home: Path) -> None:
    use_home(home)
    real = rpc.RECV_TIMEOUT_S, rpc.IDLE_TIMEOUT_S
    rpc.RECV_TIMEOUT_S, rpc.IDLE_TIMEOUT_S = 0.4, 10
    try:
        w = Wire()
        w.call("hello", _hp())
        time.sleep(1.0)
        check("idle: a call more than the receive limit after the last still succeeds on the same connection",
              "result" in w.call("hello", _hp(), id=2))
        w.a.sendall(b'{"jsonrpc": "2.0", "id": 3, "method"')
        check("idle: a line begun and not finished is cut off", w.read() is None)
        w.close()
        w = Wire()
        time.sleep(1.0)
        check("idle: a connection that never said hello gets the short limit", w.read() is None)
        w.close()
    finally:
        rpc.RECV_TIMEOUT_S, rpc.IDLE_TIMEOUT_S = real


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
        run(lambda: test_a_call_before_hello_is_refused_whatever_it_is(Path(td) / "r9"))
        run(lambda: test_the_founder_is_known_only_by_the_founder_key(Path(td) / "r10"))
        run(lambda: test_the_founder_key_is_made_private_in_a_folder_sessions_cannot_read(Path(td) / "r11"))
        run(lambda: test_a_connection_may_wait_between_calls_but_not_over_half_a_line(Path(td) / "r12"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
