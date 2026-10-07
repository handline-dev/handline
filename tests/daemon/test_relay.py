"""Tests: the relay socket (daemon/relay.py): who may connect, what a relayed device may call, and what its writes leave behind.

Run on its own: python3 tests/daemon/test_relay.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.daemon import relay, rpc, stream  # noqa: E402
from handline.infra import protected, sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import _code  # noqa: E402


def _record_gateway(pid) -> None:
    protected.put(relay.NS, relay.NAME, f"{pid}\n")


class Relay:
    """A client end of a socket pair whose other end the relay socket's handler serves on a thread of its own."""

    def __init__(self) -> None:
        self.a, b = socket.socketpair()
        self.a.settimeout(10)
        self.log: list[str] = []
        self.thread = threading.Thread(target=relay._serve_one, args=(b, self.log.append))
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

    def call(self, method, params=None, id=1):
        self.send({"jsonrpc": "2.0", "id": id, "method": method, **({"params": params} if params is not None else {})})
        return self.read()

    def open(self, device="dev_a", name="phone", role="member"):
        return self.call("relay.open", {"device_id": device, "name": name, "role": role}, id=0)

    def close(self) -> None:
        self.a.close()
        self.thread.join(timeout=10)


def _nothing_at_all(client: Relay, first=None) -> bool:
    """The connection is closed with not one byte sent back."""
    try:
        if first is not None:
            client.send(first)
    except BrokenPipeError:
        pass            # it was closed before the line went
    try:
        return client.a.recv(1) == b""
    finally:
        client.close()


def test_a_peer_that_is_not_the_gateway_gets_nothing(home: Path) -> None:
    support._board_world(home)
    opening = {"jsonrpc": "2.0", "id": 0, "method": "relay.open", "params": {"device_id": "d", "name": "p", "role": "member"}}
    check("relay: with no gateway recorded every connection is closed without a byte", _nothing_at_all(Relay(), opening))
    _record_gateway(os.getpid() + 1)
    c = Relay()
    check("relay: a peer that is not the recorded pid is closed without a byte, and the refusal is logged", _nothing_at_all(c, opening) and c.log)
    protected.put(relay.NS, relay.NAME, "not a pid\n")
    check("relay: a pid file that is not a pid refuses everything", _nothing_at_all(Relay(), opening))
    _record_gateway(os.getpid())
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "manager"
    try:
        check("relay: the recorded pid inside an agent's session is closed without a byte", _nothing_at_all(Relay(), opening))
    finally:
        sessiontoken.agent_of = real
    real_start = sessiontoken.started_at
    sessiontoken.started_at = lambda pid: protected.written_at(relay.NS, relay.NAME) + 5     # a process that came after the file: a reused pid
    try:
        check("relay: the recorded pid held by a process that started after the file was written is closed without a byte", _nothing_at_all(Relay(), opening))
        sessiontoken.started_at = lambda pid: None
        check("relay: a start time that cannot be read refuses everything", _nothing_at_all(Relay(), opening))
        sessiontoken.started_at = lambda pid: protected.written_at(relay.NS, relay.NAME) - 5   # the gateway, started before it wrote its pid
        r = Relay()
        check("relay: the recorded pid started before the file was written is served", r.open() == {"jsonrpc": "2.0", "id": 0, "result": {"ok": True}})
        r.close()
    finally:
        sessiontoken.started_at = real_start
    check("relay: the real start time of this process is before the file written now and not after it", (_record_gateway(os.getpid()), True)[1]
          and sessiontoken.started_at(os.getpid()) <= protected.written_at(relay.NS, relay.NAME))
    folder = protected.root() / sessiontoken.NS
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0)
    try:
        check("relay: a table of sessions that cannot be read refuses everything", _nothing_at_all(Relay(), opening))
    finally:
        folder.chmod(0o700)


def test_the_gateway_opens_a_connection_for_one_device_and_nothing_else(home: Path) -> None:
    support._board_world(home)
    _record_gateway(os.getpid())
    for what, first in (("not JSON", b"{"), ("a call that is not relay.open", {"jsonrpc": "2.0", "id": 0, "method": "whoami"}),
                        ("the founder's role", {"jsonrpc": "2.0", "id": 0, "method": "relay.open", "params": {"device_id": "d", "name": "p", "role": "founder"}}),
                        ("an id with a space", {"jsonrpc": "2.0", "id": 0, "method": "relay.open", "params": {"device_id": "a b", "name": "p", "role": "member"}}),
                        ("no name", {"jsonrpc": "2.0", "id": 0, "method": "relay.open", "params": {"device_id": "d", "role": "member"}}),
                        ("no id", {"jsonrpc": "2.0", "method": "relay.open", "params": {"device_id": "d", "name": "p", "role": "member"}})):
        check(f"relay: {what} as the first line closes the connection without a byte", _nothing_at_all(Relay(), first))
    r = Relay()
    check("relay.open: answered ok", r.open() == {"jsonrpc": "2.0", "id": 0, "result": {"ok": True}})
    r.call("relay.open", {"device_id": "dev_b", "name": "x", "role": "member"})
    got = r.call("relay.open", {"device_id": "dev_b", "name": "x", "role": "member"})
    check("relay.open: a second one on the connection is no method", _code(got) == "bad_request", str(got))
    got = r.call("whoami")
    check("whoami: the device, as a member, never the gateway", got["result"]["role"] == "member"
          and got["result"]["caller"] == {"kind": "device", "name": "phone", "device": "dev_a"}, str(got))
    got = r.call("hello", {"client": "gateway/1", "protocol": 1, "key": sessiontoken.founder_key_held()})
    check("hello: relayed, it answers for the device and the founder key it carries gives it nothing",
          got["result"]["role"] == "member" and r.call("settings.set", {"request_id": "k", "values": {}})["error"]["data"]["gate"] == "role", str(got))
    got = r.call("agent.create", {"request_id": "c", "name": "a.b", "role": "x", "repo": "."})
    check("agent.create: a relayed call to a founder's method is refused, gate role", _code(got) == "refused" and got["error"]["data"]["gate"] == "role", str(got))
    r.close()


