"""Tests: a Claude Code another process started inside an agent's session is not that agent.

It inherits the agent's name and hooks, so it could close the agent's tasks and speak as it. The
caller's own ancestry tells them apart: the agent's session has one Claude Code above every command and
hook it runs, the nested one two.

Run on its own: python3 tests/infra/test_nested_claude.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402


def test_a_program_path_is_claude_code_only_when_it_names_it() -> None:
    check("claude: the command, the installer's versioned binary and an npm package are told",
          all(sessiontoken.is_claude(p) for p in ("/usr/local/bin/claude",
                                                  "/opt/share/claude/versions/2.1.289",
                                                  "/opt/lib/node_modules/@anthropic-ai/claude-code/cli.js")))
    check("claude: a shell, python and nothing are not",
          not any(sessiontoken.is_claude(p) for p in ("/bin/zsh", "/usr/bin/python3", "", None)))


def test_the_count_of_claude_code_above_a_process_follows_its_ancestry() -> None:
    chain = {10: (9, "/bin/zsh"), 9: (8, "/x/claude/versions/1"), 8: (7, "/bin/bash"), 7: (6, "/x/claude/versions/1"),
             6: (1, "/bin/zsh")}
    real_proc, real_exe = sessiontoken._proc, sessiontoken._exe
    sessiontoken._proc = lambda pid: (chain[pid][0], "t") if pid in chain else None
    sessiontoken._exe = lambda pid: chain[pid][1] if pid in chain else None
    try:
        check("claude: a command with one above it counts one and is not nested",
              sessiontoken.claude_ancestors(8) == 1 and not sessiontoken.nested(8))
        check("claude: one under a second counts two and is nested",
              sessiontoken.claude_ancestors(10) == 2 and sessiontoken.nested(10))
        check("claude: a process outside any session counts none", sessiontoken.claude_ancestors(6) == 0)
    finally:
        sessiontoken._proc, sessiontoken._exe = real_proc, real_exe


def _fake_claude(root: Path) -> Path | None:
    """A copy of bash named like the installer's binary, signed again so macOS runs it; None where that
    cannot be done."""
    exe = root / "claude" / "versions" / "9.9.9"
    exe.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy("/bin/bash", exe)
        if sys.platform == "darwin":
            subprocess.run(["codesign", "--force", "-s", "-", str(exe)], capture_output=True, timeout=30, check=True)
        ok = subprocess.run([str(exe), "-c", "echo ok"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if ok.stdout.strip() == "ok" else None


def test_a_claude_inside_an_agent_s_session_cannot_speak_as_the_agent(home: Path) -> None:
    use_home(home)
    exe = _fake_claude(home / "fake")
    if exe is None:
        print("SKIPPED  nested claude: no program here can be run under the name claude")
        return
    agents.register("scout")
    probe = home / "probe.py"
    # Only the fake claude counts as Claude Code here: whoever runs this suite is under a real one already.
    probe.write_text(
        "import io, json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from handline.infra import sessiontoken\n"
        "real = sessiontoken._exe\n"
        "sessiontoken._exe = lambda pid: (real(pid) if '/fake/' in (real(pid) or '') else None)\n"
        "mode = sys.argv[1]\n"
        "if mode == 'who':\n"
        "    from handline.team import ask\n"
        "    print(json.dumps([ask.current_agent(), ask.asking_agent()]))\n"
        "elif mode == 'hook':\n"
        "    from handline.session import events\n"
        "    events.record_from_stdin(['UserPromptSubmit'])\n"
        "else:\n"
        "    from handline.cli import parser\n"
        "    parser.main(['task', 'done', 'T9', 'evidence'])\n")
    env = dict(os.environ, OCP_AGENT="scout", HANDLINE_AGENT="scout", OCP_ACCOUNT="alpha", OCP_HOME=str(home),
               HANDLINE_HOME=str(home))
    hook = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "x"})

    def under(depth: int, mode: str) -> subprocess.CompletedProcess:
        cmd = f"{shlex_quote(sys.executable)} {shlex_quote(str(probe))} {mode}; :"
        for _ in range(depth):
            cmd = f"{shlex_quote(str(exe))} -c {shlex_quote(cmd + '; :')}"
        return subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60,
                              input=hook)

    mine = under(1, "who")
    check("nested: the agent's own session still reads as the agent",
          json.loads(mine.stdout or "[]") == ["scout", "scout"], mine.stdout + mine.stderr[-200:])
    theirs = under(2, "who")
    check("nested: a claude started inside it is nobody - not the agent, and not the human either",
          json.loads(theirs.stdout or "[]") == [None, None], theirs.stdout + theirs.stderr[-200:])

    def hooks() -> int:
        con = db.connect()
        try:
            return con.execute("SELECT count(*) FROM events WHERE agent = 'scout' AND kind = 'UserPromptSubmit'"
                               ).fetchone()[0]
        finally:
            con.close()

    under(2, "hook")
    check("nested: a hook a nested claude fires is ignored", hooks() == 0, str(hooks()))
    under(1, "hook")
    check("nested: the agent's own hook is recorded", hooks() == 1, str(hooks()))

    own = under(1, "done")
    nested = under(2, "done")
    check("nested: the agent's own `hl task done` is taken as the agent's (it gets as far as looking for the task)",
          "no task T9" in own.stdout, own.stdout + own.stderr[-200:])
    check("nested: the same command from a nested claude is refused as an inherited name, and says so",
          "second Claude Code" in nested.stdout and "no task" not in nested.stdout, nested.stdout + nested.stderr[-200:])


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def main() -> int:
    def tests(td):
        run(lambda: test_a_program_path_is_claude_code_only_when_it_names_it())
        run(lambda: test_the_count_of_claude_code_above_a_process_follows_its_ancestry())
        run(lambda: test_a_claude_inside_an_agent_s_session_cannot_speak_as_the_agent(Path(td) / "n1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
