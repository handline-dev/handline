"""T946: a body a shell would expand (a backtick, $( ) comes from `--file PATH` or `-` (stdin), on
hl say, task new, task handoff and task memo add/edit - the text arrives byte for byte.

Run on its own: python3 tests/team/test_body_input.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.cli import ask as cliask  # noqa: E402

TRICKY = "ran `git log` and $(date): stays literal\nsecond line"


def test_text_arg_reads_stdin_for_a_dash_and_refuses_what_is_empty_or_doubled(home: Path) -> None:
    saved = sys.stdin
    try:
        sys.stdin = io.StringIO(TRICKY)
        check("dash: '-' as the text reads stdin whole", cliask._text_arg("-", None, "body") == (TRICKY, None))
        sys.stdin = io.StringIO(TRICKY)
        check("dash: '-' as --file reads stdin too", cliask._text_arg(None, "-", "body") == (TRICKY, None))
        sys.stdin = io.StringIO("  \n")
        text, err = cliask._text_arg("-", None, "body")
        check("dash: an empty stdin is refused, naming stdin", text is None and "stdin" in err, str(err))
        text, err = cliask._text_arg("words", "-", "body")
        check("dash: text and --file - together are refused", text is None and "not both" in err, str(err))
    finally:
        sys.stdin = saved


def test_the_refusal_names_the_ways_out_that_exist(home: Path) -> None:
    from handline.session import shellguard
    why = shellguard.guard("Bash", {"command": 'hl task new "`x`"'})
    check("guard: the refusal names task new and memo, and stdin with -",
          why and "hl task new" in why and "hl task memo add" in why and "stdin with -" in why, str(why))


def main() -> int:
    def tests(td):
        run(lambda: test_text_arg_reads_stdin_for_a_dash_and_refuses_what_is_empty_or_doubled(Path(td) / "i1"))
        run(lambda: test_the_refusal_names_the_ways_out_that_exist(Path(td) / "i3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
