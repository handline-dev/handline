"""Tests: the stored per-agent view (daemon/agentview.py) and the three methods that serve it, agents.list,
agent.get and agent.timeline. The drift guard compares the stored view with what the screens and the daemon
work out for themselves, on several states of one agent.

Run on its own: python3 tests/daemon/test_agentview.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import agentview, delivery, handoff_note, lifecycle, rpc  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.session import term  # noqa: E402
from handline.view import tui  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _ui_row(con, name, now):
    rows, _ = tui.agents_data(con, now)
    return next(r for r in rows if r.name == name)


def _drift(con, label: str, now: int) -> None:
    """The stored view agrees with the screen's own row and with the daemon's own deciders."""
    agentview.refresh(con, now)
    view, row, ui = agentview.stored(con, "backend"), db.get_agent_state(con, "backend"), _ui_row(con, "backend", now)
    check(f"{label}: the state word and what is said beside it are the screen's", agentview.screen_word(view) == (ui.state, ui.until),
          f"{view['state']} {view['state_detail']!r} vs {ui.state} {ui.until!r}")
    check(f"{label}: the state column renders from the view alone",
          tui.state_cell_of_view(view) == tui.state_cell(ui.state, ui.until))
    check(f"{label}: has_dialog is the DIALOG word", view["has_dialog"] == (ui.state == "DIALOG"))
    check(f"{label}: wall_reason and would_wake are the daemon's",
          view["wall_reason"] == delivery.wall_reason(con, "backend", row) and view["would_wake"] == bool(lifecycle.would_wake(con, row)))
    left = handoff_note.cache_left(con, "backend", now)
    check(f"{label}: the cache time is the daemon's",
          (view["cache_until"] is None) == (left is None) and (left is None or view["cache_until"] == agentview._ts(now + left)))
    check(f"{label}: account, task and parent", view["account"] == agents.current_account("backend", con)
          and (view["task"] or {}).get("id") == db.current_task(con, "backend")
          and view["parent"] == actions.supervisor_of(actions.routing(), "backend"), str(view))


def test_the_stored_view_does_not_drift_from_what_the_screens_work_out(home: Path) -> None:
    support._daemon_home(home, 20)
    now = int(time.time())
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        _drift(con, "idle", now)
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        _drift(con, "running", now)
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=now + 3600, detail="usage limit")
        _drift(con, "parked", now)
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.set_cursor(con, "bgwork:backend", 1)
        _drift(con, "background", max(now, int(time.time())))        # one clock for both sides, after the cursor was stamped
        check("background: the view says since when", agentview.stored(con, "backend")["background_since"] is not None)
        db.clear_cursor(con, "bgwork:backend")
        db.block_open(con, "backend", term.MODAL, "pick one", "", now, now - 1)
        _drift(con, "dialog", now)
        v = agentview.stored(con, "backend")
        check("dialog: the view carries the block and the blocker's headline", v["has_dialog"] and v["block"]["kind"] == term.MODAL
              and v["blocker"], str(v))
        tid = db.new_task(con, "do the thing", "backend", "manager")
        db.set_current_task(con, "backend", tid)
        _drift(con, "task", now)
        check("task: the view names the current task", agentview.stored(con, "backend")["task"] == {"id": tid, "title": "do the thing"})
    finally:
        con.close()


def test_the_fields_a_quiet_agent_never_sets_are_checked_where_they_are_set(home: Path) -> None:
    support._daemon_home(home, 20)
    now = int(time.time())
    con = db.connect()
    try:
        agentview.refresh(con, now)
        check("quiet: a quiet agent would not wake, is not at a wall and has no parent",
              (v := agentview.stored(con, "backend"))["would_wake"] is False and v["wall_reason"] is None and v["parent"] is None, str(v))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.set_closed(con, "backend", None)
        db.add_request(con, "product", "backend", "do it", None, None, None, "say", 0, None)
        _drift(con, "queued request", now)
        check("would_wake: a closed agent with a request waiting is one the daemon is trying to wake",
              agentview.stored(con, "backend")["would_wake"] is True)
        db.clear_closed(con, "backend")
        db.add_event(con, "backend", "StopFailure", {"limit": "five-hour limit"})
        _drift(con, "at the wall", now)
        check("wall_reason: an agent whose turn ended on the usage limit says so",
              agentview.stored(con, "backend")["wall_reason"] == "backend is at its usage limit")
        agents.register("manager")
        hand_edit_team("""
