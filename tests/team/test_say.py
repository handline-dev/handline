"""Tests: `ocp say` (T293, auditor #1655/#1657).

Run on its own: python3 tests/team/test_say.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


def _say_world(home: Path) -> None:
    _board_world(home)


def test_say_delivers_ahead_of_the_queue_and_never_parks(home: Path) -> None:
    """T293: a say is delivered as its own row, kind='say', past whatever the
    target already holds (send_now) and never the head of its queue - and the
    requester never parks on it, only the fixed 30-minute reply window."""
    _say_world(home)
    con = db.connect()
    try:
        ok, msg, first = askmod.ask("worker", "do the real thing", requester="manager")
        check("setup: an ordinary ask queues first", ok, msg)
        check("setup: it is worker's head", db.head_of_queue(con, "worker")["id"] == first)

        ok, msg, rid = askmod.say("worker", "is T1 close to done?", requester="manager")
        check("say: opens", ok, msg)
        row = db.get_request(con, rid)
        check("say: kind is 'say'", row["kind"] == "say", row["kind"])
        check("say: no files, ever - the row carries none", row["files"] is None)
        check("say: state is queued - not yet a decided answer",
              row["state"] == "queued", row["state"])
        check("say: never the head of the queue - the ordinary ask still is",
              db.head_of_queue(con, "worker")["id"] == first)
        check("say: delivered ahead of the queue - by its kind, with no mark", db.tells_now(row))
        check("say: the requester never parks on it",
              db.get_agent_state(con, "manager") is None
              or db.get_agent_state(con, "manager")["state"] != "PARKED")
    finally:
        con.close()


def test_is_lookup_bash_matches_the_allowlist_by_prefix(home: Path) -> None:
    """#1657: the read-only Bash allowlist, matched by command prefix so a
    flag on a listed command is still a look-up, but an unlisted command
    is not, whatever its own flags."""
    for cmd in ("git log", "git log --oneline -5", "git show HEAD", "git diff --stat",
               "git status", "ls", "ls -la", "cat foo.py",
               "head -n 20 bar.py", "hl read 42", "hl inbox --all", "hl task list",
               "hl team", "hl task board"):
        check(f"is_lookup_bash: {cmd!r} is a look-up", actions.is_lookup_bash(cmd))
    for cmd in ("git commit -m x", "git push", "rm -rf /", "curl http://x", "pytest",
               "npm test", "gitlog", "cats foo.py",
               "git branch", "git branch -a", "git branch -D main"):
        check(f"is_lookup_bash: {cmd!r} is not a look-up", not actions.is_lookup_bash(cmd))


def test_is_lookup_bash_refuses_a_shell_operator_before_the_prefix_check(home: Path) -> None:
    """A pipe, redirect or chain is refused outright, whatever command it names."""
    for cmd in ("cat > x", "git log | sh", "head -1 a && make", "ls; rm -rf /",
               "cat foo `whoami`", "git log $(echo x)", "cat foo\nrm bar"):
        check(f"is_lookup_bash: {cmd!r} is refused for its shell operator, not "
              f"read as a look-up", not actions.is_lookup_bash(cmd))


NO_SUCH = 99999


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_say_delivers_ahead_of_the_queue_and_never_parks(Path(td) / 'sayqueue'))
        run(lambda: test_is_lookup_bash_matches_the_allowlist_by_prefix(Path(td) / 'saylookup'))
        run(lambda: test_is_lookup_bash_refuses_a_shell_operator_before_the_prefix_check(
            Path(td) / 'sayshellop'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
