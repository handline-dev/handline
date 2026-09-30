"""Tests: `ocp ask --timebox` and the notice to a supervisor about an agent running with nothing asked of it (T83).

Run on its own: python3 tests/session/test_timebox.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import types

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.cli import parser as cliparser  # noqa: E402
from ocp.daemon import timebox  # noqa: E402
from ocp.session import bgjobs  # noqa: E402

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
    idle_close.set_bg_seconds("bg_check_s", 10**6)   # T450's check is tested on its own
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


def _delivered(con, requester: str, target: str, ago_s: int, kind: str | None = None, **kw) -> int:
    rid = db.add_request(con, requester, target, "do the thing", None, "peer", "async",
                         kind, 0, None, **kw)
    db.mark_delivered(con, rid)
    at = int(time.time()) - ago_s
    con.execute("UPDATE requests SET delivered_at = ?, typed_at = ?, landed_at = ? WHERE id = ?", (at, at, at, rid))
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
    # Only a task waits for a free agent (a message is typed at once), so a task edge is
    # what leaves the first ask undelivered and foldable.
    hand_edit_team(TEAM.replace('- { from: "manager", to: "dev.*", relation: supervisor, response: async }',
                                '- { from: "manager", to: "dev.*", relation: supervisor, response: async, kind: task }'))
    orcamod.typeable_now = lambda h: False
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


def _doing(con, tid: int, target: str) -> int:
    """Link a queued, non-fyi request to `tid` so it reads doing - T286's
    clock only runs in that state, the same live read `task_state` makes."""
    rid = db.add_request(con, askmod.HUMAN, target, "look at it", None, "supervisor",
                         "fire_and_forget", None, 0, None)
    db.link_request(con, tid, rid)
    return rid


def test_a_doing_tasks_timeout_starts_silently_then_repeats(home: Path) -> None:
    """T286, founder #1627's revision: a task's timeout only counts while it
    is doing. The clock starts silently the first sweep after it becomes
    doing (becoming doing is not itself running long), then tells its
    opener each time it runs past, again every further stretch - unlike a
    request's once-only timebox - until it is done or extended."""
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        tid = db.new_task(con, "ship it", "marina", "manager", timeout_s=3600)
        timebox.sweep(con, lambda _m: None, now)
        check("task timeout: a draft with no request out gets no clock at all",
              db.get_task(con, tid)["timeout_at"] is None and not sent,
              str(dict(db.get_task(con, tid))))

        _doing(con, tid, "marina")
        timebox.sweep(con, lambda _m: None, now)
        check("task timeout: doing starts the clock silently, timeout_s from now",
              db.get_task(con, tid)["timeout_at"] == now + 3600 and not sent,
              str(dict(db.get_task(con, tid))))

        timebox.sweep(con, lambda _m: None, now + 3600)
        check("task timeout: the opener is told once it is past",
              len(sent) == 1 and sent[0][0] == "term_manager", str(sent))
        text = sent[0][1]
        check("task timeout: the notice names the task, its timeout, and both answers",
              f"T{tid}" in text and "ship it" in text and "60m" in text
              and "edit" in text and "release" in text, text)
        payload = con.execute("SELECT payload FROM events WHERE kind = 'task_timeout_exceeded' "
                              "AND agent = 'manager'").fetchone()[0]
        check("task timeout: recorded as an event that is not agent activity, "
              "carrying the notice text itself (auditor #1670 point 4)",
              payload is not None and text in payload
              and "task_timeout_exceeded" not in db.AGENT_ACTIVITY_KINDS, payload)
        row = db.get_task(con, tid)
        check("task timeout: the deadline is pushed out by timeout_s from now, not left alone",
              row["timeout_at"] == now + 7200, str(dict(row)))

        timebox.sweep(con, lambda _m: None, now + 3605)
        check("task timeout: not told again before the next stretch is up", len(sent) == 1, str(sent))
        timebox.sweep(con, lambda _m: None, now + 7200)
        check("task timeout: told again once the next stretch passes - unlike a request's "
              "once-only timebox", len(sent) == 2, str(sent))

        actions.edit_task(con, tid, "manager", timeout_s=7200)
        timebox.sweep(con, lambda _m: None, now + 7210)
        check("task timeout: extending it (edit --timeout) resets the clock - no third notice yet",
              len(sent) == 2, str(sent))

        actions.mark_task_done(con, tid, "marina", reason="shipped")
        timebox.sweep(con, lambda _m: None, now + 1000000)
        check("task timeout: done, the clock stops for good", len(sent) == 2, str(sent))
    finally:
        con.close()


def test_a_task_opened_inside_ocp_gets_the_default_timeout_once_it_is_doing(home: Path) -> None:
    """T301: findings, holds and `task begin` never pass `task new -t`, so their
    tasks carry no timeout_s. The clock is set where the task becomes doing -
    the sweep - so "every task has a timeout" holds for them too."""
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        tid = db.new_task(con, "opened inside ocp", "marina", "manager")
        check("default timeout: nothing is set while it is not doing",
              db.get_task(con, tid)["timeout_s"] is None)
        _doing(con, tid, "marina")
        timebox.sweep(con, lambda _m: None, now)
        row = db.get_task(con, tid)
        check("default timeout: doing, it takes the default and the clock starts silently",
              row["timeout_s"] == actions.DEFAULT_TASK_TIMEOUT_S
              and row["timeout_at"] == now + actions.DEFAULT_TASK_TIMEOUT_S and not sent,
              str(dict(row)))
        check("default timeout: and it is within the 4h maximum",
              actions.DEFAULT_TASK_TIMEOUT_S <= actions.MAX_TASK_TIMEOUT_S)
        timebox.sweep(con, lambda _m: None, now + actions.DEFAULT_TASK_TIMEOUT_S)
        check("default timeout: past it, the opener is told like any other task",
              len(sent) == 1 and sent[0][0] == "term_manager", str(sent))
        other = db.new_task(con, "asked for 30m", "marina", "manager", timeout_s=1800)
        _doing(con, other, "marina")
        timebox.sweep(con, lambda _m: None, now + actions.DEFAULT_TASK_TIMEOUT_S + 1)
        check("default timeout: a task that was given one keeps it",
              db.get_task(con, other)["timeout_s"] == 1800)
    finally:
        con.close()


