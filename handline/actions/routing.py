"""The routing table as the daemon reads it, who supervises whom, and who may list whose messages."""
from __future__ import annotations

from ..team import ask as askmod
from ..infra import paths
from ..team import team


# The name a human's own requests are sent under, and the inbox `hl`
# shows. Taken from `ask`, which is where decisions are addressed: a second
# copy of the name would empty the panel the day it changed.
HUMAN = askmod.HUMAN


# team.yaml as the screen reads it, re-parsed only when the file changes. A
# frame is drawn every two seconds and the routing table is a human-edited file
# that changes a few times a week. Keyed on the file, not just its clock: one
# process can read two homes, and two homes' files are the same age.
_TEAM: dict = {"key": None, "team": None}


def routing() -> "team.Team | None":
    """The routing table, or None when it cannot be read.

    None rather than an exception: the screen draws a team that has no
    hierarchy the same way it drew every team before there was one, and a typo
    in team.yaml must not take the dashboard down with it - `hl doctor` is
    where a broken routing table is a finding.
    """
    p = paths.team_file()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if _TEAM["key"] != key:
        try:
            _TEAM["team"] = team.Team.load()
        except Exception:  # noqa: BLE001
            _TEAM["team"] = None
        _TEAM["key"] = key
    return _TEAM["team"]


def supervisor_of(t, agent: str) -> str | None:
    """Which agent this one answers to, out of the routing table.

    A supervisor edge is the relation the team model already writes down -
    `manager -> dev.*` is what makes the devs the manager's - so the tree is
    read from the policy the daemon routes on rather than from a second list
    that would drift from it.

    Only an edge whose `from` names one agent counts. A wildcard source is a
    permission, not a chain of command: `* -> reviewer` says anyone may ask the
    reviewer, and the human's own `human -> *` would otherwise make the
    whole team its children and draw a tree that says nothing.
    """
    if t is None:
        return None
    best: tuple[int, str] | None = None
    for e in t.edges:
        if e.get("relation") != "supervisor":
            continue
        src, dst = str(e.get("from", "")), str(e.get("to", ""))
        if "*" in src or src == agent or src == HUMAN:
            continue
        if not t.matches(dst, agent):
            continue
        # The most specific edge wins, the way a route does: a name beats a
        # team, and a team beats everyone.
        rank = 0 if dst == agent else (1 if dst.endswith(".*") else 2)
        if best is None or rank < best[0]:
            best = (rank, src)
    if best is None:
        # No edge says so: the name does (`harbor.sea` answers to `harbor`).
        boss = team.parent_name(agent)
        return boss if boss is not None and t.known(boss) else None
    return best[1]


def listing_refusal(caller: str | None, target: str) -> str | None:
    """Why `caller` may not list `target`'s messages, or None when it may.

    Another agent's queue, digest or handoff is context that agent's project
    owns: an agent reading it carries it into its own, and may leak it. The
    human, the agent itself and its supervisor may. A caller with no name is
    refused by the command before it gets here.
    """
    if caller in (None, HUMAN, target):
        return None
    if supervisor_of(routing(), target) == caller:
        return None
    return (f"{caller} may not list {target}'s messages: only {target}, its supervisor "
            f"and the human may. `hl read <id>` shows one request by number.")


def listable_agents(caller: str | None) -> tuple[str, ...] | None:
    """Whose messages `caller` may list without naming one: None for anyone
    (the human, or no name), else itself and the agents it supervises."""
    if caller in (None, HUMAN):
        return None
    t = routing()
    return (caller, *(a for a in paths.list_agents() if supervisor_of(t, a) == caller))


def reach(caller: str | None) -> set[str] | None:
    """The agents whose tasks `caller` sees: itself and everyone under it, walked
    down the supervisor edges. None (everything) for the human or no name."""
    if caller in (None, HUMAN):
        return None
    t = routing()
    boss = {a: supervisor_of(t, a) for a in paths.list_agents()}
    seen, todo = {caller}, [caller]
    while todo:
        top = todo.pop()
        for a, b in boss.items():
            if b == top and a not in seen:
                seen.add(a)
                todo.append(a)
    return seen


def agent_tree() -> list[tuple[str, int]]:
    """Every registered agent as (name, depth), supervisors above their reports.

    An agent whose supervisor is not registered, or that is in a cycle, is
    drawn at the root: a name missing from this list reads as an agent that is
    not there, so nothing may drop out of it.
    """
    names = paths.list_agents()
    t = routing()
    boss = {}
    for n in names:
        s = supervisor_of(t, n)
        boss[n] = s if s in names and s != n else None
    # A cycle would recurse forever. Anything that cannot be walked up to a
    # root in as many steps as there are agents is one, and it is drawn flat.
    for n in list(boss):
        seen, cur = {n}, boss[n]
        while cur is not None:
            if cur in seen:
                boss[n] = None
                break
            seen.add(cur)
            cur = boss.get(cur)

    out: list[tuple[str, int]] = []

    def walk(parent: str | None, depth: int) -> None:
        for n in names:
            if boss[n] == parent:
                out.append((n, depth))
                walk(n, depth + 1)

    walk(None, 0)
    return out
