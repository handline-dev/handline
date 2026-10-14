"""Tests: the Stop-hook gate (T288) - stopgate.decide, its wiring into
record_from_stdin, and the founder-only enforce switch (T298).

Run on its own: python3 tests/session/test_stopgate.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import stopgate  # noqa: E402
from handline.infra import hookspool, protected  # noqa: E402


def _open_task(home: Path) -> int:
    _board_world(home)
    ok, msg, tid = actions.task_new("manager", "worker", "doing something", "doing something")
    check("setup: the task opens and is delivered", ok, msg)
    return tid


def test_stop_wait_reason_becomes_a_reason_naming_the_task(home: Path) -> None:
    tid = _open_task(home)
    block, reason = stopgate.decide("worker", {"session_id": "s0"})
    check("T480: on by default - a stop with no recorded wait is refused", block is True, (block, reason))
    check("T480: the refusal says why and names the commands that fix it",
          reason is not None and "Stop refused" in reason and f"T{tid} is current with no recorded wait" in reason
          and f"`hl task handoff T{tid} <next>" in reason and f"`hl task done T{tid}`" in reason
          and f"`hl task edit T{tid} --blocked-by T<m>`" in reason, reason)
    stopgate.set_enforced(False)
    block, reason = stopgate.decide("worker", {"session_id": "s1"})
    check("switched off by the founder: never blocks", block is False, (block, reason))
    check("but still gives a reason naming the current task",
          reason is not None and f"T{tid}" in reason, reason)
    check("T401 item 18: names task handoff as the finish step, not task done, "
          "and drops the open-say wait",
          "hl task handoff" in reason and "hl say" not in reason, reason)
    con = db.connect()
    try:
        rows = con.execute("SELECT payload FROM events WHERE agent = 'worker' "
                           "AND kind = 'stop_gate'").fetchall()
        check("and logs a stop_gate event with the reason and enforced=false for that one",
              len(rows) == 2 and json.loads(rows[1]["payload"])["enforced"] is False,
              [dict(r) for r in rows])
    finally:
        con.close()

    stopgate.set_enforced(True)
    # A new session, so this is a first sighting again - the same task.
    block2, reason2 = stopgate.decide("worker", {"session_id": "s2"})
    check("enforced: now actually blocks", block2 is True, (block2, reason2))
    check("still naming the same current task", f"T{tid}" in reason2, reason2)


def test_a_roster_proposal_waiting_on_the_founder_is_a_recorded_wait(home: Path) -> None:
    _open_task(home)
    ok, said = hr.propose("add", "helper", repo="/repo", requester="worker", why="more hands")
    check("setup: the worker raises a roster proposal", ok, said)
    check("a proposal of its own waiting on the founder lets its turn end",
          stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
    hr.decide(said.split()[1], "rejected", askmod.HUMAN)
    block, reason = stopgate.decide("worker", {"session_id": "s2"})
    check("once decided it is no wait: the current task needs its own", block is True and reason, (block, reason))


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

    ok, msg, tid2 = actions.task_new("manager", "worker", "another task", "another task")
    check("setup: worker already holds one, so this stays queued behind it", ok, msg)
    block, reason = stopgate.decide("worker", {"session_id": "s2"})
    check("same session, task unchanged still: not reported a third time",
          (block, reason) == (False, None), (block, reason))

    con = db.connect()
    try:
        db.set_current_task(con, "worker", tid2)
    finally:
        con.close()
    block, reason = stopgate.decide("worker", {"session_id": "s2"})
    check("T324: same session, a new current task: reported again, naming it",
          block is True and reason is not None and f"T{tid2} is current" in reason, (block, reason))
    block, reason = stopgate.decide("worker", {"session_id": "s2"})
    check("T324: and only once for that one", (block, reason) == (False, None), (block, reason))


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


def _told(con, requester: str, body: str, notice: bool = False, parent: int | None = None) -> int:
    rid = db.add_request(con, requester, "worker", body, None, None, "fire_and_forget", "say", 0, parent,
                         notice=notice)
    con.execute("UPDATE requests SET delivered_at = 1 WHERE id = ?", (rid,))
    return rid


def test_a_question_left_unanswered_is_reminded_once_when_the_turn_ends(home: Path) -> None:
    _board_world(home)
    con = db.connect()
    try:
        rid = _told(con, db.HUMAN, "다음에 뭐 할 거예요")
        block, reason = stopgate.decide("worker", {"session_id": "s1"})
        check("reminder: the founder's message with no answer is reminded, naming the command to answer it",
              block is True and f"#{rid}" in reason and f"hl say {db.HUMAN} --parent {rid}" in reason
              and "what you write in the terminal reaches no one" in reason, (block, reason))
        block, reason = stopgate.decide("worker", {"session_id": "s1"})
        con2 = db.connect()
        try:
            logged = [json.loads(r["payload"]) for r in con2.execute(
                "SELECT payload FROM events WHERE agent = 'worker' AND kind = 'stop_gate'")]
        finally:
            con2.close()
        check("reminder: the reminder is logged as a stop_gate event naming the message",
              logged == [{"answer_reminder": rid, "enforced": True}], str(logged))
        check("reminder: once per message, never repeated", (block, reason) == (False, None), (block, reason))
        block, reason = stopgate.decide("worker", {"session_id": "s2"})
        check("reminder: not even in a new session", (block, reason) == (False, None), (block, reason))
        rid2 = _told(con, db.HUMAN, "and this one?")
        block, reason = stopgate.decide("worker", {"session_id": "s2"})
        check("reminder: a new message is a new reminder", block is True and f"#{rid2}" in reason, (block, reason))
    finally:
        con.close()
    block, reason = stopgate.decide("worker", {"session_id": "s3", "stop_hook_active": True})
    check("reminder: a stop that is itself the reminder's turn is never blocked", (block, reason) == (False, None))


def test_no_reminder_when_it_answered_or_the_message_asks_nothing(home: Path) -> None:
    _board_world(home)
    con = db.connect()
    try:
        rid = _told(con, "manager", "which branch is it on?")
        db.add_request(con, "worker", "manager", "main", None, None, "fire_and_forget", "say", 0, None)
        check("no reminder: it said something to the sender after the question",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        _told(con, "manager", "FYI the build is green")
        check("no reminder: an agent's message with no question asks nothing",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        _told(con, db.NOTICE_REQUESTER, "T5 done by x: is it good?", notice=True)
        check("no reminder: a notice never asks, whatever it says",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        _told(con, db.NOTICE_REQUESTER, "T6 unblocked - is it?")
        check("no reminder: nor does any line from the task board",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        rid2 = _told(con, "manager", "can you look at the retry loop？")
        block, reason = stopgate.decide("worker", {"session_id": "s1"})
        check("reminder: an agent's question, even with a full-width mark, is reminded and addressed to it",
              block is True and f"hl say manager --parent {rid2}" in reason, (block, reason))
    finally:
        con.close()


def test_a_reply_answers_the_agent_and_asks_nothing_whoever_sent_it(home: Path) -> None:
    _board_world(home)
    con = db.connect()
    try:
        ask = db.add_request(con, "worker", db.HUMAN, "may I?", None, None, "fire_and_forget", "say", 0, None)
        _told(con, db.HUMAN, "handed to orcapipe", parent=ask)
        check("reply: the machine's 'handed to' answer to the agent's own ask gives no reminder",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        _told(con, db.HUMAN, "cleared - it can be given its work again", parent=ask)
        check("reply: nor does a 'cleared' one", stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        _told(con, db.HUMAN, "approve? yes", parent=ask)
        check("reply: nor a reply with a question mark in it",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        older = _told(con, db.HUMAN, "what is the status?")
        _told(con, db.HUMAN, "ok", parent=older)
        check("reply: and the question it answers is not found behind it",
              stopgate.decide("worker", {"session_id": "s1"}) == (False, None))
        con2 = db.connect()
        try:
            check("reminder: no event is logged for any of them",
                  con2.execute("SELECT 1 FROM events WHERE agent = 'worker' AND kind = 'stop_gate'").fetchone() is None)
        finally:
            con2.close()
    finally:
        con.close()


def test_the_task_wait_comes_first_and_the_reminder_waits_for_it(home: Path) -> None:
    tid = _open_task(home)
    con = db.connect()
    try:
        rid = _told(con, db.HUMAN, "status?")
    finally:
        con.close()
    block, reason = stopgate.decide("worker", {"session_id": "s1"})
    check("order: a current task with no wait is the reason given, the question is not mentioned",
          block is True and f"T{tid}" in reason and f"#{rid}" not in reason, (block, reason))
    con = db.connect()
    try:
        db.set_current_task(con, "worker", None)
    finally:
        con.close()
    block, reason = stopgate.decide("worker", {"session_id": "s1"})
    check("order: and the reminder is still owed once the task no longer holds it",
          block is True and f"#{rid}" in reason, (block, reason))


def test_the_enforce_switch_is_the_founders_to_flip(home: Path) -> None:
    _board_world(home)
    check("on by default", stopgate.enforced() is True)
    os.environ["OCP_AGENT"] = "manager"
    try:
        stopgate.set_enforced(True)
        check("never set", False)
    except protected.NotTheFounder:
        pass
    finally:
        del os.environ["OCP_AGENT"]
    rc, out = _cli(["hooks", "gate", "stop-wait"], as_agent=None)
    check("founder: show", rc == 0 and "gate: on" in out, out)
    rc, out = _cli(["hooks", "gate", "stop-wait", "off"], as_agent=None)
    check("founder: set off", rc == 0 and "off (warn-only)" in out and stopgate.enforced() is False, out)
    rc, out = _cli(["hooks", "gate", "stop-wait", "on"], as_agent=None)
    check("founder: set on", rc == 0 and "gate: on" in out, out)
    check("enforced() now reads True", stopgate.enforced() is True)
    rc, out = _cli(["hooks", "gate", "stop-wait", "on"])
    check("an agent session is refused, not the founder's to set", rc == 1, out)


def test_record_from_stdin_blocks_only_when_enforced_and_never_marks_idle(home: Path) -> None:
    tid = _open_task(home)
    stopgate.set_enforced(False)
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
            drain_spool()
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


# Run in a fresh interpreter with handline.daemon an empty package, so any import of a module of it is seen.
_HOOK_REACH = """
import io, json, sys, types
pkg = types.ModuleType("handline.daemon"); pkg.__path__ = []
import handline; sys.modules["handline.daemon"] = pkg
from handline.session import events
for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart"):
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": ev, "tool_name": "Bash", "tool_input": {"command": "ls"}}))
    events.record_from_stdin([ev])
