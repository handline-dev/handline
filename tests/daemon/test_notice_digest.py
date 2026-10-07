"""Results told to whoever opened the work come in one digest after a wait, ride on any
message sooner, and a failure, a block or a FATAL is told at once.

Run on its own: python3 tests/daemon/test_notice_digest.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import term  # noqa: E402
from handline.daemon import noticedigest, states as dmn_states  # noqa: E402
from handline.session import idle_close  # noqa: E402

TEAM = """
agents:
  backend: {}
  worker: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
"""


def _world(home: Path) -> list:
    """backend (the opener) can be typed at; worker does the work."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    agents.register("worker")
    sessions_are("backend")
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    termmod.screen_blocker = lambda h: None
    termmod.typeable_now = lambda h: True
    termmod.live_terminals = lambda: {"term_x"}
    db.set_agent_state(db.connect(), "backend", dmn_states.RUNNING)      # busy unless a test frees it
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    termmod.send = send
    return sent


def _finish(con, result: str) -> int:
    ok, msg, tid = actions.task_new("backend", "worker", f"work for {result[:10]}", "do it")
    check("setup: the task opens", ok, msg)
    ok, msg = actions.mark_task_done(con, tid, "worker", reason=result)
    check("setup: the owner finishes it", ok, msg)
    return tid


def _notice(con, result: str) -> int:
    """A result waiting in the digest, written as the task board writes one. An opener is told its
    own done at once, so a real done never waits; the digest holds what is left."""
    tid = 100 + len(con.execute("SELECT id FROM requests WHERE notice = 1").fetchall())
    db.add_request(con, db.NOTICE_REQUESTER, "backend", f"T{tid} done by worker: {result}", None, None,
                   "fire_and_forget", "say", 0, None, notice=True)
    return tid


def _notices(con) -> list:
    return con.execute("SELECT * FROM requests WHERE notice = 1 AND target = 'backend' ORDER BY id").fetchall()


