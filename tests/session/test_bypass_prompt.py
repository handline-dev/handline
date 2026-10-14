"""Tests: claude's bypass-permissions warning screen is recognised and its highlight read.

Run on its own: python3 tests/session/test_bypass_prompt.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

# The founder's capture, whole: unnumbered options, the cursor on "No, exit".
_RULE = "─" * 150


def _screen(cursor: str = "No, exit") -> list[str]:
    def row(label: str) -> str:
        return f"  ❯ {label}" if label == cursor else f"    {label}"
    return ["", _RULE,
            "  WARNING: Claude Code running in Bypass Permissions mode",
            "",
            "  In Bypass Permissions mode, Claude Code will not ask for your approval before running "
            "potentially dangerous commands.",
            "  This mode should only be used in a sandboxed container/VM that has restricted internet "
            "access and can easily be restored if damaged.",
            "",
            "  By proceeding, you accept all responsibility for actions taken while running in "
            "Bypass Permissions mode.",
            "",
            "  https://code.claude.com/docs/en/security",
            "",
            row("No, exit"),
            row("Yes, I accept"),
            "",
            "  Enter to confirm · Esc to cancel"]


def test_the_founders_screen_is_recognised_and_its_highlight_read(home: Path) -> None:
    from handline.vendors import claude
    check("screen: the cursor on 'No, exit' is read as that option",
          claude._bypass_screen(_screen("No, exit")) == "No, exit")
    check("screen: and on 'Yes, I accept'", claude._bypass_screen(_screen("Yes, I accept")) == "Yes, I accept")
    check("screen: no cursor row is showing-but-unread, not absent", claude._bypass_screen(_screen("")) == "")
    check("screen: the folder-trust screen is not it",
          claude._bypass_screen(LIVE_TRUST_PROMPT_CAPTURE.split("\n")) is None)
    cut = [r for r in _screen("No, exit") if "No, exit" not in r and "Yes, I accept" not in r]
    check("screen: the warning with its options cut off is not read as one to answer",
          claude._bypass_screen(cut) is None)
    quoted = ["  an agent quoting: WARNING: Claude Code running in Bypass Permissions mode"] + AT_THE_PROMPT
    check("screen: a transcript that quotes the words is not it", claude._bypass_screen(quoted) is None)


def main() -> int:
    def tests(td):
        run(lambda: test_the_founders_screen_is_recognised_and_its_highlight_read(Path(td) / 'bp0'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
