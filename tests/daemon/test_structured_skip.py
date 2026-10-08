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
from handline.daemon import (applysettings, delivery, evaluate, handoff, handoff_note, landing, lifecycle,  # noqa: E402
                             messaging, reconciler, timebox)
from handline.session import triggers  # noqa: E402

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


def _consulted(call, agent: str = "frontend") -> bool:
    """Did the call ask whether `agent` is structured? A skip that is not asked for is no skip, whatever this world makes of the call
    without it: the behaviour checks cannot tell a call that declined from one that had nothing to do."""
    seen: list[str] = []
    real = agents.is_structured
    agents.is_structured = lambda n: (seen.append(n), real(n))[1]
    try:
        call()
    finally:
        agents.is_structured = real
    return agent in seen


def _asked_first(call, agent: str = "frontend") -> bool:
    """Did the call ask whether `agent` is structured before it read or wrote anything of the daemon's? An entry point that declines
    after a read, or leaves the declining to a call further in, is not the skip: the call that follows may have nothing to do in this
    world and look as though it declined."""
    events: list[str] = []
    real_kind = agents.is_structured
    agents.is_structured = lambda n: (events.append(f"kind:{n}"), real_kind(n))[1]
    wrapped = {}
    for name, fn in list(vars(db).items()):
        if callable(fn) and getattr(fn, "__module__", "").startswith("handline.db") and not name.startswith("__") and name != "connect":
            def spy(*a, _fn=fn, _name=name, **kw):
                events.append(f"db:{_name}")
                return _fn(*a, **kw)
            wrapped[name] = fn
            setattr(db, name, spy)
    try:
        call()
    finally:
        for name, fn in wrapped.items():
            setattr(db, name, fn)
        agents.is_structured = real_kind
    return bool(events) and events[0] == f"kind:{agent}"


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


def test_a_tick_drives_the_terminal_agent_and_leaves_the_structured_one_untouched(home: Path) -> None:
    con, typed = _world(home)
    try:
        before = _snapshot(con, "frontend")
        lines: list[str] = []
        evaluate.tick(con, lines.append)
        check("tick: the structured agent's record is exactly as it was", _snapshot(con, "frontend") == before,
              f"{_snapshot(con, 'frontend')} {lines}")
        check("tick: nothing was typed at it", all("frontend" not in t[1] for t in typed) and not any("frontend" in l for l in lines),
              f"{typed} {lines}")
        check("tick: the terminal agent beside it was told its work", any("something for you" in t[1] for t in typed) or
              _snapshot(con, "backend")["events"], f"{typed}")
        check("tick: the loop asks which agents are structured", _consulted(lambda: evaluate.tick(con, LOG)))
        supervised: list[str] = []
        real_supervise = evaluate.supervise
        evaluate.supervise = lambda c, agent, *a, **kw: (supervised.append(agent), real_supervise(c, agent, *a, **kw))[1]
        try:
            evaluate.tick(con, LOG)
        finally:
            evaluate.supervise = real_supervise
        check("tick: the loop does not even supervise the structured agent, and does the terminal one",
              "frontend" not in supervised and "backend" in supervised, str(supervised))
        check("tick: and hl ps still lists both", {"backend", "frontend"} <= set(paths.list_agents()))
    finally:
        con.close()


def test_startup_and_the_reconciler_leave_it_alone(home: Path) -> None:
    con, typed = _world(home)
    try:
        # A terminal that is gone, a handoff left in flight and a deadline come due: for the terminal agent that is the reconciler's
        # to correct; for the structured one it is the Go daemon's, and nothing is recorded here.
        for name in ("backend", "frontend"):
            db.set_agent_state(con, name, "PARKED", wake_at=int(time.time()) - 10)
            db.start_handoff(con, f"h-{name}", name, "sess-1", "usage", int(time.time()) - 10)
        termmod.alive = lambda h: False
        termmod.live_terminals = lambda: set()
        sessions_are()
        before = _snapshot(con, "frontend")
        reconciler.expire_handoffs(con, LOG)
        reconciler.reconcile(con, LOG)
        reconciler.sweep(con, LOG, [])
        evaluate.start_waiting(con, LOG)
        check("reconcile: the structured agent keeps its terminal, its state and its handoff", _snapshot(con, "frontend") == before,
              f"{_snapshot(con, 'frontend')}")
        check("reconcile: the terminal agent's dead handoff and terminal are corrected, as before",
              _snapshot(con, "backend")["events"] and db.get_agent_state(con, "backend")["terminal"] is None
              or db.get_agent_state(con, "backend")["state"] != "PARKED", f"{_snapshot(con, 'backend')}")
        checked: list[str] = []
        real_need = lifecycle.start_or_wake_needed
        lifecycle.start_or_wake_needed = lambda c, agent, r: (checked.append(agent), real_need(c, agent, r))[1]
        try:
            evaluate.start_waiting(con, LOG)
        finally:
            lifecycle.start_or_wake_needed = real_need
        check("reconcile: start_waiting looks at the terminal agent and not at the structured one",
              "frontend" not in checked and "backend" in checked, str(checked))
        for name, call in {"expire_handoffs": lambda: reconciler.expire_handoffs(con, LOG),
                           "reconcile": lambda: reconciler.reconcile(con, LOG),
                           "sweep": lambda: reconciler.sweep(con, LOG, []),
                           "start_waiting": lambda: evaluate.start_waiting(con, LOG)}.items():
            check(f"reconcile: {name} asks which agents are structured", _consulted(call))
        check("reconcile: no agent row is made for a structured agent that had none",
              not _first_seen(con, home))
    finally:
        con.close()


