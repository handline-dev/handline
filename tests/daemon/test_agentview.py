"""Tests: the stored per-agent view (daemon/agentview.py) and the three methods that serve it, agents.list,
agent.get and agent.timeline. The drift guard compares the stored view with what the daemon's own deciders
work out, on several states of one agent.

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
from handline.daemon import agentview, delivery, handoff_note, lifecycle  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.session import states as dmn_states, idle_close, observe as obs, stopgate, term  # noqa: E402
from handline.team import ask as askmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _drift(con, label: str, now: int) -> None:
    """The stored view agrees with the daemon's own deciders."""
    agentview.refresh(con, now)
    view, row = agentview.stored(con, "backend"), db.get_agent_state(con, "backend")
    word, detail = screenstate.screen_state(con, row, now)
    check(f"{label}: the state word and what is said beside it are screen_state's", agentview.screen_word(view) == (word, detail),
          f"{view['state']} {view['state_detail']!r} vs {word} {detail!r}")
    check(f"{label}: has_dialog is the DIALOG word", view["has_dialog"] == (word == "DIALOG"))
    check(f"{label}: wall_reason and would_wake are the daemon's",
          view["wall_reason"] == stopgate.wall_reason(con, "backend", row) and view["would_wake"] == bool(lifecycle.would_wake(con, row)))
    left = handoff_note.cache_left(con, "backend", now)
    check(f"{label}: the cache time is the daemon's",
          (view["cache_until"] is None) == (left is None) and (left is None or view["cache_until"] == agentview._ts(now + left)))
    check(f"{label}: cache_ignored and config_error", view["cache_ignored"] is idle_close.ignore_cache_expiry("backend")
          and view["config_error"] is None, str(view))
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
""")
        _drift(con, "supervised", now)
        check("parent: the agent its routing table says it answers to", agentview.stored(con, "backend")["parent"] == "manager")
    finally:
        con.close()


def _seed_view_fields(home: Path, con, now: int) -> dict:
    """Five agents, one for each case the spec's fixtures name: ready, blocked, busy, walled and plain."""
    for n in ("a.ready", "a.blocked", "a.busy", "a.walled", "a.plain"):
        agents.register(n)
        db.set_account(con, n, "alpha")
    for n, state in (("a.ready", dmn_states.IDLE), ("a.blocked", dmn_states.IDLE), ("a.busy", dmn_states.RUNNING),
                     ("a.walled", dmn_states.IDLE), ("a.plain", dmn_states.IDLE)):
        db.set_agent_state(con, n, state)
    for n in ("a.ready", "a.blocked", "a.busy"):
        tid = db.new_task(con, f"{n} work", n, "manager")
        db.set_current_task(con, n, tid)
        db.set_task_queued(con, tid, "manager")
        if n == "a.blocked":
            db.add_block(con, tid, db.new_task(con, "first", "manager", "manager"))
    db.set_closed(con, "a.walled", "sess-wall")
    db.add_request(con, "reviewer", "a.walled", "look", None, "peer", "async", None, 0, None)
    write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=now + 1800)
    return {"view_ready_agent": "a.ready", "view_blocked_agent": "a.blocked", "view_busy_agent": "a.busy",
            "view_walled_agent": "a.walled", "view_plain_agent": "a.plain"}


def test_the_view_says_what_kind_how_many_tasks_ready_and_walled(home: Path) -> None:
    support._daemon_home(home, 20)
    now = int(time.time())
    con = db.connect()
    try:
        _seed_view_fields(home, con, now)
        agentview.refresh(con, now)
        v = {n: agentview.stored(con, n) for n in ("a.ready", "a.blocked", "a.busy", "a.walled", "a.plain")}
        check("kind: the model and its effort, as the Rail's column", v["a.ready"]["kind"] == agents.kind(agents.read("a.ready")), str(v["a.ready"]))
        check("ready: idle, a queued current task, no open blocker", v["a.ready"]["ready"] is True and v["a.ready"]["task_count"] == 1)
        check("ready: an open blocker holds it", v["a.blocked"]["ready"] is False and v["a.blocked"]["task_count"] == 1)
        check("ready: a running agent is not", v["a.busy"]["ready"] is False)
        check("wall: the account, the window's reset as unix seconds, not a guess",
              v["a.walled"]["wall"] == {"account": "alpha", "clock": now + 1800, "assumed": False}, str(v["a.walled"]["wall"]))
        check("wall: null with nothing to wake it", v["a.plain"]["wall"] is None and v["a.ready"]["wall"] is None)
        check("task_count: none for an agent with no tasks", v["a.plain"]["task_count"] == 0)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5)
        agentview.refresh(con, now)
        check("wall: the clock is null when the window's reset is not known",
              (w := agentview.stored(con, "a.walled")["wall"]) and w["account"] == "alpha" and w["clock"] is None, str(w))
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5, reset_5h=now + 1800)
        agentview.refresh(con, now)
        check("wall: gone the moment the account has room", agentview.stored(con, "a.walled")["wall"] is None)
        db.set_current_task(con, "a.plain", None)
        draft = db.new_task(con, "not approved yet", "a.plain", "manager")
        agentview.refresh(con, now)
        check("task_count: a draft that is not the current task is not counted (open_task_counts)",
              agentview.stored(con, "a.plain")["task_count"] == 0, str(agentview.stored(con, "a.plain")))
        db.set_current_task(con, "a.plain", draft)
        agentview.refresh(con, now)
        check("task_count: the same draft counts once it is the current task", agentview.stored(con, "a.plain")["task_count"] == 1)
    finally:
        con.close()


