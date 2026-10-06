"""Tests: the calls of one connection are served concurrently (daemon/rpc.py): a call that waits holds up none behind it, replies come in the
order the calls finish and are matched by id, a hang-up ends the waiting calls, a connection has a bounded number of calls in flight and the
daemon a bounded number of connections, and `hello` and the writes keep their rules.

Run on its own: python3 tests/daemon/test_rpc_concurrency.py   (all areas: python3 tests/test_ocp.py)
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


class Held:
    """A method that holds its call until released: `t.held`."""
    def __enter__(self):
        self.entered, self.release, self.n = threading.Semaphore(0), threading.Event(), 0

        def held(ctx, params):
            self.n += 1
            self.entered.release()
            self.release.wait(20)
            return {"done": params.get("n")}
        rpc.METHODS["t.held"] = rpc.Method(held, "anonymous")
        return self

    def __exit__(self, *exc):
        self.release.set()
        rpc.METHODS.pop("t.held", None)


def _world(home: Path) -> None:
    support._board_world(home)
    db.connect().close()        # the database is made (and migrated) before calls race to open it, as the daemon has done at its start


def _until(pred, secs: float = 30.0) -> bool:
    end = time.monotonic() + secs
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


def test_a_waiting_call_does_not_hold_up_the_calls_behind_it(home: Path) -> None:
    _world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "manager"
    w = Wire()
    try:
        w.call("hello", {"client": "t", "protocol": 1})
        w.send({"jsonrpc": "2.0", "id": "wait", "method": "inbox.list", "params": {"wait_s": 30}})
        t0 = time.time()
        quick = w.call("whoami", id="quick")
        check("concurrency: the call behind a waiting inbox.list is answered first", quick["id"] == "quick" and "result" in quick and time.time() - t0 < 10, str(quick))
        ask = _add_request("worker", "manager", "something for you")
        got = w.read()
        check("concurrency: and the waiting one returns when an item arrives, by its own id", got["id"] == "wait" and [i["id"] for i in got["result"]["items"]] == [ask], str(got))
    finally:
        sessiontoken.agent_of = real
        w.close()


def _add_request(requester: str, target: str, body: str) -> int:
    con = db.connect()
    try:
        return db.add_request(con, requester, target, body, None, None, None, "ask", 0, None)
    finally:
        con.close()


def test_replies_come_in_the_order_the_calls_finish_matched_by_id(home: Path) -> None:
    _world(home)
    with Held() as h:
        w = Wire()
        try:
            w.call("hello", _hp())
            for n in (1, 2, 3):
                w.send({"jsonrpc": "2.0", "id": f"slow-{n}", "method": "t.held", "params": {"n": n}})
            check("concurrency: all three are being served at once", all(h.entered.acquire(timeout=10) for _ in range(3)) and h.n == 3)
            w.send({"jsonrpc": "2.0", "id": 99, "method": "whoami"})
            first = w.read()
            check("concurrency: a quick call sent after three held ones is answered before them", first["id"] == 99, str(first))
            h.release.set()
            rest = [w.read() for _ in range(3)]
            check("concurrency: the held ones follow, each with its own id and result", sorted(r["id"] for r in rest) == ["slow-1", "slow-2", "slow-3"]
                  and all(r["result"]["done"] == int(r["id"][-1]) for r in rest), str(rest))
        finally:
            w.close()


def test_a_client_that_hangs_up_ends_its_waiting_calls(home: Path) -> None:
    _world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "manager"
    w = Wire()
    try:
        w.call("hello", {"client": "t", "protocol": 1})
        serving = lambda: sum(1 for t in threading.enumerate() if t.name.endswith("(_serve_call)"))
        for n in range(3):
            w.send({"jsonrpc": "2.0", "id": n, "method": "inbox.list", "params": {"wait_s": 600}})
        w.a.settimeout(0.5)
        try:
            early = w.read()
        except OSError:
            early = None
        w.a.settimeout(10)
        check("hang up: three waiting calls are three threads serving", _until(lambda: serving() == 3, 10), f"{serving()} serving; an early reply: {early}")
        w.close()
        check("hang up: they all end within a second or two of it", _until(lambda: serving() == 0, 5), str(serving()))
    finally:
        sessiontoken.agent_of = real


def test_a_client_that_shuts_its_write_side_still_gets_its_replies(home: Path) -> None:
    _world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "manager"
    try:
        with Held() as h:
            w = Wire()
            try:
                w.call("hello", {"client": "t", "protocol": 1})
                w.send({"jsonrpc": "2.0", "id": 2, "method": "t.held", "params": {"n": 2}})
                h.entered.acquire(timeout=10)
                w.a.shutdown(socket.SHUT_WR)
                time.sleep(0.2)
                h.release.set()
                got = w.read()
                check("half-close: a slow call's reply still arrives after SHUT_WR", got.get("id") == 2 and got.get("result") == {"done": 2}, str(got))
                w.a.settimeout(5)
                check("half-close: and then the connection ends", w.a.recv(1) == b"")
            finally:
                w.close()
        w = Wire()
        try:
            w.call("hello", {"client": "t", "protocol": 1})
            w.send({"jsonrpc": "2.0", "id": 3, "method": "inbox.list", "params": {"wait_s": 600}})
            time.sleep(0.2)
            t0 = time.time()
            w.a.shutdown(socket.SHUT_WR)
            got = w.read()
            check("half-close: a waiting inbox.list is answered at once", got.get("id") == 3 and "result" in got and time.time() - t0 < 5, str(got))
        finally:
            w.close()
    finally:
        sessiontoken.agent_of = real


def test_a_connection_has_a_bounded_number_of_calls_in_flight(home: Path) -> None:
    _world(home)
    real = rpc.MAX_INFLIGHT
    rpc.MAX_INFLIGHT = 3
    try:
        with Held() as h:
            w = Wire()
            try:
                w.call("hello", _hp())
                for n in range(3):
                    w.send({"jsonrpc": "2.0", "id": n, "method": "t.held", "params": {"n": n}})
                check("in flight: three calls are served at once", all(h.entered.acquire(timeout=10) for _ in range(3)))
                busy = w.call("whoami", id="fourth")
                check("in flight: the fourth is answered daemon_busy, at once, with its id", busy["id"] == "fourth" and busy["error"]["code"] == rpc.BUSY
                      and busy["error"]["data"]["code"] == "daemon_busy", str(busy))
                h.release.set()
                [w.read() for _ in range(3)]
                after = w.call("whoami", id="fifth")
                check("in flight: once they finish, calls are served again", "result" in after, str(after))
            finally:
                w.close()
    finally:
        rpc.MAX_INFLIGHT = real


class _Listener:
    """A listening socket replaced: hands out the connections it was given, then ends."""
    def __init__(self, conns) -> None:
        self.conns, self.gate = list(conns), threading.Event()

    def accept(self):
        if not self.conns:
            raise OSError("closed")
        return self.conns.pop(0), None

    def settimeout(self, t) -> None:
        pass

    def close(self) -> None:
        pass


def test_the_daemon_serves_a_bounded_number_of_connections(home: Path) -> None:
    _world(home)
    real = socket_server.MAX_CONNECTIONS
    socket_server.MAX_CONNECTIONS = 1
    try:
        ends = [socket.socketpair() for _ in range(3)]
        for a, b in ends:
            a.settimeout(10)
        server = socket_server.SocketServer(lambda m: None)
        server._stop = threading.Event()
        server._sock = _Listener([ends[0][1], ends[1][1]])
        server._accept_loop()
        refused = ends[1][0].recv(4096)
        got = json.loads(refused.split(b"\n")[0])
        check("connections: one past the cap is told daemon_busy and closed", got["error"]["code"] == rpc.BUSY and got["error"]["data"]["code"] == "daemon_busy"
              and ends[1][0].recv(1) == b"", str(refused))
        check("connections: the first is still being served", server._open == 1)
        ends[0][0].close()
        check("connections: and when it ends the count falls", _until(lambda: server._open == 0, 10), str(server._open))
        server._sock = _Listener([ends[2][1]])
        server._accept_loop()
        ends[2][0].sendall(b'{"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "t", "protocol": 1}}\n')
        check("connections: and a new one is served", b'"result"' in ends[2][0].recv(65536))
        ends[2][0].close()
    finally:
        socket_server.MAX_CONNECTIONS = real


def test_hello_comes_first_and_alone_and_a_write_with_one_request_id_runs_once(home: Path) -> None:
    _world(home)
    w = Wire()
    try:
        hello = {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": _hp()}
        call = {"jsonrpc": "2.0", "id": 2, "method": "whoami"}
        w.a.sendall(json.dumps(hello).encode() + b"\n" + json.dumps(call).encode() + b"\n")
        first, second = w.read(), w.read()
        check("hello: sent in one write with the next call, it is answered first and the call is served after it", first["id"] == 1 and second["id"] == 2
              and second["result"]["role"] == "founder", str((first, second)))
        for n in (3, 4, 5, 6):
            w.send({"jsonrpc": "2.0", "id": n, "method": "settings.set", "params": {"changes": {"active_cap": 4}, "request_id": "same"}})
        replies = [w.read() for _ in range(4)]
        check("writes: four copies of one write with one request_id, sent without waiting, are one write: the rest replayed",
              all("result" in r for r in replies) and sum(1 for r in replies if r["result"].get("replayed")) == 3, str(replies))
        check("writes: and the value was written", __import__("handline.usage.load", fromlist=["x"]).default_cap() == 4)
    finally:
        w.close()


def test_the_framing_fixtures_pass_against_this_daemon(home: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance
    _world(home)

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: None
    try:
        results = [r for r in conformance.run_all([Path("contract/fixtures/framing.json")], {"founder": connect}, {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n in results if st != "pass"]
    check("fixtures: the framing fixtures pass against this daemon", not bad and len(results) == 3, str(bad or results))


def main() -> int:
    def tests(td):
        run(lambda: test_a_waiting_call_does_not_hold_up_the_calls_behind_it(Path(td) / "n1"))
        run(lambda: test_replies_come_in_the_order_the_calls_finish_matched_by_id(Path(td) / "n2"))
        run(lambda: test_a_client_that_hangs_up_ends_its_waiting_calls(Path(td) / "n3"))
        run(lambda: test_a_client_that_shuts_its_write_side_still_gets_its_replies(Path(td) / "n8"))
        run(lambda: test_a_connection_has_a_bounded_number_of_calls_in_flight(Path(td) / "n4"))
        run(lambda: test_the_daemon_serves_a_bounded_number_of_connections(Path(td) / "n5"))
        run(lambda: test_hello_comes_first_and_alone_and_a_write_with_one_request_id_runs_once(Path(td) / "n6"))
        run(lambda: test_the_framing_fixtures_pass_against_this_daemon(Path(td) / "n7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
