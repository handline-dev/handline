"""The Python daemon's own start of an agent: `start_in` a worktree it has, or `spawn` it in a new one, and check a session came up.
`hl spawn` asks the Go daemon (session.start) instead; this goes with the Python daemon."""
from __future__ import annotations

import time
from pathlib import Path

from .. import db
from ..session import launch, term
from ..team import agents
from ..team.spawn import ensure_extras, extra_paths, pick_base, register_worker, worktree_path

# How long to wait for the agent to be ready, and then for its own hook to
# confirm it got the first task. One number: they describe the same patience,
# and drifting them apart would be silent.
SUBMIT_WAIT_S = 60

# How long to wait for a launched agent to show up in the process table before
# saying it did not. `hl launch` execs claude, which takes a few seconds to be
# visible; the daemon's own wake waits about this long for the same reason.
STARTED_TIMEOUT_S = 20


def _confirm_session(agent: str) -> str | None:
    """None once a session is actually running for `agent`; otherwise the
    warning `start_in` and `spawn` return instead of "started" (a terminal
    handle back from Orca is not a session - oh-my-zsh's own
    update prompt once ate a launch command whole and left a bare shell,
    "started" regardless).
    """
    from . import delivery  # late: delivery imports lifecycle, which imports this module
    if delivery.wait_for_session(agent, STARTED_TIMEOUT_S):
        return None
    return (f"no session is running in it after {STARTED_TIMEOUT_S}s - the terminal is "
            f"there and claude is not. Go and look (t): whatever it printed is on that shell.")


def prompt_landed(agent: str, since_id: int, timeout_s: float = SUBMIT_WAIT_S) -> bool:
    """Wait until the agent's own hook reports a submitted prompt.

    Orca reports that input was accepted, which is not the same as submitted -
    for the Claude TUI it cannot observe the difference at all. UserPromptSubmit
    is the agent itself saying it received a task, so that is what we believe.

    It matches any prompt for that agent, so a human typing into the terminal
    first would also satisfy it. That is the right trade: the question being
    asked is whether this agent is taking instructions at all.
    """
    deadline = time.time() + timeout_s
    while True:
        con = db.connect()
        try:
            rows = db.events_after(con, since_id, agent=agent)
        finally:
            con.close()
        if any(r["kind"] == "UserPromptSubmit" for r in rows):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)


def start_in(agent: str, worktree: str, cmd: str | None = None) -> tuple[bool, str]:
    """Start an agent in a worktree it already has.

    A terminal can be closed by a human, or lost when Orca restarts, without
    anything happening to the work: the branch, the checkout and whatever is
    uncommitted in it are all still there. Making a second worktree for that
    agent would be the wrong repair - it would strand the first.

    `cmd` is what the terminal runs; a wake passes its launch with the resume id
    and prompt file, since a closed session's pane is closed with it.
    """
    if not term.available():
        return False, term.missing()
    if not Path(worktree).exists():
        return False, f"{agent}: its worktree {worktree} is gone"
    _, extra_told = ensure_extras(agent, worktree)
    handle, why = term.open_terminal(cmd or launch.command_for(agent), cwd=worktree, title=agent)
    if handle is None and why:
        return False, f"{agent}: could not start in {worktree}: {why}"
    if handle:
        con = db.connect()
        try:
            db.set_terminal(con, agent, handle, worktree)
            register_worker(con, agent, handle)
        finally:
            con.close()
    said = "\n".join([*extra_told, f"{agent}: started in {worktree} (terminal {handle or 'unknown'})"])
    problem = _confirm_session(agent)
    if problem:
        return False, f"{said}\n  but {problem}"
    return True, said


def _dry_run_text(wt: str | None, repo: Path, agent: str, base_ref: str | None, why: str, cmd: str) -> str:
    """What `spawn` would do, as the host's own commands where it has them (Orca's)."""
    steps = term.manual("start", wt=wt, repo=str(repo), name=agent, base_ref=base_ref, command=cmd)
    if wt:
        return f"would start in its own worktree {wt}\n  {steps}"
    return f"would branch from {base_ref} ({why})\n  {steps}"


