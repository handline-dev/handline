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

import os
import subprocess
import time
from dataclasses import dataclass

from . import term
from .. import vendors
from ..infra import textsafe


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
    """Is this process an agent's session, rather than something else of ours?"""
    return any(drv.is_session(command) for drv in vendors.known())


# Why the last reading came back None: which call, how it failed, the first line of its stderr. Never an environment
# value - the output of `ps eww` is not read into it, only its exit status and error line.
_why: str | None = None
_failing = False
report = None       # the daemon's log, set when it starts: told once when readings begin to fail and once when they recover


def _miss(why: str) -> None:
    global _why
    _why = why


def _ps_failed(call: str, out) -> str:
    """How a `ps` call failed, from its exit status and the first line of what it said on stderr."""
    first = textsafe.oneline((out.stderr or "").strip().splitlines()[0] if (out.stderr or "").strip() else "")[:120]
    return f"{call} exited {out.returncode}" + (f": {first}" if first else "")


def _run_ps(argv: list[str], timeout: int):
    """`ps` with `argv`, or None with the cause noted when it could not run or timed out."""
    call = " ".join(argv[:2])
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _miss(f"{call} timed out after {timeout}s")
    except OSError as e:
        _miss(f"{call} could not run: {type(e).__name__} {e.errno}")
    except subprocess.SubprocessError as e:
        _miss(f"{call} failed: {type(e).__name__}")
    return None


def process_table() -> list[tuple[int, str]] | None:
    """(pid, command) for everything running, or None if we could not look (the cause is in `_why`).

    None is not an empty machine: a caller that treats the two alike concludes
    every session is gone the one time `ps` fails.
    """
    out = _run_ps(["ps", "-Ao", "pid=,command="], 20)
    if out is None:
        return None
    if out.returncode != 0:
        _miss(_ps_failed("ps -Ao", out))
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


def _ps_command(pid: int, flags: str) -> str | None:
    r = _run_ps(["ps", flags, "-o", "command=", "-p", str(pid)], 10)
    if r is None:
        return None
    if r.returncode != 0:
        _miss(_ps_failed(f"ps {flags}", r))
        return None
    if not r.stdout.strip():
        _miss(f"ps {flags} gave nothing: the process is gone")
        return None
    return r.stdout


def _owner_uid(pid: int) -> int | None:
    r = _run_ps(["ps", "-o", "uid=", "-p", str(pid)], 10)
    if r is None:
        return None
    if r.returncode != 0:
        _miss(_ps_failed("ps -o uid", r))
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        _miss("ps -o uid gave no number")
        return None


# `ps eww` gives a live process of ours no environment now and then (one being replaced, or `ps` raced
# by it); a second look a moment later has it.
ENV_RETRY_S = 0.3


def _env_or_reason(pid: int) -> tuple[str | None, bool]:
    """`process_env`'s answer, plus whether a `None` there is definitely
    another user's process (T146) rather than one of the other misses that
    can happen to a live pid we should not give up on.

    An environment `ps` withholds is looked at again once if the process is ours, since `ps` gives
    a live process of ours none now and then. If it is still withheld the process is left out of the
    table, as another user's is: one process whose environment cannot be read must not make the whole
    reading unreadable, which fails every agent's delivery closed at once (it did). The agent that
    process is the session of is covered by the screen (`delivery.session_present`)."""
    argv = _ps_command(pid, "ww")
    with_env = _ps_command(pid, "eww")
    if argv is None or with_env is None:
        return None, False
    if with_env == argv:
        owner = _owner_uid(pid)
        if owner is None or owner != os.getuid():
            return None, True
        time.sleep(ENV_RETRY_S)
        with_env = _ps_command(pid, "eww")
        if with_env is None or with_env == argv:
            return None, True
    prefix = argv.rstrip("\n")
    if not with_env.startswith(prefix):
        _miss("ps eww did not begin with ps ww's argv")
        return None, False
    return with_env[len(prefix):], False


