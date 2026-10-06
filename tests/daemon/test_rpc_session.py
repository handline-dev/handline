"""Tests: session.start, wake, stop, restart, park and terminal.close (contract/protocol.md): what each does, who may
call it, the gate each refusal names (the terminal lock, the state), a retry, and the legacy verbs and commands they
re-expose still working from an agent session.

The session actions themselves (lifecycle, spawn, the terminal host) are replaced by recorders: they have their own
tests, and this file asks what the methods do with their answers.

Run on its own: python3 tests/daemon/test_rpc_session.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.actions import control  # noqa: E402
from handline.daemon import delivery, lifecycle, socket_server  # noqa: E402
from handline.infra import lock, sessiontoken  # noqa: E402
from handline.session import observe as obs  # noqa: E402
from handline.session import term as termmod  # noqa: E402
from handline.daemon import client as dmn_client  # noqa: E402
from handline.cli import task as cli_task  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict, as_agent: str | None = "manager") -> dict:
    """One call on a fresh connection: from an agent session, or from the founder when as_agent is None."""
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


class Actions:
    """The session actions replaced for a test, each recording its calls and answering what it is told."""
    def __enter__(self):
        import os
        self.calls: list[tuple] = []
        self.answer: tuple[bool, str] = (True, "done")
        self.sessions: dict | None = {}
        self.present = True
        self.saved = [(m, n, getattr(m, n)) for m, n in ((lifecycle, "wake_agent"), (control, "close_result"),
                      (control, "restart_result"), (control, "park_result"), (control, "start_result"),
                      (control, "unstall"), (termmod, "close_session"), (termmod, "close_terminal"), (termmod, "available"),
                      (obs, "live_sessions"), (delivery, "session_present"), (delivery, "_wait_session_gone"), (os, "kill"))]
        rec = lambda name: (lambda *a, **k: (self.calls.append((name, tuple(x for x in a[1:] if not callable(x)), k)), self.answer)[1])  # noqa: E731
        lifecycle.wake_agent = rec("wake")
        control.close_result = rec("close")
        control.restart_result = rec("restart")
        control.park_result = rec("park")
        control.start_result = rec("start")
        control.unstall = lambda con, agent: (self.calls.append(("unstall", (agent,), {})), f"{agent}: STALLED -> QUEUED")[1]
        termmod.close_session = lambda handle, **k: (self.calls.append(("exit", (handle,), {})), self.answer)[1]
        termmod.close_terminal = lambda handle, tab=True: (self.calls.append(("close_terminal", (handle,), {})), self.answer)[1]
        termmod.available = lambda: True
        obs.live_sessions = lambda: self.sessions
        delivery.session_present = lambda agent: self.present
        delivery._wait_session_gone = lambda agent: True
        os.kill = lambda pid, sig: self.calls.append(("kill", (pid, sig), {}))
        return self

    def __exit__(self, *exc):
        for m, n, v in self.saved:
            setattr(m, n, v)

    def named(self, name: str) -> list:
        return [c for c in self.calls if c[0] == name]


def _state(agent: str, state: str, closed: bool = False, terminal: str | None = "t1") -> None:
    con = db.connect()
    db.set_agent_state(con, agent, state)
    db.set_terminal(con, agent, terminal, None)
    if closed:
        con.execute("UPDATE agent_state SET closed_at = ? WHERE agent = ?", (1, agent))
    con.close()


def _events(kind: str = "rpc_write") -> list:
    con = db.connect()
    try:
        return con.execute("SELECT agent, payload FROM events WHERE kind = ? ORDER BY id", (kind,)).fetchall()
    finally:
        con.close()


def test_each_method_does_what_its_verb_does_and_is_an_event_under_its_caller(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        r = _call("session.wake", {"agent": "worker", "request_id": "w1"})["result"]
        check("session.wake: accepted, with the action's words", r["accepted"] is True and r["said"] == "worker: done", str(r))
        check("session.wake: and the wake ran once", len(a.named("wake")) == 1 and a.named("wake")[0][1] == ("worker",), str(a.calls))
        r = _call("session.wake", {"agent": "worker", "request_id": "w1"})["result"]
        check("session.wake: the same request_id is replayed and wakes nothing again", r["replayed"] is True and len(a.named("wake")) == 1)
        r = _call("session.stop", {"agent": "worker", "mode": "close", "fresh": True, "request_id": "s1"})["result"]
        check("session.stop close: closes, fresh as asked", r["accepted"] and a.named("close") == [("close", ("worker", True), {})], str(a.calls))
        _call("session.restart", {"agent": "worker", "fresh": True, "force": True, "request_id": "r1"}, as_agent=None)
        check("session.restart: fresh and force as asked, by the caller (the founder is human)",
              a.named("restart") == [("restart", ("worker", True, True, db.HUMAN), {})], str(a.named("restart")))
        _call("session.restart", {"agent": "worker", "request_id": "r2"})
        check("session.restart: from an agent it is that agent that restarts", a.named("restart")[-1][1][3] == "manager")
        _call("session.park", {"agent": "worker", "request_id": "p1"})
        check("session.park: parks", len(a.named("park")) == 1)
        _call("session.start", {"agent": "worker", "repo": "/r", "base": "dev", "request_id": "st1"}, as_agent=None)
        check("session.start: the repo and base go through", a.named("start") == [("start", ("worker", "/r", "dev"), {})], str(a.named("start")))
        _call("session.start", {"agent": "worker", "request_id": "st2"})
        check("session.start: base defaults to main", a.named("start")[-1][1] == ("worker", None, "main"))
    who = {json.loads(e["payload"])["method"]: json.loads(e["payload"])["actor"]["name"] for e in _events()}
    check("every call is an event naming its actor", who.get("session.park") == "manager" and who.get("session.restart") in ("manager", getpass_user()), str(who))


def getpass_user() -> str:
    import getpass
    return getpass.getuser()


def test_a_stalled_agent_is_unstalled_by_wake_as_the_verb_does(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        _state("worker", "STALLED")
        r = _call("session.wake", {"agent": "worker", "request_id": "u1"})["result"]
        check("session.wake: a stalled agent is cleared, not launched", r["said"] == "worker: STALLED -> QUEUED"
              and len(a.named("unstall")) == 1 and not a.named("wake"), str(a.calls))


def test_a_refusal_names_the_gate_that_said_no(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        a.answer = (False, "worker is RUNNING; not closing a session mid-work")
        for method, params in (("session.wake", {}), ("session.stop", {"mode": "close"}), ("session.restart", {}),
                               ("session.park", {}), ("session.start", {})):
            r = _call(method, {"agent": "worker", "request_id": f"g-{method}", **params})
            check(f"{method}: the state's refusal is refused with gate state", _code(r) == "refused"
                  and r["error"]["data"]["gate"] == "state" and "RUNNING" in r["error"]["message"], str(r))
        a.answer = (False, f"{lifecycle.TYPING_ELSEWHERE}; not waking twice")
        r = _call("session.wake", {"agent": "worker", "request_id": "g-race"})
        check("session.wake: a lock lost to a race is the terminal_lock gate", r["error"]["data"]["gate"] == "terminal_lock", str(r))
        a.calls.clear()
        with lock.terminal("worker") as mine:
            check("(the test holds the terminal lock)", mine)
            for method, params in (("session.wake", {}), ("session.stop", {"mode": "close"}), ("session.stop", {"mode": "exit"}),
                                   ("session.stop", {"mode": "kill"}), ("session.restart", {}), ("session.park", {}),
                                   ("session.start", {})):
                r = _call(method, {"agent": "worker", "request_id": f"l-{method}-{params}", **params}, as_agent=None)
                check(f"{method} {params}: refused at once while the terminal lock is held", _code(r) == "refused"
                      and r["error"]["data"]["gate"] == "terminal_lock", str(r))
            r = _call("terminal.close", {"agent": "worker", "request_id": "l-tc"}, as_agent=None)
            check("terminal.close: and so is it", r["error"]["data"]["gate"] == "terminal_lock", str(r))
        check("nothing ran behind a held lock", not a.calls, str(a.calls))
        a.answer = (True, "done")
        r = _call("session.wake", {"agent": "worker", "request_id": "l-after"})
        check("the lock let go, the same agent is woken", "result" in r, str(r))


def test_a_call_that_is_malformed_or_names_nobody_is_refused_before_anything_runs(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        r = _call("session.wake", {"agent": "nobody", "request_id": "n1"})
        check("session.wake: an unknown agent is not_found", _code(r) == "not_found", str(r))
        r = _call("session.wake", {"request_id": "n2"})
        check("session.wake: no agent is bad_request", _code(r) == "bad_request" and r["error"]["data"]["field"] == "agent", str(r))
        r = _call("session.wake", {"agent": "worker"})
        check("session.wake: no request_id is bad_request", _code(r) == "bad_request" and r["error"]["data"]["field"] == "request_id", str(r))
        for params, field in (({"mode": "halt"}, "mode"), ({}, "mode"), ({"mode": "exit", "fresh": True}, "fresh"),
                              ({"mode": "close", "fresh": "yes"}, "fresh")):
            r = _call("session.stop", {"agent": "worker", "request_id": "m", **params})
            check(f"session.stop {params}: bad_request naming {field}", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))
        r = _call("session.restart", {"agent": "worker", "force": 1, "request_id": "m2"})
        check("session.restart: force is true or false", _code(r) == "bad_request", str(r))
        r = _call("session.start", {"agent": "worker", "account": "a", "request_id": "m3"})
        check("session.start: a named account is not served yet, and says so", _code(r) == "bad_request"
              and r["error"]["data"]["field"] == "account", str(r))
        r = _call("session.start", {"agent": "worker", "repo": 3, "request_id": "m4"})
        check("session.start: repo is a string", _code(r) == "bad_request", str(r))
        check("none of it reached an action", not a.calls, str(a.calls))
        w = Wire()
        r = w.call("session.wake", {"agent": "worker", "request_id": "m5"})
        w.close()
        check("session.wake: before hello is refused like any call", _code(r) == "bad_request", str(r))


def test_stop_exit_and_kill_act_on_the_session_and_say_when_there_is_none(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        _state("worker", "IDLE")
        r = _call("session.stop", {"agent": "worker", "mode": "exit", "request_id": "e1"})["result"]
        check("session.stop exit: asks the session to quit without recording a close",
              r["accepted"] and a.named("exit") == [("exit", ("t1",), {})] and not a.named("close"), str(a.calls))
        a.present = False
        r = _call("session.stop", {"agent": "worker", "mode": "exit", "request_id": "e2"})
        check("session.stop exit: no session running is refused, state", r["error"]["data"]["gate"] == "state", str(r))
        a.answer = (False, "agent is mid-turn; not closing")
        a.present = True
        r = _call("session.stop", {"agent": "worker", "mode": "exit", "request_id": "e3"})
        check("session.stop exit: a turn in progress is refused with the terminal's words", "mid-turn" in r["error"]["message"], str(r))
        a.sessions = {"worker": 4242}
        r = _call("session.stop", {"agent": "worker", "mode": "kill", "request_id": "k1"}, as_agent=None)["result"]
        check("session.stop kill: ends the process the table lists for that agent", a.named("kill") == [("kill", (4242, signal.SIGTERM), {})]
              and r["accepted"], str(a.calls))
        a.sessions = {"manager": 1}
        r = _call("session.stop", {"agent": "worker", "mode": "kill", "request_id": "k2"}, as_agent=None)
        check("session.stop kill: no process for it is refused and nothing is signalled", r["error"]["data"]["gate"] == "state"
              and len(a.named("kill")) == 1, str(r))
        a.sessions = None
        r = _call("session.stop", {"agent": "worker", "mode": "kill", "request_id": "k3"}, as_agent=None)
        check("session.stop kill: an unreadable process table is refused, never guessed at", _code(r) == "refused"
              and "process table" in r["error"]["message"] and len(a.named("kill")) == 1, str(r))


def test_a_retry_after_a_lost_reply_replays_the_effect_and_repeats_none(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        _state("worker", "IDLE")
        a.sessions = {"worker": 4242}
        for name, params in (("restart", {"fresh": True}), ("exit", {}), ("kill", {}), ("wake", {}), ("close", {}), ("park", {}), ("start", {})):
            method = "session.stop" if name in ("exit", "kill", "close") else f"session.{name}"
            call = {"agent": "worker", "request_id": f"lost-{name}", **params, **({"mode": name} if method == "session.stop" else {})}
            first = _call(method, call, as_agent=None)
            n = len(a.calls)
            a.sessions = {"worker": 5151}       # what the tick woke in between
            con = db.connect()
            con.execute("DELETE FROM rpc_replies WHERE request_id = ?", (f"lost-{name}",))     # the crash: the effect kept, the reply not
            con.close()
            retry = _call(method, call, as_agent=None)
            check(f"{method} {name}: a retry after a lost reply answers the first run's result, marked replayed",
                  retry.get("result", {}).get("replayed") is True and retry["result"]["said"] == first["result"]["said"], str((first, retry)))
            check(f"{method} {name}: and does nothing a second time", len(a.calls) == n, str(a.calls[n:]))
            a.sessions = {"worker": 4242}
    check("kill: the session asked about is the only one signalled", [c[1][0] for c in a.named("kill")] == [4242], str(a.named("kill")))


def test_exit_kill_and_terminal_close_act_with_the_terminal_lock_held(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        _state("worker", "CLOSED", closed=True)
        a.sessions = {"worker": 4242}
        held_during: dict = {}

        def probe(name):
            def rec(*args, **kw):
                with lock.terminal("worker") as mine:
                    held_during[name] = not mine        # the lock is ours, so a second taker is refused
                return a.answer if name != "kill" else None
            return rec
        termmod.close_session = probe("exit")
        termmod.close_terminal = lambda handle, tab=True: probe("close_terminal")()
        import os
        os.kill = lambda pid, sig: probe("kill")()
        a.present = False
        _call("session.stop", {"agent": "worker", "mode": "kill", "request_id": "h1"}, as_agent=None)
        a.present = True
        _call("session.stop", {"agent": "worker", "mode": "exit", "request_id": "h2"}, as_agent=None)
        a.present = False
        _call("terminal.close", {"agent": "worker", "request_id": "h3"}, as_agent=None)
        check("the lock is held while the action runs, so a tick cannot start between the check and it",
              held_during == {"kill": True, "exit": True, "close_terminal": True}, str(held_during))


def test_kill_is_not_an_agents_to_send(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        a.sessions = {"worker": 4242}
        r = _call("session.stop", {"agent": "worker", "mode": "kill", "request_id": "ak1"})
        check("session.stop kill: refused to an agent session, gate role, nothing signalled", r["error"]["data"]["gate"] == "role"
              and not a.named("kill"), str(r))
        for mode in ("exit", "close"):
            r = _call("session.stop", {"agent": "worker", "mode": mode, "request_id": f"ak-{mode}"})
            check(f"session.stop {mode}: an agent may, as the legacy verbs allow", "result" in r or r["error"]["data"]["gate"] != "role", str(r))


def test_terminal_close_frees_the_terminal_of_a_closed_session_only(home: Path) -> None:
    support._board_world(home)
    with Actions() as a:
        r = _call("terminal.close", {"agent": "worker", "request_id": "c1"})
        check("terminal.close: an agent session is refused by role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
        r = _call("terminal.close", {"agent": "worker", "request_id": "c2"}, as_agent=None)
        check("terminal.close: an agent with no terminal is refused, state", r["error"]["data"]["gate"] == "state", str(r))
        _state("worker", "IDLE")
        r = _call("terminal.close", {"agent": "worker", "request_id": "c3"}, as_agent=None)
        check("terminal.close: a running session is refused, state, and the terminal stays", r["error"]["data"]["gate"] == "state"
              and not a.named("close_terminal"), str(r))
        _state("worker", "CLOSED", closed=True)
        a.present = True
        r = _call("terminal.close", {"agent": "worker", "request_id": "c4"}, as_agent=None)
        check("terminal.close: a session the process table still lists is refused too", r["error"]["data"]["gate"] == "state", str(r))
        a.present = False
        a.answer = (False, "the host said no")
        r = _call("terminal.close", {"agent": "worker", "request_id": "c5"}, as_agent=None)
        check("terminal.close: a host that will not close it is refused with why", "the host said no" in r["error"]["message"], str(r))
        a.answer = (True, "")
        r = _call("terminal.close", {"agent": "worker", "request_id": "c6"}, as_agent=None)
        con = db.connect()
        handle = db.get_terminal(con, "worker")
        con.close()
        check("terminal.close: a closed session's terminal is closed, the handle dropped, an event kept",
              r["result"]["accepted"] and handle is None and a.named("close_terminal")[-1] == ("close_terminal", ("t1",), {})
              and len(_events("terminal_closed")) == 1, str(r))


def test_the_legacy_verbs_and_commands_these_methods_re_expose_still_work_from_an_agent_session(home: Path) -> None:
    support._board_world(home)
    real = dmn_client.send, cli_task.dmn_client.send
    with Actions() as a:
        try:
            dmn_client.send = cli_task.dmn_client.send = lambda payload, timeout=0: socket_server._handle_request(payload, lambda m: None)
            rc, out = support._cli(["close", "worker"])
            check("hl close: works at role agent", rc == 0 and a.named("close") == [("close", ("worker", False), {})], out)
            rc, out = support._cli(["restart", "worker", "--fresh"])
            check("hl restart: works at role agent", rc == 0 and a.named("restart")[-1][1][:2] == ("worker", True), out)
            rc, out = support._cli(["resume", "worker"])
            check("hl resume (session.wake's command): works at role agent", rc == 0 and bool(out.strip()), out)
            for verb in ("wake", "park", "start"):
                reply = socket_server._handle_request({"verb": verb, "agent": "worker"}, lambda m: None)
                check(f"the {verb} verb: still answers {{ok, said}} to a legacy caller", reply["ok"] is True and a.named(verb), str(reply))
        finally:
            dmn_client.send, cli_task.dmn_client.send = real


def test_the_session_fixtures_pass_against_this_daemon(home: Path) -> None:
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
    _state("worker", "RUNNING")
    real = sessiontoken.agent_of
    results = []
    try:
        with lock.terminal("manager") as mine:        # the harness holds this one's terminal lock for the run
            check("(the test holds the terminal lock)", mine)
            for role in ("founder", "agent"):
                sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
                results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/session.json"), Path("contract/fixtures/session_stop.json")], {role: connector()},
                                                                     {"founder_key": _hp()["key"], "running_agent": "worker",
                                                                      "locked_agent": "manager"}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every session fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_each_method_does_what_its_verb_does_and_is_an_event_under_its_caller(Path(td) / "s0"))
        run(lambda: test_a_stalled_agent_is_unstalled_by_wake_as_the_verb_does(Path(td) / "s1"))
        run(lambda: test_a_refusal_names_the_gate_that_said_no(Path(td) / "s2"))
        run(lambda: test_a_call_that_is_malformed_or_names_nobody_is_refused_before_anything_runs(Path(td) / "s3"))
        run(lambda: test_stop_exit_and_kill_act_on_the_session_and_say_when_there_is_none(Path(td) / "s4"))
        run(lambda: test_terminal_close_frees_the_terminal_of_a_closed_session_only(Path(td) / "s5"))
        run(lambda: test_a_retry_after_a_lost_reply_replays_the_effect_and_repeats_none(Path(td) / "s8"))
        run(lambda: test_exit_kill_and_terminal_close_act_with_the_terminal_lock_held(Path(td) / "s9"))
        run(lambda: test_kill_is_not_an_agents_to_send(Path(td) / "s10"))
        run(lambda: test_the_legacy_verbs_and_commands_these_methods_re_expose_still_work_from_an_agent_session(Path(td) / "s6"))
        run(lambda: test_the_session_fixtures_pass_against_this_daemon(Path(td) / "s7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
