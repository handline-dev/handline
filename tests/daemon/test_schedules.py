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
from handline.team import schedules
from handline.infra import cron  # noqa: E402


def _at(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi).timestamp())


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
    rc, out = _cli(["schedule", "add", "--agent", "backend", "--cron", "*/30 * * * *", "--message", "patrol: check hl team"],
                   as_agent=None)
    check("cli: add writes agent.yaml and shows it", rc == 0 and "1. */30 * * * *" in out and "patrol" in out, out)
    check("cli: and the daemon reads it back", schedules.of("backend")
          == [{"cron": "*/30 * * * *", "message": "patrol: check hl team"}])
    rc, out = _cli(["schedule", "add", "--agent", "backend", "--cron", "nope", "--message", "x"], as_agent=None)
    check("cli: a bad expression is refused and nothing is written", rc == 1 and len(schedules.of("backend")) == 1, out)
    rc, out = _cli(["schedule", "list"], as_agent=None)
    check("cli: list names every agent that has one", rc == 0 and "backend 1." in out, out)
    rc, out = _cli(["schedule", "edit", "--agent", "backend", "--index", "1", "--cron", "0 9 * * 1-5", "--message", "new words"],
                   as_agent=None)
    check("cli: edit replaces that one and shows it", rc == 0 and "1. 0 9 * * 1-5" in out and schedules.of("backend")
          == [{"cron": "0 9 * * 1-5", "message": "new words"}], out)
    for label, argv in (("a bad --index", ["--index", "2", "--cron", "0 9 * * *", "--message", "x"]),
                        ("a bad cron", ["--index", "1", "--cron", "nope", "--message", "x"]),
                        ("no --cron", ["--index", "1", "--message", "x"]),
                        ("no --message", ["--index", "1", "--cron", "0 9 * * *"]),
                        ("no --index", ["--cron", "0 9 * * *", "--message", "x"])):
        rc, out = _cli(["schedule", "edit", "--agent", "backend", *argv], as_agent=None)
        check(f"cli: edit with {label} is refused and nothing changes", rc == 1 and schedules.of("backend")
              == [{"cron": "0 9 * * 1-5", "message": "new words"}], out)
    rc, out = _cli(["schedule", "remove", "--agent", "backend", "--index", "1"], as_agent=None)
    check("cli: remove drops it", rc == 0 and schedules.of("backend") == [], out)


def main() -> int:
    def tests(td):
        run(lambda: test_a_cron_expression_names_its_minutes(Path(td) / "c1"))
        run(lambda: test_the_schedules_setting_is_validated_and_set_from_the_command_line(Path(td) / "c2"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