def _seed_attention_fields(home: Path, con, now: int) -> dict:
    """One agent for each case of agent_view_attention.json; the account is quiet, so only the case's own reading is at a limit."""
    for n in ("a.fire", "a.ask", "a.alert", "a.stuck", "a.ignored", "a.broken", "a.plain", "a.busy"):
        agents.register(n)
        db.set_account(con, n, "alpha")
    write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5, reset_5h=now + 1800)
    for n in ("a.fire", "a.ask", "a.alert", "a.stuck", "a.ignored"):
        db.set_terminal(con, n, f"t-{n}", f"/w/{n}")
        db.set_agent_state(con, n, dmn_states.IDLE)
    db.set_agent_state(con, "a.busy", dmn_states.RUNNING)
    db.set_closed(con, "a.plain", "sess")
    db.set_agent_state(con, "a.plain", dmn_states.IDLE)
    write_agent_status("a.fire", "alpha", ctx_pct=99, ctx_used=50_000_000, session_id="s1")
    askmod.ask("human", "which one?", requester="a.ask")
    db.block_open(con, "a.alert", term.MODAL, "pick one", "", now, now - 1)
    db.add_event(con, "a.stuck", "StopFailure", {"limit": "five-hour limit"})
    idle_close.set_agent_ignore_cache_expiry("a.ignored", True)
    (paths.agent_dir("a.broken") / "agent.yaml").write_text("model: [unclosed\n")
    return {"view_fire_agent": "a.fire", "view_ask_agent": "a.ask", "view_alert_agent": "a.alert", "view_stuck_agent": "a.stuck",
            "view_ignored_agent": "a.ignored", "view_broken_agent": "a.broken", "view_plain_agent": "a.plain", "view_busy_agent": "a.busy"}


def test_the_view_says_which_trigger_fires_what_it_asks_of_the_founder_and_why_a_config_fails(home: Path) -> None:
    support._daemon_home(home, 20)
    now = int(time.time())
    con = db.connect()
    try:
        v = _seed_attention_fields(home, con, now)
        agentview.refresh(con, now)
        got = {n: agentview.stored(con, n) for n in v.values()}
        check("fire: the context at its limit, for an agent whose triggers apply", got["a.fire"]["fire"] == "context", str(got["a.fire"]))
        check("fire: null for a closed agent whatever its readings", got["a.plain"]["fire"] is None and got["a.plain"]["attention"] is None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=now + 1800)
        agentview.refresh(con, now)
        check("fire: the shortest window at the wall is `usage`, before the context", agentview.stored(con, "a.fire")["fire"] == "usage")
        write_status(home, "alpha", pct_5h=20, pct_7d=100, ctx_pct=5, reset_5h=now + 1800)
        agentview.refresh(con, now)
        check("fire: the longest window at the wall is `usage_7d`", agentview.stored(con, "a.fire")["fire"] == "usage_7d")
        check("fire: null once a parked agent is past its wall", (db.set_agent_state(con, "a.fire", dmn_states.PARKED, wake_at=now + 60),
                                                                  agentview.refresh(con, now), agentview.stored(con, "a.fire")["fire"])[2] is None)
        check("attention: an open ask to the founder", got["a.ask"]["attention"] == "ask")
        check("attention: a dialog needs a human", got["a.alert"]["attention"] == "alert")
        db.block_open(con, "a.plain", "no session", "its process is gone", "", now, now - 1)
        agentview.refresh(con, now)
        check("attention: a delivery block the founder has been told of is an alert though no dialog is up",
              (v := agentview.stored(con, "a.plain"))["attention"] == "alert" and v["state"] != "waiting", str(v))
        check("attention: a usage limit is stuck", got["a.stuck"]["attention"] == "stuck", str(got["a.stuck"]))
        check("attention: a running turn", got["a.busy"]["attention"] == "running")
        askmod.ask("human", "and this?", requester="a.alert")
        agentview.refresh(con, now)
        check("attention: the most urgent wins (an ask beats a dialog)", agentview.stored(con, "a.alert")["attention"] == "ask")
        check("cache_ignored: its own switch, else the global one", got["a.ignored"]["cache_ignored"] is True and got["a.plain"]["cache_ignored"] is False)
        broken = got["a.broken"]
        check("config_error: a line saying why, and no kind", isinstance(broken["config_error"], str) and broken["config_error"]
              and "\n" not in broken["config_error"] and "kind" not in broken and broken["model"] is None, str(broken))
        check("config_error: null, with a kind, when it reads", got["a.plain"]["config_error"] is None and got["a.plain"]["kind"])
    finally:
        con.close()


