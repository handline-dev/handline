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
from ocp.daemon import noticedigest, states as dmn_states  # noqa: E402
from ocp.session import idle_close  # noqa: E402

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
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.screen_blocker = lambda h: None
    orcamod.typeable_now = lambda h: True
    orcamod.live_terminals = lambda: {"term_x"}
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    orcamod.send = send
    return sent


def _finish(con, result: str) -> int:
    ok, msg, tid = actions.task_new("backend", "worker", f"work for {result[:10]}", "do it")
    check("setup: the task opens", ok, msg)
    ok, msg = actions.mark_task_done(con, tid, "worker", reason=result)
    check("setup: the owner finishes it", ok, msg)
    return tid


def _notices(con) -> list:
    return con.execute("SELECT * FROM requests WHERE notice = 1 AND target = 'backend' ORDER BY id").fetchall()


def test_results_wait_then_arrive_in_one_digest(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        t1 = _finish(con, "PASS one")
        t2 = _finish(con, "PASS two")
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


def test_a_message_before_the_wait_carries_the_results_and_the_digest_has_nothing_left(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        _finish(con, "PASS one")
        first = int(_notices(con)[0]["landed_at"])
        askmod.ask("backend", "please look", requester="worker")
        check("carry: a message typed sooner carries the result", len(sent) == 1 and "Also: T" in sent[0][1], str(sent))
        before = len(sent)
        noticedigest.sweep(con, lambda _m: None, first + 5 * idle_close.NOTICE_DIGEST_S)
        check("carry: so the digest, when due, finds nothing and types nothing", len(sent) == before, str(sent))
    finally:
        con.close()


def test_a_failure_or_a_block_is_told_at_once_not_held(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        _finish(con, "FAIL @abc123 the suite broke")
        check("urgent: a task that ends FAIL is typed to its opener now, as a message of its own",
              len(sent) == 1 and "FAIL @abc123" in sent[0][1] and not _notices(con), str(sent))
        _finish(con, "blocked: needs the founder")
        check("urgent: and so is one that ends blocked", len(sent) == 2 and "blocked: needs" in sent[1][1], str(sent))
        _finish(con, "PASS and unblocked the failing build")
        check("urgent: a PASS that mentions failure later in its words is still a result", len(sent) == 2
              and len(_notices(con)) == 1, str(sent))
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
              and "worker is blocked: a permission prompt" in sent[0][1], str(sent))
        noticedigest.sweep(con, lambda _m: None, now + 200)
        check("blocked: once per spell", len(sent) == 1, str(sent))
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
        orcamod.send = lambda handle, text, enter=True: (sent.append(text) or (True, ""))
        idle_close.set_notice_digest_s(60, "backend")
        _finish(con, "PASS soon")
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
        run(lambda: test_a_message_before_the_wait_carries_the_results_and_the_digest_has_nothing_left(Path(td) / 'n1'))
        run(lambda: test_a_failure_or_a_block_is_told_at_once_not_held(Path(td) / 'n2'))
        run(lambda: test_an_agent_going_fatal_is_told_once_to_whoever_waits_on_it(Path(td) / 'n3'))
        run(lambda: test_work_that_cannot_be_given_is_told_once_it_is_on_the_founders_list(Path(td) / 'n5'))
        run(lambda: test_the_wait_is_a_common_setting_with_a_per_agent_override(Path(td) / 'n4'))
        run(lambda: test_ocp_notice_digest_shows_and_sets_the_wait(Path(td) / 'n6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