agents:
  manager: {}
  backend: {}

edges:
  - { from: "manager", to: "backend", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
        tui.EXPANDED.add("manager")         # a closed supervisor's report is not drawn
        try:
            _drift(con, "supervised", now)
        finally:
            tui.EXPANDED.discard("manager")
        check("parent: the agent its routing table says it answers to", agentview.stored(con, "backend")["parent"] == "manager")
    finally:
        con.close()


def test_one_agents_failure_does_not_stale_the_others(home: Path) -> None:
    support._daemon_home(home, 20)
    agents.register("frontend")
    agents.register("marketing")
    con = db.connect()
    logged = []
    real = agentview.compute
    try:
        agentview.refresh(con, 1000)
        before = agentview.stored(con, "frontend")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        db.set_agent_state(con, "marketing", dmn_states.RUNNING)
        db.set_agent_state(con, "frontend", dmn_states.RUNNING)

        def broken(c, name, *a, **k):
            if name == "frontend":
                raise RuntimeError("unreadable")
            return real(c, name, *a, **k)
        agentview.compute = broken
        agentview.refresh(con, 2000, logged.append)
    finally:
        agentview.compute = real
    check("failure: the agent before it and the agent after it are refreshed",
          agentview.stored(con, "backend")["state"] == "running" and agentview.stored(con, "marketing")["state"] == "running")
    check("failure: the one that failed keeps its last view", agentview.stored(con, "frontend") == before)
    check("failure: and it is logged by name", any("frontend" in m and "unreadable" in m for m in logged), str(logged))
    con.close()


def test_refresh_writes_only_what_changed_and_forgets_a_gone_agent(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    try:
        agentview.refresh(con, 1000)
        first = con.execute("SELECT updated FROM agent_view WHERE agent = 'backend'").fetchone()[0]
        agentview.refresh(con, 2000)
        check("refresh: an unchanged view is not rewritten", con.execute(
            "SELECT updated FROM agent_view WHERE agent = 'backend'").fetchone()[0] == first == 1000)
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        agentview.refresh(con, 3000)
        check("refresh: a changed one is", con.execute("SELECT updated FROM agent_view WHERE agent = 'backend'").fetchone()[0] == 3000)
        con.execute("INSERT INTO agent_view(agent, view, updated) VALUES('ghost', '{}', 1)")
        agentview.refresh(con, 4000)
        check("refresh: a view of an agent that is gone is dropped",
              con.execute("SELECT 1 FROM agent_view WHERE agent = 'ghost'").fetchone() is None)
    finally:
        con.close()


def test_the_tick_stores_every_agents_view(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    try:
        from handline.daemon import evaluate
        evaluate.tick(con, lambda m: None)
        have = {v["name"] for v in agentview.stored_all(con)}
        check("tick: a view is stored for every registered agent", have == set(paths.list_agents()) and "backend" in have, str(have))
    finally:
        con.close()


def _as(name):
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: name
    return real


def test_the_methods_serve_the_view_and_the_agents_detail(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    db.set_agent_state(con, "backend", dmn_states.RUNNING)
    agentview.refresh(con)
    db.add_event(con, "backend", "Stop", {"text": "turn ended"})
    con.close()
    w = Wire()
    w.call("hello", _hp())
    listed = w.call("agents.list", {})["result"]["agents"]
    mine = next(a for a in listed if a["name"] == "backend")
    check("agents.list: every registered agent, the stored view", {a["name"] for a in listed} == set(paths.list_agents())
          and mine["state"] == "running" and mine["has_dialog"] is False, str(mine))
    check("agents.list: the spec's required fields are all there", all(k in mine for k in (
        "name", "role", "model", "effort", "state", "state_since", "account", "task", "cache_until", "has_dialog", "parent")))
    got = w.call("agent.get", {"agent": "backend"})["result"]
    check("agent.get: the view plus the session and terminal facts", got["state"] == "running" and got["terminal"] == "term_x"
          and got["session_open"] is True and got["input_holders"][0]["kind"] == "daemon"
          and set(got["account_pick"]) == {"account", "reason"} and got["prompt"] is None, str(got))
    check("agent.get: an agent that is not there is not_found", _code(w.call("agent.get", {"agent": "nobody"})) == "not_found")
    check("agent.get: and a missing name is bad_request", _code(w.call("agent.get", {})) == "bad_request")
    tl = w.call("agent.timeline", {"agent": "backend"})["result"]
    check("agent.timeline: newest first, with its kind, time, and text",
          tl["items"] and tl["items"][0]["kind"] == "Stop" and tl["items"][0]["text"] == "turn ended"
          and tl["items"][0]["at"].endswith("Z") and tl["more"] is False, str(tl))
    ids = [i["id"] for i in tl["items"]]
    check("agent.timeline: ids fall", ids == sorted(ids, reverse=True))
    con = db.connect()
    for n in range(5):
        db.add_event(con, "backend", "Stop", {"n": n})
    con.close()
    page = w.call("agent.timeline", {"agent": "backend", "limit": 3})["result"]
    older = w.call("agent.timeline", {"agent": "backend", "limit": 3, "before": page["items"][-1]["id"]})["result"]
    check("agent.timeline: a page says there is more and `before` goes on from it", len(page["items"]) == 3 and page["more"]
          and older["items"][0]["id"] < page["items"][-1]["id"], str((page, older)))
    check("agent.timeline: a bad limit is bad_request", _code(w.call("agent.timeline", {"agent": "backend", "limit": 0})) == "bad_request")
    w.close()


def test_an_agent_reads_the_view_and_hl_ps_still_works_from_its_session(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    db.set_agent_state(con, "backend", dmn_states.RUNNING)
    con.close()
    real = _as("frontend")
    try:
        w = Wire()
        w.call("hello", {"client": "t", "protocol": 1})
        listed = w.call("agents.list", {})
        got = w.call("agent.get", {"agent": "backend"})
        timeline = w.call("agent.timeline", {"agent": "backend"})
        w.close()
    finally:
        sessiontoken.agent_of = real
    check("roles: an agent may read agents.list and agent.get", "result" in listed and "result" in got, str((listed, got)))
    check("roles: agent.timeline is a member's: refused at the role gate", _code(timeline) == "refused"
          and timeline["error"]["data"]["gate"] == "role", str(timeline))
    os.environ["OCP_AGENT"] = "frontend"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = climod.main(["ps"])
    finally:
        del os.environ["OCP_AGENT"]
    check("hl ps: still works from an agent session and shows the word agents.list serves",
          rc == 0 and "backend" in out.getvalue() and "RUNNING" in out.getvalue(), out.getvalue()[:300])


def test_a_database_made_before_the_view_gets_it_from_a_plain_connect(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "v2.db"
    dbschema = db.schema
    dbschema.connect(p).close()
    raw = __import__("sqlite3").connect(p)
    raw.execute("DROP TABLE agent_view")
    raw.execute("DELETE FROM schema_version WHERE version > 2")
    raw.commit()
    raw.close()
    con = dbschema.connect(p)
    check("view: a version-2 database gets the table from a plain connect, as an additive step does",
          con.execute("SELECT 1 FROM sqlite_master WHERE name = 'agent_view'").fetchone() is not None
          and db.schema_version(con) == db.LATEST_VERSION)
    con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_stored_view_does_not_drift_from_what_the_screens_work_out(Path(td) / "a1"))
        run(lambda: test_the_fields_a_quiet_agent_never_sets_are_checked_where_they_are_set(Path(td) / "a2a"))
        run(lambda: test_one_agents_failure_does_not_stale_the_others(Path(td) / "a2b"))
        run(lambda: test_refresh_writes_only_what_changed_and_forgets_a_gone_agent(Path(td) / "a2"))
        run(lambda: test_the_tick_stores_every_agents_view(Path(td) / "a3"))
        run(lambda: test_the_methods_serve_the_view_and_the_agents_detail(Path(td) / "a4"))
        run(lambda: test_an_agent_reads_the_view_and_hl_ps_still_works_from_its_session(Path(td) / "a5"))
        run(lambda: test_a_database_made_before_the_view_gets_it_from_a_plain_connect(Path(td) / "a6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