def test_a_doing_tasks_timeout_pauses_while_it_is_not_doing(home: Path) -> None:
    """The clock only runs while a task reads doing - its request answered
    (or `ocp task release`), it pauses with no notice, and does not fire on
    however long it sat not-doing; doing again, the clock restarts fresh."""
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        tid = db.new_task(con, "x", "marina", "manager", timeout_s=60)
        rid = _doing(con, tid, "marina")
        timebox.sweep(con, lambda _m: None, now)
        check("task timeout: doing, the clock started", db.get_task(con, tid)["timeout_at"] is not None)

        con.execute("UPDATE requests SET state = 'done' WHERE id = ?", (rid,))
        timebox.sweep(con, lambda _m: None, now + 10000)
        check("task timeout: no longer doing (its request answered) - paused, no notice",
              db.get_task(con, tid)["timeout_at"] is None and not sent,
              str((dict(db.get_task(con, tid)), sent)))

        _doing(con, tid, "marina")
        timebox.sweep(con, lambda _m: None, now + 10000)
        check("task timeout: doing again - the clock restarts fresh, timeout_s from now",
              db.get_task(con, tid)["timeout_at"] == now + 10060, str(dict(db.get_task(con, tid))))
    finally:
        con.close()


def test_a_task_timeout_notice_goes_to_whoever_opened_it_not_the_owner(home: Path) -> None:
    """A task's owner and its opener can be two different agents (task #1914);
    the timeout notice is the opener's to act on, the same as every other
    task-edit notice - it is never typed at the owner instead."""
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        tid = db.new_task(con, "x", "dohyun", "marina", timeout_s=60)
        _doing(con, tid, "dohyun")
        timebox.sweep(con, lambda _m: None, now)               # starts the clock
        timebox.sweep(con, lambda _m: None, now + 60)          # past it
        check("task timeout: the notice goes to the opener's terminal, not the owner's",
              len(sent) == 1 and sent[0][0] == "term_marina", str(sent))
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


