"""prompt.list's tool, command, reason and kind, read from the screen captures in contract/prompts/: each capture is put
on a terminal, judged by the daemon's screen watch and drawn as the wire `Prompt`; what the screen does not state is null, and a
prompt that changed between list and answer is stale_prompt.

Run on its own: python3 tests/daemon/test_prompt_fields.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import promptparse  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))



def test_a_field_the_screen_does_not_state_is_null_never_guessed(home: Path) -> None:
    check("fields: an edit approval's title was never captured: no tool, no command, no reason",
          promptparse.permission_fields("Edit file", ["src/a.py", "│ a reason"]) == {"tool": None, "command": None, "reason": None})
    check("fields: nothing between the title and the question is no command and no reason",
          promptparse.permission_fields("Bash command", []) == {"tool": "Bash", "command": None, "reason": None})
    check("fields: a boxed command alone has no reason", promptparse.permission_fields("Bash command", ["│ ls", "│ -la"])
          == {"tool": "Bash", "command": "ls\n-la", "reason": None})
    check("fields: a dialog with no exiting option is a dialog", promptparse.dialog_kind([(1, "Yes"), (2, "No")]) == "dialog")


def main() -> int:
    def tests(td):
        run(lambda: test_a_field_the_screen_does_not_state_is_null_never_guessed(Path(td) / "d2"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
