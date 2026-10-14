"""Tests: the daemon always answers (a database it cannot use is a reply with the reason, never a hangup the
client reads as "the daemon is not running"), dev mode restarts onto a tree that was still being written when
the process started, and the daemon says which code it runs.

Run on its own: python3 tests/daemon/test_daemon_replies.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.cli import daemon as clidaemon  # noqa: E402
from handline.daemon import rpc, socket_server  # noqa: E402
from handline.infra import daemonclient as client
from handline.infra import gitrun  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402
from test_socket import _serve_one_roundtrip  # noqa: E402


class WrongSchema:
    """db.connect raising what a daemon on old (or new) code gets."""

    def __init__(self, kind="newer", have=2, latest=1) -> None:
        self.err = db.SchemaError("x", kind, have, latest)
        self.real = db.connect
        db.connect = self.boom

    def boom(self, *a, **k):
        raise self.err

    def restore(self) -> None:
        db.connect = self.real


def test_a_legacy_verb_the_database_blocks_is_answered_with_the_reason(home: Path) -> None:
    support.use_home(home)
    for kind, have, latest, said in (("newer", 2, 1, "newer than this daemon (2 vs 1)"), ("older", 1, 2, "older than this daemon needs (1 vs 2)")):
        w = WrongSchema(kind, have, latest)
        try:
            reply = _serve_one_roundtrip({"verb": "say", "requester": "a", "target": "b", "body": "x"})
        finally:
            w.restore()
        check(f"legacy: a database {kind} than the daemon is a reply, not a hangup", reply is not None and reply["ok"] is False, str(reply))
        check(f"legacy: and it says why, and what to run ({kind})", said in reply["said"] and "hl service install" in reply["said"], str(reply))


def test_a_json_rpc_call_the_database_blocks_is_a_typed_refusal_and_the_connection_goes_on(home: Path) -> None:
    support.use_home(home)
    w = Wire()
    hello = w.call("hello", _hp())
    check("rpc: hello needs no database and says which schema this daemon knows", hello["result"]["schema"] == db.LATEST_VERSION)
    blocked = WrongSchema()
    try:
        r = w.call("agents.list", {})
    finally:
        blocked.restore()
    d = r["error"]["data"]
    check("rpc: the call is refused at the schema gate with the reason and the fix",
          _code(r) == "refused" and d["gate"] == "schema" and "newer than this daemon" in d["reason"] and d["hint"] == "hl service install", str(r))
    check("rpc: the connection is still there", "result" in w.call("agents.list", {}))
    w.close()


def test_hl_ps_says_which_code_the_daemon_is_on_and_when_the_checkout_has_moved_past_it(home: Path) -> None:
    support.use_home(home)
    real = (client.connect, client.call, gitrun.run)
    client.connect = lambda timeout=0: object()
    client.call = lambda sock, method, params=None, **k: {"daemon": "aaa1111"}
    try:
        gitrun.run = lambda cwd, *a, **k: (0, "aaa1111\n", "")
        same = clidaemon._code_line()
        gitrun.run = lambda cwd, *a, **k: (0, "bbb2222\n", "")
        behind = clidaemon._code_line()
        client.connect = lambda timeout=0: None
        silent = clidaemon._code_line()
    finally:
        client.connect, client.call, gitrun.run = real
    check("ps: the line names the daemon's code", same == "  code: aaa1111", same)
    check("ps: and says to restart when the checkout is elsewhere", "bbb2222" in behind and "hl service install" in behind, behind)
    check("ps: a daemon that does not answer is not guessed at", "unknown" in silent, silent)


def main() -> int:
    def tests(td):
        run(lambda: test_a_legacy_verb_the_database_blocks_is_answered_with_the_reason(Path(td) / "p1"))
        run(lambda: test_a_json_rpc_call_the_database_blocks_is_a_typed_refusal_and_the_connection_goes_on(Path(td) / "p2"))
        run(lambda: test_hl_ps_says_which_code_the_daemon_is_on_and_when_the_checkout_has_moved_past_it(Path(td) / "p8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
