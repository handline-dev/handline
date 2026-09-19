"""Talking to the Orca terminal an agent is running in.

This is what makes "hand off, park, resume" actually reclaim context (team
model section 2, axis 3: the cost of an async handoff is that the requester
returns its context). Recording the handoff is only half of it - without
clearing the session the context never drops, so a context trigger fires again
on the very next tick and the agent hands off forever.

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

# Sending /clear mid-turn would discard work in flight, so we never force it.
# The daemon asks with a short timeout because this call blocks its whole tick:
# waiting two minutes on one busy agent stops supervision of every other agent.
# A busy agent is not a failure, it is just busy, so we come back next tick.
#
# Measured 2026-09-17 against the shells left behind by three closed sessions,
# because three reviews in a row declined to trust this probe against a terminal
# with no TUI in it. Do not trust it there: it is not consistent. The short
# probe said not-satisfied every time, at the full 3.1s, and so did two of the
# three long waits - but the third returned satisfied after 63.7s on a terminal
# whose session had ended half an hour earlier and did not start again for
# another ten minutes. So "no TUI" does not reliably read as "not idle", and
# nothing may be sent to a terminal on the strength of this probe alone when we
# have any other way to know whether a session is there. The process table is
# that other way, and it is in observe.py.
#
# The cost is consistent even when the answer is not: a "no" always takes the
# whole timeout, so probing a session that has already exited pays two minutes
# for an answer that was free elsewhere.
IDLE_TIMEOUT_MS = 120_000
IDLE_PROBE_MS = 3_000


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


def _result(out: str, *path: str):
    """Dig one value out of an `--json` reply, or None."""
    try:
        node = json.loads(out).get("result", {})
    except (json.JSONDecodeError, TypeError):
        return None
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def run_create(objective: str) -> str | None:
    """Start an orchestration Run - the card a team's work hangs under.

    It also *binds* the calling terminal to that Run, which is why this is
    called once and remembered: calling it again makes a second card with the
    same name and moves the binding to it, and a team split across two cards
    is a tree that draws half of itself.
    """
    ok, out = _run(["orchestration", "run-create", "--objective", objective, "--json"])
    if not ok:
        return None
    return _result(out, "run", "id") or _result(out, "id")


def run_current() -> str | None:
    """The Run this terminal is already bound to, if any."""
    ok, out = _run(["orchestration", "run-current", "--json"])
    return _result(out, "run", "id") if ok else None


def run_exists(run: str) -> bool:
    """Is this Run still there? `--id`, not `--run`: the flag differs per verb,
    and reading the wrong one as "gone" made a new Run for every agent."""
    ok, out = _run(["orchestration", "run-show", "--id", run, "--json"])
    return ok and bool(_result(out, "run", "id"))


def run_use(run: str) -> bool:
    """Bind this terminal to an existing Run, so tasks land in the team's card."""
    ok, _out = _run(["orchestration", "run-use", "--id", run, "--json"])
    return ok


def task_create(spec: str, title: str, display_name: str, run: str,
                parent: str | None = None) -> str | None:
    """A task in the Run, optionally under another - this is the tree Orca draws."""
    args = ["orchestration", "task-create", "--spec", spec, "--task-title", title,
            "--display-name", display_name, "--run", run, "--json"]
    if parent:
        args += ["--parent", parent]
    ok, out = _run(args)
    if not ok:
        return None
    return _result(out, "task", "id") or _result(out, "id")


def dispatch(task: str, handle: str, run: str) -> str | None:
    """Bind a task to a terminal, which is what makes it a worker in the card.

    No `--inject`: the terminal is already running `ocp launch`, and the spec
    is metadata for the card rather than an instruction for the agent. Orca
    injecting it too would deliver the same thing twice.
    """
    ok, out = _run(["orchestration", "dispatch", "--task", task, "--to", handle,
                    "--run", run, "--json"])
    if not ok:
        return None
    return _result(out, "dispatch", "id") or _result(out, "dispatchId") or task


def focus(handle: str) -> tuple[bool, str]:
    """Bring this terminal's tab to the front in the Orca window.

    The one thing the screen does that is not about state: the reader stops
    deciding and starts working, in the agent's own session, with everything it
    can see. Nothing is typed and nothing is read - which is why it is safe on
    an agent that is mid-turn, where every other key here is not.
    """
    return _run(["terminal", "switch", "--terminal", handle], timeout=20)