def process_env(pid: int) -> str | None:
    """pid's own environment, past its argv - never argv itself, which a
    caller controls and can put an env-looking token inside (a launch
    argument such as `--append-system-prompt` carrying literal text like
    `OCP_AGENT=x`), and which `hl launch` reads to name the real agent.

    `ps eww` prints argv followed by the environment; `ps ww` prints argv
    alone. The two share the exact same argv rendering, so `eww`'s output
    with that shared prefix cut off is the environment and nothing else.
    None whenever no environment came back: the process is gone (`ps` exits
    non-zero, empty stdout), it is another user's - `ps` prints their command
    at rc 0 but withholds their environment, so the two calls come back
    byte-identical - or the prefix does not match, which this refuses rather
    than guess at where argv ends. None is "I don't know", never "the
    variable was not set".
    """
    return _env_or_reason(pid)[0]


def _extract(env: str, name: str) -> str | None:
    for token in env.split():
        if token.startswith(f"{name}="):
            return token.split("=", 1)[1]
    return None


def env_of(pid: int, name: str) -> str | None:
    """One environment variable of a running process, as it was started with.

    The variables `hl launch` sets are how a process says which agent and
    which installation it belongs to; nothing it writes down can be trusted to
    the same degree. None means unreadable or unset, and those two are the same
    answer here: neither says the process is somebody else's.
    """
    env = process_env(pid)
    return _extract(env, name) if env is not None else None


def account_of_process(pid: int) -> str | None:
    """The account a running process is spending: the one its own config-dir
    variable names, read off its environment. It is what the process was
    started with and never changes, so it is the one answer that cannot be out
    of date - unlike a column recorded when a move was decided.
    None when unreadable, or a config dir that is not one of ours.

    Which variable is the vendor's answer (`config_dir_env`), so each known
    vendor's is asked; which account it names is layout, not vendor
    (`vendors.account_named_by`)."""
    for drv in vendors.known():
        cfg = env_of(pid, drv.config_dir_env())
        acct = vendors.account_named_by(cfg) if cfg else None
        if acct:
            return acct
    return None


def live_sessions() -> dict[str, int] | None:
    """`_live_sessions`, and the cause of a failed reading said once to `report` (and its end, once)."""
    global _failing
    found = _live_sessions()
    if found is None and not _failing:
        _failing = True
        if report:
            report(f"process table could not be read: {_why or 'no cause noted'}")
    elif found is not None and _failing:
        _failing = False
        if report:
            report("process table readable again")
    return found


def _live_sessions() -> dict[str, int] | None:
    """agent -> pid for every agent session that is actually running, whatever
    vendor it runs in (`is_session`).

    Read from the environment `hl launch` set before exec'ing the vendor's
    binary, which is the same string the rest of the system keys on. A fold is
    excluded: it is a headless run that inherits whatever OCP_AGENT its caller
    had, and it is not the agent's session.

    None the same way `process_table` returns it (T146): a pid the table just
    named as one of ours, whose own `ps` calls then missed, fails the whole
    reading rather than being silently left out - unless it is definitely
    another user's process, which is left out on its own.
    """
    table = process_table()
    if table is None:
        return None

    found: dict[str, int] = {}
    for pid, cmd in table:
        if not is_session(cmd):
            continue
        env, not_ours = _env_or_reason(pid)
        if env is None:
            if not_ours:
                continue
            sessions = sum(1 for _, c in table if is_session(c))
            _miss(f"pid {pid}, one of {sessions} session processes of {len(table)}: its environment could not be read ({_why or 'no cause noted'})")
            return None
        name = _extract(env, "HANDLINE_AGENT") or _extract(env, "OCP_AGENT")
        if name:
            found.setdefault(name, pid)
    return found


def diagnose() -> list[str]:
    """One line per process the table takes for an agent's session, saying how it was read: whose it is
    named as, or why it could not be. For the person looking into a session the table did not list.
    Only the agent's name is read out of the environment, never the rest of it."""
    table = process_table()
    if table is None:
        return ["the process table could not be read"]
    lines = []
    for pid, cmd in table:
        if not is_session(cmd):
            continue
        env, not_ours = _env_or_reason(pid)
        if env is None:
            lines.append(f"pid {pid}: " + ("another user's process" if not_ours else "its environment could not be read"))
        else:
            lines.append(f"pid {pid}: OCP_AGENT={textsafe.oneline(_extract(env, 'OCP_AGENT') or 'unset')[:60]}")
    return lines or ["no process looks like a session"]


def observe(agents: list[str], terminals: dict[str, str | None]) -> dict[str, Observed]:
    """One look at the world, for all the agents at once."""
    handles = term.live_terminals() if term.available() else None
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
