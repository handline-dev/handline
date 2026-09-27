"""Tests: the Stop-hook gate (T288) - stopgate.decide, its wiring into
record_from_stdin, and the founder-only enforce switch (T298).

Run on its own: python3 tests/session/test_stopgate.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.session import stopgate  # noqa: E402
from ocp.infra import protected  # noqa: E402


def _open_task(home: Path) -> int:
    _board_world(home)
    ok, msg, tid = actions.task_new("manager", "worker", "doing something")
    check("setup: the task opens and is delivered", ok, msg)
    return tid


def test_stop_wait_reason_becomes_a_reason_naming_the_task(home: Path) -> None:
    tid = _open_task(home)
    block, reason = stopgate.decide("worker", {"session_id": "s1"})
    check("warn-only by default: never blocks", block is False, (block, reason))
    check("but still gives a reason naming the current task",
          reason is not None and f"T{tid}" in reason, reason)
    con = db.connect()
    try:
        rows = con.execute("SELECT payload FROM events WHERE agent = 'worker' "
                           "AND kind = 'stop_gate'").fetchall()
        check("and logs one stop_gate event with the reason and enforced=false",
              len(rows) == 1 and json.loads(rows[0]["payload"])["enforced"] is False,
              [dict(r) for r in rows])
    finally:
        con.close()

    stopgate.set_enforced(True)
    # A new session, so this is a first sighting again - the same task.
    block2, reason2 = stopgate.decide("worker", {"session_id": "s2"})
    check("enforced: now actually blocks", block2 is True, (block2, reason2))
    check("still naming the same current task", f"T{tid}" in reason2, reason2)


def test_stop_hook_active_and_once_per_session_guard_the_loop(home: Path) -> None:
    tid = _open_task(home)
    block, reason = stopgate.decide("worker", {"session_id": "s1", "stop_hook_active": True})
    check("stop_hook_active: never even reasons about it", (block, reason) == (False, None),
          (block, reason))

    block, reason = stopgate.decide("worker", {"session_id": "s1"})
    check("first sighting this session: a reason", reason is not None, reason)
    block, reason = stopgate.decide("worker", {"session_id": "s1"})
    check("same session, same current task: not reported again", (block, reason) == (False, None),
          (block, reason))

    block, reason = stopgate.decide("worker", {"session_id": "s2"})
    check("a new session: reported again for the same task", reason is not None, reason)

    ok, msg, tid2 = actions.task_new("manager", "worker", "another task")
    check("setup: worker already holds one, so this stays queued behind it", ok, msg)
    block, reason = stopgate.decide("worker", {"session_id": "s2"})
    check("same session, task unchanged still: not reported a third time",
          (block, reason) == (False, None), (block, reason))


def test_stop_gate_exempts_founder_chat_the_wall_and_a_closed_session(home: Path) -> None:
    _open_task(home)
    check("no agent at all (founder's own chat): exempt",
          stopgate.decide(None, {"session_id": "s1"}) == (False, None))
    check("the founder's own name: exempt",
          stopgate.decide(askmod.HUMAN, {"session_id": "s1"}) == (False, None))

    con = db.connect()
    try:
        db.set_closed(con, "worker", None)
    finally:
        con.close()
    check("a session the daemon has already closed: exempt",
          stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
    con = db.connect()
    try:
        db.clear_closed(con, "worker")
        db.add_event(con, "worker", "StopFailure", {"error": "rate_limit", "limit": "rate_limit"})
    finally:
        con.close()
    check("at its usage limit: exempt",
          stopgate.decide("worker", {"session_id": "s1"}) == (False, None))


def test_the_enforce_switch_is_the_founders_to_flip(home: Path) -> None:
    _board_world(home)
    check("off by default", stopgate.enforced() is False)
    os.environ["OCP_AGENT"] = "manager"
    try:
        stopgate.set_enforced(True)
        check("never set", False)
    except protected.NotTheFounder:
        pass
    finally:
        del os.environ["OCP_AGENT"]
    rc, out = _cli(["hooks", "gate"], as_agent=None)
    check("founder: show", rc == 0 and "off (warn-only)" in out, out)
    rc, out = _cli(["hooks", "gate", "on"], as_agent=None)
    check("founder: set on", rc == 0 and "gate: on" in out, out)
    check("enforced() now reads True", stopgate.enforced() is True)
    rc, out = _cli(["hooks", "gate", "on"])
    check("an agent session is refused, not the founder's to set", rc == 1, out)


def test_record_from_stdin_blocks_only_when_enforced_and_never_marks_idle(home: Path) -> None:
    tid = _open_task(home)
    os.environ["OCP_AGENT"] = "worker"
    try:
        con = db.connect()
        try:
            db.set_agent_state(con, "worker", dmn_states.RUNNING)
        finally:
            con.close()

        buf = io.StringIO()
        raw = json.dumps({"hook_event_name": "Stop", "session_id": "s1"})
        real_in = sys.stdin
        sys.stdin = io.StringIO(raw)
        try:
            with contextlib.redirect_stdout(buf):
                evmod.record_from_stdin(["Stop"])
        finally:
            sys.stdin = real_in
        check("warn-only: lets the stop through", buf.getvalue().strip() == "{}", buf.getvalue())
        con = db.connect()
        try:
            check("warn-only: the ordinary Stop capture ran - worker reads IDLE",
                  db.get_agent_state(con, "worker")["state"] == dmn_states.IDLE)
        finally:
            con.close()

        del os.environ["OCP_AGENT"]
        stopgate.set_enforced(True)
        os.environ["OCP_AGENT"] = "worker"
        con = db.connect()
        try:
            db.set_agent_state(con, "worker", dmn_states.RUNNING)
        finally:
            con.close()
        buf2 = io.StringIO()
        raw2 = json.dumps({"hook_event_name": "Stop", "session_id": "s2"})
        sys.stdin = io.StringIO(raw2)
        try:
            with contextlib.redirect_stdout(buf2):
                evmod.record_from_stdin(["Stop"])
        finally:
            sys.stdin = real_in
        out = json.loads(buf2.getvalue())
        check("enforced: blocks with a reason", out.get("decision") == "block"
              and f"T{tid}" in out.get("reason", ""), buf2.getvalue())
        con = db.connect()
        try:
            check("enforced: the ordinary Stop capture never ran - worker still reads RUNNING, "
                  "since the turn did not really end",
                  db.get_agent_state(con, "worker")["state"] == dmn_states.RUNNING)
        finally:
            con.close()
    finally:
        del os.environ["OCP_AGENT"]


def main() -> int:
    def tests(td):
        run(lambda: test_stop_wait_reason_becomes_a_reason_naming_the_task(Path(td) / 'sg0'))
        run(lambda: test_stop_hook_active_and_once_per_session_guard_the_loop(Path(td) / 'sg1'))
        run(lambda: test_stop_gate_exempts_founder_chat_the_wall_and_a_closed_session(Path(td) / 'sg2'))
        run(lambda: test_the_enforce_switch_is_the_founders_to_flip(Path(td) / 'sg3'))
        run(lambda: test_record_from_stdin_blocks_only_when_enforced_and_never_marks_idle(Path(td) / 'sg4'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
