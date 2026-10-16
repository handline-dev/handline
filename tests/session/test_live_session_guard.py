"""Tests: a process whose environment `ps` withholds is only "not ours" when it is another user's, and what the
diagnosis and an unreadable process table say.

Run on its own: tools/test.sh tests/session/test_live_session_guard.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

SHELL = ["$ "]


def test_a_process_whose_environment_ps_withholds_is_not_ours_only_if_it_is_not_ours(home: Path) -> None:
    _daemon_home(home, 20)
    obsmod.live_sessions = REAL_OBSERVE["live_sessions"]
    real = (obsmod._ps_command, obsmod._owner_uid, obsmod.process_table, obsmod.ENV_RETRY_S)
    me = os.getuid()
    calls: list[str] = []
    try:
        obsmod.ENV_RETRY_S = 0
        argv = "claude --resume abc\n"
        obsmod.process_table = lambda: [(900, "claude --resume abc")]

        def ps(pid, flags, env_after=None):
            calls.append(flags)
            if flags == "ww":
                return argv
            return argv if (env_after is None or calls.count("eww") < env_after) else argv + "OCP_AGENT=backend X=1\n"
        # Another user's process: ps withholds the environment for good - left out, as it always was.
        obsmod._ps_command = ps
        obsmod._owner_uid = lambda pid: me + 1
        check("environment: another user's process is not ours, and the table is still readable",
              obsmod._env_or_reason(900) == (None, True) and obsmod.live_sessions() == {})
        # One of ours that comes back without an environment once: looked at again, and found.
        calls.clear()
        obsmod._owner_uid = lambda pid: me
        obsmod._ps_command = lambda pid, flags: ps(pid, flags, env_after=2)
        env, notours = obsmod._env_or_reason(900)
        check("environment: our own process that withheld it once is read on the second look",
              notours is False and env and "OCP_AGENT=backend" in env, str((env, notours)))
        calls.clear()
        check("environment: and the live session is listed", obsmod.live_sessions() == {"backend": 900})
        # One of ours that never gives it: left out of the table like another user's, and the reading
        # stays readable - one such process must not blank every agent's session (it did, for an hour).
        obsmod._ps_command = lambda pid, flags: argv
        check("environment: our own process that withholds it for good is left out, the reading stays readable",
              obsmod._env_or_reason(900) == (None, True) and obsmod.live_sessions() == {})
        obsmod.process_table = lambda: [(900, "claude --resume abc"), (901, "claude --resume def")]
        envs = {900: argv, 901: "claude --resume def\nOCP_AGENT=reviewer X=1\n"}

        def one_unreadable(pid, flags):
            if flags == "ww":
                return argv if pid == 900 else "claude --resume def\n"
            return envs[pid] if pid == 901 else argv
        obsmod._ps_command = one_unreadable
        check("environment: so another agent's session is still listed beside the one nobody could read",
              obsmod.live_sessions() == {"reviewer": 901}, str(obsmod.live_sessions()))
        obsmod._owner_uid = lambda pid: None
        check("environment: an owner that cannot be read is what it always was: left out",
              obsmod._env_or_reason(900) == (None, True))
    finally:
        obsmod._ps_command, obsmod._owner_uid, obsmod.process_table, obsmod.ENV_RETRY_S = real


def test_the_diagnosis_names_each_session_looking_process_and_leaks_no_environment(home: Path) -> None:
    _daemon_home(home, 20)
    real = (obsmod.process_table, obsmod._env_or_reason)
    try:
        obsmod.process_table = lambda: [(1, "claude one"), (2, "claude two"), (3, "claude three"), (4, "vim notes"),
                                        (5, "claude five")]
        envs = {1: ("OCP_AGENT=backend ANTHROPIC_SECRET=sk-ant-hunter2 X=1", False), 2: (None, True),
                3: (None, False), 5: ("PATH=/bin HOME=/h", False)}
        obsmod._env_or_reason = lambda pid: envs[pid]
        got = obsmod.diagnose()
        check("diagnose: each session-looking process is named with how it was read, others left out",
              got == ["pid 1: OCP_AGENT=backend", "pid 2: another user's process",
                      "pid 3: its environment could not be read", "pid 5: OCP_AGENT=unset"], str(got))
        check("diagnose: and nothing of the rest of an environment is in it", "hunter2" not in " ".join(got))
        obsmod.process_table = lambda: None
        check("diagnose: an unreadable table says so", obsmod.diagnose() == ["the process table could not be read"])
        obsmod.process_table = lambda: [(4, "vim notes")]
        check("diagnose: and none looking like a session says that", obsmod.diagnose() == ["no process looks like a session"])
    finally:
        obsmod.process_table, obsmod._env_or_reason = real


def test_a_preamble_with_a_dash_p_in_it_does_not_make_a_session_headless(home: Path) -> None:
    """The cause of the process table missing the auditor from 14:35 (T1087): a session's command line carries
    its whole preamble, the last session's handoff note among it, and one `git commit-tree -p main` in that
    note read as claude's `-p`, so the live session was not a session."""
    drv = vendors.get("claude")
    exe = "/Users/you/.local/bin/claude"
    note = "--append-system-prompt written by the agent: made it with commit-tree $T -p main -p abc, then mkdir -p x"
    sessions = (f"{exe} --model opus --effort high --no-chrome {note}",
                f"{exe} --model opus --append-system-prompt {note} --settings {{\"deny\": [\"-p \"]}}",
                f"{exe} --resume abc --append-system-prompt see ps -p 1")
    for line in sessions:
        check(f"is_session: a -p inside the preamble leaves {line[40:80]!r} a session", drv.is_session(line))
    for line in (f"{exe} --model haiku -p fold it",
                 f"{exe} --model haiku -p",
                 f"{exe} -p {note}"):
        check(f"is_session: claude's own -p still makes {line[:40]!r} headless", not drv.is_session(line))


