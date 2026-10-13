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


def _stdin(text: str, argv: list[str], as_agent: str = "manager") -> tuple[int, str]:
    saved = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        return _cli(argv, as_agent=as_agent)
    finally:
        sys.stdin = saved


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


def test_say_task_new_handoff_and_memo_take_their_text_from_a_file_or_stdin(home: Path) -> None:
    _board_world(home)
    note = home / "body.txt"
    note.write_text(TRICKY)
    con = db.connect()
    try:
        rc, out = _cli(["task", "new", "-t", "1h", "--assign", "worker", "from a file", "--file", str(note)])
        tid = int(out.split("task T", 1)[1].split()[0]) if rc == 0 else 0
        check("task new: --file is the body, byte for byte", rc == 0
              and db.task_requests(con, tid)[0]["body"] == TRICKY, out)
        rc, out = _stdin(TRICKY, ["task", "new", "-t", "1h", "--assign", "worker", "from stdin", "-"])
        tid2 = int(out.split("task T", 1)[1].split()[0]) if rc == 0 else 0
        check("task new: '-' reads the body from stdin", rc == 0
              and db.task_requests(con, tid2)[0]["body"] == TRICKY, out)
        rc, out = _stdin(TRICKY, ["task", "new", "-t", "1h", "--assign", "worker", "both", "x", "--file", "-"])
        check("task new: a body and --file together are refused", rc == 1 and "not both" in out, out)
        rc, out = _stdin("", ["task", "new", "-t", "1h", "--assign", "worker", "empty", "-"])
        check("task new: an empty stdin is refused, nothing opened", rc == 1 and "stdin" in out, out)
        rc, out = _cli(["task", "new", "-t", "1h", "--assign", "worker", "no brief"])
        check("task new: with no body at all it still asks for one", rc == 1 and "needs a body" in out, out)

        ok, _, hand = actions.task_new("manager", "worker", "to hand on", "w")
        rc, out = _stdin(TRICKY, ["task", "handoff", f"T{hand}", "manager", "-"], as_agent="worker")
        check("handoff: '-' reads the note from stdin", rc == 0
              and db.task_handoffs(con, hand)[-1]["note"] == TRICKY, out)

        rc, out = _cli(["task", "memo", f"T{tid}", "add", "--file", str(note)], as_agent="worker")
        memos = db.task_memos(con, tid)
        check("memo add: --file is the memo, byte for byte", rc == 0 and memos and memos[-1]["body"] == TRICKY, out)
        rc, out = _stdin("new words with `ticks`", ["task", "memo", f"T{tid}", "edit", str(memos[-1]["id"]), "-"],
                         as_agent="worker")
        check("memo edit: the id, then '-' reads the new text from stdin", rc == 0
              and db.get_memo(con, memos[-1]["id"])["body"] == "new words with `ticks`", out)
        rc, out = _cli(["task", "memo", f"T{tid}", "add", "inline", "--file", str(note)], as_agent="worker")
        check("memo add: inline text and --file together are refused", rc == 1, out)

        sent = []
        from handline.cli import say as clisay
        real = clisay._send
        clisay._send = lambda verb, **kw: sent.append((verb, kw)) or 0
        try:
            rc, _ = _stdin(TRICKY, ["say", "worker", "-"])
            rc2, _ = _cli(["say", "worker", "--file", str(note)])
        finally:
            clisay._send = real
        check("say: '-' and --file both hand the body over untouched",
              rc == 0 and rc2 == 0 and [kw["body"] for _, kw in sent] == [TRICKY, TRICKY], str(sent))
    finally:
        con.close()


def test_the_refusal_names_the_ways_out_that_exist(home: Path) -> None:
    from handline.session import shellguard
    why = shellguard.guard("Bash", {"command": 'hl task new "`x`"'})
    check("guard: the refusal names task new and memo, and stdin with -",
          why and "hl task new" in why and "hl task memo add" in why and "stdin with -" in why, str(why))


def main() -> int:
    def tests(td):
        run(lambda: test_text_arg_reads_stdin_for_a_dash_and_refuses_what_is_empty_or_doubled(Path(td) / "i1"))
        run(lambda: test_say_task_new_handoff_and_memo_take_their_text_from_a_file_or_stdin(Path(td) / "i2"))
        run(lambda: test_the_refusal_names_the_ways_out_that_exist(Path(td) / "i3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
