"""prompt.list's tool, command, reason and kind, read from the screen captures in contract/prompts/: each capture is put
on a terminal, judged by the daemon's screen watch and drawn as the wire `Prompt`; what the screen does not state is null, and a
prompt that changed between list and answer is stale_prompt.

Run on its own: python3 tests/daemon/test_prompt_fields.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.actions import prompt_blocks  # noqa: E402
from handline.daemon import prompts, rpc_agents  # noqa: E402
from handline.session import promptparse, waiting  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_prompt_blocks import _Term, _open, _pass  # noqa: E402
from test_rpc_input import Answers, _call, _code  # noqa: E402

CAPTURES = sorted(Path("contract/prompts").glob("*.json"))


def _prompt_of(screen: list[str]) -> dict | None:
    con = db.connect()
    try:
        _Term(screen)
        _pass(con)
        row = _open(con)
        return rpc_agents.prompt_of(prompt_blocks.of_row(row)) if row else None
    finally:
        con.close()


def test_every_capture_reads_as_the_fixture_says(home: Path) -> None:
    check("captures: there is one per kind the daemon lists", len(CAPTURES) >= 4, str(CAPTURES))
    for f in CAPTURES:
        cap = json.loads(f.read_text())
        _daemon_home(home / cap["name"], 20)
        got = _prompt_of(cap["screen"])
        exp = cap["expect"]
        seen = got and {"kind": got["kind"], "tool": got["tool"], "command": got["command"], "reason": got["reason"],
                        "options": [o["key"] for o in got["options"]]}
        check(f"{cap['name']}: kind, tool, command, reason and option keys are what the capture's screen says", seen == exp, str((seen, exp)))
        check(f"{cap['name']}: the raw lines are always there", bool(got and got["lines"]), str(got))


def test_a_field_the_screen_does_not_state_is_null_never_guessed(home: Path) -> None:
    check("fields: an edit approval's title was never captured: no tool, no command, no reason",
          promptparse.permission_fields("Edit file", ["src/a.py", "│ a reason"]) == {"tool": None, "command": None, "reason": None})
    check("fields: nothing between the title and the question is no command and no reason",
          promptparse.permission_fields("Bash command", []) == {"tool": "Bash", "command": None, "reason": None})
    check("fields: a boxed command alone has no reason", promptparse.permission_fields("Bash command", ["│ ls", "│ -la"])
          == {"tool": "Bash", "command": "ls\n-la", "reason": None})
    check("fields: a dialog with no exiting option is a dialog", promptparse.dialog_kind([(1, "Yes"), (2, "No")]) == "dialog")


def test_a_prompt_that_changed_between_list_and_answer_is_stale(home: Path) -> None:
    cap = json.loads(Path("contract/prompts/permission_bash_boxless.json").read_text())
    _daemon_home(home, 20)
    waiting.note("backend", "Bash", {"command": "rm -rf build"}, after_event_id=7)
    con = db.connect()
    try:
        _Term(cap["screen"])
        prompts.watch_once(con, lambda m: None)
    finally:
        con.close()
    listed = _call("prompt.list", {"agent": "backend"})["result"]["prompts"][0]
    check("stale: the listed prompt reads the capture's command", listed["command"] == "rm -rf build", str(listed))
    waiting.note("backend", "Bash", {"command": "rm -rf /"}, after_event_id=8)
    con = db.connect()
    try:
        _Term([ln.replace("rm -rf build", "rm -rf /") for ln in cap["screen"]])
        prompts.watch_once(con, lambda m: None)
    finally:
        con.close()
    with Answers() as sent:
        r = _call("prompt.answer", {"agent": "backend", "prompt": listed["id"], "choice": "yes", "request_id": "s1"})
        check("stale: answering the prompt listed before the screen changed is stale_prompt, carrying the one open now, and sends nothing",
              _code(r) == "stale_prompt" and r["error"]["data"]["current"]["command"] == "rm -rf /" and not sent.calls, str(r))


def main() -> int:
    def tests(td):
        run(lambda: test_every_capture_reads_as_the_fixture_says(Path(td) / "d0"))
        run(lambda: test_a_field_the_screen_does_not_state_is_null_never_guessed(Path(td) / "d2"))
        run(lambda: test_a_prompt_that_changed_between_list_and_answer_is_stale(Path(td) / "d1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
