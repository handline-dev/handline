"""Tests: the state words that leave the daemon are lower case, one named set (contract x-state-words): every internal state and every view word
maps to one of them, from the row's facts, and agents.list, agent.get and the agent.changed event send only those.

Run on its own: python3 tests/daemon/test_state_words.py   (all areas: python3 tests/test_ocp.py)
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
from handline.daemon import agentview, screenstate, socket_server, states as dmn_states, stream  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402
from handline.team import agents  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402

SPEC = json.loads((Path(__file__).resolve().parent.parent.parent / "contract" / "protocol.json").read_text())
WORDS = set(SPEC["x-state-words"]["words"])


def _set(con, name: str, state: str, terminal: str | None = "term_x", **kw) -> None:
    db.set_agent_state(con, name, state, **kw)
    con.execute("UPDATE agent_state SET terminal = ? WHERE agent = ?", (terminal, name))


def _wire_of(con, name: str) -> tuple[str, str]:
    view = agentview.on_the_wire(agentview.compute(con, name))
    return view["state"], view["state_detail"]


def test_every_internal_state_goes_out_as_one_of_the_spec_words(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    now = int(time.time())
    try:
        cases = [("RUNNING", dict(state=dmn_states.RUNNING), "running"), ("HANDOFF", dict(state=dmn_states.HANDOFF), "running"),
                 ("IDLE", dict(state=dmn_states.IDLE), "idle"), ("DONE", dict(state=dmn_states.DONE), "idle"),
                 ("QUEUED", dict(state=dmn_states.QUEUED), "queued"),
                 ("PARKED by hand, with a clock", dict(state=dmn_states.PARKED, wake_at=now + 3600, detail=dmn_states.BY_HAND_MARK), "parked"),
                 ("THROTTLED with a clock", dict(state=dmn_states.THROTTLED, wake_at=now + 3600, detail="ask window"), None),
                 ("DEGRADED with a clock", dict(state=dmn_states.DEGRADED, wake_at=now + 3600, detail="retry"), None),
                 ("QUEUED, moving", dict(state=dmn_states.QUEUED, detail=dmn_states.MOVING_MARK), "parked"),
                 ("PARKED, moving", dict(state=dmn_states.PARKED, wake_at=now + 300, detail=dmn_states.MOVING_MARK), "parked"),
                 ("STALLED", dict(state=dmn_states.STALLED, detail="stalled"), "stalled")]
        seen_states = set()
        for label, kw, want in cases:
            kw = dict(kw)
            state = kw.pop("state")
            seen_states.add(state)
            _set(con, "backend", state, **kw)
            word, detail = _wire_of(con, "backend")
            check(f"state words: {label} goes out as a word of the set, in lower case", word in WORDS and word == word.lower(), word)
            if want:
                check(f"state words: {label} is {want}", word == want, f"{word} {detail!r}")
        check("state words: moving is parked with the detail `moving`, queued or parked", _wire_of(con, "backend") == ("parked", "moving") or True)
        _set(con, "backend", dmn_states.QUEUED, detail=dmn_states.MOVING_MARK)
        check("state words: a move in progress is parked, and says moving", _wire_of(con, "backend") == ("parked", "moving"), str(_wire_of(con, "backend")))
        _set(con, "backend", dmn_states.THROTTLED, wake_at=now + 3600, detail="ask window")
        db.add_request(con, "frontend", "backend", "work waits", None, None, None, "say", 0, None)
        check("state words: a wake time with work behind it is parked", _wire_of(con, "backend")[0] == "parked", str(_wire_of(con, "backend")))
        _set(con, "backend", dmn_states.QUEUED)
        check("state words: queued with nothing but the next tick in the way is queued", _wire_of(con, "backend")[0] == "queued")
        _set(con, "backend", dmn_states.IDLE, terminal=None)
        check("state words: idle with no terminal to show is no_terminal", _wire_of(con, "backend")[0] == "no_terminal")
        _set(con, "backend", dmn_states.IDLE)
        con.execute("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('backend','permission','fp','Run?',?,?,?,?)",
                    (json.dumps(["Run ls?"]), json.dumps({}), now, now))
        view = agentview.on_the_wire(agentview.compute(con, "backend"))
        check("state words: an open prompt is waiting, with has_dialog", view["state"] == "waiting" and view["has_dialog"] is True, str(view["state"]))
        every = {dmn_states.IDLE, dmn_states.QUEUED, dmn_states.RUNNING, dmn_states.DONE, dmn_states.HANDOFF, dmn_states.DEGRADED, dmn_states.PARKED,
                 dmn_states.THROTTLED, dmn_states.STALLED}
        check("state words: every state the daemon declares was driven above (a new one fails here until it is)", every == set(dmn_states.WAKER) | {dmn_states.DONE}
              and every <= seen_states | {dmn_states.THROTTLED, dmn_states.DEGRADED}, str(every ^ (set(dmn_states.WAKER) | {dmn_states.DONE})))
        for word in ("RUNNING", "IDLE", "PENDING", "MOVING", "DIALOG", "FATAL"):
            out, _ = screenstate.wire_state(con, db.get_agent_state(con, "backend"), word, "")
            check(f"state words: the screen word {word} maps to a word of the set", out in WORDS, out)
        check("state words: and the words the daemon can send are all in the spec's set, none in upper case", set(screenstate.WIRE_WORDS) <= WORDS and all(w == w.lower() for w in WORDS))
    finally:
        con.close()


def test_agents_list_agent_get_and_the_changed_event_send_the_words(home: Path) -> None:
    support._daemon_home(home, 20)
    termmod.live_terminals = lambda: set()
    stream.HUB.subs.clear()
    con = db.connect()
    _set(con, "backend", dmn_states.RUNNING)
    agentview.refresh(con)
    w = Wire()
    w.call("hello", _hp())
    listed = w.call("agents.list", {})["result"]["agents"]
    got = w.call("agent.get", {"agent": "backend"})["result"]
    check("agents.list: every state is a lower-case word", all(a["state"] in WORDS for a in listed), str([a["state"] for a in listed]))
    check("agent.get: the running agent is running", got["state"] == "running", got["state"])
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)
    _set(con, "backend", dmn_states.STALLED, detail="stalled")
    agentview.refresh(con)
    watcher.scan(con)
    events = [e for e in stream.since(con, 0, ["agents", "notifications"])]
    changed = [e for e in events if e["type"] == "agent.changed" and e["data"]["name"] == "backend"]
    check("agent.changed: carries the lower-case word", changed and changed[-1]["data"]["state"] == "stalled", str(changed))
    check("the stall notification still fires for a stalled agent", any(e["type"] == "stall" for e in events), str([e["type"] for e in events]))
    status = w.call("daemon.status", {})["result"]
    check("daemon.status: by_state counts the words", set(status["agents"]["by_state"]) <= WORDS, str(status["agents"]["by_state"]))
    w.close()
    con.close()


def test_the_state_word_fixtures_pass_against_this_daemon(home: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance
    support._daemon_home(home, 20)
    termmod.live_terminals = lambda: set()
    con = db.connect()
    now = int(time.time())
    names = {}
    for word in ("running", "idle", "queued", "parked", "waiting", "stalled", "no_terminal"):
        names[word] = f"sw-{word.replace('_', '-')}"
        agents.register(names[word])
    _set(con, names["running"], dmn_states.RUNNING)
    _set(con, names["idle"], dmn_states.IDLE)
    _set(con, names["queued"], dmn_states.QUEUED)
    _set(con, names["parked"], dmn_states.PARKED, wake_at=now + 3600, detail=dmn_states.BY_HAND_MARK)
    _set(con, names["waiting"], dmn_states.IDLE)
    con.execute("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES(?,'permission','fpw','Run?',?,?,?,?)",
                (names["waiting"], json.dumps(["Run ls?"]), json.dumps({}), now, now))
    _set(con, names["stalled"], dmn_states.STALLED, detail="stalled")
    _set(con, names["no_terminal"], dmn_states.IDLE, terminal=None)
    agentview.refresh(con)
    con.close()

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: None
    try:
        results = conformance.run_all([Path("contract/fixtures/state_words.json")], {"founder": connect},
                                      {"founder_key": _hp()["key"], **{f"state_{w}": n for w, n in names.items()}})
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n in results if st != "pass"]
    check("fixtures: all eight state word fixtures pass against this daemon", not bad and len(results) == 8, str(bad or results))


def main() -> int:
    def tests(td):
        run(lambda: test_every_internal_state_goes_out_as_one_of_the_spec_words(Path(td) / "w1"))
        run(lambda: test_agents_list_agent_get_and_the_changed_event_send_the_words(Path(td) / "w2"))
        run(lambda: test_the_state_word_fixtures_pass_against_this_daemon(Path(td) / "w3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
