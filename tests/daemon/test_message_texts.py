"""Tests: the task board's says from before the notice column are never carried.
Run on its own: python3 tests/daemon/test_message_texts.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

TEAM = """
agents:
  manager: {}
  worker: {}
  x.helper: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
"""
NOTE = "Fix the two things in the report: the cap over the note, and the resolve verb. Then hand it back."


def test_task_board_says_from_before_the_notice_column_are_never_carried(home: Path) -> None:
    """T949: the upgrade adds `notice` and backfills nothing, so a weeks-old "done by" say that was
    landed and undelivered when T935 shipped does not drip into every later delivery."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    for a in ("manager", "worker", "x.helper"):
        agents.register(a)
    con = db.connect()
    try:
        old = db.add_request(con, db.NOTICE_REQUESTER, "worker", "T7 done by x.helper: weeks ago", None, None,
                             "fire_and_forget", "say", 0, None, state="done")
        new = db.add_request(con, db.NOTICE_REQUESTER, "worker", "T8 done by x.helper: just now", None, None,
                             "fire_and_forget", "say", 0, None, notice=True)
        con.execute("UPDATE requests SET notice = NULL WHERE id = ?", (old,))
        check("setup: the old say is landed and undelivered, like a notice", db.get_request(con, old)["state"] == "landed"
              and db.get_request(con, old)["delivered_at"] is None)
        check("old rows: only the row born as a notice is carried", [r["id"] for r in db.pending_notices(con, "worker")] == [new])
        # an old database: the column is gone, with such a row in it
        con.commit()
        con.execute("ALTER TABLE requests DROP COLUMN notice")
        con.commit()
    finally:
        con.close()
    con = support.upgrade()
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(requests)")]
        check("upgrade: connecting to an old database adds the column", "notice" in cols, str(cols))
        check("upgrade: and backfills nothing - the row that was there before is not carried",
              db.pending_notices(con, "worker") == [])
        later = db.add_request(con, db.NOTICE_REQUESTER, "worker", "T9 done by x.helper: after", None, None,
                               "fire_and_forget", "say", 0, None, notice=True)
        check("upgrade: a notice written after it is carried, and a second connect adds nothing twice",
              [r["id"] for r in db.pending_notices(con, "worker")] == [later])
    finally:
        con.close()
    con = support.upgrade()
    try:
        check("upgrade: reconnecting keeps the column and the row", [r["id"] for r in db.pending_notices(con, "worker")] == [later])
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_task_board_says_from_before_the_notice_column_are_never_carried(Path(td) / 'm4'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
