"""What is actually true about the agents right now.

Everything else in this system reads a row we wrote ourselves. That is how a
state that went wrong stayed wrong: `UserPromptSubmit` set an agent RUNNING,
nothing ever set it back, and for two and a half hours every screen faithfully
reported a value nobody could contradict. The cure is not more hooks - an edge
that is missed is missed forever - it is asking the world again on a schedule.

Two questions are answerable from outside, and both are answered for every
agent at once, so the cost does not grow with the team:

    does its terminal still exist   one `orca terminal list`
    is a session running in it      one pass over the process table

Measured at nine agents: 0.13s and 0.09s. Cost was never the reason this was
missing.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from . import orca


@dataclass
class Observed:
    """One agent as the outside world reports it.

    None means we could not look, which is not the same as no. Every caller has
    to keep those apart: acting on "I could not ask Orca" as though it were
    "the terminal is gone" would tear down a healthy team the first time Orca
    was slow to start.
    """
    terminal_alive: bool | None = None
    session_pid: int | None = None
    session_seen: bool = False      # whether the process table could be read


def is_session(command: str) -> bool:
    """Is this process an agent's session, rather than something else of ours?

    A fold is a headless `claude -p` that inherits whatever OCP_AGENT its caller
    had. Counting it would tell the sweep an agent is alive for as long as a
    fold runs and closed the moment it ends.
    """
    if "claude" not in command:
        return False
    return " -p " not in command and not command.endswith(" -p")


def process_table() -> list[tuple[int, str]] | None:
    """(pid, command) for everything running, or None if we could not look.

    None is not an empty machine: a caller that treats the two alike concludes
    every session is gone the one time `ps` fails.
    """
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,command="],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return rows


def env_of(pid: int, name: str) -> str | None:
    """One environment variable of a running process, as it was started with.

    The variables `ocp launch` sets are how a process says which agent and
    which installation it belongs to; nothing it writes down can be trusted to
    the same degree. None means unreadable or unset, and those two are the same
    answer here: neither says the process is somebody else's.
    """
    try:
        env = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for token in env.split():
        if token.startswith(f"{name}="):
            return token.split("=", 1)[1]
    return None


def live_sessions() -> dict[str, int] | None:
    """agent -> pid for every claude session that is actually running.

    Read from the environment `ocp launch` set before exec'ing claude, which is
    the same string the rest of the system keys on. A fold is excluded: it is a
    headless `claude -p` that inherits whatever OCP_AGENT its caller had, and it
    is not the agent's session.
    """
    table = process_table()
    if table is None:
        return None

    found: dict[str, int] = {}
    for pid, cmd in table:
        if not is_session(cmd):
            continue
        name = env_of(pid, "OCP_AGENT")
        if name:
            found.setdefault(name, pid)
    return found


def observe(agents: list[str], terminals: dict[str, str | None]) -> dict[str, Observed]:
    """One look at the world, for all the agents at once."""
    handles = orca.live_terminals() if orca.available() else None
    sessions = live_sessions()
    out: dict[str, Observed] = {}
    for a in agents:
        o = Observed(session_seen=sessions is not None)
        h = terminals.get(a)
        if handles is not None and h:
            o.terminal_alive = h in handles
        if sessions is not None:
            o.session_pid = sessions.get(a)
        out[a] = o
    return out
