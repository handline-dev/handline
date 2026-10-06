"""Tests: agent.create and operation.get (contract/protocol.md): the operation an agent.create starts, what each step leaves,
a failure half way, a retry after a lost reply, an operation the daemon died in, and who may call them.

The terminal host is replaced by what `spawn` is told to answer: it has its own tests.

Run on its own: python3 tests/daemon/test_rpc_operations.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import db  # noqa: E402
from handline.daemon import rpc_operations, socket_server  # noqa: E402
from handline.infra import paths, sessiontoken  # noqa: E402
from handline.team import agents  # noqa: E402

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


class Spawn:
    """`spawn` replaced: records the calls and answers what it is told."""
    def __enter__(self):
        self.calls, self.answer = [], (True, "worker: worktree /wt")
        self.real = rpc_operations.spawnmod.spawn
        rpc_operations.spawnmod.spawn = lambda agent, repo, base="main", **kw: (self.calls.append((agent, str(repo), base)), self.answer)[1]
        return self

    def __exit__(self, *exc):
        rpc_operations.spawnmod.spawn = self.real


def _create(name: str, home: Path, rid: str, **extra) -> dict:
    return _call("agent.create", {"name": name, "role": "does the checks", "repo": str(home), "request_id": rid, **extra})


def _settled(op: str) -> dict:
    for _ in range(100):
        got = _call("operation.get", {"operation": op})["result"]
        if got["state"] != "running":
            return got
        time.sleep(0.05)
    return got


def _events(op: str) -> list[dict]:
    con = db.connect()
    try:
        return [e["data"] for e in socket_server.stream.since(con, 0, ("operations",)) if e["data"]["id"] == op]
    finally:
        con.close()


def test_agent_create_registers_starts_and_reports_each_step_on_the_operations_topic(home: Path) -> None:
    support._board_world(home)
    with Spawn() as sp:
        r = _create("worker.new", home, "c1", base="dev")
        op = r["result"]["operation"]
        check("agent.create: the reply is accepted and names an operation", r["result"]["accepted"] and isinstance(op, str), str(r))
        got = _settled(op)
        check("agent.create: the operation ends done with the agent as its result", got["state"] == "done" and got["steps_done"] == 2
              and got["result"] == {"agent": "worker.new"} and got["agent"] == "worker.new" and got["kind"] == "agent.create", str(got))
        check("agent.create: the agent is registered and was started on the repo and base asked for",
              "worker.new" in paths.list_agents() and sp.calls == [("worker.new", str(home.resolve()), "dev")], str(sp.calls))
        seen = [(e["state"], e["step"]) for e in _events(op)]
        check("agent.create: the topic carried the start, each step and the end in order",
              seen[0] == ("running", None) and ("running", "register") in seen and ("running", "start") in seen and seen[-1] == ("done", None), str(seen))


def test_a_start_that_fails_leaves_the_agent_registered_and_says_so(home: Path) -> None:
    support._board_world(home)
    with Spawn() as sp:
        sp.answer = (False, "no terminal host is running")
        op = _create("worker.half", home, "h1")["result"]["operation"]
        got = _settled(op)
        check("agent.create: a failed start ends the operation failed at one step of two, naming why",
              got["state"] == "failed" and got["steps_done"] == 1 and "no terminal host" in got["message"], str(got))
        check("agent.create: and the agent stays registered, which the result says", "worker.half" in paths.list_agents()
              and got["result"] == {"agent": "worker.half", "registered": True}, str(got))
        check("agent.create: the topic ended on the failure", _events(op)[-1]["state"] == "failed")
        r = _create("worker.half", home, "h2")
        check("agent.create: asking again for the agent that now exists is refused, gate state", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))


def test_a_registration_that_fails_ends_the_operation_before_any_start(home: Path) -> None:
    support._board_world(home)
    real = agents.register

    def boom(*a, **kw):
        raise ValueError("agent.yaml would not be written")
    agents.register = boom
    try:
        with Spawn() as sp:
            op = _create("worker.reg", home, "g1")["result"]["operation"]
            got = _settled(op)
            check("agent.create: a registration that raises ends the operation failed at register, nothing started",
                  got["state"] == "failed" and "register failed" in got["message"] and got["result"]["registered"] is False and not sp.calls, str(got))
    finally:
        agents.register = real


def test_a_retry_after_a_lost_reply_answers_the_first_operation_and_starts_nothing_again(home: Path) -> None:
    support._board_world(home)
    with Spawn() as sp:
        first = _create("worker.retry", home, "r1")["result"]
        _settled(first["operation"])
        con = db.connect()
        con.execute("DELETE FROM rpc_replies")            # the daemon died after the effect, before the reply was kept
        con.commit()
        con.close()
        again = _create("worker.retry", home, "r1")["result"]
        check("agent.create: the retry answers the same operation, marked replayed", again["operation"] == first["operation"] and again.get("replayed"), str(again))
        check("agent.create: and the agent was started once", len(sp.calls) == 1, str(sp.calls))


def test_an_operation_the_daemon_died_in_is_reported_failed(home: Path) -> None:
    support._board_world(home)
    con = db.connect()
    con.execute("INSERT INTO operations(id, kind, state, steps_done, steps_total, agent, created_at, updated_at) "
                "VALUES('lost', 'agent.create', 'running', 1, 2, 'x.y', 1, 1)")
    con.commit()
    con.close()
    got = _call("operation.get", {"operation": "lost"})["result"]
    check("operation.get: a running operation with no thread is failed, and says the daemon stopped", got["state"] == "failed"
          and "daemon stopped" in got["message"], str(got))


def test_agent_create_refuses_what_it_cannot_do_and_starts_nothing(home: Path) -> None:
    support._board_world(home)
    with Spawn() as sp:
        r = _call("agent.create", {"name": "worker.no", "role": "x", "repo": str(home), "request_id": "n1"}, as_agent="manager")
        check("agent.create: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
        for params, code, field in (({"account": "acct.one"}, "bad_request", "account"), ({"effort": "huge"}, "bad_request", "effort"),
                                    ({"name": 5}, "bad_request", "name"), ({"role": ""}, "bad_request", "role")):
            r = _call("agent.create", {"name": "worker.no", "role": "x", "repo": str(home), "request_id": f"n-{params}", **params})
            check(f"agent.create: {params} is {code}", _code(r) == code and r["error"]["data"].get("field") == field, str(r))
        r = _call("agent.create", {"name": "worker.no", "role": "x", "repo": str(home / "nope"), "request_id": "n2"})
        check("agent.create: a repo that is no directory is not_found", _code(r) == "not_found" and r["error"]["data"]["what"] == "repo", str(r))
        r = _create("worker.no", home, "n3", parent="ghost")
        check("agent.create: a parent that is no agent is not_found", _code(r) == "not_found" and r["error"]["data"]["what"] == "agent", str(r))
        r = _create("worker.no", home, "n4", parent="manager")
        check("agent.create: a name that is not under its parent is bad_request", _code(r) == "bad_request" and r["error"]["data"]["field"] == "name", str(r))
        r = _call("operation.get", {"operation": "nope"})
        check("operation.get: an unknown operation is not_found", _code(r) == "not_found", str(r))
        check("agent.create: none of these registered or started anything", "worker.no" not in paths.list_agents() and not sp.calls, str(sp.calls))


def test_operation_get_is_refused_to_an_agent_session(home: Path) -> None:
    support._board_world(home)
    with Spawn():
        op = _create("worker.get", home, "o1")["result"]["operation"]
        _settled(op)
        r = _call("operation.get", {"operation": op}, as_agent="manager")
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
        with Spawn():
            for role in ("founder", "agent"):
                sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
                results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/operations.json")], {role: connector()},
                                                                     {"founder_key": _hp()["key"], "new_agent": "fixture.created", "repo_dir": str(home)})
                            if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every operations fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_agent_create_registers_starts_and_reports_each_step_on_the_operations_topic(Path(td) / "d0"))
        run(lambda: test_a_start_that_fails_leaves_the_agent_registered_and_says_so(Path(td) / "d1"))
        run(lambda: test_a_registration_that_fails_ends_the_operation_before_any_start(Path(td) / "d2"))
        run(lambda: test_a_retry_after_a_lost_reply_answers_the_first_operation_and_starts_nothing_again(Path(td) / "d3"))
        run(lambda: test_an_operation_the_daemon_died_in_is_reported_failed(Path(td) / "d4"))
        run(lambda: test_agent_create_refuses_what_it_cannot_do_and_starts_nothing(Path(td) / "d5"))
        run(lambda: test_operation_get_is_refused_to_an_agent_session(Path(td) / "d6"))
        run(lambda: test_the_operations_fixtures_pass_against_this_daemon(Path(td) / "d7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
