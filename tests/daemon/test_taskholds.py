"""Tests (T721): a task waiting for its owner always shows why, and one whose owner could take it
and never does is told to the founder once.

Run on its own: python3 tests/daemon/test_taskholds.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp import actions  # noqa: E402
from ocp.daemon import delivery, evaluate, lifecycle, locks, taskholds, taskqueue  # noqa: E402


REAL_PUSH = askmod.push


def _world(home: Path) -> None:
    """manager and worker; worker idle in a live terminal with a session, nothing on its screen."""
    _board_world(home)
    delivery._NOTED.clear()
    delivery._FAILED.clear()
    actions._WORLD["at"] = 0.0
    sessions_are("worker")
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.live_terminals = lambda: {"term_w"}
    orcamod.screen_blocker = lambda h: None
    orcamod.typeable_now = lambda h: True
    locks.running_elsewhere = lambda: 4242
    # task_new would type the task at once; these tests are about the ones not yet typed.
    askmod.push = lambda _con, agent, rid=None: ""
    con = db.connect()
    try:
        db.set_terminal(con, "worker", "term_w", "wt")
        db.set_account(con, "worker", "alpha")
        db.set_agent_state(con, "worker", "IDLE")
    finally:
        con.close()


def _waiting(con, title: str = "waits") -> int:
    """A queued task of worker's with nothing out yet, behind whatever worker holds."""
    t = db.new_task(con, title, "worker", "manager")
    actions.set_queued(con, t, "manager", True)
    return t


def _why(con, tid: int) -> tuple[str, bool] | None:
    actions._WORLD["at"] = 0.0
    return taskholds.task_reason(con, db.get_task(con, tid), int(time.time()))


def _said(con, tid: int) -> str:
    actions._WORLD["at"] = 0.0
    taskholds.sweep(con, lambda _m: None, int(time.time()))
    return (taskholds.wait_line(con, db.get_task(con, tid), actions.task_state(con, db.get_task(con, tid))) or "")


def _reports(con) -> int:
    return con.execute("SELECT COUNT(*) FROM requests WHERE target = ? AND fyi = 1", (db.HUMAN,)).fetchone()[0]