def test_background_work_is_not_asked_about(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        idle_close.paths.config_file().write_text("unasked_running_s: 120\n")
        db.set_agent_state(con, "dohyun", dmn_states.IDLE, detail="finished its turn")
        db.set_cursor(con, "bgwork:dohyun", 1)
        timebox.sweep(con, lambda _m: None, now)
        timebox.sweep(con, lambda _m: None, now + 121)
        check("unasked: an idle agent with background work is not asked about - nobody is told "
              "(sweep_forgotten_background settles it)", not sent, str(sent))
    finally:
        con.close()


def test_a_running_agent_with_background_work_and_nothing_queued_is_still_asked_about(home: Path) -> None:
    """T359 (auditor #6652): no stale notice reaches a RUNNING agent now, so the unasked-running one
    must - a hung turn, or a permission prompt (no Stop event), with background work behind it."""
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active")
        db.set_cursor(con, "bgwork:marina", 1)
        timebox.sweep_unasked_running(con, lambda _m: None, now)
        timebox.sweep_unasked_running(con, lambda _m: None, now + timebox.UNASKED_RUNNING_S + 1)
        timebox.sweep_unasked_running(con, lambda _m: None, now + timebox.UNASKED_RUNNING_S + 2)
        check("running with background work: the supervisor is told, once",
              len(sent) == 1 and "marina" in sent[0][1], str(sent))
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


def _owe(con, agent: str) -> None:
    """`agent` has asked something and waits on the answer: it owes nothing, and is owed nothing."""
    db.add_request(con, agent, "manager", "is this right?", None, "peer", "async", None, 0, None)


def test_background_work_is_stale_at_the_limit_and_may_stop_after_the_veto(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        _owe(con, "marina")
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


def _forgotten(con, agent: str, silent_s: int) -> int:
    """`agent` has background work, nothing owed, and has shown nothing for `silent_s`."""
    return _stale_bg(con, agent, silent_s)


def test_forgotten_background_work_is_told_to_finish_then_may_be_stopped(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        S, V = idle_close.close_timeout_s("marina"), idle_close.bg_grace_s("marina")
        check("forgotten: the defaults are the 10-minute idle close and 5 minutes of grace", (S, V) == (600, 300))
        st = types.SimpleNamespace(session_id="s")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = _forgotten(con, "marina", S - 1)
        timebox.sweep(con, lambda _m: None, now)
        check("forgotten: before its idle window is over nothing is typed", not sent, str(sent))
        now = _forgotten(con, "marina", S)
        timebox.sweep(con, lambda _m: None, now)
        check("forgotten: when the window is over the agent itself is told, once, to finish or stop it",
              len(sent) == 1 and sent[0][0] == "term_marina" and "Finish it or stop it" in sent[0][1]
              and "no request open" in sent[0][1], str(sent))
        check("forgotten: nobody else is asked - no supervisor ping, and none for the unasked clock",
              all(h == "term_marina" for h, _ in sent))
        check("forgotten: it is marked and recorded",
              db.get_cursor(con, "bg_prompt:marina") == now
              and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'background_prompted'").fetchone()[0] == 1)
        timebox.sweep(con, lambda _m: None, now + 5)
        check("forgotten: told once while the stretch goes on", len(sent) == 1, str(sent))
        from ocp.ui import tui
        check("forgotten: the screen says it was told, so a stop is not a surprise",
              "told to finish or stop it" in tui._background_detail(con, "marina"),
              tui._background_detail(con, "marina"))
        row = db.get_agent_state(con, "marina")
        check("forgotten: until the second window is over the work is held and the session not closed",
              not dmn_delivery.bg_may_stop(con, "marina", now + V - 1)
              and not dmn_lifecycle.should_close(con, "marina", "IDLE", row, now + V - 1, st))
        timebox.sweep(con, lambda _m: None, now + V)
        check("forgotten: the grace over with nothing shown and the work may be stopped, and the close is free",
              dmn_delivery.bg_may_stop(con, "marina", now + V)
              and dmn_lifecycle.should_close(con, "marina", "IDLE", row, now + V, st))
        db.add_event(con, "marina", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'marina' AND id = (SELECT MAX(id) FROM events)",
                    (now + V + 5,))
        check("forgotten: a reply that shows something restarts the grace",
              not dmn_delivery.bg_may_stop(con, "marina", now + V + 10)
              and dmn_delivery.bg_may_stop(con, "marina", now + V + 5 + V))
    finally:
        con.close()


def test_the_close_that_stops_forgotten_work_records_why_once_in_tick_order(home: Path) -> None:
    """The close runs before the sweep in a tick and leaves the session closed, with its work
    gone, so the why is written by the close and never depends on the sweep seeing it."""
    _world(home)
    con = db.connect()
    try:
        G = idle_close.bg_grace_s("marina")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = _forgotten(con, "marina", 600)
        db.set_cursor(con, "bg_prompt:marina", now - G)
        st = types.SimpleNamespace(session_id="s", ctx_pct=1)
        real = (orcamod.background_work, orcamod.close_session, orcamod.close_terminal,
                dmn_delivery._wait_session_gone, dmn_delivery._past_dialog, dmn_lifecycle._answer_exit_dialog)
        orcamod.background_work = lambda h: "1 shell"
        orcamod.close_session = lambda h: (True, "")
        orcamod.close_terminal = lambda h, tab=True: (False, "kept")
        dmn_delivery._wait_session_gone = lambda a: True
        dmn_delivery._past_dialog = lambda *a, **k: ""
        dmn_lifecycle._answer_exit_dialog = lambda *a, **k: False
        try:
            row = db.get_agent_state(con, "marina")
            check("close order: the grace is over and the close is due",
                  dmn_lifecycle.should_close(con, "marina", "IDLE", row, now, st))
            ok, said = dmn_lifecycle.close_agent(con, "marina", lambda _m: None, st)
            timebox.sweep(con, lambda _m: None, now + 1)
            rows = con.execute("SELECT payload FROM events WHERE kind = 'background_abandoned'").fetchall()
            check("close order: closed, and exactly one abandonment is recorded with its why",
                  ok and len(rows) == 1 and "told to finish or stop it" in rows[0][0], str((ok, said, rows)))
            check("close order: the sweep after it clears the marks", not db.get_cursor(con, "bg_prompt:marina")
                  and not db.get_cursor(con, "bg_stop_noted:marina"))
        finally:
            (orcamod.background_work, orcamod.close_session, orcamod.close_terminal,
             dmn_delivery._wait_session_gone, dmn_delivery._past_dialog, dmn_lifecycle._answer_exit_dialog) = real
    finally:
        con.close()


def test_background_work_is_checked_every_n_minutes_naming_each_command(home: Path) -> None:
    """T450: the agent is told which commands run and for how long, whatever it owes and
    whether it closes, again every N minutes - not the T424 idle close."""
    sent = _world(home)
    idle_close.set_bg_seconds("bg_check_s", 600)
    con = db.connect()
    try:
        now = int(time.time())
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        _owe(con, "marina")                     # T424 leaves it alone: it owes something
        idle_close.set_agent_close_timeout("marina", idle_close.NEVER)   # and it never closes
        done = home / "b1.output"
        done.write_text("tests ok\n\n[exited with code 0]\n")
        raw = lambda cmd, out, bg=True: {"tool_name": "Bash", "tool_use_id": "t", "session_id": "s",  # noqa: E731
                                         "tool_input": {"command": cmd, "run_in_background": bg},
                                         "tool_response": f"Command running in background with ID: x. Output is being written to: {out}"}
        bgjobs.note("marina", raw("pytest -x tests/", "/x/tasks/a0.output"))
        bgjobs.note("marina", raw("npm run watch", "/x/tasks/w2.output"))
        bgjobs.note("marina", raw("make all", "/x/tasks/d3.output", bg=False))
        check("check: only a call made in the background is recorded",
              con.execute("SELECT COUNT(*) FROM events WHERE kind = 'background_started'").fetchone()[0] == 2)
        con.execute("UPDATE events SET payload = REPLACE(payload, '/x/tasks/w2.output', ?) WHERE kind = 'background_started' "
                    "AND payload LIKE '%w2%'", (str(done),))
        db.set_cursor(con, "bgwork:marina", 1)
        db.set_cursor(con, "bgcount:marina", 2)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (now - 3600,))
        con.commit()
        timebox.sweep(con, lambda _m: None, now)
        check("check: told once, naming the command still running and not the one that ended",
              len(sent) == 1 and "pytest -x tests/" in sent[0][1] and "npm run watch" not in sent[0][1]
              and "Stop what you no longer need" in sent[0][1], str(sent))
        timebox.sweep(con, lambda _m: None, now + 5)
        check("check: not again before N minutes", len(sent) == 1, str(sent))
        every = idle_close.bg_check_s("marina")
        check("check: N defaults to 10 minutes", every == 600, str(every))
        timebox.sweep(con, lambda _m: None, now + every)
        check("check: and again after N minutes while it runs", len(sent) == 2, str(sent))
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active")
        timebox.sweep(con, lambda _m: None, now + 2 * every)
        check("check: mid-turn it waits for the turn's end", len(sent) == 2, str(sent))
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        idle_close.set_bg_seconds("bg_check_s", 120, "marina")
        timebox.sweep(con, lambda _m: None, now + 2 * every + 5)
        check("check: told at once when it was due while busy; its own N is read", len(sent) == 3, str(sent))
        timebox.sweep(con, lambda _m: None, now + 2 * every + 5 + 119)
        check("check: its own N of 2 minutes holds", len(sent) == 3, str(sent))
        timebox.sweep(con, lambda _m: None, now + 2 * every + 5 + 120)
        check("check: and then asks", len(sent) == 4, str(sent))
        # dohyun owes nothing, so T424's own notice comes first, and this one waits behind it
        db.set_agent_state(con, "dohyun", dmn_states.IDLE, detail="finished its turn")
        db.set_terminal(con, "dohyun", "term_dohyun", None)
        db.set_cursor(con, "bgwork:dohyun", 1)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:dohyun'", (now - 3600,))
        con.commit()
        before = len(sent)
        timebox.sweep(con, lambda _m: None, now + 2 * every + 5 + 240)
        timebox.sweep(con, lambda _m: None, now + 2 * every + 5 + 240 + timebox.NOTICE_RETRY_S + 1)
        told = [t for h, t in sent[before:] if h == "term_dohyun"]
        check("check: behind a T424 notice in flight it says nothing of its own",
              len(told) == 1 and "Finish it or stop it" in told[0], str(sent[before:]))
        check("check: the footer's count is what caps the list, killed jobs and all",
              bgjobs.footer_count("2 shells, 1 monitor") == 3 and bgjobs.footer_count(None) == 0
              and [j["command"] for j in bgjobs.running(con, "marina", 1)] == ["pytest -x tests/"]
              and len(bgjobs.running(con, "marina")) == 1)
    finally:
        con.close()


def _answers_every_check(home: Path, every: int) -> None:
    sent = _world(home)
    idle_close.set_bg_seconds("bg_check_s", every)
    con = db.connect()
    try:
        t0 = int(time.time())

        def event(kind: str, ts: int) -> None:
            rid = db.add_event(con, "marina", kind, {})
            con.execute("UPDATE events SET ts = ? WHERE id = ?", (ts, rid))

        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        db.set_cursor(con, "bgwork:marina", 1)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (t0,))
        event("Stop", t0 + 300)                           # it worked five minutes, then idled
        answers: list[int] = []
        seen = 0
        for t in range(t0 + 300, t0 + 3600, 30):
            for a in [a for a in answers if a <= t]:
                event("UserPromptSubmit", a)
                event("Stop", a + 5)
                answers.remove(a)
            timebox.sweep(con, lambda _m: None, t)
            for _h, text in sent[seen:]:
                if text.startswith("Background work has been running"):
                    answers.append(t + 20)                # it answers the check 20s after
            seen = len(sent)
        checks = [x for _h, x in sent if x.startswith("Background work has been running")]
        prompted = con.execute("SELECT ts FROM events WHERE kind = 'background_prompted'").fetchall()
        check("answering: it was checked, and answered",
              len(checks) >= 1 and (every > 300 or len(checks) >= 5), str(sent))
        check("answering: yet T424 still told it to finish or stop, once",
              len(prompted) == 1, str(sent))
        at = db.get_cursor(con, "bg_prompt:marina")
        check("answering: on schedule - its own five minutes of work, then the ten of silence",
              t0 + 900 <= at <= t0 + 900 + 60, f"{at - t0}")
        check("answering: and with nothing more shown the grace ends and it may be stopped",
              dmn_delivery.bg_may_stop(con, "marina", at + idle_close.bg_grace_s("marina") + 60))
    finally:
        con.close()


def test_an_agent_that_answers_every_check_is_still_told_to_stop_and_closed_on_schedule(home: Path) -> None:
    """The check and T424 both default to 10 minutes on different clocks: answering the
    check must not keep resetting the silence T424 measures (auditor, T450)."""
    _answers_every_check(home, 600)


def test_answering_checks_shorter_than_the_notice_does_not_hold_the_notice_off(home: Path) -> None:
    """Every answer turn is excused, not only the latest: with a check of 2 minutes the earlier
    answers must not count as activity either (auditor re-audit, T450)."""
    _answers_every_check(home, 120)


def test_a_turn_that_ends_before_a_check_is_written_is_not_its_answer(home: Path) -> None:
    """Checks and activity are paired by event order, not by clock: a tick's check is
    written seconds after the tick began, and a Stop in that gap still came before it
    (auditor #6383, #6430, T540)."""
    _world(home)
    con = db.connect()
    try:
        t0 = int(time.time())
        db.set_cursor(con, "bgwork:marina", 1)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (t0,))

        def event(kind: str, ts: int) -> None:
            rid = db.add_event(con, "marina", kind, {})
            con.execute("UPDATE events SET ts = ? WHERE id = ?", (ts, rid))

        T = t0 + 300
        for gap in (0, 5, 10):
            con.execute("DELETE FROM events WHERE agent = 'marina'")
            event("Stop", T + gap)                        # its real turn ends ...
            event("background_checked", T)                # ... before the check, stamped as the tick began
            event("UserPromptSubmit", T + 20)
            event("Stop", T + 25)                         # the answer turn
            shown = dmn_delivery.bg_last_shown(con, "marina")
            check(f"order: a Stop {gap}s into the tick is shown, its answer turn is not",
                  shown == T + gap, str(shown))
        event("UserPromptSubmit", T + 60)                 # real work after the answer
        check("order: work after the answer turn is shown", dmn_delivery.bg_last_shown(con, "marina") == T + 60)
    finally:
        con.close()


def test_a_check_that_was_not_typed_leaves_no_event_to_mistake_a_real_turn_for_its_answer(home: Path) -> None:
    """The check event is written before the typing, so a check that is refused or whose
    typing raises must take it back: else the next real turn looks like its answer and
    counts as silence (auditor #6446, T540)."""
    _world(home)
    idle_close.set_bg_seconds("bg_check_s", 600)
    con = db.connect()
    real = dmn_delivery.type_notice
    try:
        now = int(time.time())
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        db.set_cursor(con, "bgwork:marina", 1)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (now - 3600,))

        def checks() -> int:
            return con.execute("SELECT COUNT(*) FROM events WHERE kind = 'background_checked'").fetchone()[0]

        def real_turn(ts: int) -> None:
            rid = db.add_event(con, "marina", "UserPromptSubmit", {})
            con.execute("UPDATE events SET ts = ? WHERE id = ?", (ts, rid))

        def refuse(*_a, **_k):
            return False

        def blow_up(*_a, **_k):
            raise RuntimeError("orca call failed")

        dmn_delivery.type_notice = refuse
        timebox.sweep_background_check(con, lambda _m: None, now)
        check("untyped: a refused check leaves no event", checks() == 0, str(checks()))
        real_turn(now + 5)
        check("untyped: and the real turn after it is shown",
              dmn_delivery.bg_last_shown(con, "marina") == now + 5, str(dmn_delivery.bg_last_shown(con, "marina")))
        db.set_cursor(con, "notice_try:marina", 0)
        dmn_delivery.type_notice = blow_up
        try:
            timebox.sweep_background_check(con, lambda _m: None, now + 10)
            raised = False
        except RuntimeError:
            raised = True
        check("untyped: a typing that raises still raises", raised)
        check("untyped: and leaves no event either", checks() == 0, str(checks()))
        check("untyped: no check is recorded as asked", not db.get_cursor(con, "bg_check:marina"))
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_forgotten_background_work_is_left_when_something_is_owed_or_it_never_closes(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        S = idle_close.close_timeout_s("marina")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        _owe(con, "marina")
        now = _forgotten(con, "marina", S)
        timebox.sweep(con, lambda _m: None, now)
        check("forgotten: an agent waiting on an answer it asked is not told to stop",
              not [s for s in sent if s[0] == "term_marina"], str(sent))
        con.execute("UPDATE requests SET state = 'done'")
        db.add_request(con, "manager", "marina", "do it", None, "peer", "async", None, 0, None)
        timebox.sweep(con, lambda _m: None, now)
        check("forgotten: nor one with a request open", not [s for s in sent if s[0] == "term_marina"], str(sent))
        con.execute("UPDATE requests SET state = 'done'")
        idle_close.set_agent_close_timeout("marina", idle_close.NEVER)
        timebox.sweep(con, lambda _m: None, now)
        check("forgotten: nor one set to never close", not [s for s in sent if s[0] == "term_marina"], str(sent))
        idle_close.set_agent_close_timeout("marina", None)
        orcamod.typeable_now = lambda h: False
        now = _forgotten(con, "marina", S)
        timebox.sweep(con, lambda _m: None, now)
        check("forgotten: a prompt that could not be typed is not marked, so it is tried again",
              not db.get_cursor(con, "bg_prompt:marina") and not [s for s in sent if s[0] == "term_marina"])
        orcamod.typeable_now = lambda h: True
        timebox.sweep(con, lambda _m: None, now + timebox.NOTICE_RETRY_S + 1)
        check("forgotten: and it goes out once the terminal takes it", db.get_cursor(con, "bg_prompt:marina"))
        con.execute("DELETE FROM cursors WHERE name = 'bgwork:marina'")
        timebox.sweep(con, lambda _m: None, now + timebox.NOTICE_RETRY_S + 2)
        check("forgotten: the work ending clears the marks",
              not db.get_cursor(con, "bg_prompt:marina") and not db.get_cursor(con, "bg_stop_noted:marina"))
    finally:
        con.close()


def test_the_background_times_follow_the_agent_then_the_global_setting_then_the_default(home: Path) -> None:
    _world(home)
    check("times: with nothing set, the notice is the idle close and the grace is 5 minutes",
          (idle_close.bg_notice_s("marina"), idle_close.bg_grace_s("marina")) == (600, 300))
    idle_close.set_close_timeout(1200)
    check("times: the notice follows the idle close", idle_close.bg_notice_s("marina") == 1200)
    idle_close.set_bg_seconds("bg_notice_s", 400)
    idle_close.set_bg_seconds("bg_grace_s", 120)
    check("times: the global settings win over the defaults",
          (idle_close.bg_notice_s("dohyun"), idle_close.bg_grace_s("dohyun")) == (400, 120))
    idle_close.set_bg_seconds("bg_grace_s", 60, "marina")
    check("times: an agent's own wins over the global one, and only for it",
          idle_close.bg_grace_s("marina") == 60 and idle_close.bg_grace_s("dohyun") == 120
          and idle_close.bg_notice_s("marina") == 400)
    idle_close.set_bg_seconds("bg_grace_s", None, "marina")
    check("times: clearing it goes back to the global one", idle_close.bg_grace_s("marina") == 120)
    bad = []
    for args in (("bg_grace_s", 0), ("bg_grace_s", -5), ("other", 5), ("bg_notice_s", None)):
        try:
            idle_close.set_bg_seconds(*args)
            bad.append(args)
        except ValueError:
            pass
    check("times: zero, negative, another key and a global clear are refused", not bad, str(bad))


def test_a_forgotten_agent_is_stopped_past_its_window_when_its_own_times_are_shorter(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        idle_close.set_bg_seconds("bg_notice_s", 60, "marina")
        idle_close.set_bg_seconds("bg_grace_s", 30, "marina")
        st = types.SimpleNamespace(session_id="s")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = _forgotten(con, "marina", 60)
        timebox.sweep(con, lambda _m: None, now)
        check("own times: told at its own 60s, well inside the 10-minute idle close",
              len(sent) == 1 and "30s" in sent[0][1], str(sent))
        row = db.get_agent_state(con, "marina")
        check("own times: not closed before the grace is over",
              not dmn_lifecycle.should_close(con, "marina", "IDLE", row, now + 29, st))
        check("own times: closed once it is, though the idle window has not come",
              dmn_lifecycle.should_close(con, "marina", "IDLE", row, now + 30, st))
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
        _owe(con, "marina")
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


def test_a_running_agent_with_background_work_is_never_stale(home: Path) -> None:
    """T359: one foreground step can run 50 minutes with no hook event; an agent mid-turn is not
    told to its supervisor as one whose background work is forgotten, nor may its session be stopped."""
    sent = _world(home)
    con = db.connect()
    try:
        _owe(con, "marina")
        S, V = dmn_delivery.STALE_BG_S, dmn_delivery.STALE_BG_VETO_S
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="working")
        now = _stale_bg(con, "marina", S + 10, V + 5)
        timebox.sweep_stale_background(con, lambda _m: None, now)
        check("running: silent past the limit it is not stale, not told, and may not be stopped",
              not dmn_delivery.bg_stale(con, "marina", now) and not dmn_delivery.bg_may_stop(con, "marina", now)
              and not sent, str(sent))
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        timebox.sweep_stale_background(con, lambda _m: None, now)
        check("running: the same silence once its turn has ended is stale, and the supervisor is told",
              dmn_delivery.bg_stale(con, "marina", now) and len(sent) == 1, str(sent))
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
              and f"{len(dmn_delivery.NUDGE_SCHEDULE_S)} reminders" in sent[0][1], str(sent))
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