def _first_seen(con, home: Path) -> bool:
    _agent("fresh", True)
    reconciler.reconcile(con, LOG)
    return con.execute("SELECT 1 FROM agent_state WHERE agent = 'fresh'").fetchone() is not None


def test_every_acting_call_is_a_no_op_for_a_structured_agent(home: Path) -> None:
    con, typed = _world(home)
    try:
        st = types.SimpleNamespace(transcript_path=str(home / "frontend.jsonl"), session_id="sess-frontend", trusted_ctx_used=80_000,
                                   trusted_short_pct=None, trusted_long_pct=None, ctx_pct=5)
        trig = triggers.Trigger(True, "context", "context over")
        row = db.get_agent_state(con, "frontend")
        now = int(time.time())
        calls = {
            "delivery.recover_unsent": lambda: delivery.recover_unsent(con, "frontend", LOG),
            "delivery.deliver_queued": lambda: delivery.deliver_queued(con, "frontend", LOG),
            "delivery.deliver_answers": lambda: delivery.deliver_answers(con, "frontend", LOG),
            "delivery.type_notice": lambda: delivery.type_notice(con, "frontend", "hello"),
            "delivery.nudge_forgotten_resolve": lambda: delivery.nudge_forgotten_resolve(con, "frontend", row, now, LOG),
            "delivery.track_background_work": lambda: delivery.track_background_work(con, "frontend", row),
            "delivery.enforce_edges": lambda: delivery.enforce_edges(con, "frontend", LOG),
            "messaging.send_message": lambda: messaging.send_message(con, "frontend", "hi", LOG),
            "messaging.tell_undelivered": lambda: messaging.tell_undelivered(con, "frontend", LOG),
            "messaging.deliver_pending": lambda: messaging.deliver_pending(con, "frontend", LOG),
            "landing.sweep": lambda: landing.sweep(con, "frontend", LOG),
            "lifecycle.should_close": lambda: lifecycle.should_close(con, "frontend", "IDLE", row, now + 10**6, st),
            "lifecycle.close_agent": lambda: lifecycle.close_agent(con, "frontend", LOG, st),
            "lifecycle.close_fresh": lambda: lifecycle.close_fresh(con, "frontend", LOG, st),
            "lifecycle.restart_agent": lambda: lifecycle.restart_agent(con, "frontend", LOG, st),
            "lifecycle.wake_agent": lambda: lifecycle.wake_agent(con, "frontend", LOG, force=True),
            "lifecycle._start_for_work": lambda: lifecycle._start_for_work(con, "frontend", LOG),
            "handoff.perform_handoff": lambda: handoff.perform_handoff(con, "frontend", trig, LOG),
            "handoff.settle_owed_clear": lambda: handoff.settle_owed_clear(con, "frontend", LOG),
            "handoff.force_move": lambda: handoff.force_move(con, "frontend", "alpha", "test", LOG),
            "handoff_note.ask": lambda: handoff_note.ask(con, "frontend", LOG),
            "handoff_note.at_cache_edge": lambda: handoff_note.at_cache_edge(con, "frontend", now, st, LOG),
            "handoff_note.hold": lambda: handoff_note.hold(con, "frontend", now, LOG),
            "evaluate.supervise": lambda: evaluate.supervise(con, "frontend", LOG, now, {}, []),
        }
        for name, call in calls.items():
            before = _snapshot(con, "frontend")
            n_typed = len(typed)
            try:
                result = call()
            except Exception as e:  # noqa: BLE001
                check(f"skip: {name} does not fail for a structured agent", False, f"{type(e).__name__}: {e}")
                continue
            check(f"skip: {name} records nothing and types nothing", _snapshot(con, "frontend") == before and len(typed) == n_typed,
                  f"{result} {_snapshot(con, 'frontend')} {typed[n_typed:]}")
            check(f"skip: {name} asks whether the agent is structured before it does anything else", _asked_first(call))
        edges: list[str] = []
        real_edges = delivery.enforce_edges
        delivery.enforce_edges = lambda c, agent, log: (edges.append(agent), real_edges(c, agent, log))[1]
        try:
            delivery.deliver_queued(con, "frontend", LOG)
        finally:
            delivery.enforce_edges = real_edges
        check("skip: deliver_queued declines itself, not by way of the edge check it starts with", not edges, str(edges))
        req = con.execute("SELECT id FROM requests WHERE target = 'frontend' ORDER BY id LIMIT 1").fetchone()
        before = _snapshot(con, "frontend")
        said = messaging.tell_now(con, int(req["id"]), LOG)
        check("skip: messaging.tell_now leaves the request queued for the Go daemon", _snapshot(con, "frontend") == before and
              "Go daemon" in said and len(typed) == 0, f"{said}")
    finally:
        con.close()


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


