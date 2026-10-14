"""Tests: a done is told to its opener at once, a non-opener hears it in the digest, and `ocp notice-digest` shows
and sets the wait.
Run on its own: python3 tests/daemon/test_notice_digest.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import states as dmn_states

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


def _notices(con) -> list:
    return con.execute("SELECT * FROM requests WHERE notice = 1 AND target = 'backend' ORDER BY id").fetchall()


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
        run(lambda: test_every_done_is_told_to_its_opener_at_once_not_held(Path(td) / 'n2'))
        run(lambda: test_only_a_non_opener_is_told_in_the_digest_when_a_task_ends(Path(td) / 'n2b'))
        run(lambda: test_ocp_notice_digest_shows_and_sets_the_wait(Path(td) / 'n6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
