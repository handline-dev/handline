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
    (home / "team.yaml").write_text(TEAM)
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


def main() -> int:
    def tests(td):
        for i, fn in enumerate((
                test_a_duration_needs_its_unit,
                test_ask_keeps_its_timebox,
                test_the_asker_is_told_once_when_a_request_runs_past_its_timebox,
                test_a_resolved_or_cancelled_request_stops_the_clock,
                test_a_notice_that_cannot_be_typed_is_tried_again,
                test_a_human_asker_is_recorded_not_typed_at,
                test_a_supervisor_is_told_once_of_an_agent_running_with_nothing_asked,
                test_an_open_request_or_a_short_run_is_not_unasked,
                test_background_work_counts_as_running_and_the_limit_is_configurable,
                test_the_human_is_the_supervisor_of_an_agent_with_none)):
            try:
                run(lambda fn=fn, i=i: fn(Path(td) / f"tb{i}"))
            finally:
                dmn_delivery.session_present = REAL_SESSION_PRESENT
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
