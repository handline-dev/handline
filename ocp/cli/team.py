"""`ocp org` and `ocp team`."""
from __future__ import annotations


from .. import actions
from ..team import ask as askmod
from ..daemon import lifecycle as dmn_lifecycle
from .. import db
from ..session import launch
from ..session import orca
from ..infra import paths, sealed
from ..team import spawn as spawnmod
from ..team import team
from ..ui import tui
from ..session import waiting

from .ask import _who_refusal
from .health import _name_col


def cmd_org(args) -> int:
    """Put every live agent in the team's Run, under whoever it reports to.

    A terminal Orca knows nothing about is a tab; a worker in a Run is a child
    card with a name, a parent and its own output. Agents started before this
    existed are tabs, and this is what adopts them - supervisors first, so a
    report's parent task exists by the time the report is registered.
    """
    con = db.connect()
    try:
        if not orca.available():
            print("orca is not on PATH - nothing to register with")
            return 1
        run = spawnmod.ensure_run(con)
        if not run:
            print("could not create an orchestration Run (is the Orca runtime up?)")
            return 1
        print(f"run {run}")
        # Supervisors before their reports: `--parent` needs the parent task to
        # exist, and a tree built child-first is a list.
        order = [name for name, _depth in actions.agent_tree()]
        w = _name_col(order)
        for name in order:
            row = db.get_agent_state(con, name)
            handle = row["terminal"] if row else None
            if not handle:
                print(f"  {name:<{w}} no terminal - nothing to register")
                continue
            if not orca.alive(handle):
                print(f"  {name:<{w}} terminal {handle} is gone")
                continue
            got = spawnmod.register_worker(con, name, handle)
            fresh = db.get_agent_state(con, name)
            print(f"  {name:<{w}} task {(fresh['task'] if fresh else None) or '-'}"
                  + (f" dispatch {got}" if got else " (not dispatched)"))
        return 0
    finally:
        con.close()


def cmd_team(args) -> int:
    """Who you may ask, who may ask you, and what they are doing right now.

    An agent that cannot see its own team has one way to get work done by
    somebody else: spawn a subagent, which dies with its session, holds no
    account, has no inbox and appears on no screen. The roster is in team.yaml
    and the states are in the database; neither was ever put in front of the
    agent whose team it is.
    """
    me = args.agent or askmod.asking_agent()
    if not me:
        print(_who_refusal("ask"))
        return 1
    try:
        t = team.Team.load()
    except Exception as e:  # noqa: BLE001
        print(f"team.yaml is invalid: {e}")
        return 1
    con = db.connect()
    try:
        mine, theirs = [], []
        for other in paths.list_agents():
            if other == me:
                continue
            out = t.route(me, other)
            back = t.route(other, me)
            if out.allowed:
                mine.append((other, _in_your_words(out.relation), _doing(con, other)))
            if back.allowed:
                theirs.append((other, _in_your_words(back.relation, sending=False)))
        w = _name_col([n for n, _r, _d in mine] + [n for n, _r in theirs])
        print(f"you are {me}")
        if mine:
            print("\nyou may ask:")
            for name, rel, doing in mine:
                print(f"  {name:<{w}}{rel:<12}{doing}")
        else:
            print("\nthere is nobody you may ask.")
        if theirs:
            print("\nmay ask you: "
                  + ", ".join(f"{n} ({rel})" for n, rel in theirs))
        print("\n`ocp task new --owner <name> -t <time> \"<title>\" \"<body>\"` gives one work, "
              "and `ocp say <name> \"...\"` sends a message. A task is the only way to give an "
              "agent work:\na subagent you spawn yourself dies with this session and "
              "nobody can see it.")
        return 0
    finally:
        con.close()


def _in_your_words(relation: str | None, sending: bool = True) -> str:
    """The edge's relation from the reader's side.

    team.yaml names the relation of the *sender*: `manager -> dev.*` is a
    supervisor edge, so read straight back it told the manager that its own
    developers were its supervisors.
    """
    if relation == "supervisor":
        return "your report" if sending else "your manager"
    if relation == "subordinate":
        return "your manager" if sending else "your report"
    return "peer"


def _doing(con, agent: str) -> str:
    """One line on what an agent is doing, for the team list."""
    row = db.get_agent_state(con, agent)
    if not row:
        return "never started"
    word, extra = tui.screen_state(con, row)
    state = word + (f" {extra}" if extra else "")
    waiting = db.unreached_count(con, agent)
    if waiting:
        state += f" · {waiting} waiting"
    if dmn_lifecycle.would_wake(con, row):
        walled = launch.wake_wait_reason(agent, row, con)
        if walled:
            state += f" · {walled}"
    if not row["terminal"]:
        state += " · no terminal yet"
    return state


def cmd_team_seal(args) -> int:
    try:
        sealed.require_founder()
    except PermissionError as e:
        print(e)
        return 1
    n = sealed.seal_everything()
    print(f"sealed {n} file(s) as they are now")
    return 0


def cmd_team_init(args) -> int:
    p = paths.team_file()
    if p.exists() and not args.force:
        print(f"{p} already exists (use --force to overwrite)")
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    sealed.require_founder()
    p.write_text(team.STARTER)
    sealed.seal(sealed.TEAM, team.STARTER)
    print(f"wrote {p}")
    return 0


def cmd_team_check(args) -> int:
    try:
        t = team.Team.load()
    except Exception as e:  # noqa: BLE001
        print(f"team.yaml is invalid: {e}")
        return 1
    names = sorted(set(t.agents) | {a for m in t.teams.values() for a in (m or [])})
    print(f"{len(t.teams)} teams, {len(names)} agents, {len(t.edges)} edges, {len(t.deny)} denies")
    print(f"max_chain_depth={t.max_chain_depth} ask_rate_per_hour={t.ask_rate_per_hour}")
    registered = set(paths.list_agents())
    for n in names:
        mark = "ok" if n in registered else "not registered with ocp"
        print(f"  {n:<14} {mark}")
    if args.matrix:
        print("\nrouting matrix (rows ask columns):")
        width = max(len(n) for n in names) + 2
        print(" " * width + "".join(f"{n[:6]:>8}" for n in names))
        for a in names:
            cells = []
            for b in names:
                d = t.route(a, b)
                cells.append("     -  " if a == b else ("   allow" if d.allowed else "    DENY"))
            print(f"{a:<{width}}" + "".join(cells))
    return 0
