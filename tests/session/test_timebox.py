"""Tests: `ocp ask --timebox` and the notice to a supervisor about an agent running with nothing asked of it (T83).

Run on its own: python3 tests/session/test_timebox.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.cli import parser as cliparser  # noqa: E402
from ocp.daemon import timebox  # noqa: E402

TEAM = """
teams:
  dev: [marina, dohyun]
agents:
  manager: {}
  marina:  {}
  dohyun:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: async }
  - { from: "dev.*", to: "manager", relation: peer, response: async }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
"""


REAL_SESSION_PRESENT = dmn_delivery.session_present


def _world(home: Path) -> list[str]:
    """Three agents with open sessions, and a `send` that records what is typed."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    sent: list[tuple[str, str]] = []
    orca_seen()
    dmn_delivery.session_present = lambda a: True
    orcamod.typeable_now = lambda h: True
    orcamod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
    con = db.connect()
    try:
        for a in ("manager", "marina", "dohyun"):
            agents.register(a)
            db.set_terminal(con, a, f"term_{a}", "wt")
        con.commit()
    finally:
        con.close()
    return sent


def _delivered(con, requester: str, target: str, ago_s: int, **kw) -> int:
    rid = db.add_request(con, requester, target, "do the thing", None, "peer", "async",
                         None, 0, None, **kw)
    db.mark_delivered(con, rid)
    con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (int(time.time()) - ago_s, rid))
    return rid


def test_a_duration_needs_its_unit(home: Path) -> None:
    check("timebox: 90m, 2h, 45s and 1d parse",
          [timefmt.parse_span(t) for t in ("90m", "2h", "45s", "1d")] == [5400, 7200, 45, 86400])
    bad = []
    for t in ("90", "m", "0m", "-5m", "1.5h", "2w", ""):
        try:
            timefmt.parse_span(t)
        except ValueError:
            continue
        bad.append(t)
    check("timebox: no unit, no number, zero, negative or fractional is refused", not bad, str(bad))
    args = cliparser.build_parser().parse_args(["ask", "marina", "hi", "--timebox", "90m"])
    check("timebox: ocp ask --timebox 90m reaches the command in seconds", args.timebox == 5400)


def test_ask_keeps_its_timebox(home: Path) -> None:
    _world(home)
    ok, _, rid = askmod.ask("marina", "look at it", requester="manager", timebox_s=5400)
    ok2, _, rid2 = askmod.ask("marina", "and this", requester="manager")
    ok3, why, _ = askmod.ask("human", "fyi", requester="manager", timebox_s=60)
    con = db.connect()
    try:
        check("timebox: an ask keeps the timebox it was given",
              ok and db.get_request(con, rid)["timebox_s"] == 5400)
        check("timebox: an ask without one leaves it to the config",
              ok2 and db.get_request(con, rid2)["timebox_s"] is None)
        check("timebox: a request to the human is not timed", not ok3 and "--timebox" in why, why)
        check("timebox: the default is two hours until config.yaml says otherwise",
              timebox.timebox_s(db.get_request(con, rid2)) == 7200)
        idle_close.paths.config_file().write_text("timebox_s: 600\n")
        check("timebox: config.yaml's timebox_s is the default",
              timebox.timebox_s(db.get_request(con, rid2)) == 600
              and timebox.timebox_s(db.get_request(con, rid)) == 5400)
    finally:
        con.close()


def test_a_folded_ask_keeps_its_timebox(home: Path) -> None:
    """T95: `--mergeable` folds an ask into one already queued; its `--timebox`
    used to vanish with it."""
    _world(home)
    orcamod.typeable_now = lambda h: False      # nothing is delivered, so the first stays foldable
    ok, _, first = askmod.ask("marina", "review the branch", requester="manager", mergeable=True)
    ok2, msg, merged = askmod.ask("marina", "and its new commit", requester="manager",
                                  mergeable=True, timebox_s=900)
    con = db.connect()
    try:
        check("timebox: the second ask folded into the first", ok and ok2 and merged == first, msg)
        check("timebox: and its timebox is the merged row's",
              db.get_request(con, first)["timebox_s"] == 900, str(dict(db.get_request(con, first))))
        ok3, _, again = askmod.ask("marina", "one more", requester="manager", mergeable=True)
        check("timebox: a fold with none leaves the row's own",
              ok3 and again == first and db.get_request(con, first)["timebox_s"] == 900)
    finally:
        con.close()