def test_a_relayed_write_is_the_devices_own(home: Path) -> None:
    support._board_world(home)
    _record_gateway(os.getpid())
    ok, _, one = actions.task_new("manager", "worker", "first", "do it", timeout_s=3600)
    ok, _, two = actions.task_new("manager", "worker", "second", "do it", timeout_s=3600)
    a, b = Relay(), Relay()
    a.open("dev_a", "phone")
    b.open("dev_b", "tablet")
    got = a.call("task.cancel", {"task": one, "reason": "not needed", "request_id": "1"})
    check("relayed write: a member device may cancel a task", got["result"]["accepted"] is True, str(got))
    con = db.connect()
    ev = [json.loads(r["payload"]) for r in con.execute("SELECT payload FROM events WHERE kind = 'rpc_write'")]
    con.close()
    check("relayed write: its event names the device as the actor", ev and ev[-1]["method"] == "task.cancel"
          and ev[-1]["actor"] == {"kind": "device", "name": "phone", "device": "dev_a"}, str(ev))
    again = a.call("task.cancel", {"task": one, "reason": "not needed", "request_id": "1"}, id=2)
    check("relayed write: the same device's retry is replayed", again["result"].get("replayed") is True, str(again))
    other = b.call("task.cancel", {"task": two, "reason": "not needed", "request_id": "1"})
    check("relayed write: another device with the same request_id is not answered with the first's reply",
          other["result"].get("replayed") is None and other["result"]["accepted"] is True and "T%d" % two in other["result"]["said"], str(other))
    a.close()
    b.close()


def test_a_closed_connection_ends_the_devices_subscriptions(home: Path) -> None:
    support._board_world(home)
    _record_gateway(os.getpid())
    r = Relay()
    r.open()
    got = r.call("subscribe", {"topics": ["inbox"], "since": 0})
    check("subscribe: relayed, it is accepted", "result" in got, str(got))
    held = len(stream.HUB.subs)
    check("subscribe: the device holds one", held >= 1)
    r.close()
    check("relay: closing the connection drops the device's subscription", len(stream.HUB.subs) == held - 1, str(len(stream.HUB.subs)))


def test_without_a_gateway_the_daemon_serves_as_it_did(home: Path) -> None:
    support._board_world(home)
    from test_rpc import Wire, _hp
    w = Wire()
    got = w.call("hello", _hp())
    check("no gateway: the daemon's own socket still answers hello as the founder", got["result"]["role"] == "founder", str(got))
    w.close()


def test_a_devices_task_writes_are_recorded_under_the_device_so_its_retry_finds_them(home: Path) -> None:
    from handline.daemon import rpc_tasks
    support._board_world(home)
    _record_gateway(os.getpid())
    ok, _, tid = actions.task_new("manager", "worker", "first", "do it", timeout_s=3600)
    a = Relay()
    a.open("dev_a", "phone")
    got = a.call("task.memo.add", {"task": tid, "text": "a note", "request_id": "m1"})
    con = db.connect()
    rows = [r["caller"] for r in con.execute("SELECT caller FROM write_effects WHERE request_id = 'm1'")]
    con.close()
    check("device retry: a memo written by a device is recorded under device:<id>, not as human", got.get("result", {}).get("accepted") is True and rows == ["device:dev_a"], str((got, rows)))
    import types
    ctx = types.SimpleNamespace(caller=rpc.DeviceCaller("dev_a", "phone"))
    con = db.connect()
    rpc_tasks._recorded(ctx, {"request_id": "d1"}, "task.done")(con, "done")
    rows = [r["caller"] for r in con.execute("SELECT caller FROM write_effects WHERE request_id = 'd1'")]
    con.close()
    check("device retry: so is what an action records in its own transaction (done, reopen, merge, queue, release)", rows == ["device:dev_a"], str(rows))
    a.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_peer_that_is_not_the_gateway_gets_nothing(Path(td) / "r1"))
        run(lambda: test_the_gateway_opens_a_connection_for_one_device_and_nothing_else(Path(td) / "r2"))
        run(lambda: test_a_relayed_write_is_the_devices_own(Path(td) / "r3"))
        run(lambda: test_a_closed_connection_ends_the_devices_subscriptions(Path(td) / "r4"))
        run(lambda: test_without_a_gateway_the_daemon_serves_as_it_did(Path(td) / "r5"))
        run(lambda: test_a_devices_task_writes_are_recorded_under_the_device_so_its_retry_finds_them(Path(td) / "r6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
