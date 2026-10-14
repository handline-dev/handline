"""Tests: the SGR reader that turns a row's codes into style runs.
Run on its own: python3 tests/daemon/test_termstyled.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import ansi  # noqa: E402
from handline.session import backends  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

RED = "\x1b[1;31mred\x1b[0m plain"


def test_the_reader_turns_sgr_codes_into_runs() -> None:
    check("ansi: text without codes has no runs", ansi.parse("hello  ") == ("hello", []))
    check("ansi: bold red, then reset", ansi.parse(RED) == ("red plain", [{"from": 0, "to": 3, "style": {"bold": True, "fg": 1}}]))
    check("ansi: bright colours are palette 8-15", ansi.parse("\x1b[91;102ma")[1][0]["style"] == {"fg": 9, "bg": 10})
    check("ansi: 256 colours and true colour", ansi.parse("\x1b[38;5;208;48;2;1;2;255mx")[1][0]["style"] == {"fg": 208, "bg": "#0102ff"})
    check("ansi: the colon form with an empty colour space", ansi.parse("\x1b[38:2::1:2:3mx")[1][0]["style"] == {"fg": "#010203"})
    check("ansi: neighbours with one style are one run", len(ansi.parse("\x1b[4mab\x1b[4mcd")[1]) == 1)
    check("ansi: 22 clears bold and dim, 39 and 49 the default colours", ansi.parse("\x1b[1;2;31;41mA\x1b[22;39;49mB")[1] == [{"from": 0, "to": 1, "style": {"bold": True, "dim": True, "fg": 1, "bg": 1}}])
    text, spans = ansi.parse("ab\x1b[44m   \x1b[m")
    check("ansi: blank cells with a background run past the end of the text", text == "ab" and spans == [{"from": 2, "to": 5, "style": {"bg": 4}}], str(spans))
    check("ansi: other escapes and controls are dropped", ansi.parse("\x1b[?25h\x1b(B\x0ea\x1b]0;t\x07b") == ("ab", []))
    check("ansi: a wide character is one cell", ansi.parse("\x1b[3m漢字\x1b[m")[1] == [{"from": 0, "to": 2, "style": {"italic": True}}])


def main() -> int:
    def tests(td):
        run(lambda: test_the_reader_turns_sgr_codes_into_runs())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
