"""Tests: session.input, session.keys, session.type and session.interrupt (contract/protocol.md): what each sends, the gates
each refusal names (role, terminal lock, input holder, wall, screen blocker) and their order, a retry after a lost reply,
the lock held while they type so the tick types nothing beside them, and what counts as typing for the idle hold.

The terminal host is replaced by a recorder: it has its own tests; this file asks what the methods send and refuse.

Run on its own: python3 tests/daemon/test_rpc_typing.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.daemon import delivery, inputhold, socket_server  # noqa: E402
from handline.infra import lock, sessiontoken  # noqa: E402
from handline.session import stopgate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402
import test_taskholds  # noqa: E402

METHODS = {"session.input": {"text": "hello"}, "session.type": {"text": "hello"}, "session.keys": {"keys": ["esc"]},
           "session.interrupt": {}}


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


def _code(r: dict):
    return r.get("error", {}).get("data", {}).get("code")


def _gate(r: dict):
    return r.get("error", {}).get("data", {}).get("gate")


class Host:
    """The terminal host replaced: every send recorded, the screen clear unless a test says otherwise."""

    def __enter__(self):
        self.sent: list = []
        self.during: list = []              # what each send saw of the terminal lock
        self.blocker = None
        self.saved = [(termmod, n, getattr(termmod, n)) for n in ("send", "send_to_session", "screen_blocker", "available")]
        termmod.available = lambda: True

        def probe():
            with lock.terminal("worker") as mine:
                self.during.append(not mine)       # not ours to take: the call holds it

        def send(handle, text, enter=True):
            probe()
            self.sent.append((handle, text, enter))
            return True, ""

        def to_session(handle, text, settle_s=0, enter=True):
            probe()
            self.sent.append((handle, text, enter, "session"))
            return True, ""
        termmod.send, termmod.send_to_session = send, to_session
        termmod.screen_blocker = lambda handle: self.blocker
        db_con = db.connect()
        db.set_terminal(db_con, "worker", "t1", None)
        db.set_agent_state(db_con, "worker", "IDLE")
        db_con.close()
        return self

    def __exit__(self, *exc):
        for m, n, v in self.saved:
            setattr(m, n, v)


def _hold(agent: str, name: str, ago: int = 0) -> None:
    con = db.connect()
    con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
    inputhold.take(con, agent, "person", name, int(time.time()) - ago)
    con.close()


def test_each_method_sends_what_it_says_and_records_that_the_agent_was_typed_at(home: Path) -> None:
    support._board_world(home)
    with Host() as h:
        r = _call("session.input", {"agent": "worker", "text": "do the thing", "request_id": "i1"})["result"]
        check("session.input: a line goes to the session with Enter", r["accepted"] and h.sent[-1] == ("t1", "do the thing", True, "session"), str(h.sent))
        _call("session.input", {"agent": "worker", "text": "half a line", "enter": False, "request_id": "i2"})
        check("session.input: enter false leaves it unsent", h.sent[-1] == ("t1", "half a line", False, "session"), str(h.sent))
        _call("session.input", {"agent": "worker", "text": "line one\nline two", "request_id": "i3"})
        check("session.input: a newline is part of the text", h.sent[-1][1] == "line one\nline two")
        _call("session.type", {"agent": "worker", "text": "ab\tc", "request_id": "t1"})
        check("session.type: characters as typed, no Enter, no prompt check", h.sent[-1] == ("t1", "ab\tc", False), str(h.sent))
        _call("session.keys", {"agent": "worker", "keys": ["down", "down", "enter", "shift-tab", "ctrl-c", "esc", "7", "tab", "up"], "request_id": "k1"})
        check("session.keys: each named key in order, Enter as an Enter", h.sent[-9:] == [
            ("t1", "\x1b[B", False), ("t1", "\x1b[B", False), ("t1", "", True), ("t1", "\x1b[Z", False), ("t1", "\x03", False),
            ("t1", "\x1b", False), ("t1", "7", False), ("t1", "\t", False), ("t1", "\x1b[A", False)], str(h.sent[-9:]))
        _call("session.interrupt", {"agent": "worker", "request_id": "n1"})
        check("session.interrupt: Esc, once", h.sent[-1] == ("t1", "\x1b", False))
        check("typed at: the daemon's settle window sees the person's typing", db.get_cursor(db.connect(), "typed_at:worker") >= int(time.time()) - 5)
        r = _call("session.interrupt", {"agent": "worker", "request_id": "n1"})["result"]
        n = len(h.sent)
        check("a retry of the same request_id is replayed and sends nothing again", r["replayed"] is True and len(h.sent) == n)
        con = db.connect()
        con.execute("DELETE FROM rpc_replies WHERE request_id = 'k1'")
        con.close()
        r = _call("session.keys", {"agent": "worker", "keys": ["down", "down", "enter", "shift-tab", "ctrl-c", "esc", "7", "tab", "up"], "request_id": "k1"})["result"]
        check("and one after a lost reply replays the recorded effect and sends nothing again", r["replayed"] is True and len(h.sent) == n, str(r))
        check("every send held the terminal lock while it typed", h.during and all(h.during), str(h.during))
        who = [json.loads(e["payload"])["actor"]["name"] for e in db.connect().execute("SELECT payload FROM events WHERE kind = 'rpc_write'").fetchall()]
        check("every call is an event under its actor", len(who) >= 6 and set(who) == {getpass_user()}, str(who))


def getpass_user() -> str:
    import getpass
    return getpass.getuser()


def test_the_gates_refuse_in_the_plans_order_and_name_themselves(home: Path) -> None:
    support._board_world(home)
    with Host() as h:
        for method, extra in METHODS.items():
            ask = {"agent": "worker", "request_id": f"g-{method}", **extra}
            r = _call(method, ask, as_agent="manager")
            check(f"{method}: an agent session is refused by role", _gate(r) == "role", str(r))
        with lock.terminal("worker") as mine:
            check("(the test holds the terminal lock)", mine)
            _hold("worker", "someone.else")
            for method, extra in METHODS.items():
                r = _call(method, {"agent": "worker", "request_id": f"l-{method}", **extra})
                check(f"{method}: refused at once while the terminal lock is held, before the holder is looked at", _gate(r) == "terminal_lock", str(r))
        for method, extra in METHODS.items():
            r = _call(method, {"agent": "worker", "request_id": f"h-{method}", **extra})
            check(f"{method}: refused while another holds the input", _gate(r) == "input_holder" and "someone.else" in r["error"]["message"], str(r))
        check("nothing was sent past a gate", not h.sent, str(h.sent))
        con = db.connect()
        inputhold.release(con, "worker")
        con.close()
        orig = stopgate.wall_reason
        stopgate.wall_reason = lambda con, agent, row=None: f"{agent} is at its usage limit"
        try:
            r = _call("session.input", {"agent": "worker", "text": "hi", "request_id": "w1"})
            check("session.input: at the usage limit is refused, gate wall", _gate(r) == "wall" and not h.sent, str(r))
            for method in ("session.type", "session.keys", "session.interrupt"):
                r = _call(method, {"agent": "worker", "request_id": f"w-{method}", **METHODS[method]})
                check(f"{method}: passes the wall, as a key does", "result" in r, str(r))
        finally:
            stopgate.wall_reason = orig
        h.sent.clear()
        h.blocker = termmod.Blocker(termmod.MODAL, "a dialog")
        r = _call("session.input", {"agent": "worker", "text": "hi", "request_id": "b1"})
        check("session.input: a dialog open is refused, gate screen_blocker, saying what to use instead",
              _gate(r) == "screen_blocker" and "prompt.answer" in r["error"]["message"] and not h.sent, str(r))
        for method in ("session.type", "session.keys", "session.interrupt"):
            r = _call(method, {"agent": "worker", "request_id": f"b-{method}", **METHODS[method]})
            check(f"{method}: passes an open dialog, as the keys would", "result" in r, str(r))
        h.blocker = None
        _hold("worker", getpass_user())
        r = _call("session.type", {"agent": "worker", "text": "mine", "request_id": "own"})
        check("the one who holds the input may type", "result" in r, str(r))
        con = db.connect()
        inputhold.release(con, "worker")
        con.close()
        r = _call("session.type", {"agent": "worker", "text": "anyone", "request_id": "free"})
        check("with the daemon holding, anyone may type", "result" in r, str(r))


def test_a_malformed_call_is_refused_before_anything_is_sent(home: Path) -> None:
    support._board_world(home)
    with Host() as h:
        for method, extra in METHODS.items():
            r = _call(method, {"agent": "nobody", "request_id": "x", **extra})
            check(f"{method}: an unknown agent is not_found", _code(r) == "not_found", str(r))
            r = _call(method, {"request_id": "x", **extra})
            check(f"{method}: no agent is bad_request naming it", _code(r) == "bad_request" and r["error"]["data"]["field"] == "agent", str(r))
            r = _call(method, {"agent": "worker", **extra})
            check(f"{method}: no request_id is bad_request naming it", _code(r) == "bad_request" and r["error"]["data"]["field"] == "request_id", str(r))
        for method in ("session.input", "session.type"):
            for bad in ("", "a\x1bb", "a\x03", "a\x7fb", 5, None):
                r = _call(method, {"agent": "worker", "text": bad, "request_id": "t"})
                check(f"{method}: text {bad!r} is bad_request naming text", _code(r) == "bad_request" and r["error"]["data"]["field"] == "text", str(r))
        r = _call("session.type", {"agent": "worker", "text": "a\nb", "request_id": "t"})
        check("session.type: a newline is an Enter, so it is not typing", _code(r) == "bad_request", str(r))
        r = _call("session.input", {"agent": "worker", "text": "hi", "enter": "yes", "request_id": "t"})
        check("session.input: enter is true or false", _code(r) == "bad_request" and r["error"]["data"]["field"] == "enter", str(r))
        for bad in ([], ["f13"], ["esc", 3], "esc", None, ["esc"] * 65, [["esc"]]):
            r = _call("session.keys", {"agent": "worker", "keys": bad, "request_id": "t"})
            check(f"session.keys: {str(bad)[:20]} is bad_request naming keys", _code(r) == "bad_request" and r["error"]["data"]["field"] == "keys", str(r))
        check("none of it reached the terminal", not h.sent, str(h.sent))
        con = db.connect()
        db.set_terminal(con, "worker", None, None)
        con.close()
        r = _call("session.keys", {"agent": "worker", "keys": ["esc"], "request_id": "nt"})
        check("an agent with no terminal is refused, state", _gate(r) == "state", str(r))


def test_typing_counts_for_the_idle_hold_and_a_failed_send_is_refused(home: Path) -> None:
    support._board_world(home)
    with Host() as h:
        _hold("worker", getpass_user(), ago=inputhold.idle_s() - 20)
        _call("session.keys", {"agent": "worker", "keys": ["up"], "request_id": "tp"})
        con = db.connect()
        row = con.execute("SELECT last_typed FROM input_holders WHERE agent = 'worker'").fetchone()
        con.close()
        check("the holder's typing counts the idle hold from now", row["last_typed"] > int(time.time()) - 5, str(dict(row)))
        termmod.send = lambda handle, text, enter=True: (False, "the host said no")
        r = _call("session.interrupt", {"agent": "worker", "request_id": "fail"})
        check("a send the host refused is refused with its words and is not recorded as sent", _code(r) == "refused" and "the host said no" in r["error"]["message"], str(r))
        con = db.connect()
        check("(and leaves no effect to replay)", con.execute("SELECT COUNT(*) FROM write_effects WHERE request_id = 'fail'").fetchone()[0] == 0)
        con.close()


def test_the_tick_types_nothing_beside_a_control_call(home: Path) -> None:
    test_taskholds._world(home)
    sent: list = []
    termmod.send = lambda handle, text, enter=True: (sent.append(text), (True, ""))[1]
    con = db.connect()
    try:
        actions.task_new("manager", "worker", "the work", "the work")
        seen: list = []
        real = termmod.send_keys
        termmod.screen_blocker = lambda h: None

        def keys_while_the_tick_tries(handle, keys):
            seen.append(delivery.deliver_queued(db.connect(), "worker", lambda _m: None))     # the tick's delivery, mid-call
            return real(handle, keys)
        termmod.send_keys = keys_while_the_tick_tries
        try:
            r = _call("session.keys", {"agent": "worker", "keys": ["esc"], "request_id": "beside"})
        finally:
            termmod.send_keys = real
        check("the control call went through", "result" in r, str(r))
        check("the tick's delivery, tried while it typed, typed nothing and was held", seen == [False] and sent == ["\x1b"], f"{seen} {sent}")
        check("and the task keeps its held mark, for the lock", (delivery.held_reason("worker", int(time.time())) or ("",))[0].startswith("its terminal is locked"))
        check("afterwards the task goes out", delivery.deliver_queued(con, "worker", lambda _m: None) and len(sent) == 2, str(sent))
    finally:
        con.close()


def test_the_daemon_sends_escape_on_no_occasion_of_its_own(home: Path) -> None:
    root = Path(__file__).resolve().parent.parent.parent / "handline" / "daemon"
    users = [f.name for f in root.glob("*.py") if f.name != "rpc_typing.py" and ('term.ESCAPE' in f.read_text() and "term.send(" in f.read_text())]
    check("no daemon module besides the typing methods sends the Esc constant to a terminal", users == [], str(users))


def test_the_typing_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    support._board_world(home)
    con = db.connect()
    for a in ("manager", "worker"):
        db.set_terminal(con, a, f"t-{a}", None)
        db.set_agent_state(con, a, "IDLE")
    inputhold.take(con, "worker", "person", "someone.else", int(time.time()))
    con.close()
    real = (sessiontoken.agent_of, stopgate.wall_reason, termmod.available, termmod.screen_blocker, termmod.send, termmod._screen_tail)
    sent = []
    termmod.send = lambda handle, text, enter=True: (sent.append((handle, text)) or (True, ""))
    termmod._screen_tail = lambda handle: ["Resume this session with:", "user@host ~ % "] if handle == "t-shelled" else None
    stopgate.wall_reason = lambda con, agent, row=None: f"{agent} is at its usage limit" if agent == "walled" else None
    termmod.available = lambda: True
    termmod.screen_blocker = lambda handle: termmod.Blocker(termmod.MODAL, "a dialog") if handle == "t-manager" else None
    agents.register("walled")
    agents.register("backend")
    agents.register("shelled")
    con = db.connect()
    db.set_terminal(con, "shelled", "t-shelled", None)
    db.set_terminal(con, "walled", "t-walled", None)
    db.set_terminal(con, "backend", "t-backend", None)
    con.close()
    results = []
    try:
        with lock.terminal("backend") as mine:
            check("(the test holds the terminal lock)", mine)
            for role in ("founder", "agent"):
                sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
                results += [(*r, role) for r in conformance.run_all(
                    [Path("contract/fixtures/typing.json"), Path("contract/fixtures/shell_input.json")], {role: connect},
                    {"founder_key": _hp()["key"], "locked_agent": "backend", "held_agent": "worker", "walled_agent": "walled",
                     "dialog_agent": "manager", "shell_agent": "shelled"}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of, stopgate.wall_reason, termmod.available, termmod.screen_blocker, termmod.send, termmod._screen_tail = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every typing fixture passes against this daemon, each as its own role", not bad and results, str(bad))
    check("fixtures: nothing is typed at the agent whose pane is a shell prompt", not [s for s in sent if s[0] == "t-shelled"], str(sent))
    check("fixtures: the shell prompt fixture ran, as the founder", any(n == "session.input.shell_prompt_gate" and r == "founder" for _, _, n, r in results), str(results))


def main() -> int:
    def tests(td):
        run(lambda: test_each_method_sends_what_it_says_and_records_that_the_agent_was_typed_at(Path(td) / "y0"))
        run(lambda: test_the_gates_refuse_in_the_plans_order_and_name_themselves(Path(td) / "y1"))
        run(lambda: test_a_malformed_call_is_refused_before_anything_is_sent(Path(td) / "y2"))
        run(lambda: test_typing_counts_for_the_idle_hold_and_a_failed_send_is_refused(Path(td) / "y3"))
        run(lambda: test_the_tick_types_nothing_beside_a_control_call(Path(td) / "y4"))
        run(lambda: test_the_daemon_sends_escape_on_no_occasion_of_its_own(Path(td) / "y5"))
        run(lambda: test_the_typing_fixtures_pass_against_this_daemon(Path(td) / "y6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
