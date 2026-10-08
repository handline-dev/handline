"""Tests: operation.get (contract/protocol.md): an operation's row, one the daemon died in, and who may call it.

Run on its own: python3 tests/daemon/test_rpc_operations.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import db  # noqa: E402
from handline.daemon import socket_server  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict, as_agent: str | None = None) -> dict:
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params)
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _row(op: str, state: str) -> None:
    con = db.connect()
    con.execute("INSERT INTO operations(id, kind, state, steps_done, steps_total, agent, created_at, updated_at) "
                "VALUES(?, 'agent.create', ?, 1, 2, 'x.y', 1, 1)", (op, state))
    con.commit()
    con.close()


def test_an_operation_the_daemon_died_in_is_reported_failed(home: Path) -> None:
    support._board_world(home)
    _row("lost", "running")
    got = _call("operation.get", {"operation": "lost"})["result"]
    check("operation.get: a running operation with no thread is failed, and says the daemon stopped", got["state"] == "failed"
          and "daemon stopped" in got["message"], str(got))
    _row("ended", "done")
    got = _call("operation.get", {"operation": "ended"})["result"]
    check("operation.get: an ended operation is read as it is", got["state"] == "done" and got["steps_done"] == 1, str(got))
    r = _call("operation.get", {"operation": "nope"})
    check("operation.get: an unknown operation is not_found", _code(r) == "not_found", str(r))


def test_operation_get_is_refused_to_an_agent_session(home: Path) -> None:
    support._board_world(home)
    _row("op", "done")
    r = _call("operation.get", {"operation": "op"}, as_agent="manager")
    check("operation.get: below the method's role (member), an agent session is refused, gate role",
          _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_the_operations_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connector():
        def connect():
            a, b = socket.socketpair()
            threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
            return a
        return connect
    support._board_world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/operations.json")], {role: connector()},
                                                                 {"founder_key": _hp()["key"]})
                        if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every operations fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_an_operation_the_daemon_died_in_is_reported_failed(Path(td) / "d4"))
        run(lambda: test_operation_get_is_refused_to_an_agent_session(Path(td) / "d6"))
        run(lambda: test_the_operations_fixtures_pass_against_this_daemon(Path(td) / "d7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
