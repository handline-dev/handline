"""hl spawn - give an agent its own Orca worktree and start it there.

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
import subprocess
import time
from pathlib import Path

from . import agents
from .. import db
from ..session import launch, term
from ..infra import textsafe

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
    warning `start_in` and `spawn` return instead of "started" (#1976, T146
    pt.2: a terminal handle back from Orca is not a session - oh-my-zsh's own
    update prompt once ate a launch command whole and left a bare shell,
    "started" regardless). The one check `hl spawn`, the daemon's auto-start
    and `actions.control.start` each need, instead of their own copy.
    """
    from ..daemon import delivery  # late: delivery's package pulls lifecycle, which imports this module
    if delivery.wait_for_session(agent, STARTED_TIMEOUT_S):
        return None
    return (f"no session is running in it after {STARTED_TIMEOUT_S}s - the terminal is "
            f"there and claude is not. Go and look (t): whatever it printed is on that shell.")


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


def start_in(agent: str, worktree: str, cmd: str | None = None) -> tuple[bool, str]:
    """Start an agent in a worktree it already has.

    A terminal can be closed by a human, or lost when Orca restarts, without
    anything happening to the work: the branch, the checkout and whatever is
    uncommitted in it are all still there. Making a second worktree for that
    agent would be the wrong repair - it would strand the first.

    `cmd` is what the terminal runs; a wake passes its launch with the resume id
    and prompt file, since a closed session's pane is closed with it (T407).
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
    spec = (f"{agent}: a Handline agent. Work reaches it as a task (`hl task new --owner {agent}`); "
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
