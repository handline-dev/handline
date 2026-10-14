"""Tests: a timebox's duration needs its unit, and only the measured background-work dialog gets the key that stops it.

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


def _doing(con, tid: int, target: str) -> int:
    """Link a queued, non-fyi request to `tid` so it reads doing - T286's
    clock only runs in that state, the same live read `task_state` makes."""
    rid = db.add_request(con, askmod.HUMAN, target, "look at it", None, "supervisor",
                         "fire_and_forget", None, 0, None)
    db.link_request(con, tid, rid)
    return rid


def test_stopping_key_presses_only_the_measured_dialog(home: Path) -> None:
    def dialog(title="Background work is running", footer="Enter to confirm \u00b7 Esc to cancel",
               marked=("\u276f 1. Exit and stop tasks", "  2. Move to background and exit", "  3. Stay")):
        return termmod.Dialog(title, (), footer, (title, *marked))

    check("stopping_key: the measured dialog is pressed with Enter",
          termmod.stopping_key(dialog()) == (termmod.ENTER, ""))
    near = (
        ("a different title", dialog(title="Something else is running")),
        ("a footer without Enter to confirm", dialog(footer="Esc to cancel")),
        ("the keep option highlighted", dialog(marked=("  1. Exit and stop tasks", "\u276f 3. Stay"))),
        ("a highlighted option that only resembles it", dialog(marked=("\u276f 1. Exit and stop all tasks",))),
        ("two marked lines", dialog(marked=("\u276f 1. Exit and stop tasks", "\u276f 3. Stay"))),
        ("no marked line", dialog(marked=("  1. Exit and stop tasks", "  3. Stay"))),
    )
    for what, d in near:
        key, why = termmod.stopping_key(d)
        check(f"stopping_key: {what} presses nothing", key is None and why, f"{key!r} {why}")


def main() -> int:
    def tests(td):
        run(lambda: test_a_duration_needs_its_unit(Path(td) / 'tb0'))
        run(lambda: test_stopping_key_presses_only_the_measured_dialog(Path(td) / 'tb16'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