def test_timebox_and_apply_settings_skip_it_and_a_task_it_holds_is_not_clocked_here(home: Path) -> None:
    con, typed = _world(home)
    try:
        told: list[tuple[str, str]] = []
        real = delivery.type_notice
        delivery.type_notice = lambda c, a, text: (told.append((a, text)), True)[1]
        now = int(time.time())
        for name in ("backend", "frontend"):
            db.set_agent_state(con, name, "RUNNING")
        # a task each holds, doing, with a clock already past
        tids = {}
        for name in ("backend", "frontend"):
            tid = db.new_task(con, f"work of {name}", name, "reviewer", timeout_s=60)
            db.set_current_task(con, name, tid)
            con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?", (now - 100, tid))
            tids[name] = tid
        before = _snapshot(con, "frontend")
        clock = {n: con.execute("SELECT timeout_at FROM tasks WHERE id = ?", (t,)).fetchone()[0] for n, t in tids.items()}
        for sweep in (timebox.sweep_unasked_running, timebox.sweep_stale_background, timebox.sweep_forgotten_background,
                      timebox.sweep_background_check, timebox.sweep_stop_wait_backstop, timebox.sweep_forgotten_resolve_capped,
                      timebox.sweep_task_timeouts, applysettings.sweep):
            sweep(con, LOG, now + 10**5)
        after = {n: con.execute("SELECT timeout_at FROM tasks WHERE id = ?", (t,)).fetchone()[0] for n, t in tids.items()}
        for sweep in (timebox.sweep_unasked_running, timebox.sweep_stale_background, timebox.sweep_forgotten_background,
                      timebox.sweep_background_check, timebox.sweep_stop_wait_backstop, timebox.sweep_forgotten_resolve_capped,
                      timebox.sweep_task_timeouts, applysettings.sweep):
            check(f"timebox: {sweep.__module__.split('.')[-1]}.{sweep.__name__} asks which agents are structured",
                  _consulted(lambda: sweep(con, LOG, now + 10**5)))
        check("timebox: the structured agent's own record is untouched", _snapshot(con, "frontend") == before, str(_snapshot(con, "frontend")))
        check("timebox: its task's clock is not read, paused or re-armed from here", after["frontend"] == clock["frontend"], f"{clock} {after}")
        check("timebox: the terminal agent's task clock still runs", after["backend"] != clock["backend"] or
              any("backend" in t[1] or "reviewer" == t[0] for t in told), f"{clock} {after} {told}")
        check("timebox: no notice was sent about the structured agent", not any("frontend" in t[1] for t in told), str(told))
        delivery.type_notice = real
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_kind_is_read_as_the_go_daemon_reads_it(Path(td) / "k1"))
        run(lambda: test_a_tick_drives_the_terminal_agent_and_leaves_the_structured_one_untouched(Path(td) / "k2"))
        run(lambda: test_startup_and_the_reconciler_leave_it_alone(Path(td) / "k3"))
        run(lambda: test_every_acting_call_is_a_no_op_for_a_structured_agent(Path(td) / "k4"))
        run(lambda: test_the_same_calls_do_act_on_a_terminal_agent(Path(td) / "k5"))
        run(lambda: test_timebox_and_apply_settings_skip_it_and_a_task_it_holds_is_not_clocked_here(Path(td) / "k6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