print(json.dumps(sorted(m for m in sys.modules if m.startswith("handline.daemon."))))
"""


def test_the_hook_and_its_stop_gate_load_nothing_of_the_python_daemon(home: Path) -> None:
    _open_task(home)
    env = dict(os.environ, OCP_AGENT="worker")
    r = subprocess.run([sys.executable, "-c", _HOOK_REACH], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    lines = r.stdout.splitlines()
    check("hook: the gate ran and the events were recorded, with no daemon module loaded",
          r.returncode == 0 and '"decision": "block"' in r.stdout and lines[-1] == "[]"
          and hookspool.path().exists(), r.stdout + r.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_stop_wait_reason_becomes_a_reason_naming_the_task(Path(td) / 'sg0'))
        run(lambda: test_a_roster_proposal_waiting_on_the_founder_is_a_recorded_wait(Path(td) / 'sg9'))
        run(lambda: test_stop_hook_active_and_once_per_session_guard_the_loop(Path(td) / 'sg1'))
        run(lambda: test_stop_gate_exempts_founder_chat_the_wall_and_a_closed_session(Path(td) / 'sg2'))
        run(lambda: test_a_question_left_unanswered_is_reminded_once_when_the_turn_ends(Path(td) / 'sg5'))
        run(lambda: test_no_reminder_when_it_answered_or_the_message_asks_nothing(Path(td) / 'sg6'))
        run(lambda: test_the_task_wait_comes_first_and_the_reminder_waits_for_it(Path(td) / 'sg7'))
        run(lambda: test_a_reply_answers_the_agent_and_asks_nothing_whoever_sent_it(Path(td) / 'sg8'))
        run(lambda: test_the_enforce_switch_is_the_founders_to_flip(Path(td) / 'sg3'))
        run(lambda: test_record_from_stdin_blocks_only_when_enforced_and_never_marks_idle(Path(td) / 'sg4'))
        run(lambda: test_the_hook_and_its_stop_gate_load_nothing_of_the_python_daemon(Path(td) / 'sg10'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