def live_terminals() -> set[str] | None:
    """Every handle Orca currently has, or None when it could not say.

    None is not an empty set. Reading "I could not ask" as "there are none"
    would tear down a healthy team the first time Orca was slow to answer.
    """
    ok, out = _run(["terminal", "list", "--json"])
    if not ok:
        return None
    try:
        terms = json.loads(out)["result"]["terminals"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return {t["handle"] for t in terms if isinstance(t, dict) and t.get("handle")}


def alive(handle: str) -> bool:
    handles = live_terminals()
    return handles is not None and handle in handles


def idle_now(handle: str) -> bool:
    """Is the agent idle right now? Does not block the caller waiting."""
    return wait_idle(handle, IDLE_PROBE_MS)


# What Claude Code draws under a modal it opens itself ("Teach auto mode about
# your environment?", trust prompts). Anything typed plus Enter answers it.
MODAL_MARKERS = ("Enter to confirm", "Esc to cancel")
MODAL_ROWS = 3


def _screen(handle: str) -> dict | None:
    """The rendered screen, or None when it cannot be read."""
    ok, out = _run(["terminal", "read", "--terminal", handle, "--screen",
                    "--limit", "40", "--json"], timeout=30)
    term = _result(out, "terminal") if ok else None
    return term if isinstance(term, dict) else None


def modal_open(handle: str) -> bool:
    """Is a modal on the agent's screen, where typed text would answer it?

    Fail-closed: a screen that cannot be read is a yes, because the cost of a
    wrong no is an answer given to a question nobody asked.
    """
    term = _screen(handle)
    tail = term.get("tail") if term else None
    # Any other source is the accumulated stream, whose history is the
    # conversation, not the screen.
    if not isinstance(tail, list) or term.get("source") != "screen":
        return True
    # A modal draws its footer on the bottom rows. Above them is the
    # transcript, which may quote these words without anything being asked.
    screen = "\n".join(str(x) for x in tail[-MODAL_ROWS:])
    return any(m in screen for m in MODAL_MARKERS)


def unsent_text(handle: str) -> bool:
    """Is there text in the input box that nobody has submitted?

    Orca reports it as `draft` on a screen read, only while it is non-empty, and
    keeps it out of `tail`. Anything typed now lands in the middle of it and the
    Enter submits the two as one prompt. Fail-closed like `modal_open`.
    """
    term = _screen(handle)
    if term is None or term.get("source") != "screen":
        return True
    return bool(term.get("draft"))


def typeable_now(handle: str) -> bool:
    """Idle, nothing on screen that a typed line would answer, and nothing
    already typed that it would merge into."""
    return idle_now(handle) and not modal_open(handle) and not unsent_text(handle)


def clear_context(handle: str, wait: bool = True,
                  timeout_ms: int = IDLE_TIMEOUT_MS) -> tuple[bool, str]:
    """Return the session's context without destroying the agent.

    Waits for the agent to finish what it is doing first: clearing mid-turn
    would throw away work the handoff has not recorded yet.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    if wait and not wait_idle(handle, timeout_ms):
        return False, "agent is mid-turn; not clearing"
    if modal_open(handle):
        return False, "a modal is open on its screen; not typing /clear at it"
    if unsent_text(handle):
        return False, "text is left unsent in its input; not typing /clear into it"
    ok, out = send(handle, "/clear")
    return ok, "context cleared" if ok else f"could not send /clear: {out[:200]}"


def close_session(handle: str, wait: bool = True,
                  timeout_ms: int = IDLE_TIMEOUT_MS) -> tuple[bool, str]:
    """End the agent's session and leave its terminal standing.

    Orca runs our launch command inside a login shell, so claude is the shell's
    child: when it exits the shell survives and the tab, the worktree and the
    cwd stay exactly as they were. Measured on a live agent: 401MB returned to
    the machine, terminal still running, prompt in the same worktree.

    Same guard as clearing - ending a session mid-turn throws away work that
    nothing has recorded yet - and nothing is typed at a modal, where `/exit`
    and Enter answer the question instead of ending anything.

    "Ok" means the command was typed. Whether the process ended is the
    caller's to confirm from the process table.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    if wait and not wait_idle(handle, timeout_ms):
        return False, "agent is mid-turn; not closing"
    if modal_open(handle):
        return False, "a modal is open on its screen; not typing /exit at it"
    if unsent_text(handle):
        return False, "text is left unsent in its input; not typing /exit into it"
    ok, out = send(handle, "/exit")
    return ok, "/exit typed" if ok else f"could not send /exit: {out[:200]}"


def relaunch(handle: str, command: str, timeout_ms: int = IDLE_TIMEOUT_MS,
             started=None) -> tuple[bool, str]:
    """Start an agent again in the shell its session left behind.

    The command is ours, so the account comes back with it - which is why this
    does not depend on anything preserving the launch environment for us.

    `started` is asked, before the wait, whether the command actually started
    something: sending only says we typed it, and `ocp launch` refuses over the
    5h limit and exits when the account is not logged in. It comes first
    because the wait below costs its whole timeout when there is no TUI, and a
    launch that started nothing is exactly that case.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    ok, out = send(handle, command)
    if not ok:
        return False, f"could not send the launch command: {out[:200]}"
    if started is not None and not started():
        return False, "the launch command was typed but no session started"
    # Typing into a TUI that is still booting loses the opening characters, so
    # nothing may be sent to the agent until it has finished starting.
    wait_idle(handle, timeout_ms)
    return True, "relaunched"


def resume_with(handle: str, text: str) -> tuple[bool, str]:
    """Wake a cleared agent by giving it back its own handoff."""
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    ok, out = send(handle, text)
    return ok, "handoff injected" if ok else f"could not send the handoff: {out[:200]}"