def test_the_backstop_clock_is_the_tasks_not_the_agents(home: Path) -> None:
    """T323: moving on from a task to another with no recorded wait in between must not carry the
    first one's clock, or its told mark, over to the second."""
    _world(home)
    con = db.connect()
    try:
        ok, msg, t1 = actions.task_new("manager", "marina", "first", "first")
        t2 = db.new_task(con, "second", "marina", "manager")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = int(time.time())
        S = timebox.STOP_WAIT_BACKSTOP_S
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now)
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + S + 1)
        check("task clock: the first task is told after its limit",
              db.get_cursor(con, "stop_wait_told:marina") == 1)

        db.set_current_task(con, "marina", t2)        # moved on: no wait was ever recorded in between
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + S + 2)
        check("task clock: the second task starts its own clock and is not yet told",
              db.get_cursor(con, "stop_wait_since:marina") == now + S + 2
              and not db.get_cursor(con, "stop_wait_told:marina"))
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + 2 * S + 1)
        check("task clock: and is not told at the first one's deadline",
              not db.get_cursor(con, "stop_wait_told:marina"))
        timebox.sweep_stop_wait_backstop(con, lambda _m: None, now + 2 * S + 3)
        check("task clock: it is told once its own limit has passed",
              db.get_cursor(con, "stop_wait_told:marina") == 1
              and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'stop_wait_stuck'").fetchone()[0] == 2)
    finally:
        con.close()


def test_the_founder_is_told_once_after_15_minutes_stuck_with_no_recorded_wait(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("manager", "marina", "do the thing", "do the thing")
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
        ok, msg, t_blocker = actions.task_new("manager", "marina", "unrelated", "unrelated")
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
        check("row: idle, background and how long", (word, detail) == ("IDLE", "background 12m"),
              str((word, detail)))
        _stale_bg(con, "marina", dmn_delivery.STALE_BG_S + 5)
        word, detail = tui.screen_state(con, db.get_agent_state(con, "marina"))
        check("row: and stale once the agent has shown nothing that long",
              word == "IDLE" and detail == "background 3h, stale", detail)
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


def test_the_notices_name_the_next_step_and_use_task_verbs(home: Path) -> None:
    """T399: the timebox notice says the request is still the target's; the task-timeout
    notice caps the title, names the holder and offers no release for a begun task; the
    nudges of a task use task verbs; the supervisor is pointed at a message, not `ocp ask`."""
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        rid = _delivered(con, "manager", "marina", 3000, timebox_s=3600)
        sid = _delivered(con, "manager", "dohyun", 3000, timebox_s=3600, kind="say")
        timebox.sweep(con, lambda _m: None, now + 700)
        texts = {h: t for h, t in sent}
        check("timebox: an ask is told it is still the target's and how to reach it",
              "still marina's" in texts.get("term_manager", "") or any(f"#{rid}" in t and "still marina's" in t for _, t in sent),
              str(sent))
        check("timebox: a say gets no next-step text", not any(f"#{sid}" in t and "nothing to do unless" in t for _, t in sent),
              str(sent))
        db.mark_timebox_noticed(con, rid)
        db.mark_timebox_noticed(con, sid)
        sent.clear()
        long_title = "Task timeout: every task gets a default " * 20
        tid = db.new_task(con, long_title, "marina", "manager", timeout_s=3600)
        _doing(con, tid, "marina")
        timebox.sweep(con, lambda _m: None, now)
        timebox.sweep(con, lambda _m: None, now + 3600)
        text = sent[0][1]
        check("task timeout: the title is cut and the holder named", len(text) < 400 and "held by marina" in text
              and long_title[:60].rstrip() in text and long_title[:120] not in text, text)
        check("task timeout: a task that was not begun can be released", f"ocp task release T{tid}" in text, text)
        sent.clear()
        db.add_event(con, "manager", "task_begin", {"task_id": tid})
        timebox.sweep(con, lambda _m: None, now + 3600 + 3600)
        check("task timeout: a begun task is not offered a release that would be refused",
              sent and "release" not in sent[-1][1] and "--timeout" in sent[-1][1], str(sent))
    finally:
        con.close()


def test_an_unasked_running_notice_points_at_a_message(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        now = int(time.time())
        db.set_agent_state(con, "marina", dmn_states.RUNNING, detail="active")
        timebox.sweep_unasked_running(con, lambda _m: None, now)
        timebox.sweep_unasked_running(con, lambda _m: None, now + timebox.UNASKED_RUNNING_S + 1)
        check("unasked running: the supervisor is pointed at a message, not ocp ask",
              sent and "ocp say marina" in sent[0][1] and "ocp ask" not in sent[0][1], str(sent))
    finally:
        con.close()


def test_a_forgotten_tasks_nudges_use_task_verbs(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        tid = db.new_task(con, "ship it", "marina", "manager", timeout_s=3600)
        rid = _delivered(con, "manager", "marina", dmn_delivery.NUDGE_SCHEDULE_S[-1] + 60, kind="task")
        db.link_request(con, tid, rid)
        db.set_cursor(con, "notify:marina", rid)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = int(time.time())
        row = db.get_agent_state(con, "marina")
        told: list[str] = []
        real = dmn_delivery.type_notice
        dmn_delivery.type_notice = lambda c, a, t: (told.append(t), True)[1]
        try:
            check("nudge: a forgotten task is nudged", dmn_delivery.nudge_forgotten_resolve(
                con, "marina", row, now, lambda _m: None), str(told))
        finally:
            dmn_delivery.type_notice = real
        check("nudge: it says the task is still theirs, names the task verbs and the recorded wait, not resolve",
              told and f"T{tid} (#{rid}) is still yours" in told[0] and f"ocp task done T{tid}" in told[0]
              and "--blocked-by" in told[0] and "ocp resolve" not in told[0], str(told))
        db.set_cursor(con, "nudge_count:marina", len(dmn_delivery.NUDGE_SCHEDULE_S))
        db.set_cursor(con, "nudge:marina", rid)
        sent.clear()
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, now)
        check("cap notice: a task head is pointed at release or a message, with no 'resolve another way'",
              sent and f"T{tid} (#{rid})" in sent[0][1] and f"ocp task release T{tid}" in sent[0][1]
              and "another way" not in sent[0][1] and "ocp ask" not in sent[0][1], str(sent))
    finally:
        con.close()


def test_the_capped_notice_offers_no_release_for_a_begun_task(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        tid = db.new_task(con, "ship it", "marina", "manager", timeout_s=3600)
        rid = _delivered(con, "manager", "marina", dmn_delivery.NUDGE_SCHEDULE_S[-1] + 60, kind="task")
        db.link_request(con, tid, rid)
        db.add_event(con, "manager", "task_begin", {"task_id": tid})
        db.set_cursor(con, "notify:marina", rid)
        db.set_cursor(con, "nudge:marina", rid)
        db.set_cursor(con, "nudge_count:marina", len(dmn_delivery.NUDGE_SCHEDULE_S))
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, int(time.time()))
        check("cap notice: a begun task is not offered a release that would be refused",
              sent and f"T{tid} (#{rid})" in sent[0][1] and "release" not in sent[0][1]
              and "ocp say marina" in sent[0][1], str(sent))
    finally:
        con.close()


def test_a_cancelled_task_handoff_names_the_task_state(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        tid = db.new_task(con, "ship it", "marina", "manager", timeout_s=3600)
        rid = _delivered(con, "manager", "marina", 60, kind="task")
        db.link_request(con, tid, rid)
        row = db.get_request(con, rid)
        ok, why = dmn_delivery.notify_cancelled(con, row, "no longer needed")
        check("cancel: the notice says what state the task is left in", ok and sent
              and f"T{tid} is" in sent[-1][1] and "ocp task list" in sent[-1][1], str((ok, why, sent)))
        plain = _delivered(con, "manager", "marina", 60)
        sent.clear()
        dmn_delivery.notify_cancelled(con, db.get_request(con, plain), "no longer needed")
        check("cancel: an ordinary request's notice is unchanged", sent and "ocp task list" not in sent[-1][1], str(sent))
    finally:
        con.close()


def test_a_stand_in_behind_an_ask_blocked_head_is_nudged_and_capped(home: Path) -> None:
    """T343/T346: the agent's own ask, linked to its head's task, is why that head is
    skipped - so it does not also excuse the request standing in for it. An unrelated
    open ask still does."""
    sent = _world(home)
    con = db.connect()
    try:
        span = dmn_delivery.NUDGE_SCHEDULE_S[0] + 60
        t_head = db.new_task(con, "needs a mockup", "marina", "manager", timeout_s=3600)
        r_head = _delivered(con, "manager", "marina", span + 60, kind="task")
        db.link_request(con, t_head, r_head)
        t_next = db.new_task(con, "other work", "marina", "manager", timeout_s=3600)
        r_next = _delivered(con, "manager", "marina", span, kind="task")
        db.link_request(con, t_next, r_next)
        ask = db.add_request(con, "marina", "dohyun", "does this mockup work?", None, "peer", "async",
                             None, 0, None)
        db.link_request(con, t_head, ask)
        db.set_cursor(con, "notify:marina", r_next)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        now = int(time.time())
        row = db.get_agent_state(con, "marina")
        found = dmn_delivery.forgotten_resolve(con, "marina", row, now)
        check("stand-in: told and open behind the ask-blocked head, it is what is forgotten",
              found and found["head"]["id"] == r_next, str(found))
        told: list[str] = []
        real = dmn_delivery.type_notice
        dmn_delivery.type_notice = lambda c, a, t: (told.append(t), True)[1]
        try:
            check("stand-in: nudged", dmn_delivery.nudge_forgotten_resolve(
                con, "marina", row, now, lambda _m: None) and f"T{t_next}" in told[0], str(told))
        finally:
            dmn_delivery.type_notice = real

        db.set_cursor(con, "nudge:marina", r_next)
        db.set_cursor(con, "nudge_count:marina", len(dmn_delivery.NUDGE_SCHEDULE_S))
        sent.clear()
        timebox.sweep_forgotten_resolve_capped(con, lambda _m: None, now)
        check("stand-in: capped, its supervisor is told", sent and f"T{t_next} (#{r_next})" in sent[0][1],
              str(sent))

        other = db.add_request(con, "marina", "dohyun", "unrelated question", None, "peer", "async",
                               None, 0, None)
        check("stand-in: an ask that is not why the head is blocked still means it is waiting",
              other and dmn_delivery.forgotten_resolve(con, "marina", row, now) is None)
    finally:
        con.close()


def test_a_block_that_clears_hands_the_head_back_and_it_is_the_one_nudged(home: Path) -> None:
    """T344/T347: once the ask is answered the true head is re-told, and it - not the
    stand-in left open behind it - is what a later silence nudges."""
    _world(home)
    con = db.connect()
    try:
        span = dmn_delivery.NUDGE_SCHEDULE_S[0] + 60
        t_head = db.new_task(con, "needs a mockup", "marina", "manager", timeout_s=3600)
        r_head = _delivered(con, "manager", "marina", span + 60, kind="task")
        db.link_request(con, t_head, r_head)
        t_next = db.new_task(con, "other work", "marina", "manager", timeout_s=3600)
        r_next = _delivered(con, "manager", "marina", span, kind="task")
        db.link_request(con, t_next, r_next)
        ask = db.add_request(con, "marina", "dohyun", "does this mockup work?", None, "peer", "async",
                             None, 0, None)
        db.link_request(con, t_head, ask)
        db.set_cursor(con, "notify:marina", r_next)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        con.execute("UPDATE requests SET state = 'done', verdict = 'yes', announced_at = ? WHERE id = ?",
                    (int(time.time()), ask))
        check("cleared: the true head is the one to work again",
              dmn_delivery.effective_task_head(con, "marina")["id"] == r_head)
        db.set_cursor(con, "notify:marina", r_head)     # what `deliver_queued` does when it re-tells it
        now = int(time.time())
        found = dmn_delivery.forgotten_resolve(con, "marina", db.get_agent_state(con, "marina"), now)
        check("cleared: the re-told head is what is nudged, the stand-in is behind it",
              found and found["head"]["id"] == r_head and found["behind"] == 1, str(found))
    finally:
        con.close()

def main() -> int:
    def tests(td):
        try:
            run(lambda: test_a_duration_needs_its_unit(Path(td) / 'tb0'))
            run(lambda: test_the_notices_name_the_next_step_and_use_task_verbs(Path(td) / 'tb17'))
            run(lambda: test_an_unasked_running_notice_points_at_a_message(Path(td) / 'tb18a'))
            run(lambda: test_a_forgotten_tasks_nudges_use_task_verbs(Path(td) / 'tb18'))
            run(lambda: test_the_capped_notice_offers_no_release_for_a_begun_task(Path(td) / 'tb18b'))
            run(lambda: test_a_cancelled_task_handoff_names_the_task_state(Path(td) / 'tb19'))
            run(lambda: test_a_stand_in_behind_an_ask_blocked_head_is_nudged_and_capped(Path(td) / 'tb20'))
            run(lambda: test_a_block_that_clears_hands_the_head_back_and_it_is_the_one_nudged(Path(td) / 'tb21'))
            run(lambda: test_ask_keeps_its_timebox(Path(td) / 'tb1'))
            run(lambda: test_a_folded_ask_keeps_its_timebox(Path(td) / 'tb1b'))
            run(lambda: test_the_asker_is_told_once_when_a_request_runs_past_its_timebox(Path(td) / 'tb2'))
            run(lambda: test_a_doing_tasks_timeout_starts_silently_then_repeats(Path(td) / 'tb2b'))
            run(lambda: test_a_task_opened_inside_ocp_gets_the_default_timeout_once_it_is_doing(Path(td) / 'tb2c'))
            run(lambda: test_a_doing_tasks_timeout_pauses_while_it_is_not_doing(Path(td) / 'tb2b2'))
            run(lambda: test_a_task_timeout_notice_goes_to_whoever_opened_it_not_the_owner(
                Path(td) / 'tb2c'))
            run(lambda: test_a_resolved_or_cancelled_request_stops_the_clock(Path(td) / 'tb3'))
            run(lambda: test_a_notice_that_cannot_be_typed_is_tried_again(Path(td) / 'tb4'))
            run(lambda: test_a_human_asker_is_recorded_not_typed_at(Path(td) / 'tb5'))
            run(lambda: test_a_supervisor_is_told_once_of_an_agent_running_with_nothing_asked(Path(td) / 'tb6'))
            run(lambda: test_an_open_request_or_a_short_run_is_not_unasked(Path(td) / 'tb7'))
            run(lambda: test_background_work_is_not_asked_about(Path(td) / 'tb8'))
            run(lambda: test_the_human_is_the_supervisor_of_an_agent_with_none(Path(td) / 'tb9'))
            run(lambda: test_background_work_is_stale_at_the_limit_and_may_stop_after_the_veto(Path(td) / 'tb10'))
            run(lambda: test_forgotten_background_work_is_told_to_finish_then_may_be_stopped(Path(td) / 'tb10a'))
            run(lambda: test_background_work_is_checked_every_n_minutes_naming_each_command(Path(td) / 'tb10n'))
            run(lambda: test_an_agent_that_answers_every_check_is_still_told_to_stop_and_closed_on_schedule(Path(td) / 'tb10m'))
            run(lambda: test_answering_checks_shorter_than_the_notice_does_not_hold_the_notice_off(Path(td) / 'tb10o'))
            run(lambda: test_a_turn_that_ends_before_a_check_is_written_is_not_its_answer(Path(td) / 'tb10p'))
            run(lambda: test_a_check_that_was_not_typed_leaves_no_event_to_mistake_a_real_turn_for_its_answer(Path(td) / 'tb10q'))
            run(lambda: test_the_close_that_stops_forgotten_work_records_why_once_in_tick_order(Path(td) / 'tb10f'))
            run(lambda: test_forgotten_background_work_is_left_when_something_is_owed_or_it_never_closes(Path(td) / 'tb10b'))
            run(lambda: test_the_background_times_follow_the_agent_then_the_global_setting_then_the_default(Path(td) / 'tb10c'))
            run(lambda: test_a_forgotten_agent_is_stopped_past_its_window_when_its_own_times_are_shorter(Path(td) / 'tb10e'))
            run(lambda: test_a_stale_agent_is_nudged_to_resolve_but_a_fresh_one_is_not(Path(td) / 'tb11'))
            run(lambda: test_the_supervisor_is_told_once_per_stale_stretch(Path(td) / 'tb12'))
            run(lambda: test_a_running_agent_with_background_work_is_never_stale(Path(td) / 'tbrun'))
            run(lambda: test_a_running_agent_with_background_work_and_nothing_queued_is_still_asked_about(Path(td) / 'tbrunbg'))
            run(lambda: test_the_supervisor_is_told_once_the_nudge_schedule_is_used_up(Path(td) / 'tb12a'))
            run(lambda: test_the_founder_is_told_once_after_15_minutes_stuck_with_no_recorded_wait(
                Path(td) / 'tb12b'))
            run(lambda: test_the_backstop_clock_is_the_tasks_not_the_agents(Path(td) / 'tbclock'))
            run(lambda: test_the_row_says_how_long_and_when_stale(Path(td) / 'tb13'))
            run(lambda: test_the_exit_dialog_is_answered_by_stopping_only_over_stale_work_it_can_read(Path(td) / 'tb14'))
            run(lambda: test_migration_adds_the_timebox_columns_to_an_older_requests_table(Path(td) / 'tb15'))
            run(lambda: test_stopping_key_presses_only_the_measured_dialog(Path(td) / 'tb16'))
        finally:
            dmn_delivery.session_present = REAL_SESSION_PRESENT
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
