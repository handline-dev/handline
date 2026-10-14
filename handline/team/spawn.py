"""An agent's worktrees: the base a new one branches from, the ones it has, and its card in the host's Run.

The agent name is the join key across every system (design section 2): the
worktree name, the task label and the folder name are all the same string.

Choosing the base branch is the thing that is easy to get wrong. An
isolated worktree takes origin/<base> by default, so if local <base> is ahead
the new worktree silently loses those commits. We check which side leads and
branch from that one, and refuse when they have diverged.

`hl spawn` asks the Go daemon (session.start); the Python daemon's own start is `daemon/spawn`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import agents
from .. import db
from ..session import term
from ..infra import textsafe


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """`git`'s own text, clean at the source, so a caller that embeds `out` or
    `err` in a message it returns need not sanitise it first."""
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    return (r.returncode, textsafe.oneline(r.stdout.strip()),
            textsafe.oneline(r.stderr.strip()))


def _has_commit(repo: Path, ref: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo)[0] == 0


def pick_base(repo: Path, base: str) -> tuple[str | None, str]:
    """Return (ref to branch from, explanation).

    None means the two have diverged, or there is nothing to branch from: a human has to act.
    """
    _run(["git", "fetch", "-q", "origin"], cwd=repo)
    if not _has_commit(repo, base):
        # `git worktree add ... <base>` would die on the name (T483: legacy had no main).
        if _has_commit(repo, f"origin/{base}"):
            return f"origin/{base}", f"no local {base}; using origin/{base}"
        _, head, _ = _run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=repo)
        if head and _has_commit(repo, head):
            return head, f"no {base} here; using the checked-out {head}"
        return None, f"{repo} has no {base} branch and no commit to branch from - make a first commit there"
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


def worktree_path(recorded: str | None) -> str | None:
    """The directory an agent works in, out of what its row holds.

    `hl launch` records what Orca handed it, which is a selector - the repo's
    id, then `::`, then the path - while `spawn` records a plain path. Testing
    the selector as a path finds nothing, so an agent that still had its
    worktree was given a second one beside it.
    """
    if not recorded:
        return None
    path = recorded.split("::", 1)[-1]
    return path if Path(path).is_dir() else None


def extra_paths(row) -> list[str]:
    """The agent's further worktrees that are still there, out of what its row holds. A path gone is not listed (and `ensure_extras` makes it again)."""
    try:
        listed = json.loads(row["extra_worktrees"]) if row and row["extra_worktrees"] else []
    except (ValueError, KeyError, IndexError):
        return []
    return [p for p in (worktree_path(x) for x in listed if isinstance(x, str)) if p] if isinstance(listed, list) else []


def all_paths(row) -> list[str]:
    """Every worktree the agent has: its first, then the others."""
    first = worktree_path(row["worktree"] if row else None)
    return ([first] if first else []) + extra_paths(row)


def _repo_of(worktree: str) -> Path | None:
    """The repository a worktree belongs to (the folder that holds its .git), or None."""
    code, out, _ = _run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=Path(worktree))
    return Path(out).parent if code == 0 and out else None


def ensure_extras(agent: str, first: str, repo: Path | None = None) -> tuple[list[str], list[str]]:
    """Make the worktrees agent.yaml `extra_worktrees` asks for and the agent has not got, beside its first, and record them: (its further
    worktrees, what to tell). Each is `<agent>-2`, `<agent>-3` on a branch of that name from main, which is the host's own way of making a
    worktree; the agent checks out what it wants there. One it has is kept as it is and never made twice, so a restart, a wake or the
    reconciler's recovery cannot make a third or strand the first. Failing to make one is told, not fatal: the agent starts with what it has."""
    try:
        want = agents.extra_worktrees(agents.read(agent))
    except Exception:  # noqa: BLE001 - a config that cannot be read asks for nothing here
        return [], []
    con = db.connect()
    try:
        have = extra_paths(db.get_agent_state(con, agent))
    finally:
        con.close()
    if len(have) >= want:
        return have[:want], []
    told: list[str] = []
    repo = repo or _repo_of(first)
    if repo is None:
        return have, [f"{agent}: could not tell which repository {first} belongs to, so its extra worktree was not made"]
    base_ref, why = pick_base(repo, "main")
    if base_ref is None:
        return have, [f"{agent}: no extra worktree made: {why}"]
    for n in range(len(have) + 2, want + 2):
        wt, failed = term.create_worktree(str(repo), f"{agent}-{n}", base_ref)
        if wt is None:
            told.append(f"{agent}: could not make {agent}-{n}: {failed}")
            break
        have.append(wt)
        told.append(f"{agent}: made worktree {wt} (branch {agent}-{n} from {base_ref})")
    if told:
        con = db.connect()
        try:
            db.set_extra_worktrees(con, agent, have)
        finally:
            con.close()
    return have, told


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
    bound = term.run_current()
    if bound:
        if bound != run:
            db.set_meta(con, RUN_KEY, bound)
        return bound
    if run and term.run_exists(run):
        term.run_use(run)
        return run
    run = term.run_create("Handline team")
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
    if not term.has_runs():
        return None
    run = ensure_run(con)
    if not run:
        return None
    row = db.get_agent_state(con, agent)
    if row and row["task"]:
        # Already a worker; the dispatch binds the (possibly new) terminal.
        return term.dispatch(row["task"], handle, run)
    parent_task = None
    try:
        boss = actions_supervisor(agent)
    except Exception:  # noqa: BLE001
        boss = None
    if boss:
        boss_row = db.get_agent_state(con, boss)
        parent_task = boss_row["task"] if boss_row else None
    spec = (f"{agent}: a Handline agent. Work reaches it as a task (`hl task new --assign {agent}`); "
            f"this task is the card it appears in.")
    task = term.task_create(spec, title=agent, display_name=agent, run=run,
                            parent=parent_task)
    if not task and parent_task:
        # A parent in another Run - an older card, or one made before this
        # remembered which Run was the team's - is a reason to draw the agent
        # flat, not a reason not to draw it.
        task = term.task_create(spec, title=agent, display_name=agent, run=run)
    if not task:
        return None
    db.set_task(con, agent, task)
    return term.dispatch(task, handle, run)


def actions_supervisor(agent: str) -> str | None:
    """Who this agent reports to, out of the routing table.

    Imported late: `actions` reaches the daemon, and the daemon reaches here.
    """
    from .. import actions
    return actions.supervisor_of(actions.routing(), agent)