def test_a_task_behind_the_owners_current_one_says_which(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        ok, msg, first = actions.task_new("manager", "worker", "first", "first")
        t = _waiting(con)
        why = _why(con, t)
        check("behind: names the task the owner holds", why is not None and why[0].startswith(f"behind T{first}")
              and not why[1], str(why))
        check("behind: and the line beside the task says it", f"behind T{first}" in _said(con, t))
        child = db.new_task(con, "review it", "team.auditor", "worker", parent_task=first)
        db.add_block(con, first, child)
        why = _why(con, t)
        check("behind: an owner waiting on a child says so", why is not None and f"waiting for T{child}" in why[0], str(why))
    finally:
        con.close()


def test_a_blocked_task_names_what_blocks_it(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        t = _waiting(con)
        dep = db.new_task(con, "first", "manager", "manager")
        db.add_block(con, t, dep)
        why = _why(con, t)
        check("blocked: names the blocker and its owner", why == (f"blocked by T{dep} (manager)", False), str(why))
    finally:
        con.close()


def test_a_refused_delivery_shows_the_refusal(home: Path) -> None:
    _world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }

deny:
  - { from: "manager", to: "worker", reason: "no more work for worker" }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
    con = db.connect()
    try:
        t = _waiting(con)
        why = _why(con, t)
        check("refused: the refusal's own words", why is not None and "no more work for worker" in why[0]
              and not why[1], str(why))
    finally:
        con.close()


def test_an_owner_that_is_not_registered_is_named(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "to nobody", "ghost", "manager")
        con.execute("UPDATE tasks SET queued_by = 'manager' WHERE id = ?", (t,))
        why = _why(con, t)
        check("unregistered: says nothing will take it", why is not None and "ghost is not registered" in why[0], str(why))
    finally:
        con.close()


def test_a_request_behind_an_open_head_says_which(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        ok, msg, first = actions.task_new("manager", "worker", "first", "first")
        (req,) = db.task_requests(con, first)
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (int(time.time()), req["id"]))
        t = db.new_task(con, "second", "worker", "manager")
        ok, msg, rid = None, None, db.add_request(con, "manager", "worker", "second", None, "supervisor",
                                                  "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        con.execute("UPDATE tasks SET queued_by = 'manager' WHERE id = ?", (t,))
        db.set_current_task(con, "worker", t)
        why = _why(con, t)
        check("head: a request behind the owner's open one names it",
              why is not None and why[0] == f"behind #{req['id']}, which worker has not resolved" and not why[1], str(why))
    finally:
        con.close()


def test_every_state_of_the_owner_gives_its_own_reason(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        (req,) = db.task_requests(con, t)
        check("untold: an idle, reachable owner is able - the next tick types it",
              _why(con, t) == (taskholds.READY, True), str(_why(con, t)))

        db.set_agent_state(con, "worker", "RUNNING")
        why = _why(con, t)
        check("mid-turn: says so, and is not able", why is not None and "mid-turn" in why[0] and not why[1], str(why))
        db.set_agent_state(con, "worker", "HANDOFF")
        why = _why(con, t)
        check("handoff: says so", why is not None and "mid-handoff" in why[0] and not why[1], str(why))
        db.set_agent_state(con, "worker", "IDLE")

        db.add_event(con, "worker", "StopFailure", {"limit": "5h"})
        why = _why(con, t)
        check("usage limit: says it is at its limit", why is not None and "usage limit" in why[0] and not why[1], str(why))
        db.add_event(con, "worker", "UserPromptSubmit", {})

        db.block_open(con, "worker", orcamod.MODAL, "Trust this folder? - 1. Yes 2. No", "request #1",
                      int(time.time()), int(time.time()) + 180, title="Trust this folder?")
        why = _why(con, t)
        check("dialog: the dialog's own title", why is not None and "Trust this folder?" in why[0] and not why[1], str(why))
        db.block_clear(con, "worker")

        sessions_are()
        why = _why(con, t)
        check("no session: a terminal with nothing running in it", why is not None and "no session" in why[0]
              and not why[1], str(why))
        sessions_are("worker")

        orcamod.live_terminals = lambda: set()
        why = _why(con, t)
        check("terminal gone: Orca no longer has it", why is not None and "terminal is gone" in why[0], str(why))
        orcamod.live_terminals = lambda: {"term_w"}

        con.execute("UPDATE agent_state SET terminal = NULL WHERE agent = 'worker'")
        why = _why(con, t)
        check("no terminal: says so", why is not None and "no terminal" in why[0] and not why[1], str(why))
        db.set_terminal(con, "worker", "term_w", "wt")
        db.set_agent_state(con, "worker", "IDLE")

        db.set_closed(con, "worker", "sess-1")
        why = _why(con, t)
        check("closed: it is woken for the task, and says so", why is not None and "closed" in why[0], str(why))
        con.execute("UPDATE agent_state SET closed_at = NULL WHERE agent = 'worker'")

        delivery.note_failure("worker", "could not be woken - launch refused")
        why = _why(con, t)
        check("wake failed: the failure's words, not able", why == ("could not be woken - launch refused", False), str(why))
    finally:
        con.close()


def test_a_screen_that_will_not_take_it_while_the_owner_reads_idle_is_able(home: Path) -> None:
    """T458's misread: the state says idle, the screen says mid-turn. The reason is shown and the
    owner counts as able, so the founder is told if it goes on."""
    _world(home)
    con = db.connect()
    try:
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        delivery.note_hold("worker", "its screen is not idle (mid-turn, or a dialog is open)", contestable=True)
        why = _why(con, t)
        check("misread: idle in the db, busy on screen - shown and able",
              why == ("its screen is not idle (mid-turn, or a dialog is open)", True), str(why))
        db.set_agent_state(con, "worker", "RUNNING")
        check("misread: a RUNNING owner is not able", _why(con, t)[1] is False)
        db.set_agent_state(con, "worker", "IDLE")
        delivery._NOTED["worker"] = ("its screen is not idle", int(time.time()) - delivery.HELD_FRESH_S - 1, True)
        check("misread: a reason not renewed has stopped being true", _why(con, t) == (taskholds.READY, True))
    finally:
        con.close()


def test_the_founder_is_told_once_when_an_able_owner_never_takes_it(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        now = int(time.time())
        before = _reports(con)
        taskholds.sweep(con, lambda _m: None, now)
        taskholds.sweep(con, lambda _m: None, now + taskholds.ABLE_WARN_S - 1)
        check("warn: not before the bound", _reports(con) == before)
        taskholds.sweep(con, lambda _m: None, now + taskholds.ABLE_WARN_S)
        check("warn: at the bound, one report", _reports(con) == before + 1, str(_reports(con) - before))
        taskholds.sweep(con, lambda _m: None, now + taskholds.ABLE_WARN_S + 60)
        check("warn: and only one", _reports(con) == before + 1)

        db.set_agent_state(con, "worker", "RUNNING")
        taskholds.sweep(con, lambda _m: None, now + 2 * taskholds.ABLE_WARN_S)
        db.set_agent_state(con, "worker", "IDLE")
        taskholds.sweep(con, lambda _m: None, now + 2 * taskholds.ABLE_WARN_S + 1)
        taskholds.sweep(con, lambda _m: None, now + 3 * taskholds.ABLE_WARN_S + 1)
        check("warn: a busy spell restarts the clock, and a later miss is told again", _reports(con) == before + 2)
    finally:
        con.close()


def test_a_busy_owner_is_never_blamed(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        db.set_agent_state(con, "worker", "RUNNING")
        before, now = _reports(con), int(time.time())
        for i in range(4):
            taskholds.sweep(con, lambda _m: None, now + i * taskholds.ABLE_WARN_S)
        check("no warning for an owner that is working", _reports(con) == before)
    finally:
        con.close()


def test_the_reason_is_kept_for_other_processes_and_dropped_when_told(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        ok, msg, first = actions.task_new("manager", "worker", "first", "first")
        con.execute("UPDATE requests SET delivered_at = ?", (int(time.time()),))      # told: not itself waiting
        t = _waiting(con)
        taskholds.sweep(con, lambda _m: None, int(time.time()))
        waits = taskholds.agent_waits(con, "worker")
        check("kept: the waiting task has its reason on record", any(i == t and "behind" in w for i, w in waits), str(waits))
        rc, out = _cli(["task", "list"], as_agent="manager")
        check("list: the task's line says what it waits for", f"waiting: behind T{first}" in out, out)
        rc, out = _cli(["team"], as_agent="manager")
        check("team: so does the owner's line", f"T{t} waits: behind T{first}" in out, out)
        rc, out = _cli(["task", "board"], as_agent="manager")
        check("board: and the board", f"waiting: behind T{first}" in out, out)

        db.mark_task_done(con, first, "done", "reason")
        db.set_current_task(con, "worker", None)
        actions.advance_current_task(con, "worker")
        db.mark_task_done(con, t, "done", "reason")
        taskholds.sweep(con, lambda _m: None, int(time.time()))
        check("dropped: a finished task keeps no reason", taskholds.agent_waits(con, "worker") == []
              and db.meta_names(con, taskholds.PREFIX) == [])
    finally:
        con.close()


def test_a_queued_task_never_reads_without_a_reason(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "not seen yet", "worker", "manager")
        actions.set_queued(con, t, "manager", True)
        row = db.get_task(con, t)
        db.clear_meta(con, taskholds.PREFIX + str(t))
        line = taskholds.wait_line(con, row, "queued")
        check("unseen: before the daemon has looked, it says so", line is not None and "not looked at it yet" in line, str(line))
        locks.running_elsewhere = lambda: None
        line = taskholds.wait_line(con, row, "queued")
        check("daemon down: nothing retries, and the line says so",
              line is not None and "daemon is not running" in line, str(line))
        taskholds.sweep(con, lambda _m: None, int(time.time()))
        line = taskholds.wait_line(con, row, "queued")
        check("daemon down: the last reason is kept beside it", line is not None and "was:" in line, str(line))
        check("not waiting: a done task has no line", taskholds.wait_line(con, row, "done") is None)
    finally:
        con.close()


def test_a_free_owner_is_given_the_next_task_on_the_next_tick(home: Path) -> None:
    """T721: the retry gate was set before the try, so a task that landed delayed the next one a minute."""
    _world(home)
    con = db.connect()
    try:
        ok, msg, first = actions.task_new("manager", "worker", "first", "first")
        second, third = _waiting(con, "second"), _waiting(con, "third")
        now = int(time.time())
        db.mark_task_done(con, first, "done", "reason")
        db.set_current_task(con, "worker", None)
        taskqueue.sweep(con, lambda _m: None, now)
        check("tick: the free owner is sent the next task", db.current_task(con, "worker") == second
              and len(db.task_requests(con, second)) == 1)
        db.mark_task_done(con, second, "done", "reason")
        db.set_current_task(con, "worker", None)
        taskqueue.sweep(con, lambda _m: None, now + 1)
        check("tick: and the one after it a tick later, not a minute later",
              db.current_task(con, "worker") == third and len(db.task_requests(con, third)) == 1)
    finally:
        con.close()


def test_a_real_tick_records_why_a_task_waits(home: Path) -> None:
    """The sweep is wired into the tick: take it out and the recording and the warning go with it."""
    _world(home)
    orcamod.send = lambda h, t, enter=True: (True, "")
    con = db.connect()
    try:
        ok, msg, first = actions.task_new("manager", "worker", "first", "first")
        t = _waiting(con)
        evaluate.tick(con, lambda _m: None)
        rec = db.get_meta(con, taskholds.PREFIX + str(t))
        check("tick: the waiting task has its reason in meta after one tick",
              rec is not None and f"behind T{first}" in rec, str(rec))
    finally:
        con.close()


def test_the_daemons_own_paths_note_why_they_did_not_type(home: Path) -> None:
    """No test writes the note here: deliver_queued and supervise do."""
    _world(home)
    sent: list[str] = []
    orcamod.send = lambda h, t, enter=True: (sent.append(t), (True, ""))[1]
    con = db.connect()
    try:
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        orcamod.typeable_now = lambda h: False
        check("screen: deliver_queued declines", not delivery.deliver_queued(con, "worker", lambda _m: None))
        why = _why(con, t)
        check("screen: and the task says why, able on an idle row",
              why == ("its screen is not idle (mid-turn, or a dialog is open)", True), str(why))
        orcamod.typeable_now = lambda h: True
        check("screen: a later delivery lands it", delivery.deliver_queued(con, "worker", lambda _m: None) and sent)
        check("screen: and nothing is held or waiting any more",
              delivery.held_reason("worker", int(time.time())) is None and _why(con, t) is None,
              str(_why(con, t)))
    finally:
        orcamod.typeable_now = lambda h: True
        con.close()


def test_a_wake_or_start_that_fails_in_the_tick_is_noted(home: Path) -> None:
    _world(home)
    con = db.connect()
    real_wake, real_start = lifecycle.wake_agent, lifecycle._start_for_work
    try:
        orcamod.send = lambda h, t, enter=True: (True, "")
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        sessions_are()                      # nothing runs: the sweep leaves it closed
        db.set_closed(con, "worker", "sess-1")
        lifecycle.wake_agent = lambda c, a, log, **k: (False, "launch refused")
        evaluate.tick(con, lambda _m: None)
        held = delivery.held_reason("worker", int(time.time()))
        check("wake: the daemon noted the failed wake", held is None and delivery.failed_reason("worker", int(time.time())) == "could not be woken - launch refused",
              str(held))
        db.clear_closed(con, "worker")
        con.execute("UPDATE agent_state SET terminal = NULL WHERE agent = 'worker'")
        lifecycle._start_for_work = lambda c, a, log: "worker: no repo"
        evaluate.tick(con, lambda _m: None)
        held = delivery.held_reason("worker", int(time.time()))
        check("start: and the failed start", delivery.failed_reason("worker", int(time.time())) == "could not be started - worker: no repo",
              str(held))
        check("start: and the task's reason is that, not able",
              (_why(con, t) or ("", True))[0] == "could not be started - worker: no repo"
              and _why(con, t)[1] is False, str(_why(con, t)))
    finally:
        lifecycle.wake_agent, lifecycle._start_for_work = real_wake, real_start
        con.close()


def test_a_parked_agents_due_wake_notes_its_failure_and_clears_it_when_it_works(home: Path) -> None:
    _world(home)
    con = db.connect()
    real_wake = lifecycle.wake_agent
    try:
        orcamod.send = lambda h, t, enter=True: (True, "")
        ok, msg, t = actions.task_new("manager", "worker", "the work", "the work")
        sessions_are()
        db.set_closed(con, "worker", "sess-1")
        db.set_agent_state(con, "worker", "PARKED", wake_at=int(time.time()) - 5)
        lifecycle.wake_agent = lambda c, a, log, **k: (False, "launch refused")
        evaluate.tick(con, lambda _m: None)
        check("parked and due: a wake that fails is noted",
              delivery.failed_reason("worker", int(time.time())) == "could not be woken - launch refused",
              str(delivery.failed_reason("worker", int(time.time()))))
        # A real wake leaves the agent open, so the tick's later wake site has nothing to clear it.
        lifecycle.wake_agent = lambda c, a, log, **k: (db.clear_closed(c, a), (True, "woken"))[1]
        evaluate.tick(con, lambda _m: None)
        check("parked and due: the next wake that works clears it",
              delivery.failed_reason("worker", int(time.time())) is None,
              str(delivery.failed_reason("worker", int(time.time()))))
    finally:
        lifecycle.wake_agent = real_wake
        con.close()


def main() -> int:
    def tests(td):
        try:
            _tests(td)
        finally:
            askmod.push = REAL_PUSH

    def _tests(td):
        run(lambda: test_a_task_behind_the_owners_current_one_says_which(Path(td) / "h1"))
        run(lambda: test_a_blocked_task_names_what_blocks_it(Path(td) / "h2"))
        run(lambda: test_a_refused_delivery_shows_the_refusal(Path(td) / "h3"))
        run(lambda: test_an_owner_that_is_not_registered_is_named(Path(td) / "h4"))
        run(lambda: test_a_request_behind_an_open_head_says_which(Path(td) / "h5"))
        run(lambda: test_every_state_of_the_owner_gives_its_own_reason(Path(td) / "h6"))
        run(lambda: test_a_screen_that_will_not_take_it_while_the_owner_reads_idle_is_able(Path(td) / "h7"))
        run(lambda: test_the_founder_is_told_once_when_an_able_owner_never_takes_it(Path(td) / "h8"))
        run(lambda: test_a_busy_owner_is_never_blamed(Path(td) / "h9"))
        run(lambda: test_the_reason_is_kept_for_other_processes_and_dropped_when_told(Path(td) / "h10"))
        run(lambda: test_a_queued_task_never_reads_without_a_reason(Path(td) / "h11"))
        run(lambda: test_a_free_owner_is_given_the_next_task_on_the_next_tick(Path(td) / "h12"))
        run(lambda: test_a_real_tick_records_why_a_task_waits(Path(td) / "h13"))
        run(lambda: test_the_daemons_own_paths_note_why_they_did_not_type(Path(td) / "h14"))
        run(lambda: test_a_wake_or_start_that_fails_in_the_tick_is_noted(Path(td) / "h15"))
        run(lambda: test_a_parked_agents_due_wake_notes_its_failure_and_clears_it_when_it_works(Path(td) / "h16"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
