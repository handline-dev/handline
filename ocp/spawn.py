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

from . import agents, db, launch

# How long to wait for the agent's own hook to confirm it got the first task.
SUBMIT_WAIT_S = 60


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


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


def spawn(agent: str, repo: Path, base: str = "main", prompt: str | None = None,
          dry_run: bool = False) -> tuple[bool, str]:
    """Create the worktree named after the agent and start the agent in it."""
    if not shutil.which("orca"):
        return False, "orca is not on PATH"
    try:
        agents.account_of(agent)
    except Exception as e:  # noqa: BLE001
        return False, str(e)

    base_ref, why = pick_base(repo, base)
    if base_ref is None:
        return False, why

    cmd = launch.command_for(agent)
    if dry_run:
        return True, (f"would branch from {base_ref} ({why})\n"
                      f"  orca worktree create --repo path:{repo} --name {agent} "
                      f"--base-branch {base_ref}\n"
                      f"  orca terminal create --worktree <new> --command \"{cmd}\"")

    code, out, err = _run([
        "orca", "worktree", "create", "--repo", f"path:{repo}",
        "--name", agent, "--base-branch", base_ref, "--json",
    ])
    if code != 0:
        return False, f"worktree create failed: {err or out}"
    try:
        wt = json.loads(out)["result"]["worktree"]["path"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, f"could not read the new worktree path from: {out[:200]}"

    code, out, err = _run([
        "orca", "terminal", "create", "--worktree", f"path:{wt}",
        "--title", agent, "--command", cmd, "--json",
    ])
    if code != 0:
        return False, f"worktree {wt} created, but starting the agent failed: {err or out}"
    try:
        handle = json.loads(out)["result"]["terminal"]["handle"]
    except (json.JSONDecodeError, KeyError, TypeError):
        handle = "?"

    lines = [f"{agent}: worktree {wt}", f"  branched from {base_ref} ({why})",
             f"  terminal {handle}"]

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
        _run(["orca", "terminal", "wait", "--terminal", handle,
              "--for", "tui-idle", "--timeout-ms", "60000"])
        _run(["orca", "terminal", "send", "--terminal", handle,
              "--text", prompt, "--enter", "--wait-submit", "30"])
        if prompt_landed(agent, since):
            lines.append("  first task submitted")
        else:
            lines.append(f"  WARNING: {agent} never reported receiving the task. It is "
                         f"probably sitting in the input box unsent - check the terminal, "
                         f"clear it with an interrupt and send again.")

    return True, "\n".join(lines)
