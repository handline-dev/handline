"""Tests: `hl device pair | list | revoke`, the founder's commands to the gateway's local socket: what they send (the founder key in hello, the
call and its request_id), what they print, and what they say when there is no gateway, no key or a refusal.

The gateway is a script on a socketpair (nothing here binds a socket).

Run on its own: python3 tests/team/test_hl_device.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.cli import device  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402


class FakeGateway:
    """Answers one connection: the hello, then the one call, with what `reply` says (a result dict or an error dict)."""

    def __init__(self, reply: dict) -> None:
        self.client, server = socket.socketpair()
        self.seen: list[dict] = []
        self.reply = reply
        self.thread = threading.Thread(target=self._serve, args=(server,), daemon=True)
        self.thread.start()

    def _serve(self, conn) -> None:
        buf = b""
        with conn:
            while buf.count(b"\n") < 2:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            self.seen = [json.loads(ln) for ln in buf.split(b"\n") if ln.strip()]
            out = [{"jsonrpc": "2.0", "id": 1, "result": {"role": "founder"}},
                   {"jsonrpc": "2.0", "id": 2, **self.reply}]
            conn.sendall(b"".join(json.dumps(m).encode() + b"\n" for m in out))


def _run(argv: list[str], gateway: FakeGateway | None, key: str | None = "founder-key") -> tuple[int, str, str]:
    from handline.cli import parser
    args = parser.build_parser().parse_args(["device", *argv])
    real = sessiontoken.founder_key_held
    sessiontoken.founder_key_held = lambda: key
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = device.cmd_device(args, gateway.client if gateway else None)
    finally:
        sessiontoken.founder_key_held = real
    return rc, out.getvalue(), err.getvalue()


PAIRED = {"result": {"accepted": True, "code": "ABCD-1234", "expires_at": "2026-10-07T09:05:00Z", "fingerprint": "AA:BB",
                     "uri": "handline://pair?host=192.168.1.5:7443&fp=AA:BB&code=ABCD-1234"}}


def test_pair_prints_the_code_the_uri_the_address_and_the_fingerprint(home: Path) -> None:
    support.use_home(home)
    gw = FakeGateway(PAIRED)
    rc, out, err = _run(["pair", "--name", "phone"], gw)
    hello, call = gw.seen
    check("pair: hello carries the founder key and the call is device.pair with the name and a request_id",
          hello["method"] == "hello" and hello["params"]["key"] == "founder-key" and call["method"] == "device.pair"
          and call["params"]["name"] == "phone" and len(call["params"]["request_id"]) >= 8, str(gw.seen))
    check("pair: it prints the code, when it expires, the uri, the address and the fingerprint", rc == 0 and "ABCD-1234" in out and "2026-10-07T09:05:00Z" in out
          and "uri: handline://pair?host=192.168.1.5:7443" in out and "address: 192.168.1.5:7443" in out and "fingerprint: AA:BB" in out, out + err)
    gw = FakeGateway({"result": {**PAIRED["result"], "fingerprint": "", "uri": "handline://pair?host=mac.tail.ts.net:443&code=C1"}})
    rc, out, _ = _run(["pair"], gw)
    check("pair: with no fingerprint (Tailscale mode) it says there is none and why, and sends no name",
          rc == 0 and "fingerprint: none" in out and "name" not in gw.seen[1]["params"], out)


def test_list_and_revoke(home: Path) -> None:
    support.use_home(home)
    gw = FakeGateway({"result": {"devices": [{"id": "dev_a", "name": "phone", "role": "member", "created": "2026-10-01T00:00:00Z", "last_seen": "2026-10-07T08:00:00Z", "connected": True},
                                              {"id": "dev_bb", "name": "tablet", "role": "member", "created": "2026-10-02T00:00:00Z", "last_seen": None, "connected": False}]}})
    rc, out, _ = _run(["list"], gw)
    check("list: one line per device with its id, name, whether it is connected and when last seen",
          rc == 0 and "dev_a" in out and "phone" in out and "connected" in out and "dev_bb" in out and "offline" in out and "last seen never" in out and gw.seen[1]["method"] == "devices.list", out)
    rc, out, _ = _run(["list"], FakeGateway({"result": {"devices": []}}))
    check("list: none paired is said", rc == 0 and "no device is paired" in out, out)
    gw = FakeGateway({"result": {"accepted": True}})
    rc, out, _ = _run(["revoke", "dev_a"], gw)
    check("revoke: device.revoke with the id and a request_id, and it says so", rc == 0 and gw.seen[1]["method"] == "device.revoke" and gw.seen[1]["params"]["device"] == "dev_a"
          and gw.seen[1]["params"]["request_id"] and "dev_a revoked" in out, out)


def test_it_fails_closed_and_says_plainly_why(home: Path) -> None:
    support.use_home(home)
    rc, out, err = _run(["list"], None)
    check("no gateway: nothing listens on the socket, and it says where and that none is running", rc == 1 and "no gateway is running" in err and "gateway.sock" in err and not out, err)
    gw = FakeGateway({"result": {"devices": []}})
    rc, out, err = _run(["list"], gw, key=None)
    check("no founder key (an agent's session): refused before anything is sent", rc == 1 and "founder's" in err and gw.seen == [] and not out, err)
    gw = FakeGateway({"error": {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "role"}}})
    rc, out, err = _run(["revoke", "dev_a"], gw)
    check("refusal: the gateway's refusal is printed with its gate and the exit is 1", rc == 1 and "gate role" in err and not out, err)
    rc, out, err = _run(["revoke", "dev_a"], FakeGateway({"error": {"code": -32001, "message": "no such device", "data": {"code": "not_found"}}}))
    check("not_found: said, exit 1", rc == 1 and "no such device" in err, err)


def test_the_parser_has_the_command(home: Path) -> None:
    support.use_home(home)
    sessiontoken.ensure_founder_key()
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = climod.main(["device", "list"])
    check("parser: `hl device list` runs and, with no gateway in this home, says so and exits 1", rc == 1 and "no gateway is running" in err.getvalue(), err.getvalue())
    check("parser: revoke needs an id", _raises_exit(lambda: climod.main(["device", "revoke"])))


def _raises_exit(fn) -> bool:
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            fn()
        except SystemExit:
            return True
    return False


def main() -> int:
    def tests(td):
        run(lambda: test_pair_prints_the_code_the_uri_the_address_and_the_fingerprint(Path(td) / "d1"))
        run(lambda: test_list_and_revoke(Path(td) / "d2"))
        run(lambda: test_it_fails_closed_and_says_plainly_why(Path(td) / "d3"))
        run(lambda: test_the_parser_has_the_command(Path(td) / "d4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
