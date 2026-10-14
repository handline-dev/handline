"""T395: the daemon's screen watch keeps one row per agent for the prompt its screen shows, and
the bridge answers through that row - never through a screen read of its own.

Run on its own: python3 tests/daemon/test_prompt_blocks.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "session"))


def test_the_answer_is_a_socket_verb(home: Path) -> None:
    from handline.daemon import socket_server
    check("answer_block is a verb of the daemon's socket", "answer_block" in socket_server.VERBS)


def main() -> int:
    def tests(td):
        run(lambda: test_the_answer_is_a_socket_verb(Path(td) / 'b7'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
