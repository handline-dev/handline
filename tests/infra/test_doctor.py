"""Tests: `hl doctor` checks that read the home themselves, with no daemon.

Run on its own: tools/test.sh tests/infra/test_doctor.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import db  # noqa: E402
from handline.infra import doctor  # noqa: E402
from handline.session import states  # noqa: E402


def test_doctor_names_an_agent_left_running_with_nothing_heard(home: Path) -> None:
    support.use_home(home)
    now = int(time.time())
    con = db.connect()
    try:
        for agent, state, heard in (("quiet", states.RUNNING, now - states.STUCK_RUNNING_S - 125),
                                    ("busy", states.RUNNING, now - states.STUCK_RUNNING_S + 60),
                                    ("idle", states.IDLE, now - 3 * states.STUCK_RUNNING_S),
                                    ("new", states.RUNNING, None)):
            db.set_agent_state(con, agent, state)
            if heard is not None:
                db.add_event(con, agent, "commit", ts=heard)
        con.commit()
    finally:
        con.close()
    r = doctor.Report()
    doctor._check_stuck_running(r)
    said = [row[1] for row in r.rows]
    check("doctor: RUNNING and silent past the threshold is a warning with the whole minutes",
          [row[:2] for row in r.rows] == [(doctor.WARN, "agent quiet: RUNNING with nothing heard for 32m")], str(r.rows))
    check("doctor: a RUNNING agent heard from since, an idle one and one with no activity are not named",
          not any(a in s for s in said for a in ("busy", "idle", "new")), str(said))


def test_hl_doctor_says_when_rejected_hook_lines_are_piling_up(home: Path) -> None:
    support.use_home(home)
    rejected = home / "hook-spool-rejected.jsonl"
    row = lambda: next((r for r in doctor.run().rows if "hook line" in r[1]), None)  # noqa: E731
    check("doctor: no rejected file, no note", row() is None)
    rejected.write_text("")
    check("doctor: an empty one, no note", row() is None)
    rejected.write_text(json.dumps({"rejected_at": 1760000000, "error": "KeyError", "message": "'kind'", "line": "{}"}) + "\n"
                        + json.dumps({"rejected_at": 1760000500, "error": "ValueError", "message": "x", "line": "y"}) + "\n")
    got = row()
    check("doctor: a warning with the count, the size and the oldest",
          got and got[0] == doctor.WARN and got[1].startswith("2 hook line(s)") and f"{rejected.stat().st_size} bytes" in got[1]
          and "oldest set aside" in got[1], got)
    check("doctor: and says what to do with them", got and "delete the file" in got[2] and str(rejected) in got[2], got)


def main() -> int:
    def tests(td):
        run(lambda: test_doctor_names_an_agent_left_running_with_nothing_heard(Path(td) / "stuck"))
        run(lambda: test_hl_doctor_says_when_rejected_hook_lines_are_piling_up(Path(td) / "rejected"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
