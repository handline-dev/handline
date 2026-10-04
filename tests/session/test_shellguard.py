"""Tests: the shell-substitution guard (T401 item 17) - shellguard.guard,
substitutes, and the reason it gives.

Run on its own: python3 tests/session/test_shellguard.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import shellguard  # noqa: E402


def test_a_safe_ocp_command_is_let_through() -> None:
    check("no backtick or $(: nothing to refuse",
          shellguard.guard("Bash", {"command": 'hl say worker "just a note"'}) is None)
    check("a command with no ocp in it is never looked at",
          shellguard.guard("Bash", {"command": 'echo "`whoami`"'}) is None)
    check("a tool other than Bash is never looked at",
          shellguard.guard("Write", {"command": 'hl say worker "`whoami`"'}) is None)


def test_a_substitution_is_refused_with_the_nothing_ran_reason() -> None:
    why = shellguard.guard("Bash", {"command": 'hl say worker "`whoami`"'})
    check("a backtick in double quotes is refused", why is not None, why)
    check("T401 item 17: says up front that nothing ran, heredocs and file writes included",
          why.startswith("Refused: nothing in this command ran, including any heredoc or "
                          "file write in it."), why)
    check("names --file on hl task handoff as one of the safe verbs",
          "hl task handoff" in why and "--file" in why, why)

    why2 = shellguard.guard("Bash", {"command": 'hl say worker "$(whoami)"'})
    check("$( in double quotes is refused the same way", why2 == why, (why, why2))


def main() -> int:
    def tests(_td):
        run(lambda: test_a_safe_ocp_command_is_let_through())
        run(lambda: test_a_substitution_is_refused_with_the_nothing_ran_reason())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