def test_results_wait_then_arrive_in_one_digest(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        t1 = _notice(con, "PASS one")
        t2 = _notice(con, "PASS two")
        first = int(_notices(con)[0]["landed_at"])
        check("digest: a result is a notice and types nothing", len(_notices(con)) == 2 and sent == [], str(sent))
        noticedigest.sweep(con, lambda _m: None, first + idle_close.NOTICE_DIGEST_S - 1)
        check("digest: before the wait is out, nothing is sent", sent == [] and len(db.pending_notices(con, "backend")) == 2)
        noticedigest.sweep(con, lambda _m: None, first + idle_close.NOTICE_DIGEST_S)
        check("digest: once it is out, one message is typed", len(sent) == 1, str(sent))
        text = sent[0][1]
        check("digest: one line each, 'T<n> · who · result', with the id to read",
              f"T{t1} · worker · PASS one (#" in text and f"T{t2} · worker · PASS two (#" in text
              and "2 results since" in text, text)
        check("digest: the notices are marked shown, so none waits any more", not db.pending_notices(con, "backend"))
        noticedigest.sweep(con, lambda _m: None, first + 10 * idle_close.NOTICE_DIGEST_S)
        check("digest: and a later tick sends nothing more", len(sent) == 1, str(sent))
    finally:
        con.close()


def test_an_idle_agent_is_told_at_once_and_a_busy_one_waits_for_the_digest(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        _notice(con, "PASS one")
        now = int(_notices(con)[0]["landed_at"])
        noticedigest.sweep(con, lambda _m: None, now)
        check("idle: working a turn, it waits for the digest", sent == [] and len(db.pending_notices(con, "backend")) == 1, str(sent))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        noticedigest.sweep(con, lambda _m: None, now)
        check("idle: at its prompt, the same tick types it, well before the window",
              len(sent) == 1 and "PASS one" in sent[0][1] and not db.pending_notices(con, "backend"), str(sent))
        _notice(con, "PASS two")
        db.set_cursor(con, "bgwork:backend", 1)
        noticedigest.sweep(con, lambda _m: None, now)
        check("idle: with background work running it is still busy", len(sent) == 1, str(sent))
        db.set_cursor(con, "bgwork:backend", 0)
        db.set_closed(con, "backend", None)
        noticedigest.sweep(con, lambda _m: None, now)
        check("idle: a closed agent keeps the clock, a result is not worth waking it for",
              len(sent) == 1 and len(db.pending_notices(con, "backend")) == 1, str(sent))
        db.clear_closed(con, "backend")
        noticedigest.sweep(con, lambda _m: None, now)
        check("idle: once its session is back at the prompt, it is told", len(sent) == 2 and "PASS two" in sent[1][1], str(sent))
    finally:
        con.close()


def test_a_tell_that_raises_does_not_build_a_second_digest(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        _notice(con, "PASS one")
        first = int(_notices(con)[0]["landed_at"])
        real = noticedigest.messaging.tell_now

        def boom(*a, **k):
            raise RuntimeError("wake failed")
        noticedigest.messaging.tell_now = boom
        try:
            try:
                noticedigest.sweep(con, lambda _m: None, first + idle_close.NOTICE_DIGEST_S)
            except RuntimeError:
                pass
        finally:
            noticedigest.messaging.tell_now = real
        noticedigest.sweep(con, lambda _m: None, first + idle_close.NOTICE_DIGEST_S + 5)
        digests = con.execute("SELECT * FROM requests WHERE requester = 'task-board' AND notice IS NULL "
                              "AND body LIKE ?", (db.DIGEST_HEAD + "%",)).fetchall()
        check("digest: the tell raised, the digest stays queued once and the results are marked, not re-sent",
              len(digests) == 1 and not db.pending_notices(con, "backend"), str([dict(r)["id"] for r in digests]))
        dmn_messaging.tell_undelivered(con, "backend", lambda _m: None)
        check("digest: and the tick types the queued one", len(sent) == 1 and "PASS one" in sent[0][1], str(sent))
    finally:
        con.close()


def test_only_a_digest_is_shown_whole_when_typed(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        long = "x" * 700
        rid = db.add_request(con, db.NOTICE_REQUESTER, "backend", f"T9 done by worker: FAIL {long}", None, None,
                             "fire_and_forget", "say", 0, None)
        cut = dmn_delivery.tell_text(con, db.get_request(con, rid), 1)
        check("whole: a task-board line that is not a digest is cut as before", long not in cut and "…" in cut, cut)
        rid = db.add_request(con, db.NOTICE_REQUESTER, "backend", f"{db.DIGEST_HEAD}1 result: {long}", None, None,
                             "fire_and_forget", "say", 0, None)
        whole = dmn_delivery.tell_text(con, db.get_request(con, rid), 1)
        check("whole: a digest is shown whole", long in whole, whole[:200])
    finally:
        con.close()


def test_a_message_before_the_wait_carries_the_results_and_the_digest_has_nothing_left(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        _notice(con, "PASS one")
        first = int(_notices(con)[0]["landed_at"])
        askmod.ask("backend", "please look", requester="worker")
        check("carry: a message typed sooner carries the result", len(sent) == 1 and "Also: T" in sent[0][1], str(sent))
        before = len(sent)
        noticedigest.sweep(con, lambda _m: None, first + 5 * idle_close.NOTICE_DIGEST_S)
        check("carry: so the digest, when due, finds nothing and types nothing", len(sent) == before, str(sent))
    finally:
        con.close()


def test_every_done_is_told_to_its_opener_at_once_not_held(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        _finish(con, "FAIL @abc123 the suite broke")
        check("urgent: a task that ends FAIL is typed to its opener now, as a message of its own",
              len(sent) == 1 and "FAIL @abc123" in sent[0][1] and not _notices(con), str(sent))
        _finish(con, "blocked: needs the founder")
        check("urgent: and so is one that ends blocked", len(sent) == 2 and "blocked: needs" in sent[1][1], str(sent))
        _finish(con, "PASS and unblocked the failing build")
        check("urgent: a PASS, even one that mentions failure later, is typed to its opener at once too",
              len(sent) == 3 and "PASS and unblocked" in sent[2][1] and not _notices(con), str(sent))
    finally:
        con.close()


def test_only_a_non_opener_is_told_in_the_digest_when_a_task_ends(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("backend", "worker", "work to drop", "do it")
        check("setup: the task opens", ok, msg)
        for r in db.task_requests(con, tid):
            db.close_if_queued(con, int(r["id"]), "withdrawn", None, announced=True, settle=False)
        ok, msg = actions.discard_task(con, tid, actions.HUMAN, "not needed")
        check("setup: the founder discards it", ok, msg)
        told = db.pending_notices(con, "worker")
        check("end: the owner, who is not the opener, gets a notice that waits for the digest",
              len(told) == 1 and f"T{tid} discarded by the founder" in told[0]["body"]
              and told[0]["delivered_at"] is None, str([dict(r) for r in told]))
        typed = [m for m in sent if f"T{tid} discarded by the founder" in m[1]]
        check("end: the opener in the same end is typed its own message at once, not a notice",
              len(typed) == 1 and not _notices(con), str(sent))
    finally:
        con.close()


def test_an_agent_going_fatal_is_told_once_to_whoever_waits_on_it(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("backend", "worker", "work", "do it")
        db.set_agent_state(con, "worker", dmn_states.STALLED, detail="3 starts failed: no repo")
        noticedigest.sweep(con, lambda _m: None, int(time.time()))
        check("fatal: its opener is told at once, with the reason",
              len(sent) == 1 and "worker is FATAL: 3 starts failed: no repo" in sent[0][1], str(sent))
        noticedigest.sweep(con, lambda _m: None, int(time.time()))
        check("fatal: once per spell, not once per tick", len(sent) == 1, str(sent))
        db.set_agent_state(con, "worker", dmn_states.IDLE)
        noticedigest.sweep(con, lambda _m: None, int(time.time()))
        db.set_agent_state(con, "worker", dmn_states.STALLED, detail="again")
        noticedigest.sweep(con, lambda _m: None, int(time.time()))
        check("fatal: a new spell is told again", len(sent) == 2 and "again" in sent[1][1], str(sent))
        db.set_agent_state(con, "worker", dmn_states.IDLE)
        noticedigest.sweep(con, lambda _m: None, int(time.time()))
        db.set_agent_state(con, "worker", dmn_states.STALLED, detail=f"{dmn_delivery.DIALOG_STALL}: a prompt")
        noticedigest.sweep(con, lambda _m: None, int(time.time()))
        check("fatal: a stall on a dialog is called stalled, not FATAL",
              len(sent) == 3 and "worker is stalled: cannot get past a dialog" in sent[2][1], str(sent))
    finally:
        con.close()


def test_work_that_cannot_be_given_is_told_once_it_is_on_the_founders_list(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        actions.task_new("backend", "worker", "work", "do it")
        now = int(time.time())
        db.block_open(con, "worker", "dialog open", "a permission prompt", "T1", now, show_at=now + 180)
        noticedigest.sweep(con, lambda _m: None, now + 10)
        check("blocked: before it is on the founder's list, nobody is told", sent == [], str(sent))
        noticedigest.sweep(con, lambda _m: None, now + 180)
        check("blocked: once it is, the opener is told at once", len(sent) == 1
              and "worker is blocked: dialog open: a permission prompt" in sent[0][1], str(sent))
        noticedigest.sweep(con, lambda _m: None, now + 200)
        check("blocked: once per spell", len(sent) == 1, str(sent))
        db.block_clear(con, "worker")
        noticedigest.sweep(con, lambda _m: None, now + 210)
        db.block_open(con, "worker", term.UNREADABLE, "", "T1", now, show_at=now)
        noticedigest.sweep(con, lambda _m: None, now + 220)
        check("blocked: a block with no detail still says what is in the way",
              len(sent) == 2 and "\nworker is blocked: its screen could not be read\n" in sent[1][1],
              str(sent))
    finally:
        con.close()


def test_the_wait_is_a_common_setting_with_a_per_agent_override(home: Path) -> None:
    _world(home)
    check("settings: the default is 15 minutes", idle_close.notice_digest_s("backend") == 900)
    idle_close.set_notice_digest_s(300)
    check("settings: config.yaml's value is every agent's", idle_close.notice_digest_s("backend") == 300
          and idle_close.notice_digest_s("worker") == 300)
    idle_close.set_notice_digest_s(60, "backend")
    check("settings: an agent's own wins, the others keep the common one",
          idle_close.notice_digest_s("backend") == 60 and idle_close.notice_digest_s("worker") == 300)
    idle_close.set_notice_digest_s(None, "backend")
    check("settings: clearing it follows the common one again", idle_close.notice_digest_s("backend") == 300)
    sent = []
    con = db.connect()
    try:
        termmod.send = lambda handle, text, enter=True: (sent.append(text) or (True, ""))
        idle_close.set_notice_digest_s(60, "backend")
        _notice(con, "PASS soon")
        first = int(_notices(con)[0]["landed_at"])
        noticedigest.sweep(con, lambda _m: None, first + 60)
        check("settings: the sweep uses the agent's own wait", len(sent) == 1, str(sent))
    finally:
        con.close()


def test_ocp_notice_digest_shows_and_sets_the_wait(home: Path) -> None:
    _world(home)
    out = support._capture(lambda: climod.main(["notice-digest"]))
    check("cli: shows the default, 15m", "default:" in out and "15m after the first" in out, out)
    out = support._capture(lambda: climod.main(["notice-digest", "20m"]))
    check("cli: sets the common wait", "20m after" in out, out)
    out = support._capture(lambda: climod.main(["notice-digest", "--agent", "backend", "5m"]))
    check("cli: sets one agent's own", out.startswith("backend:") and "5m after" in out, out)
    out = support._capture(lambda: climod.main(["notice-digest", "--agent", "backend", "--clear"]))
    check("cli: --clear follows the common one again", "20m after" in out, out)


def main() -> int:
    def tests(td):
        run(lambda: test_results_wait_then_arrive_in_one_digest(Path(td) / 'n0'))
        run(lambda: test_an_idle_agent_is_told_at_once_and_a_busy_one_waits_for_the_digest(Path(td) / 'n20'))
        run(lambda: test_a_tell_that_raises_does_not_build_a_second_digest(Path(td) / 'n7'))
        run(lambda: test_only_a_digest_is_shown_whole_when_typed(Path(td) / 'n8'))
        run(lambda: test_a_message_before_the_wait_carries_the_results_and_the_digest_has_nothing_left(Path(td) / 'n1'))
        run(lambda: test_every_done_is_told_to_its_opener_at_once_not_held(Path(td) / 'n2'))
        run(lambda: test_only_a_non_opener_is_told_in_the_digest_when_a_task_ends(Path(td) / 'n2b'))
        run(lambda: test_an_agent_going_fatal_is_told_once_to_whoever_waits_on_it(Path(td) / 'n3'))
        run(lambda: test_work_that_cannot_be_given_is_told_once_it_is_on_the_founders_list(Path(td) / 'n5'))
        run(lambda: test_the_wait_is_a_common_setting_with_a_per_agent_override(Path(td) / 'n4'))
        run(lambda: test_ocp_notice_digest_shows_and_sets_the_wait(Path(td) / 'n6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
