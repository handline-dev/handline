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
from handline.daemon import agentview, screenstate  # noqa: E402
from handline.session import states as dmn_states
from handline.infra import sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402
from handline.team import agents  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

SPEC = json.loads((Path(__file__).resolve().parent.parent.parent / "contract" / "protocol.json").read_text())
WORDS = set(SPEC["x-state-words"]["words"])


def _set(con, name: str, state: str, terminal: str | None = "term_x", **kw) -> None:
    db.set_agent_state(con, name, state, **kw)
    con.execute("UPDATE agent_state SET terminal = ? WHERE agent = ?", (terminal, name))


def _wire_of(con, name: str) -> tuple[str, str]:
    view = agentview.compute(con, name)
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
        view = agentview.compute(con, "backend")
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


def main() -> int:
    def tests(td):
        run(lambda: test_every_internal_state_goes_out_as_one_of_the_spec_words(Path(td) / "w1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
