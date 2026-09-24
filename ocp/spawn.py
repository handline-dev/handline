"""ocp spawn - give an agent its own Orca worktree and start it there.

The agent name is the join key across every system (design section 2): the
worktree name, the task label and the folder name are all the same string.
Doing this by hand is how you end up with an agent running inside somebody
else's worktree, invisible in Orca as anything but one more terminal - which
is exactly what happened before this existed.

Choosing the base branch is the other thing that is easy to get wrong. An
isolated worktree takes origin/<base> by default, so if local <base> is ahead
the new worktree silently loses those commits. We check which side leads and
branch from that one, and refuse when they have diverged.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from . import agents, db, launch, orca, textsafe

# How long to wait for the agent to be ready, and then for its own hook to
# confirm it got the first task. One number: they describe the same patience,
# and drifting them apart would be silent.
SUBMIT_WAIT_S = 60


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """`git`'s or Orca's own text, clean at the source, so a caller that embeds
    `out` or `err` in a message it returns need not sanitise it first. This
    does not cover a caller that `json.loads` `out`: a control byte sent
    escaped comes back real from the parse, so such a caller reads the value
    through `_parsed`."""
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    return (r.returncode, textsafe.oneline(r.stdout.strip()),
            textsafe.oneline(r.stderr.strip()))


def _parsed(out: str, *keys: str) -> str | None:
    """One string out of Orca's `--json`, or None if it is not usable.

    A worktree path and a terminal handle are identifiers: stored in the db,
    handed back to Orca, and named in messages written much later by callers
    that no longer see where the value came from. Sanitising one here would
    keep a name for something that does not exist, and leave every sink
    downstream to remember. So one carrying a control byte is refused instead,
    which is what a value that could not be read at all already does.
    """
    try:
        value = json.loads(out)
        for k in keys:
            value = value[k]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(value, str) or textsafe.has_unsafe(value):
        return None
    return value


def pick_base(repo: Path, base: str) -> tuple[str | None, str]:
    """Return (ref to branch from, explanation).

    None means the two have diverged and a human has to merge first.
    """
    _run(["git", "fetch", "-q", "origin"], cwd=repo)
    code, out, _ = _run(
        ["git", "rev-list", "--left-right", "--count", f"origin/{base}...{base}"], cwd=repo
    )
    if code != 0:
        return base, f"no origin/{base} to compare against; using local {base}"
    try:
        only_remote, only_local = (int(x) for x in out.split())
    except ValueError:
        return base, f"could not read the comparison ({out!r}); using local {base}"

    if only_remote and only_local:
        return None, (
            f"local {base} and origin/{base} have diverged "
            f"({only_local} local, {only_remote} remote). Merge origin/{base} into "
            f"{base} first, then branch from the result - picking a side would lose work."
        )
    if only_remote:
        return f"origin/{base}", f"origin/{base} leads by {only_remote}"
    if only_local:
        return base, f"local {base} leads by {only_local}"
    return base, f"{base} and origin/{base} agree"


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


def worktree_path(recorded: str | None) -> str | None:
    """The directory an agent works in, out of what its row holds.

    `ocp launch` records what Orca handed it, which is a selector - the repo's
    id, then `::`, then the path - while `spawn` records a plain path. Testing
    the selector as a path finds nothing, so an agent that still had its
    worktree was given a second one beside it.
    """
    if not recorded:
        return None
    path = recorded.split("::", 1)[-1]
    return path if Path(path).is_dir() else None


def start_in(agent: str, worktree: str) -> tuple[bool, str]:
    """Start an agent in a worktree it already has.

    A terminal can be closed by a human, or lost when Orca restarts, without
    anything happening to the work: the branch, the checkout and whatever is
    uncommitted in it are all still there. Making a second worktree for that
    agent would be the wrong repair - it would strand the first.
    """
    if not shutil.which("orca"):
        return False, "orca is not on PATH"
    if not Path(worktree).exists():
        return False, f"{agent}: its worktree {worktree} is gone"
    cmd = launch.command_for(agent)
    code, out, err = _run([
        "orca", "terminal", "create", "--worktree", f"path:{worktree}",
        "--title", agent, "--command", cmd, "--json",
    ])
    if code != 0:
        return False, f"{agent}: could not start in {worktree}: {err or out}"
    handle = _parsed(out, "result", "terminal", "handle")
    if handle:
        con = db.connect()
        try:
            db.set_terminal(con, agent, handle, worktree)
            register_worker(con, agent, handle)
        finally:
            con.close()
    return True, f"{agent}: started in {worktree} (terminal {handle or 'unknown'})"


def spawn(agent: str, repo: Path, base: str = "main", prompt: str | None = None,
          dry_run: bool = False) -> tuple[bool, str]:
    """Start the agent in its worktree: the one it has, else a new one named after it."""
    if not shutil.which("orca"):
        return False, "orca is not on PATH"
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
    if dry_run and wt:
        return True, (f"would start in its own worktree {wt}\n"
                      f"  orca terminal create --worktree path:{wt} --command \"{cmd}\"")
    if dry_run:
        return True, (f"would branch from {base_ref} ({why})\n"
                      f"  orca worktree create --repo path:{repo} --name {agent} "
                      f"--base-branch {base_ref}\n"
                      f"  orca terminal create --worktree <new> --command \"{cmd}\"")

    if not wt:
        code, out, err = _run([
            "orca", "worktree", "create", "--repo", f"path:{repo}",
            "--name", agent, "--base-branch", base_ref, "--json",
        ])
        if code != 0:
            return False, f"worktree create failed: {err or out}"
        wt = _parsed(out, "result", "worktree", "path")
        if wt is None:
            return False, f"could not read the new worktree path from: {out[:200]}"

    code, out, err = _run([
        "orca", "terminal", "create", "--worktree", f"path:{wt}",
        "--title", agent, "--command", cmd, "--json",
    ])
    if code != 0:
        return False, f"worktree {wt} is ready, but starting the agent failed: {err or out}"
    handle = _parsed(out, "result", "terminal", "handle")

    if handle:
        con = db.connect()
        try:
            db.set_terminal(con, agent, handle, wt)
            register_worker(con, agent, handle)
        finally:
            con.close()

    lines = [f"{agent}: worktree {wt}",
             f"  {'branched from ' + base_ref if base_ref else 'kept'} ({why})",
             f"  terminal {handle or 'unknown'}"]

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
        # through orca.wait_idle rather than run here: a zero exit only says the
        # wait command ran, and `result.wait.satisfied` is the answer.
        if not orca.wait_idle(handle, SUBMIT_WAIT_S * 1000):
            lines.append("  WARNING: could not wait for the agent to be ready; "
                         "the first task may have been typed into a booting TUI")
        _run(["orca", "terminal", "send", "--terminal", handle,
              "--text", prompt, "--enter", "--wait-submit", "30"])
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


# The orchestration Run this team's work hangs under. One per installation:
# the Run is the card, and every agent is a worker task inside it.
RUN_KEY = "orca:run"


def ensure_run(con) -> str | None:
    """The Run to register workers in, making one the first time.

    Orca draws a Run as a card with its workers under it, so this is what turns
    a pile of terminals into a team anyone can see. It is remembered rather
    than recreated: a second Run splits the same team across two cards, and
    `--parent` then refuses because the two tasks are in different Runs.

    A terminal that is already bound to a Run wins, because that binding is
    what a task lands in whatever `--run` says.
    """
    run = db.get_meta(con, RUN_KEY)
    bound = orca.run_current()
    if bound:
        if bound != run:
            db.set_meta(con, RUN_KEY, bound)
        return bound
    if run and orca.run_exists(run):
        orca.run_use(run)
        return run
    run = orca.run_create("orcapipe team")
    if run:
        db.set_meta(con, RUN_KEY, run)
    return run


def register_worker(con, agent: str, handle: str) -> str | None:
    """Make this agent's terminal a worker in the Run, under its supervisor.

    Without it an agent is one more tab: Orca has no idea it belongs to a team,
    which is why an agent asked to get work done reached for a subagent - the
    only child it knew how to make. The tree comes from `team.yaml`, so the
    card and the routing cannot disagree about who reports to whom.
    """
    if not orca.available():
        return None
    run = ensure_run(con)
    if not run:
        return None
    row = db.get_agent_state(con, agent)
    if row and row["task"]:
        # Already a worker; the dispatch binds the (possibly new) terminal.
        return orca.dispatch(row["task"], handle, run)
    parent_task = None
    try:
        boss = actions_supervisor(agent)
    except Exception:  # noqa: BLE001
        boss = None
    if boss:
        boss_row = db.get_agent_state(con, boss)
        parent_task = boss_row["task"] if boss_row else None
    spec = (f"{agent}: an orcapipe agent. Work reaches it through `ocp ask {agent}`; "
            f"this task is the card it appears in.")
    task = orca.task_create(spec, title=agent, display_name=agent, run=run,
                            parent=parent_task)
    if not task and parent_task:
        # A parent in another Run - an older card, or one made before this
        # remembered which Run was the team's - is a reason to draw the agent
        # flat, not a reason not to draw it.
        task = orca.task_create(spec, title=agent, display_name=agent, run=run)
    if not task:
        return None
    db.set_task(con, agent, task)
    return orca.dispatch(task, handle, run)


def actions_supervisor(agent: str) -> str | None:
    """Who this agent reports to, out of the routing table.

    Imported late: `actions` reaches the daemon, and the daemon reaches here.
    """
    from . import actions
    return actions.supervisor_of(actions.routing(), agent)
