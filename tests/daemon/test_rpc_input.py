"""Tests: the input holder (input.take, input.release, input.ask), prompt.list and prompt.answer (contract/protocol.md):
who holds a terminal's input, the daemon typing nothing at a held one, the idle hold, the prompt a screen shows and the
answer to it, and the gates each refusal names (role, terminal lock, input holder, stale prompt).

The screen actions are replaced by recorders: they have their own tests; this file asks what the methods decide.

Run on its own: python3 tests/daemon/test_rpc_input.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import getpass
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.actions import prompt_blocks  # noqa: E402
from handline.daemon import delivery, inputhold, socket_server  # noqa: E402
from handline.infra import lock, sessiontoken  # noqa: E402
from handline.session import autoaccept  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402
import test_taskholds  # noqa: E402

ME = getpass.getuser()


def _call(method: str, params: dict, as_agent: str | None = None) -> dict:
    """One call on a fresh connection: from the founder, or from an agent session when `as_agent` is given."""
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


def _hold(agent: str, name: str, kind: str = "person", ago: int = 0) -> None:
    con = db.connect()
    con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
    inputhold.take(con, agent, kind, name, int(time.time()) - ago)
    con.close()


def _holders(agent: str) -> list:
    con = db.connect()
    try:
        return inputhold.view(con, agent)
    finally:
        con.close()


def _events(kind: str) -> list:
    con = db.connect()
    try:
        return con.execute("SELECT agent, payload FROM events WHERE kind = ? ORDER BY id", (kind,)).fetchall()
    finally:
        con.close()


def test_the_founder_takes_and_releases_the_input_and_nobody_takes_it_from_a_holder(home: Path) -> None:
    support._board_world(home)
    check("holder: the daemon holds a terminal nobody took", _holders("worker") == [inputhold.DAEMON])
    r = _call("input.take", {"agent": "worker", "request_id": "t1"})["result"]
    check("input.take: accepted, with the holder it made", r["accepted"] and r["holder"]["kind"] == "person"
          and r["holder"]["name"] == ME and r["holder"]["since"].endswith("Z"), str(r))
    check("input.take: agent.get shows it as the input holder", _call("agent.get", {"agent": "worker"})["result"]["input_holders"][0]["name"] == ME)
    r = _call("input.take", {"agent": "worker", "request_id": "t2"})
    check("input.take: the holder taking it again is fine", "result" in r, str(r))
    _hold("worker", "someone.else")
    r = _call("input.take", {"agent": "worker", "request_id": "t3"})
    check("input.take: while another holds it, refused with gate input_holder", _code(r) == "refused"
          and _gate(r) == "input_holder" and "someone.else" in r["error"]["message"], str(r))
    r = _call("input.release", {"agent": "worker", "request_id": "r1"})
    check("input.release: only its holder gives it back", _gate(r) == "input_holder", str(r))
    con = db.connect()
    inputhold.release(con, "worker")
    con.close()
    r = _call("input.release", {"agent": "worker", "request_id": "r2"})["result"]
    check("input.release: the daemon already holding it is a no-op, not a refusal", r["accepted"], str(r))
    _call("input.take", {"agent": "worker", "request_id": "t4"})
    r = _call("input.release", {"agent": "worker", "request_id": "r3"})["result"]
    check("input.release: the holder hands it back to the daemon", r["accepted"] and _holders("worker") == [inputhold.DAEMON], str(r))
    check("every take and release is an event with the holders", len(_events("input_holder")) >= 4
          and json.loads(_events("input_holder")[-1]["payload"])["holders"] == [inputhold.DAEMON])
    r = _call("input.ask", {"agent": "worker", "request_id": "a1"})
    check("input.ask: reserved, refused with gate unsupported", _code(r) == "refused" and _gate(r) == "unsupported", str(r))
    for method in ("input.take", "input.release", "input.ask"):
        r = _call(method, {"agent": "worker", "request_id": "x"}, as_agent="manager")
        check(f"{method}: an agent session is refused by role", _gate(r) == "role", str(r))
    for method in ("input.take", "input.release"):
        r = _call(method, {"agent": "nobody", "request_id": "x"})
        check(f"{method}: an unknown agent is not_found", _code(r) == "not_found", str(r))
        r = _call(method, {"request_id": "x"})
        check(f"{method}: no agent is bad_request", _code(r) == "bad_request" and r["error"]["data"]["field"] == "agent", str(r))
        r = _call(method, {"agent": "worker"})
        check(f"{method}: no request_id is bad_request", _code(r) == "bad_request" and r["error"]["data"]["field"] == "request_id", str(r))


def test_a_hold_that_goes_idle_is_lost_and_typing_refreshes_it(home: Path) -> None:
    support._board_world(home)
    _hold("worker", ME, ago=inputhold.idle_s() - 5)
    check("idle: a hold not yet idle stands", _holders("worker")[0]["name"] == ME)
    _hold("worker", ME, ago=inputhold.idle_s() + 5)
    con = db.connect()
    inputhold.holder(con, "worker")
    con.close()
    check("idle: past input_hold_idle_s the hold is the daemon's again", _holders("worker") == [inputhold.DAEMON])
    check("idle: and the loss is an event like a take", json.loads(_events("input_holder")[-1]["payload"])["holders"] == [inputhold.DAEMON])
    _hold("worker", ME, ago=inputhold.idle_s() - 5)
    con = db.connect()
    inputhold.touch(con, "worker", ME)
    con.close()
    _hold("worker", ME)  # a take of one's own hold keeps `since`
    con = db.connect()
    row = con.execute("SELECT since, last_typed FROM input_holders WHERE agent = 'worker'").fetchone()
    con.close()
    check("idle: typing counts the hold from now, and keeps when it was taken", row["last_typed"] >= row["since"] and row["last_typed"] > int(time.time()) - 3, str(dict(row)))
    cfg = loadmod.config_for_write()
    cfg[inputhold.IDLE_KEY] = 30
    loadmod.write_config(cfg)
    check("idle: input_hold_idle_s is the setting", inputhold.idle_s() == 30)
    _hold("worker", ME, ago=31)
    con = db.connect()
    check("idle: and a shorter one is honoured", inputhold.holder(con, "worker") is None)
    con.close()


def test_the_daemon_types_nothing_and_presses_nothing_at_a_held_terminal(home: Path) -> None:
    test_taskholds._world(home)
    sent: list[str] = []
    termmod.send = lambda h, t, enter=True: (sent.append(t), (True, ""))[1]
    con = db.connect()
    try:
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        _hold("worker", ME)
        check("held: deliver_queued declines and types nothing", not delivery.deliver_queued(con, "worker", lambda _m: None) and not sent, str(sent))
        held = delivery.held_reason("worker", int(time.time()))
        check("held: the task keeps its held mark, and names who holds", held is not None and ME in held[0], str(held))
        check("held: _type_gate says so to every path that types", ME in (delivery._type_gate(con, "worker", "term_w") or ""))
        pressed: list = []
        real_past = delivery.blocked.past_dialog
        delivery.blocked.past_dialog = lambda *a, **k: (pressed.append(1), ("", ""))[1]
        try:
            check("held: no key is pressed past a dialog", delivery._past_dialog(con, "worker", lambda _m: None) == "" and not pressed)
        finally:
            delivery.blocked.past_dialog = real_past
        real_accepts = termmod.accepts_tool
        termmod.accepts_tool = lambda *a: pressed.append(1) or True
        try:
            row = db.get_agent_state(con, "worker")
            waiting.note("worker", "Bash", {"command": "ls"}, after_event_id=3)
            check("held: auto-accept presses nothing", not autoaccept._accept(con, "worker", row, int(time.time()), lambda _m: None) and not pressed)
        finally:
            termmod.accepts_tool = real_accepts
        with inputhold.controlling():
            check("held: a person's own control call is not held back by the hold", delivery._type_gate(con, "worker", "term_w") is None)
        check("held: and the exemption ends with the call", delivery._type_gate(con, "worker", "term_w") is not None)
        con.execute("DELETE FROM input_holders")
        check("released: the very task goes out", delivery.deliver_queued(con, "worker", lambda _m: None) and sent, str(sent))
        check("released: and its held mark is gone", delivery.held_reason("worker", int(time.time())) is None)
    finally:
        con.close()


def _block(agent: str, kind: str, detail: dict, panel=("a panel",), fingerprint="fp1", answerable: bool = True) -> None:
    con = db.connect()
    db.prompt_open(con, agent, kind, fingerprint, "title", list(panel), detail, 7 if answerable else None, answerable, int(time.time()))
    con.close()


class Answers:
    """`prompt_blocks.answer` replaced by a recorder."""

    def __enter__(self):
        self.calls: list = []
        self.reply = (True, "worker: answered")
        self.real = prompt_blocks.answer
        prompt_blocks.answer = lambda con, bid, choice: (self.calls.append((bid, choice)), self.reply)[1]
        return self

    def __exit__(self, *exc):
        prompt_blocks.answer = self.real


def test_prompt_list_shows_the_open_prompt_with_what_can_be_chosen(home: Path) -> None:
    support._board_world(home)
    check("prompt.list: nothing open is an empty list", _call("prompt.list", {"agent": "worker"})["result"] == {"prompts": []})
    _block("worker", "permission", {"tool": "Bash", "command": "rm -rf build", "reason": "clean up"})
    p = _call("prompt.list", {"agent": "worker"})["result"]["prompts"][0]
    check("prompt.list: the prompt with its id, tool, command and reason", p["id"] == "fp1" and p["kind"] == "permission"
          and p["tool"] == "Bash" and p["command"] == "rm -rf build" and p["reason"] == "clean up" and p["lines"] == ["a panel"], str(p))
    check("prompt.list: a permission prompt offers yes, no and Esc", [o["key"] for o in p["options"]] == ["yes", "no", "esc"]
          and [o["answer"] for o in p["options"]][:2] == ["yes", "no"], str(p["options"]))
    check("prompt.list: the same prompt is the one agent.get carries", _call("agent.get", {"agent": "worker"})["result"]["prompt"]["id"] == "fp1")
    con = db.connect()
    con.execute("DELETE FROM prompt_blocks")
    con.close()
    _block("worker", "permission", {}, answerable=False)
    check("prompt.list: a permission prompt no hook record pins offers nothing to press", _call("prompt.list", {"agent": "worker"})["result"]["prompts"][0]["options"] == [])
    con = db.connect()
    con.execute("DELETE FROM prompt_blocks")
    con.close()
    _block("worker", "dialog", {"options": [[1, "Yes"], [2, "No, and tell me"]]})
    check("prompt.list: a dialog's numbered options and Esc", [(o["key"], o["label"]) for o in _call("prompt.list", {"agent": "worker"})["result"]["prompts"][0]["options"]]
          == [("1", "Yes"), ("2", "No, and tell me"), ("esc", "Cancel (Esc)")])
    r = _call("prompt.list", {"agent": "worker"}, as_agent="manager")
    check("prompt.list: an agent session is refused by role", _gate(r) == "role", str(r))
    check("prompt.list: an unknown agent is not_found", _code(_call("prompt.list", {"agent": "nobody"})) == "not_found")
    check("prompt.list: no agent is bad_request", _code(_call("prompt.list", {})) == "bad_request")


def test_prompt_answer_sends_only_the_prompt_the_caller_saw(home: Path) -> None:
    support._board_world(home)
    with Answers() as a:
        _block("worker", "permission", {"tool": "Bash"})
        r = _call("prompt.answer", {"agent": "worker", "prompt": "fp1", "choice": "yes", "request_id": "p1"})["result"]
        check("prompt.answer: yes answers the permission prompt yes", r["accepted"] and a.calls[-1][1] == {"yes": True}, str(a.calls))
        _call("prompt.answer", {"agent": "worker", "prompt": "fp1", "choice": "no", "request_id": "p2"})
        _call("prompt.answer", {"agent": "worker", "prompt": "fp1", "choice": "esc", "request_id": "p3"})
        check("prompt.answer: no, and Esc, are what the screen's own answer takes", [c[1] for c in a.calls[1:]] == [{"yes": False}, {"cancel": True}], str(a.calls))
        r = _call("prompt.answer", {"agent": "worker", "prompt": "fp1", "choice": "yes", "request_id": "p1"})["result"]
        check("prompt.answer: the same request_id is replayed and presses nothing again", r["replayed"] is True and len(a.calls) == 3)
        n = len(a.calls)
        r = _call("prompt.answer", {"agent": "worker", "prompt": "other-prompt", "choice": "yes", "request_id": "p4"})
        check("prompt.answer: another prompt id is stale_prompt, carrying the current one, and nothing is sent", _code(r) == "stale_prompt"
              and r["error"]["code"] == -32003 and r["error"]["data"]["current"]["id"] == "fp1" and len(a.calls) == n, str(r))
        con = db.connect()
        con.execute("DELETE FROM prompt_blocks")
        con.close()
        r = _call("prompt.answer", {"agent": "worker", "prompt": "fp1", "choice": "yes", "request_id": "p5"})
        check("prompt.answer: a prompt that went is stale_prompt with no current one", _code(r) == "stale_prompt" and r["error"]["data"]["current"] is None and len(a.calls) == n, str(r))
        _block("worker", "dialog", {"options": [[1, "Yes"], [2, "No"]]}, fingerprint="d1")
        _call("prompt.answer", {"agent": "worker", "prompt": "d1", "choice": "2", "request_id": "p6"})
        check("prompt.answer: a dialog's option key is its number", a.calls[-1][1] == {"number": 2, "always": False}, str(a.calls))
        r = _call("prompt.answer", {"agent": "worker", "prompt": "d1", "choice": "9", "request_id": "p7"})
        check("prompt.answer: a key the prompt does not offer is bad_request naming choice", _code(r) == "bad_request" and r["error"]["data"]["field"] == "choice", str(r))
        r = _call("prompt.answer", {"agent": "worker", "prompt": "d1", "choice": {"text": "hello"}, "request_id": "p8"})
        check("prompt.answer: a text answer to a dialog that takes none is bad_request", _code(r) == "bad_request", str(r))
        con = db.connect()
        con.execute("DELETE FROM prompt_blocks")
        con.close()
        _block("worker", "question", {"options": [{"label": "A", "checked": False}, {"label": "B", "checked": False}], "other_number": 3}, fingerprint="q1")
        _call("prompt.answer", {"agent": "worker", "prompt": "q1", "choice": "2", "request_id": "p9"})
        check("prompt.answer: a question's option key is its place", a.calls[-1][1] == {"choices": [1]}, str(a.calls))
        _call("prompt.answer", {"agent": "worker", "prompt": "q1", "choice": {"text": "something else"}, "request_id": "p10"})
        check("prompt.answer: and {text} is the free-text answer", a.calls[-1][1] == {"choices": [], "text": "something else"}, str(a.calls))
        for bad in ({"text": ""}, {"text": 3}, {"words": "x"}, 5, None):
            r = _call("prompt.answer", {"agent": "worker", "prompt": "q1", "choice": bad, "request_id": "pb"})
            check(f"prompt.answer: choice {bad!r} is bad_request", _code(r) == "bad_request", str(r))
        a.reply = (False, "worker: the prompt on screen is not the one shown - nothing sent")
        r = _call("prompt.answer", {"agent": "worker", "prompt": "q1", "choice": "1", "request_id": "p11"})
        check("prompt.answer: a screen that no longer shows it is refused with the screen's words", _code(r) == "refused" and "not the one shown" in r["error"]["message"], str(r))


def test_prompt_answer_passes_the_role_lock_and_holder_gates_first(home: Path) -> None:
    support._board_world(home)
    with Answers() as a:
        _block("worker", "permission", {"tool": "Bash"})
        ask = {"agent": "worker", "prompt": "fp1", "choice": "yes", "request_id": "g"}
        r = _call("prompt.answer", ask, as_agent="manager")
        check("prompt.answer: an agent session is refused by role", _gate(r) == "role", str(r))
        with lock.terminal("worker") as mine:
            check("(the test holds the terminal lock)", mine)
            r = _call("prompt.answer", {**ask, "request_id": "g2"})
            check("prompt.answer: refused at once while the terminal lock is held", _gate(r) == "terminal_lock", str(r))
        _hold("worker", "someone.else")
        r = _call("prompt.answer", {**ask, "request_id": "g3"})
        check("prompt.answer: refused while another holds the input", _gate(r) == "input_holder", str(r))
        check("prompt.answer: and before the prompt is even compared", not a.calls)
        con = db.connect()
        inputhold.release(con, "worker")
        con.close()
        _hold("worker", ME, ago=100)
        _call("prompt.answer", {**ask, "request_id": "g4"})
        con = db.connect()
        row = con.execute("SELECT last_typed FROM input_holders WHERE agent = 'worker'").fetchone()
        con.close()
        check("prompt.answer: the holder may answer, and it counts as typing", a.calls and row["last_typed"] > int(time.time()) - 5, str(a.calls))
        con = db.connect()
        inputhold.release(con, "worker")
        con.close()
        r = _call("prompt.answer", {**ask, "request_id": "g5"})
        check("prompt.answer: with the daemon holding, anyone may answer", "result" in r, str(r))
        check("prompt.answer: before hello is refused like any call", _code(Wire().call("prompt.answer", ask)) == "bad_request")
        for field in ("prompt", "agent", "request_id"):
            r = _call("prompt.answer", {k: v for k, v in ask.items() if k != field})
            check(f"prompt.answer: no {field} is bad_request naming it", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))


def test_terminal_close_is_refused_while_another_holds_the_input(home: Path) -> None:
    support._board_world(home)
    con = db.connect()
    db.set_terminal(con, "worker", "t1", None)
    db.set_agent_state(con, "worker", "CLOSED")
    con.execute("UPDATE agent_state SET closed_at = 1 WHERE agent = 'worker'")
    con.close()
    _hold("worker", "someone.else")
    r = _call("terminal.close", {"agent": "worker", "request_id": "c1"})
    check("terminal.close: refused with gate input_holder while another holds the input", _gate(r) == "input_holder", str(r))


def test_the_session_methods_are_not_held_back_by_a_hold_and_the_hold_does_not_hold_back_the_hl_commands(home: Path) -> None:
    from handline.actions import control
    from handline.daemon import lifecycle
    support._board_world(home)
    _hold("worker", "someone.else")
    seen: list = []
    real = control.close_result
    control.close_result = lambda con, agent, fresh=False: (seen.append(delivery._type_gate(con, agent, None)), (True, "closed"))[1]
    try:
        r = _call("session.stop", {"agent": "worker", "mode": "close", "request_id": "s1"})
    finally:
        control.close_result = real
    check("session.stop: a person's close is not held back by the hold", "result" in r and seen == [None], str((r, seen)))
    check("and the exemption does not outlive the call", delivery._type_gate(db.connect(), "worker", None) is not None)
    rc, out = support._cli(["task", "list"])
    check("hl task list: works at role agent beside a hold", rc == 0, out)


def test_the_input_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    support._board_world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/input.json")], {role: connect},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every input fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_the_founder_takes_and_releases_the_input_and_nobody_takes_it_from_a_holder(Path(td) / "i0"))
        run(lambda: test_a_hold_that_goes_idle_is_lost_and_typing_refreshes_it(Path(td) / "i1"))
        run(lambda: test_the_daemon_types_nothing_and_presses_nothing_at_a_held_terminal(Path(td) / "i2"))
        run(lambda: test_prompt_list_shows_the_open_prompt_with_what_can_be_chosen(Path(td) / "i3"))
        run(lambda: test_prompt_answer_sends_only_the_prompt_the_caller_saw(Path(td) / "i4"))
        run(lambda: test_prompt_answer_passes_the_role_lock_and_holder_gates_first(Path(td) / "i5"))
        run(lambda: test_terminal_close_is_refused_while_another_holds_the_input(Path(td) / "i6"))
        run(lambda: test_the_session_methods_are_not_held_back_by_a_hold_and_the_hold_does_not_hold_back_the_hl_commands(Path(td) / "i7"))
        run(lambda: test_the_input_fixtures_pass_against_this_daemon(Path(td) / "i8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
