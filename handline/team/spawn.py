"""An agent's worktrees: the ones it has, and its card in the host's Run.

The agent name is the join key across every system (design section 2): the
worktree name, the task label and the folder name are all the same string.

`hl spawn` asks the Go daemon (session.start), which chooses the base a new worktree branches from.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import db
from ..session import term


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
    """The agent's further worktrees that are still there, out of what its row holds. A path gone is not listed."""
    try:
        listed = json.loads(row["extra_worktrees"]) if row and row["extra_worktrees"] else []
    except (ValueError, KeyError, IndexError):
        return []
    return [p for p in (worktree_path(x) for x in listed if isinstance(x, str)) if p] if isinstance(listed, list) else []


def all_paths(row) -> list[str]:
    """Every worktree the agent has: its first, then the others."""
    first = worktree_path(row["worktree"] if row else None)
    return ([first] if first else []) + extra_paths(row)


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
