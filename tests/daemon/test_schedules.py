"""Tests (T605): cron schedules per agent - the expression, the setting, and the daemon typing the message when due.

Run on its own: python3 tests/daemon/test_schedules.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.daemon import schedules  # noqa: E402
from ocp.infra import cron  # noqa: E402


def _at(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi).timestamp())


def _world(home: Path) -> list:
    """backend can be typed at: a terminal, a live session, a clear screen."""
    _daemon_home(home, 20)
    sessions_are("backend")
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.screen_blocker = lambda h: None
    orcamod.typeable_now = lambda h: False
    orcamod.live_terminals = lambda: {"term_x"}
    sent: list[tuple[str, str]] = []
    orcamod.send = lambda handle, text, enter=True: (sent.append((handle, text)), (True, ""))[1]
    return sent


def _give(agent: str, items: list[dict]) -> None:
    cfg = agents.read(agent)
    cfg["schedules"] = json.dumps(items)
    agents.write(agent, cfg)


def test_a_cron_expression_names_its_minutes(home: Path) -> None:
    t = _at(2026, 9, 30, 21, 10)
    nxt = lambda expr, ts=t: datetime.fromtimestamp(cron.next_after(cron.parse(expr), ts))
    check("cron: every 30 minutes lands on the half hour", nxt("*/30 * * * *") == datetime(2026, 9, 30, 21, 30))
    check("cron: a time already on the minute is not its own next", nxt("*/30 * * * *", _at(2026, 9, 30, 21, 30))
          == datetime(2026, 9, 30, 22, 0))
    check("cron: a monthly one is the first of the next month", nxt("0 9 1 * *") == datetime(2026, 10, 1, 9, 0))
    check("cron: weekdays skip the days between", nxt("15 8 * * 1-5", _at(2026, 10, 2, 9, 0))
          == datetime(2026, 10, 5, 8, 15))
    check("cron: with both day fields set a day matches on either",
          nxt("0 9 1 * 1", _at(2026, 9, 30, 10, 0)) == datetime(2026, 10, 1, 9, 0)
          and nxt("0 9 1 * 1", _at(2026, 10, 1, 10, 0)) == datetime(2026, 10, 5, 9, 0))
    check("cron: Sunday is 0 or 7", nxt("0 9 * * 7") == nxt("0 9 * * 0"))
    check("cron: a day that never comes has no next time", cron.next_after(cron.parse("0 0 31 2 *"), t) is None)
    for bad in ("* * * *", "61 * * * *", "*/0 * * * *", "a * * * *", "5-2 * * * *", "* * * * * *"):
        try:
            cron.parse(bad)
            check(f"cron: {bad!r} is refused", False)
        except ValueError:
            check(f"cron: {bad!r} is refused", True)


def test_the_schedules_setting_is_validated_and_set_from_the_command_line(home: Path) -> None:
    _daemon_home(home, 20)
    check("setting: none is none", schedules.parse(None) == [] and schedules.of("backend") == [])
    check("setting: a good one is kept, its message on one line",
          schedules.parse([{"cron": "*/30  * * * *", "message": "a\n b"}]) == [{"cron": "*/30 * * * *", "message": "a b"}])
    for bad in ('[{"cron": "* * * *", "message": "x"}]', '[{"cron": "* * * * *", "message": " "}]',
                '[{"cron": "* * * * *"}]', '{"cron": 1}', "not json"):
        try:
            schedules.parse(bad)
            check(f"setting: {bad!r} is refused", False)
        except ValueError:
            check(f"setting: {bad!r} is refused", True)
    rc, out = _cli(["schedule", "add", "--agent", "backend", "--cron", "*/30 * * * *", "--message", "patrol: check ocp team"],
                   as_agent=None)
    check("cli: add writes agent.yaml and shows it", rc == 0 and "1. */30 * * * *" in out and "patrol" in out, out)
    check("cli: and the daemon reads it back", schedules.of("backend")
          == [{"cron": "*/30 * * * *", "message": "patrol: check ocp team"}])
    rc, out = _cli(["schedule", "add", "--agent", "backend", "--cron", "nope", "--message", "x"], as_agent=None)
    check("cli: a bad expression is refused and nothing is written", rc == 1 and len(schedules.of("backend")) == 1, out)
    rc, out = _cli(["schedule", "list"], as_agent=None)
    check("cli: list names every agent that has one", rc == 0 and "backend 1." in out, out)
    rc, out = _cli(["schedule", "remove", "--agent", "backend", "--index", "1"], as_agent=None)
    check("cli: remove drops it", rc == 0 and schedules.of("backend") == [], out)


def test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        item = {"cron": "*/30 * * * *", "message": "patrol: check ocp team"}
        _give("backend", [item])
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        t0 = _at(2026, 9, 30, 21, 10)
        schedules.sweep(con, lambda _m: None, t0)
        check("due: seen for the first time it counts from now - nothing is typed", sent == [], str(sent))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 29))
        check("due: nothing before the time", sent == [])
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 30))
        check("due: at the time the message is typed, saying why",
              len(sent) == 1 and "[schedule */30 * * * *] patrol: check ocp team" in sent[0][1], str(sent))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 31))
        check("due: and only once for that time", len(sent) == 1, str(sent))
        check("due: recorded as an event", con.execute("SELECT COUNT(*) FROM events WHERE kind = 'schedule_fired'").fetchone()[0] == 1)

        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 0))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 31))
        check("running: a time that comes while it works is not typed", len(sent) == 1, str(sent))
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 40))
        check("running: once it stops the missed times are one message, not a pile",
              len(sent) == 2, str(sent))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 41))
        check("running: and that is the end of them", len(sent) == 2, str(sent))

        _give("backend", [dict(item, message="a new message")])
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 23, 0))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 23, 30))
        check("edit: changing the message makes it a new schedule, counted from when it was seen",
              len(sent) == 3 and "a new message" in sent[2][1], str(sent))
    finally:
        con.close()


def test_a_schedule_never_piles_up_behind_an_unread_message(home: Path) -> None:
    sent = _world(home)
    orcamod.screen_blocker = lambda h: types.SimpleNamespace(reason=lambda: "a permission prompt")
    con = db.connect()
    try:
        _give("backend", [{"cron": "*/30 * * * *", "message": "patrol"}])
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 10))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 30))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 0))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 30))
        rows = con.execute("SELECT * FROM requests WHERE requester = 'schedule'").fetchall()
        check("pile-up: a blocked screen holds the first, and no second is written behind it",
              sent == [] and len(rows) == 1 and not rows[0]["delivered_at"], str((sent, [dict(r) for r in rows])))
        orcamod.screen_blocker = lambda h: None
        dmn_messaging.tell_undelivered(con, "backend", lambda _m: None)
        check("pile-up: it is typed once the screen clears", len(sent) == 1, str(sent))
    finally:
        con.close()


def test_a_long_silence_is_still_one_message(home: Path) -> None:
    """T605 (auditor #7079): a minute schedule after a gap longer than any walk of its times must not
    fire again the tick after the first message is delivered - the cursor is the minute handled, not a slot."""
    sent = _world(home)
    con = db.connect()
    try:
        _give("backend", [{"cron": "* * * * *", "message": "patrol"}])
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        t0 = _at(2026, 9, 30, 21, 10)
        schedules.sweep(con, lambda _m: None, t0)
        later = t0 + 3 * 86400 + 30
        schedules.sweep(con, lambda _m: None, later)
        schedules.sweep(con, lambda _m: None, later + 20)
        check("silence: three days of missed minutes are one message, and no second one follows in that minute",
              len(sent) == 1, str(len(sent)))
        schedules.sweep(con, lambda _m: None, later + 70)
        check("silence: the next minute is the next message", len(sent) == 2, str(len(sent)))
    finally:
        con.close()


def test_a_removed_schedule_added_back_is_new_and_a_bad_value_is_said(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        item = {"cron": "*/30 * * * *", "message": "patrol"}
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        _give("backend", [item])
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 10))
        _give("backend", [])
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 20))
        check("removed: its cursors are dropped",
              con.execute("SELECT COUNT(*) FROM cursors WHERE name LIKE 'schedule_%'").fetchone()[0] == 0)
        _give("backend", [item])
        schedules.sweep(con, lambda _m: None, _at(2026, 10, 2, 21, 10))
        check("removed: added back later it counts from when it is seen again, not from before", sent == [], str(sent))

        cfg = agents.read("backend")
        cfg["schedules"] = "not json"
        agents.write("backend", cfg)
        rc, out = _cli(["schedule", "list", "--agent", "backend"], as_agent=None)
        check("bad value: list says so, not 'no schedules'", rc == 1 and "not JSON" in out, out)
        rc, out = _cli(["schedule", "add", "--agent", "backend", "--cron", "0 * * * *", "--message", "x"], as_agent=None)
        check("bad value: add refuses to replace it silently", rc == 1 and agents.read("backend")["schedules"] == "not json", out)
        check("bad value: the sweep just leaves it alone", schedules.of("backend") == [])
    finally:
        con.close()


def test_a_due_schedule_wakes_a_closed_session(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    woken: list[str] = []
    real = dmn_lifecycle.wake_agent
    try:
        _give("backend", [{"cron": "0 * * * *", "message": "patrol"}])
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="closed")
        db.set_closed(con, "backend", "sess-0")
        dmn_lifecycle.wake_agent = lambda c, a, log, **k: (woken.append(a), (True, "woken"))[1]
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 21, 10))
        schedules.sweep(con, lambda _m: None, _at(2026, 9, 30, 22, 0))
        check("closed: a due schedule wakes the closed session to read it", woken == ["backend"], str(woken))
    finally:
        dmn_lifecycle.wake_agent = real
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_cron_expression_names_its_minutes(Path(td) / "c1"))
        run(lambda: test_the_schedules_setting_is_validated_and_set_from_the_command_line(Path(td) / "c2"))
        run(lambda: test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent(Path(td) / "c3"))
        run(lambda: test_a_schedule_never_piles_up_behind_an_unread_message(Path(td) / "c4"))
        run(lambda: test_a_due_schedule_wakes_a_closed_session(Path(td) / "c5"))
        run(lambda: test_a_long_silence_is_still_one_message(Path(td) / "c6"))
        run(lambda: test_a_removed_schedule_added_back_is_new_and_a_bad_value_is_said(Path(td) / "c7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