def test_a_process_table_that_cannot_be_read_says_why_once_per_episode(home: Path) -> None:
    import subprocess
    import types
    _daemon_home(home, 20)
    obsmod.live_sessions = REAL_OBSERVE["live_sessions"]
    real = (obsmod.subprocess, obsmod.report, obsmod.ENV_RETRY_S, obsmod._failing)
    said: list[str] = []
    mode = {"table": "ok", "eww": "ok"}
    argv = "claude --resume abc"

    def run(cmd, **kw):
        out = lambda text="", rc=0, err="": types.SimpleNamespace(stdout=text, stderr=err, returncode=rc)
        if cmd[:2] == ["ps", "-Ao"]:
            if mode["table"] == "missing":
                raise FileNotFoundError(2, "no ps")
            if mode["table"] == "timeout":
                raise subprocess.TimeoutExpired(cmd, 20)
            if mode["table"] == "exit":
                return out(rc=1, err="ps: Operation not permitted\nsecond line is not kept")
            return out(f"900 {argv}\n")
        if cmd[1] == "ww":
            return out(argv + "\n")
        if cmd[1] == "eww":
            if mode["eww"] == "timeout":
                raise subprocess.TimeoutExpired(cmd, 10)
            if mode["eww"] == "other":
                return out("something else entirely OCP_AGENT=backend SECRET_TOKEN=hunter2\n")
            return out(argv + " OCP_AGENT=backend SECRET_TOKEN=hunter2\n")
        return out()
    try:
        obsmod.ENV_RETRY_S = 0
        obsmod.subprocess = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError)
        obsmod.report = said.append
        obsmod._failing = False
        check("process table: a readable one lists the session and says nothing", obsmod.live_sessions() == {"backend": 900} and not said, str(said))
        causes = []
        for table, want in (("missing", "ps -Ao could not run: FileNotFoundError 2"), ("timeout", "ps -Ao timed out after 20s"),
                            ("exit", "ps -Ao exited 1: ps: Operation not permitted")):
            mode["table"] = table
            said.clear()
            obsmod._failing = False
            check(f"process table: {table}: the reading is None", obsmod.live_sessions() is None)
            obsmod.live_sessions()
            check(f"process table: {table}: the cause is said once, naming the call and how it failed",
                  len(said) == 1 and said[0] == f"process table could not be read: {want}", str(said))
            causes.append(said[0])
        mode["table"] = "ok"
        said.clear()
        obsmod.live_sessions()
        check("process table: when it reads again, that is said once", said == ["process table readable again"], str(said))
        mode["eww"] = "timeout"
        obsmod._failing = False
        said.clear()
        check("process table: a process whose environment read times out fails the reading", obsmod.live_sessions() is None)
        check("process table: and the cause names the pid, how many sessions, and which call", len(said) == 1
              and "pid 900, one of 1 session processes of 1" in said[0] and "ps eww timed out after 10s" in said[0], str(said))
        causes.append(said[0])
        mode["eww"] = "other"
        obsmod._failing = False
        said.clear()
        check("process table: an eww that does not begin with the argv is a distinct cause", obsmod.live_sessions() is None
              and len(said) == 1 and "did not begin with ps ww's argv" in said[0], str(said))
        causes.append(said[0])
        check("process table: every cause is its own line, and no environment value is in any", len(set(causes)) == len(causes)
              and not any("hunter2" in c or "SECRET" in c for c in causes), str(causes))
    finally:
        obsmod.subprocess, obsmod.report, obsmod.ENV_RETRY_S, obsmod._failing = real


def main() -> int:
    def tests(td):
        run(lambda: test_a_process_whose_environment_ps_withholds_is_not_ours_only_if_it_is_not_ours(Path(td) / "g6"))
        run(lambda: test_the_diagnosis_names_each_session_looking_process_and_leaks_no_environment(Path(td) / "g10"))
        run(lambda: test_a_preamble_with_a_dash_p_in_it_does_not_make_a_session_headless(Path(td) / "g11"))
        run(lambda: test_a_process_table_that_cannot_be_read_says_why_once_per_episode(Path(td) / "g15"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