def test_the_asker_is_told_once_when_a_request_runs_past_its_timebox(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        rid = _delivered(con, "manager", "marina", 3000, timebox_s=3600)
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active (PostToolUse)")
        db.set_meta(con, f"progress:{rid}", "done: parser\nleft: tests")
        timebox.sweep(con, lambda _m: None, now)
        check("timebox: nothing before the timebox is up", not sent, str(sent))
        timebox.sweep(con, lambda _m: None, now + 700)
        check("timebox: the asker is told when it runs past",
              len(sent) == 1 and sent[0][0] == "term_manager", str(sent))
        text = sent[0][1] if sent else ""
        check("timebox: the notice names the request, target, elapsed time, state and last progress",
              f"#{rid}" in text and "marina" in text and "61m" in text
              and "RUNNING" in text and "done: parser left: tests" in text, text)
        check("timebox: it is recorded as an event that is not agent activity",
              con.execute("SELECT COUNT(*) FROM events WHERE kind = 'timebox_exceeded' "
                          "AND agent = 'manager'").fetchone()[0] == 1
              and "timebox_exceeded" not in db.AGENT_ACTIVITY_KINDS)
        timebox.sweep(con, lambda _m: None, now + 700 + timebox.NOTICE_RETRY_S + 5)
        timebox.sweep(con, lambda _m: None, now + 5 * 3600)
        check("timebox: once - not again however long it stays open", len(sent) == 1, str(sent))
    finally:
        con.close()


def test_a_resolved_or_cancelled_request_stops_the_clock(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        done = _delivered(con, "manager", "marina", 9000, timebox_s=3600)
        gone = _delivered(con, "manager", "dohyun", 9000, timebox_s=3600)
        unshown = db.add_request(con, "manager", "marina", "later", None, "peer", "async", None, 0, None,
                                 timebox_s=1)
        report = _delivered(con, "manager", "dohyun", 9000, timebox_s=1)
        con.execute("UPDATE requests SET kind = 'report' WHERE id = ?", (report,))
        con.execute("UPDATE requests SET state = 'done' WHERE id = ?", (done,))
        con.execute("UPDATE requests SET state = 'cancelled' WHERE id = ?", (gone,))
        timebox.sweep(con, lambda _m: None, int(time.time()) + 3600)
        check("timebox: resolved, cancelled, undelivered and report requests are not timed",
              not sent, str(sent))
    finally:
        con.close()


def test_a_notice_that_cannot_be_typed_is_tried_again(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        rid = _delivered(con, "manager", "marina", 4000, timebox_s=3600)
        orcamod.typeable_now = lambda h: False
        timebox.sweep(con, lambda _m: None, now)
        check("timebox: an asker mid-turn is not told, and the request stays unnoticed",
              not sent and db.get_request(con, rid)["timebox_noticed_at"] is None)
        timebox.sweep(con, lambda _m: None, now + 5)
        orcamod.typeable_now = lambda h: True
        timebox.sweep(con, lambda _m: None, now + 10)
        check("timebox: not retried inside the retry gap", not sent, str(sent))
        timebox.sweep(con, lambda _m: None, now + timebox.NOTICE_RETRY_S + 1)
        check("timebox: told once it can be, and then marked",
              len(sent) == 1 and db.get_request(con, rid)["timebox_noticed_at"] is not None, str(sent))
    finally:
        con.close()


def test_a_human_asker_is_recorded_not_typed_at(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        rid = _delivered(con, "human", "marina", 9000, timebox_s=3600)
        timebox.sweep(con, lambda _m: None, int(time.time()))
        check("timebox: a request from the human is an event, and nothing is typed anywhere",
              not sent and db.get_request(con, rid)["timebox_noticed_at"] is not None
              and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'timebox_exceeded'").fetchone()[0] == 1)
    finally:
        con.close()


def test_a_supervisor_is_told_once_of_an_agent_running_with_nothing_asked(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active (PostToolUse)")
        timebox.sweep(con, lambda _m: None, now)
        check("unasked: the clock starts when it is first seen running", not sent)
        timebox.sweep(con, lambda _m: None, now + timebox.UNASKED_RUNNING_S - 1)
        check("unasked: nothing just under the limit", not sent, str(sent))
        timebox.sweep(con, lambda _m: None, now + timebox.UNASKED_RUNNING_S + 1)
        check("unasked: its supervisor is told, not the agent",
              len(sent) == 1 and sent[0][0] == "term_manager" and "marina" in sent[0][1]
              and "RUNNING" in sent[0][1], str(sent))
        timebox.sweep(con, lambda _m: None, now + 4 * 3600)
        check("unasked: once while the same stretch goes on", len(sent) == 1, str(sent))
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        timebox.sweep(con, lambda _m: None, now + 4 * 3600 + 10)
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active")
        timebox.sweep(con, lambda _m: None, now + 4 * 3600 + 20)
        timebox.sweep(con, lambda _m: None, now + 5 * 3600)
        check("unasked: a new stretch after it went idle is told again", len(sent) == 2, str(sent))
    finally:
        con.close()


def test_an_open_request_or_a_short_run_is_not_unasked(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active")
        _delivered(con, "manager", "marina", 60, timebox_s=10 ** 6)
        timebox.sweep(con, lambda _m: None, now)
        timebox.sweep(con, lambda _m: None, now + 3 * 3600)
        check("unasked: an agent with a request open is working on it", not sent, str(sent))
        con.execute("UPDATE requests SET state = 'done'")
        timebox.sweep(con, lambda _m: None, now + 3 * 3600 + 10)
        timebox.sweep(con, lambda _m: None, now + 3 * 3600 + 20)
        check("unasked: the clock starts when its last request closed, not before", not sent, str(sent))
    finally:
        con.close()


def test_background_work_counts_as_running_and_the_limit_is_configurable(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        idle_close.paths.config_file().write_text("unasked_running_s: 120\n")
        db.set_agent_state(con, "dohyun", dmn_states.IDLE, detail="finished its turn")
        db.set_cursor(con, "bgwork:dohyun", 1)
        timebox.sweep(con, lambda _m: None, now)
        timebox.sweep(con, lambda _m: None, now + 121)
        check("unasked: an idle agent with background work shows RUNNING and is told, by config limit",
              len(sent) == 1 and "background work" in sent[0][1], str(sent))
    finally:
        con.close()


def test_the_human_is_the_supervisor_of_an_agent_with_none(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        db.set_agent_state(con, "manager", dmn_states.RUNNING, detail="active")
        timebox.sweep(con, lambda _m: None, now)
        timebox.sweep(con, lambda _m: None, now + timebox.UNASKED_RUNNING_S + 1)
        check("unasked: a top agent's notice goes to the human as an event, typed nowhere",
              not sent and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'unasked_running' "
                                       "AND agent = 'manager'").fetchone()[0] == 1)
    finally:
        con.close()


def _stale_bg(con, agent: str, silent_s: int, told_ago: int | None = None) -> int:
    """`agent` reports background work, seen 3h ago, and has shown nothing for
    `silent_s`; the supervisor was told `told_ago` seconds ago, if at all."""
    now = int(time.time())
    con.execute("DELETE FROM events WHERE agent = ?", (agent,))
    db.add_event(con, agent, "Stop", {})
    con.execute("UPDATE events SET ts = ? WHERE agent = ?", (now - silent_s, agent))
    db.set_cursor(con, f"bgwork:{agent}", 1)
    con.execute("UPDATE cursors SET updated = ? WHERE name = ?", (now - 3 * 3600, f"bgwork:{agent}"))
    if told_ago is None:
        db.clear_cursor(con, f"stale_bg_told:{agent}")
    else:
        db.set_cursor(con, f"stale_bg_told:{agent}", now - told_ago)
    return now


def test_background_work_is_stale_at_the_limit_and_may_stop_after_the_veto(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        S, V = dmn_delivery.STALE_BG_S, dmn_delivery.STALE_BG_VETO_S
        st = types.SimpleNamespace(session_id="s")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")

        def probe(silent, told_ago=None):
            now = _stale_bg(con, "marina", silent, told_ago)
            row = db.get_agent_state(con, "marina")
            return (dmn_delivery.bg_stale(con, "marina", now), dmn_delivery.bg_may_stop(con, "marina", now),
                    dmn_lifecycle.should_close(con, "marina", "IDLE", row, now, st))

        check("stale: a second short of the limit is not stale, and holds the close",
              probe(S - 1, told_ago=V + 5) == (False, False, False), str(probe(S - 1, V + 5)))
        check("stale: at the limit it is stale, but not told, so still held",
              probe(S) == (True, False, False), str(probe(S)))
        check("stale: told a second short of the veto window is still held",
              probe(S, told_ago=V - 1) == (True, False, False), str(probe(S, V - 1)))
        check("stale: told and the window over, it may be stopped and the close is no longer held",
              probe(S, told_ago=V) == (True, True, True), str(probe(S, V)))
        idle_close.set_agent_close_timeout("marina", idle_close.NEVER)
        check("stale: an agent set to never close is never stopped, whichever path asks (the veto)",
              probe(S, told_ago=V) == (True, False, False)
              and dmn_delivery.bg_holds(con, "marina", _stale_bg(con, "marina", S, V)), str(probe(S, V)))
        idle_close.set_agent_close_timeout("marina", None)
        idle_close.paths.config_file().write_text("stale_background_s: 100\n")
        check("stale: config.yaml's stale_background_s moves the limit",
              probe(99)[0] is False and probe(100)[0] is True)
    finally:
        con.close()


def test_a_stale_agent_is_nudged_to_resolve_but_a_fresh_one_is_not(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        rid = _delivered(con, "manager", "marina", 9000, timebox_s=10 ** 6)
        db.set_cursor(con, "notify:marina", rid)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        row = db.get_agent_state(con, "marina")
        now = _stale_bg(con, "marina", dmn_delivery.STALE_BG_S - 1)
        check("stale: background work not yet stale means it is not forgotten",
              dmn_delivery.forgotten_resolve(con, "marina", row, now) is None)
        now = _stale_bg(con, "marina", dmn_delivery.STALE_BG_S)
        check("stale: stale background work does not hide a forgotten resolve",
              dmn_delivery.forgotten_resolve(con, "marina", row, now) is not None)
    finally:
        con.close()


def test_the_supervisor_is_told_once_per_stale_stretch(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        S = dmn_delivery.STALE_BG_S
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = _stale_bg(con, "marina", S - 1)
        timebox.sweep_stale_background(con, lambda _m: None, now)
        check("stale notice: nothing before the limit", not sent, str(sent))
        now = _stale_bg(con, "marina", S)
        timebox.sweep_stale_background(con, lambda _m: None, now)
        check("stale notice: the supervisor is told, with the veto",
              len(sent) == 1 and sent[0][0] == "term_manager" and "marina" in sent[0][1]
              and "idle-timeout never --agent marina" in sent[0][1], str(sent))
        check("stale notice: it is marked and recorded",
              db.get_cursor(con, "stale_bg_told:marina") == now
              and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'stale_background'").fetchone()[0] == 1)
        timebox.sweep_stale_background(con, lambda _m: None, now + 5)
        check("stale notice: once while the stretch goes on", len(sent) == 1, str(sent))
        db.add_event(con, "marina", "Stop", {})
        timebox.sweep_stale_background(con, lambda _m: None, now + 10)
        check("stale notice: activity ends the stretch and clears the mark",
              db.get_cursor(con, "stale_bg_told:marina") == 0)
        timebox.sweep_stale_background(con, lambda _m: None, now + 10 + S)
        check("stale notice: a later stale stretch is told again", len(sent) == 2, str(sent))
        idle_close.set_agent_close_timeout("marina", idle_close.NEVER)
        timebox.sweep_stale_background(con, lambda _m: None, now + 10 + S)
        check("stale notice: an agent that never closes is not told it may be stopped",
              len(sent) == 2 and not db.get_cursor(con, "stale_bg_told:marina"), str(sent))
    finally:
        con.close()


def test_the_supervisor_is_told_once_the_nudge_schedule_is_used_up(home: Path) -> None:
    """T337 (founder #2002/#2003): `nudge_forgotten_resolve` stops climbing
    `NUDGE_SCHEDULE_S` rather than typing at the agent forever - an agent that
    turns out to be genuinely stuck, not forgetful, still needs someone to
    look, so its supervisor is told once instead, the same way a stale
    background stretch already is."""
    sent = _world(home)
    con = db.connect()
    try:
        rid = _delivered(con, "manager", "marina", dmn_delivery.NUDGE_SCHEDULE_S[-1] + 60)
        db.set_cursor(con, "notify:marina", rid)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = int(time.time())
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, now)
        check("cap notice: nothing before the schedule is used up", not sent, str(sent))

        db.set_cursor(con, "nudge:marina", rid)
        db.set_cursor(con, "nudge_count:marina", len(dmn_delivery.NUDGE_SCHEDULE_S))
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, now)
        check("cap notice: the supervisor is told, naming the head and the count",
              len(sent) == 1 and sent[0][0] == "term_manager" and f"#{rid}" in sent[0][1]
              and f"{len(dmn_delivery.NUDGE_SCHEDULE_S)} times" in sent[0][1], str(sent))
        check("cap notice: marked and recorded",
              db.get_cursor(con, "forgotten_capped_told:marina") == rid
              and con.execute("SELECT COUNT(*) FROM events WHERE kind = "
                              "'forgotten_resolve_capped'").fetchone()[0] == 1)
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, now + 5)
        check("cap notice: not told twice for the same head", len(sent) == 1, str(sent))

        _set_request_state(con, rid, "done", verdict="did it")
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, now + 10)
        check("cap notice: resolving it clears the mark",
              db.get_cursor(con, "forgotten_capped_told:marina") == 0)

        rid2 = _delivered(con, "manager", "marina", dmn_delivery.NUDGE_SCHEDULE_S[-1] + 60)
        db.set_cursor(con, "notify:marina", rid2)
        db.set_cursor(con, "nudge:marina", rid2)
        db.set_cursor(con, "nudge_count:marina", len(dmn_delivery.NUDGE_SCHEDULE_S))
        later = now + timebox.NOTICE_RETRY_S + 15
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, later)
        check("cap notice: a later head that is also used up is told again",
              len(sent) == 2, str(sent))
    finally:
        con.close()


def test_the_founder_is_told_once_after_15_minutes_stuck_with_no_recorded_wait(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("manager", "marina", "do the thing")
        check("setup: marina holds a current task", ok, msg)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = int(time.time())
        S = timebox.STOP_WAIT_BACKSTOP_S

        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now)
        check("backstop: the clock starts, nobody told yet",
              not db.get_cursor(con, "stop_wait_told:marina"))
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + S - 1)
        check("backstop: nothing just under the limit",
              not db.get_cursor(con, "stop_wait_told:marina"))
        before = len(sent)
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + S + 1)
        check("backstop: the founder is told once, past the limit - never typed at",
              db.get_cursor(con, "stop_wait_told:marina") == 1 and len(sent) == before, str(sent))
        check("backstop: recorded as an event naming the task",
              f"T{tid}" in con.execute("SELECT payload FROM events WHERE kind = "
                                       "'stop_wait_stuck'").fetchone()[0])
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + S + 100)
        check("backstop: once per stretch", db.get_cursor(con, "stop_wait_told:marina") == 1)

        # Activity in between (a real turn that never records a wait) must
        # not buy the task a fresh 15 minutes.
        db.clear_cursor(con, "stop_wait_since:marina")
        db.clear_cursor(con, "stop_wait_told:marina")
        now2 = int(time.time())
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now2)
        since = db.get_cursor(con, "stop_wait_since:marina")
        db.set_agent_state(con, "marina", dmn_states.RUNNING)
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now2 + S - 1)
        check("backstop: RUNNING mid-stretch does not reset the clock",
              db.get_cursor(con, "stop_wait_since:marina") == since)
        db.set_agent_state(con, "marina", dmn_states.IDLE)
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now2 + S + 1)
        check("backstop: told at the original deadline, not a fresh one after the activity",
              db.get_cursor(con, "stop_wait_told:marina") == 1)

        # Recording a wait ends the stretch.
        ok, msg, t_blocker = actions.task_new("manager", "marina", "unrelated")
        check("setup: a second task to block on", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "manager")
        check("setup: t is now blocked", ok, msg)
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now2 + S + 200)
        check("backstop: a recorded wait clears the mark",
              not db.get_cursor(con, "stop_wait_since:marina")
              and not db.get_cursor(con, "stop_wait_told:marina"))
    finally:
        con.close()