def test_the_view_carries_the_context_the_strip_reads(home: Path) -> None:
    support._daemon_home(home, 20)
    now = int(time.time())
    con = db.connect()
    try:
        agentview.refresh(con, now)
        check("ctx_pct: an agent on no account has none", agentview.stored(con, "backend")["ctx_pct"] is None)
        db.set_account(con, "backend", "alpha")
        write_status(home, "alpha", pct_5h=20, pct_7d=50)
        write_agent_status("backend", "alpha", ctx_pct=61, session_id="s1")
        agentview.refresh(con, now)
        check("ctx_pct: the figure of the agent's own session, as the strip reads it", agentview.stored(con, "backend")["ctx_pct"] == 61)
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


def _as(name):
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: name
    return real


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


def test_a_running_agent_whose_session_died_is_stuck_in_the_view_as_on_the_screen(home: Path) -> None:
    """The view is built from the tick's reading of the process table, not from none: queued work for a RUNNING agent with no session
    in its terminal is a stuck delivery to actions.stuck, and the view says the same, before the sweep has reconciled the row."""
    support._daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        db.set_terminal(con, "backend", "h1", None)
        db.add_request(con, "manager", "backend", "do it", None, None, None, None, 0, None)
        sessions_are()  # the process table is read and lists no session
        sees = lambda alive: delivery._seen.update(
            {"backend": obs.Observed(terminal_alive=alive, session_pid=None, session_seen=True)}) or delivery._seen_at.update(t=time.time())
        sees(True)
        agentview.refresh(con, int(time.time()))
        want = next((b for b in actions.stuck(con) if b["agent"] == "backend"), None)
        check("actions.stuck finds the delivery stuck: no session", want is not None and want["what"] and "no session" in want["why"], str(want))
        check("the view's attention says the same", agentview.stored(con, "backend")["attention"] == "stuck", str(agentview.stored(con, "backend")))
        delivery._seen_at["t"] = 0.0
        agentview.refresh(con, int(time.time()))
        check("with no reading from this tick the view leaves out what only the table could say",
              agentview.stored(con, "backend")["attention"] == "running", str(agentview.stored(con, "backend")))
    finally:
        con.close()


def test_the_view_reads_the_process_table_no_more_than_the_tick_did(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        db.add_request(con, "manager", "backend", "do it", None, None, None, None, 0, None)
        listed = []
        obs.live_sessions = lambda: listed.append(1) or {}
        delivery._seen.update({"backend": obs.Observed(session_seen=True)})
        delivery._seen_at["t"] = time.time()
        agentview.refresh(con, int(time.time()))
        check("a refresh with the tick's reading lists the process table not at all", not listed, str(listed))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_stored_view_does_not_drift_from_what_the_screens_work_out(Path(td) / "a1"))
        run(lambda: test_the_fields_a_quiet_agent_never_sets_are_checked_where_they_are_set(Path(td) / "a2a"))
        run(lambda: test_the_view_carries_the_context_the_strip_reads(Path(td) / "a2c"))
        run(lambda: test_the_view_says_what_kind_how_many_tasks_ready_and_walled(Path(td) / "a2d"))
        run(lambda: test_the_view_says_which_trigger_fires_what_it_asks_of_the_founder_and_why_a_config_fails(Path(td) / "a2f"))
        run(lambda: test_one_agents_failure_does_not_stale_the_others(Path(td) / "a2b"))
        run(lambda: test_refresh_writes_only_what_changed_and_forgets_a_gone_agent(Path(td) / "a2"))
        run(lambda: test_a_running_agent_whose_session_died_is_stuck_in_the_view_as_on_the_screen(Path(td) / "a8"))
        run(lambda: test_the_view_reads_the_process_table_no_more_than_the_tick_did(Path(td) / "a9"))
        run(lambda: test_a_database_made_before_the_view_gets_it_from_a_plain_connect(Path(td) / "a6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