def spawn(agent: str, repo: Path, base: str = "main", prompt: str | None = None,
          dry_run: bool = False) -> tuple[bool, str]:
    """Start the agent in its worktree: the one it has, else a new one named after it."""
    if not term.available():
        return False, term.missing()
    try:
        agents.read(agent)
    except Exception as e:  # noqa: BLE001
        return False, str(e)

    # The agent's own worktree if it still has one: `worktree create --name`
    # on a name already in use makes <name>-2 beside it and strands the branch
    # and whatever is uncommitted in the first.
    con = db.connect()
    try:
        row = db.get_agent_state(con, agent)
    finally:
        con.close()
    wt = worktree_path(row["worktree"] if row else None)

    if wt:
        base_ref, why = None, "its own worktree, kept"
    else:
        base_ref, why = pick_base(repo, base)
        if base_ref is None:
            return False, why

    cmd = launch.command_for(agent)
    if dry_run:
        text = _dry_run_text(wt, repo, agent, base_ref, why, cmd)
        want = agents.extra_worktrees(agents.read(agent))
        have = len(extra_paths(row))
        extra = [f"\n  would make {agent}-{n} beside it (branch {agent}-{n} from main)" for n in range(have + 2, want + 2)]
        return True, text + "".join(extra)

    if not wt:
        wt, failed = term.create_worktree(str(repo), agent, base_ref)
        if wt is None:
            return False, failed
    _, extra_told = ensure_extras(agent, wt, repo)

    handle, failed = term.open_terminal(cmd, cwd=wt, title=agent)
    if handle is None and failed:
        return False, f"worktree {wt} is ready, but starting the agent failed: {failed}"

    if handle:
        con = db.connect()
        try:
            db.set_terminal(con, agent, handle, wt)
            register_worker(con, agent, handle)
        finally:
            con.close()

    lines = [f"{agent}: worktree {wt}",
             f"  {'branched from ' + base_ref if base_ref else 'kept'} ({why})",
             *[f"  {t}" for t in extra_told],
             f"  terminal {handle or 'unknown'}"]

    problem = _confirm_session(agent)
    if problem:
        lines.append(f"  WARNING: {problem}")
        return False, "\n".join(lines)

    if prompt and not handle:
        # Sending to a handle we could not read means typing into nothing and
        # then blaming the agent's input box for a failure that happened here.
        lines.append("  WARNING: could not read the terminal handle, so the first task "
                     "was not sent. The agent is up; send it by hand.")
        return False, "\n".join(lines)

    if prompt:
        # Typing into a TUI that is still booting loses the opening characters
        # and leaves the remainder sitting in the input box, unsent: three
        # agents came up that way and did nothing at all, while Orca had
        # reported the send as accepted. So wait for the agent to be ready,
        # then believe its own UserPromptSubmit rather than the send receipt.
        con = db.connect()
        try:
            since = db.max_event_id(con)
        finally:
            con.close()
        # An Orca without this wait would no-op silently and we would type into
        # a booting TUI - the exact bug this exists to stop, restored. Asked
        # through term.wait_idle rather than run here: a zero exit only says the
        # wait command ran, and `result.wait.satisfied` is the answer.
        if not term.wait_idle(handle, SUBMIT_WAIT_S * 1000):
            lines.append("  WARNING: could not wait for the agent to be ready; "
                         "the first task may have been typed into a booting TUI")
        term.submit(handle, prompt, 30)
        if prompt_landed(agent, since):
            lines.append("  first task submitted")
        else:
            # The agent exists and the worktree is real, but it was asked to do
            # something and never got it. Anything driving spawns from a script
            # reads the exit code, not this text.
            lines.append(f"  WARNING: {agent} never reported receiving the task. It is "
                         f"probably sitting in the input box unsent - check the terminal, "
                         f"clear it with an interrupt and send again.")
            return False, "\n".join(lines)

    return True, "\n".join(lines)