def test_the_row_says_how_long_and_when_stale(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        _stale_bg(con, "marina", 60)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (int(time.time()) - 720,))
        word, detail = tui.screen_state(con, db.get_agent_state(con, "marina"))
        check("row: RUNNING, background and how long", (word, detail) == ("RUNNING", "background 12m"),
              str((word, detail)))
        _stale_bg(con, "marina", dmn_delivery.STALE_BG_S + 5)
        word, detail = tui.screen_state(con, db.get_agent_state(con, "marina"))
        check("row: and stale once the agent has shown nothing that long",
              word == "RUNNING" and detail == "background 3h, stale", detail)
    finally:
        con.close()


def test_the_exit_dialog_is_answered_by_stopping_only_over_stale_work_it_can_read(home: Path) -> None:
    bg_hint = "  ⏵⏵ auto mode on · 1 shell · ← for agents · ↓ to manage"
    composer_bg = COMPOSER_EMPTY[:-1] + [bg_hint]

    def stale(told_ago):
        return lambda con: _stale_bg(con, "backend", dmn_delivery.STALE_BG_S + 5, told_ago)

    told = dmn_delivery.STALE_BG_VETO_S + 5
    con, term, ok, why = _close_at(home / "a", EXIT_DIALOG, exits_on="", screen=composer_bg, prep=stale(told))
    check("stop: stale work past the veto is stopped with Enter on 'Exit and stop tasks', and closes",
          ok and term.sent == [("/exit", True), ("", True)], f"{ok} {why} {term.sent}")
    check("stop: and the choice is on the record",
          con.execute("SELECT 1 FROM events WHERE agent = 'backend' AND kind = 'stale_background_stopped'"
                      ).fetchone() is not None)
    con.close()

    con, term, ok, why = _close_at(home / "b", EXIT_DIALOG, exits_on="", screen=composer_bg, prep=stale(None))
    check("stop: stale but the supervisor not yet told is refused, nothing typed",
          not ok and term.sent == [] and "background work is running" in why, f"{ok} {why} {term.sent}")
    con.close()

    con, term, ok, why = _close_at(home / "c", EXIT_DIALOG, exits_on="", screen=composer_bg,
                                   prep=lambda c: _stale_bg(c, "backend", 60, told))
    check("stop: work that is not stale is refused however long ago its supervisor was told",
          not ok and term.sent == [], f"{ok} {why} {term.sent}")
    con.close()

    def never(con):
        idle_close.set_agent_close_timeout("backend", idle_close.NEVER)
        return stale(told)(con)

    con, term, ok, why = _close_at(home / "g", EXIT_DIALOG, exits_on="", screen=composer_bg, prep=never)
    check("stop: an agent set to never close is refused by the close itself, nothing typed",
          not ok and term.sent == [] and "background work is running" in why, f"{ok} {why} {term.sent}")
    con.close()

    other = [ln.replace("❯ 1. Exit and stop tasks", "  1. Exit and stop tasks")
             .replace("  2. Move to background and exit", "❯ 2. Move to background and exit")
             for ln in EXIT_DIALOG]
    variants = (("d", other, "a dialog whose highlighted option is not 'Exit and stop tasks'"),
                ("e", [ln.replace("Exit and stop tasks", "Exit and stop all tasks") for ln in EXIT_DIALOG],
                 "one whose label is not exactly the measured one"),
                ("f", [ln.replace("Background work is running", "Something else is running")
                       for ln in EXIT_DIALOG], "a different title"))
    for tag, dialog, what in variants:
        con, term, ok, why = _close_at(home / tag, dialog, exits_on="", screen=composer_bg, prep=stale(told))
        keys = [t for t, _ in term.sent if t != "/exit"]
        check(f"stop: {what} is declined with Esc, never chosen", not ok and keys == ["\x1b"],
              f"{ok} {why} {term.sent}")
        con.close()


