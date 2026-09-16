"""Talking to the Orca terminal an agent is running in.

This is what makes "hand off, park, resume" actually reclaim context (team
model section 2, axis 3: the cost of an async handoff is that the requester
returns its context). Folding the events and writing a handoff file is only
half of it - without clearing the session the context never drops, so a
context trigger fires again on the very next tick and the agent hands off
forever, paying for a fold each time.

`/clear` starts a fresh session in the same process: measured on a live agent,
total_input_tokens went 97801 -> 0 and the session id changed, while the
terminal, worktree and account stayed put. That is exactly the "keep the house,
empty the memory" shape we want, and it is why an agent does not need to be
destroyed and recreated to be cheap.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

# How long we will wait for an agent to stop working before giving up. Sending
# /clear mid-turn would discard work in flight, so we never force it.
IDLE_TIMEOUT_MS = 120_000


def available() -> bool:
    return shutil.which("orca") is not None


def terminal_handle() -> str | None:
    """The terminal we are running inside, if Orca launched us."""
    return os.environ.get("ORCA_TERMINAL_HANDLE")


def _run(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    try:
        r = subprocess.run(["orca", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def wait_idle(handle: str, timeout_ms: int = IDLE_TIMEOUT_MS) -> bool:
    """Whether the agent is done working.

    `ok` only says the wait command ran; `result.wait.satisfied` is the answer.
    Reading `ok` alone treats a timeout as "idle" and clears mid-turn.
    """
    ok, out = _run(
        ["terminal", "wait", "--terminal", handle, "--for", "tui-idle",
         "--timeout-ms", str(timeout_ms), "--json"],
        timeout=timeout_ms // 1000 + 30,
    )
    if not ok:
        return False
    try:
        return bool(json.loads(out)["result"]["wait"]["satisfied"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return False


def send(handle: str, text: str, enter: bool = True) -> tuple[bool, str]:
    args = ["terminal", "send", "--terminal", handle, "--text", text]
    if enter:
        args.append("--enter")
    return _run(args)


def alive(handle: str) -> bool:
    ok, out = _run(["terminal", "list", "--json"])
    if not ok:
        return False
    try:
        terms = json.loads(out)["result"]["terminals"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False
    return any(t.get("handle") == handle for t in terms)


def clear_context(handle: str, wait: bool = True) -> tuple[bool, str]:
    """Return the session's context without destroying the agent.

    Waits for the agent to finish what it is doing first: clearing mid-turn
    would throw away work that the fold has not recorded yet.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    if wait and not wait_idle(handle):
        return False, "agent did not go idle before the timeout; not clearing mid-turn"
    ok, out = send(handle, "/clear")
    return ok, "context cleared" if ok else f"could not send /clear: {out[:200]}"


def resume_with(handle: str, text: str) -> tuple[bool, str]:
    """Wake a cleared agent by giving it back its own handoff."""
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    ok, out = send(handle, text)
    return ok, "handoff injected" if ok else f"could not send the handoff: {out[:200]}"
