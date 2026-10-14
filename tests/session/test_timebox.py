"""Tests: a timebox's duration needs its unit, and the background-work times follow the agent, then the global setting.

Run on its own: python3 tests/session/test_timebox.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

TEAM = """
teams:
  dev: [marina, dohyun]
agents:
  manager: {}
  marina:  {}
  dohyun:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: async }
  - { from: "dev.*", to: "manager", relation: peer, response: async }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
"""


def test_a_duration_needs_its_unit(home: Path) -> None:
    check("timebox: 90m, 2h, 45s and 1d parse",
          [timefmt.parse_span(t) for t in ("90m", "2h", "45s", "1d")] == [5400, 7200, 45, 86400])
    bad = []
    for t in ("90", "m", "0m", "-5m", "1.5h", "2w", ""):
        try:
            timefmt.parse_span(t)
        except ValueError:
            continue
        bad.append(t)
    check("timebox: no unit, no number, zero, negative or fractional is refused", not bad, str(bad))


def _world(home: Path) -> None:
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    for a in ("manager", "marina", "dohyun"):
        agents.register(a)


def test_the_background_times_follow_the_agent_then_the_global_setting_then_the_default(home: Path) -> None:
    _world(home)
    check("times: with nothing set, the notice is the idle close and the grace is 5 minutes",
          (idle_close.bg_notice_s("marina"), idle_close.bg_grace_s("marina")) == (600, 300))
    idle_close.set_close_timeout(1200)
    check("times: the notice follows the idle close", idle_close.bg_notice_s("marina") == 1200)
    idle_close.set_bg_seconds("bg_notice_s", 400)
    idle_close.set_bg_seconds("bg_grace_s", 120)
    check("times: the global settings win over the defaults",
          (idle_close.bg_notice_s("dohyun"), idle_close.bg_grace_s("dohyun")) == (400, 120))
    idle_close.set_bg_seconds("bg_grace_s", 60, "marina")
    check("times: an agent's own wins over the global one, and only for it",
          idle_close.bg_grace_s("marina") == 60 and idle_close.bg_grace_s("dohyun") == 120
          and idle_close.bg_notice_s("marina") == 400)
    idle_close.set_bg_seconds("bg_grace_s", None, "marina")
    check("times: clearing it goes back to the global one", idle_close.bg_grace_s("marina") == 120)
    bad = []
    for args in (("bg_grace_s", 0), ("bg_grace_s", -5), ("other", 5), ("bg_notice_s", None)):
        try:
            idle_close.set_bg_seconds(*args)
            bad.append(args)
        except ValueError:
            pass
    check("times: zero, negative, another key and a global clear are refused", not bad, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_a_duration_needs_its_unit(Path(td) / 'tb0'))
        run(lambda: test_the_background_times_follow_the_agent_then_the_global_setting_then_the_default(Path(td) / 'tb10c'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
