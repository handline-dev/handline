"""Tests: the Python daemon stays off structured agents. An agent whose agent.yaml says `structured: true` is the Go
daemon's: no tick, delivery, wake, close, restart, handoff or notice from here, no record that it is closed or stalled; what only reads or
lists agents still sees it. A terminal agent beside it is driven as before.

Run on its own: python3 tests/daemon/test_structured_skip.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.actions import stuck as stuckmod  # noqa: E402
from handline.daemon import delivery, handoff, lifecycle, messaging  # noqa: E402
from handline.daemon import agentview  # noqa: E402

LOG = lambda _m: None  # noqa: E731
CASES = Path(__file__).resolve().parents[2] / "contract" / "agent-kind-cases.json"


def _agent(name: str, structured: bool) -> None:
    agents.register(name)
    cfg = agents.read(name)
    if structured:
        cfg["structured"] = True
    agents.write(name, cfg)


def _world(home: Path):
    """backend (a terminal agent) and frontend (structured) alike in everything but the switch: each has a terminal, a session running,
    an account, a reading, and work waiting. Everything typed at a terminal is recorded."""
    _daemon_home(home, 20)
    con = db.connect()
    _agent("frontend", True)
    for name in ("backend", "frontend"):
        readable_agent(con, home, name)
        db.set_account(con, name, "alpha")
        write_agent_status(name, "alpha", session_id=f"sess-{name}", transcript_path=str(home / f"{name}.jsonl"))
        support.something_waiting(con, name)
    sessions_are("backend", "frontend")
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    termmod.screen_blocker = lambda h: None
    termmod.typeable_now = lambda h: True
    termmod.live_terminals = lambda: {"term_x"}
    typed: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        typed.append((handle, text))
        return True, ""
    termmod.send = send
    termmod.send_to_session = lambda h, text: send(h, text)
    return con, typed


def _snapshot(con, agent: str) -> dict:
    """Everything the daemon records about one agent: its state row, its events, the cursors and meta it keeps, its requests."""
    return {
        "state": dict(con.execute("SELECT * FROM agent_state WHERE agent = ?", (agent,)).fetchone() or {}),
        "events": [tuple(r) for r in con.execute("SELECT kind, payload FROM events WHERE agent = ? ORDER BY id", (agent,))],
        "cursors": sorted(tuple(r) for r in con.execute("SELECT name, row_id FROM cursors WHERE name LIKE ?", (f"%{agent}%",))),
        "requests": [tuple(r) for r in con.execute(
            "SELECT id, state, delivered_at, typed_at, landed_at FROM requests WHERE target = ? OR requester = ? ORDER BY id", (agent, agent))],
        "pending": [tuple(r) for r in con.execute("SELECT * FROM pending_messages WHERE agent = ?", (agent,))],
        "handoff": [tuple(r) for r in con.execute("SELECT id, state, step FROM handoff WHERE agent = ?", (agent,))],
    }


def test_the_kind_is_read_as_the_go_daemon_reads_it(home: Path) -> None:
    _daemon_home(home, 20)
    cases = json.loads(CASES.read_text())["cases"]
    bad = []
    for i, c in enumerate(cases):
        agents.register("probe") if "probe" not in paths.list_agents() else None
        agents.agent_file("probe").write_text(c["yaml"])
        if agents.is_structured("probe") != c["structured"]:
            bad.append((i, c["yaml"]))
    check("kind: the shared cases read the same as the Go daemon reads them", not bad, str(bad))
    check("kind: an agent that does not exist is not structured", not agents.is_structured("nobody"))
    agents.agent_file("probe").write_text("structured: true\n")
    check("kind: `driven` leaves the structured agent out", "probe" not in agents.driven() and "backend" in agents.driven())
    check("kind: but the listing still has it, for the UI, hl and reads", "probe" in paths.list_agents())
    cfg = agents.read("backend")
    cfg["mode"] = "plan"
    agents.write("backend", cfg)
    check("kind: a permission mode is not the switch", not agents.is_structured("backend"))


def test_the_same_calls_do_act_on_a_terminal_agent(home: Path) -> None:
    """The control: the skips above are not vacuous. The same world, the same calls, the other agent."""
    con, typed = _world(home)
    try:
        row = db.get_agent_state(con, "backend")
        acted = []
        before = _snapshot(con, "backend")
        delivery.deliver_queued(con, "backend", LOG)
        messaging.send_message(con, "backend", "hi", LOG)
        acted.append(_snapshot(con, "backend") != before and len(typed) > 0)
        before = _snapshot(con, "backend")
        ok, why = lifecycle.close_agent(con, "backend", LOG, types.SimpleNamespace(session_id="sess-backend", ctx_pct=5))
        check("control: a terminal agent is closed or refused by the real lifecycle, not by the structured skip",
              "structured session" not in why, why)
        check("control: a terminal agent is told its work", all(acted), f"{typed}")
    finally:
        con.close()


def test_the_view_pass_leaves_the_structured_agents_row_alone(home: Path) -> None:
    con, _typed = _world(home)
    try:
        for name in ("backend", "frontend"):
            con.execute("INSERT INTO agent_view(agent, view, updated) VALUES(?, ?, 5)", (name, json.dumps({"name": name, "go": name == "frontend"})))
        con.execute("INSERT INTO agent_view(agent, view, updated) VALUES('ghost', '{}', 5)")
        before = con.execute("SELECT view, updated FROM agent_view WHERE agent = 'frontend'").fetchone()
        agentview.refresh(con, 99, LOG)
        agentview.refresh_one(con, "frontend", LOG)
        rows = {r[0]: (r[1], r[2]) for r in con.execute("SELECT agent, view, updated FROM agent_view")}
        check("view: the structured agent's row is not rewritten", tuple(rows["frontend"]) == tuple(before), str(rows["frontend"]))
        check("view: the terminal agent's row is", rows["backend"][1] == 99, str(rows["backend"]))
        check("view: a gone agent's row is still dropped", "ghost" not in rows)
    finally:
        con.close()


def test_a_structured_agent_switched_with_its_terminal_open_is_a_stuck_finding(home: Path) -> None:
    from handline import actions
    con, _typed = _world(home)
    try:
        db.set_terminal(con, "frontend", "term_x", str(home / "frontend"))
        word, why, clears = stuckmod._delivery_state(con, "frontend", {}, {"term_x"})
        check("stuck: a structured agent that still holds its terminal says so", word == "switched with its terminal open", word)
        check("stuck: and names the fix", "hl close frontend" in why and "remove `structured:`" in why and not clears, why)
        check("stuck: a terminal agent beside it does not",
              stuckmod._delivery_state(con, "backend", {}, {"term_x"})[0] != "switched with its terminal open")
        db.set_closed(con, "frontend", "sess-frontend")
        check("stuck: once it is closed it is not the finding",
              stuckmod._delivery_state(con, "frontend", {}, {"term_x"})[0] != "switched with its terminal open")
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_kind_is_read_as_the_go_daemon_reads_it(Path(td) / "k1"))
        run(lambda: test_the_same_calls_do_act_on_a_terminal_agent(Path(td) / "k5"))
        run(lambda: test_the_view_pass_leaves_the_structured_agents_row_alone(Path(td) / "k7"))
        run(lambda: test_a_structured_agent_switched_with_its_terminal_open_is_a_stuck_finding(Path(td) / "k8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