def test_stopping_key_presses_only_the_measured_dialog(home: Path) -> None:
    def dialog(title="Background work is running", footer="Enter to confirm \u00b7 Esc to cancel",
               marked=("\u276f 1. Exit and stop tasks", "  2. Move to background and exit", "  3. Stay")):
        return orcamod.Dialog(title, (), footer, (title, *marked))

    check("stopping_key: the measured dialog is pressed with Enter",
          orcamod.stopping_key(dialog()) == (orcamod.ENTER, ""))
    near = (
        ("a different title", dialog(title="Something else is running")),
        ("a footer without Enter to confirm", dialog(footer="Esc to cancel")),
        ("the keep option highlighted", dialog(marked=("  1. Exit and stop tasks", "\u276f 3. Stay"))),
        ("a highlighted option that only resembles it", dialog(marked=("\u276f 1. Exit and stop all tasks",))),
        ("two marked lines", dialog(marked=("\u276f 1. Exit and stop tasks", "\u276f 3. Stay"))),
        ("no marked line", dialog(marked=("  1. Exit and stop tasks", "  3. Stay"))),
    )
    for what, d in near:
        key, why = orcamod.stopping_key(d)
        check(f"stopping_key: {what} presses nothing", key is None and why, f"{key!r} {why}")


def test_migration_adds_the_timebox_columns_to_an_older_requests_table(home: Path) -> None:
    """Every tick selects timebox_noticed_at, so a database made before it must
    gain both columns on connect, keeping its rows and leaving them untimed."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.sqlite"
    old = sqlite3.connect(p)
    old.execute("""
        CREATE TABLE requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
          requester TEXT NOT NULL, target TEXT NOT NULL, body TEXT NOT NULL,
          files TEXT, relation TEXT, response TEXT, kind TEXT,
          chain_depth INTEGER NOT NULL DEFAULT 0, parent_id INTEGER,
          chain_id TEXT, state TEXT NOT NULL DEFAULT 'queued', verdict TEXT,
          announced_at INTEGER, resolved_at INTEGER, delivered_at INTEGER
        )
    """)
    old.execute("INSERT INTO requests(ts, requester, target, body, state, chain_id, delivered_at) "
                "VALUES(?,?,?,?,?,?,?)", (int(time.time()), "manager", "marina", "old", "queued", "1",
                                          int(time.time()) - 9000))
    old.commit()
    old.close()
    con = db.connect(p)
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(requests)")}
        check("migration: an older requests table gains both timebox columns",
              {"timebox_s", "timebox_noticed_at"} <= cols, str(sorted(cols)))
        rows = db.timeboxed_open(con)
        check("migration: its open request is watched, with the config default box",
              len(rows) == 1 and rows[0]["timebox_s"] is None and timebox.timebox_s(rows[0]) == 7200)
    finally:
        con.close()


def main() -> int:
    def tests(td):
        try:
            run(lambda: test_a_duration_needs_its_unit(Path(td) / 'tb0'))
            run(lambda: test_ask_keeps_its_timebox(Path(td) / 'tb1'))
            run(lambda: test_a_folded_ask_keeps_its_timebox(Path(td) / 'tb1b'))
            run(lambda: test_the_asker_is_told_once_when_a_request_runs_past_its_timebox(Path(td) / 'tb2'))
            run(lambda: test_a_resolved_or_cancelled_request_stops_the_clock(Path(td) / 'tb3'))
            run(lambda: test_a_notice_that_cannot_be_typed_is_tried_again(Path(td) / 'tb4'))
            run(lambda: test_a_human_asker_is_recorded_not_typed_at(Path(td) / 'tb5'))
            run(lambda: test_a_supervisor_is_told_once_of_an_agent_running_with_nothing_asked(Path(td) / 'tb6'))
            run(lambda: test_an_open_request_or_a_short_run_is_not_unasked(Path(td) / 'tb7'))
            run(lambda: test_background_work_counts_as_running_and_the_limit_is_configurable(Path(td) / 'tb8'))
            run(lambda: test_the_human_is_the_supervisor_of_an_agent_with_none(Path(td) / 'tb9'))
            run(lambda: test_background_work_is_stale_at_the_limit_and_may_stop_after_the_veto(Path(td) / 'tb10'))
            run(lambda: test_a_stale_agent_is_nudged_to_resolve_but_a_fresh_one_is_not(Path(td) / 'tb11'))
            run(lambda: test_the_supervisor_is_told_once_per_stale_stretch(Path(td) / 'tb12'))
            run(lambda: test_the_supervisor_is_told_once_the_nudge_schedule_is_used_up(Path(td) / 'tb12a'))
            run(lambda: test_the_founder_is_told_once_after_15_minutes_stuck_with_no_recorded_wait(
                Path(td) / 'tb12b'))
            run(lambda: test_the_row_says_how_long_and_when_stale(Path(td) / 'tb13'))
            run(lambda: test_the_exit_dialog_is_answered_by_stopping_only_over_stale_work_it_can_read(Path(td) / 'tb14'))
            run(lambda: test_migration_adds_the_timebox_columns_to_an_older_requests_table(Path(td) / 'tb15'))
            run(lambda: test_stopping_key_presses_only_the_measured_dialog(Path(td) / 'tb16'))
        finally:
            dmn_delivery.session_present = REAL_SESSION_PRESENT
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
