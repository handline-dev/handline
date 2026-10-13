"""`hl team`."""
from __future__ import annotations

import sys

from ..team import ask as askmod
from ..infra import daemonclient as dmn_client, paths, sealed, textsafe
from ..team import team

from . import client
from .ask import _who_refusal
from .health import _name_col


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
    try:
        lines = {a["name"]: a for a in client.call("agents.list", {"status": True})["agents"]}
    except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
        print(f"could not read what they are doing: {e}", file=sys.stderr)
        lines = {}
    mine, theirs = [], []
    for other in paths.list_agents():
        if other == me:
            continue
        out = t.route(me, other)
        back = t.route(other, me)
        if out.allowed:
            mine.append((other, _in_your_words(out.relation), _doing(other, lines.get(other))))
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
    print("\n`hl task new --assign <name> -t <time> \"<title>\" \"<body>\"` gives one work, "
          "and `hl say <name> \"...\"` sends a message. A task is the only way to give an "
          "agent work:\na subagent you spawn yourself dies with this session and "
          "nobody can see it.")
    return 0


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


def _doing(agent: str, line: dict | None) -> str:
    """One line on what an agent is doing, for the team list, from its agents.list status line (None when the daemon gave none)."""
    if line is None:
        return "?"
    if not line["has_repo"] and not line["has_terminal"]:
        return f"never started - no repo (`hl agent register {agent} --repo <path>`)"
    if not line["started"]:
        return "never started"
    state = line["screen_word"] + (f" {textsafe.oneline(line['screen_detail'])}" if line["screen_detail"] else "")
    if line["unreached"]:
        state += f" · {line['unreached']} waiting"
    waits = line["task_waits"]
    if waits:
        state += (f" · T{waits[0]['task']} waits: {textsafe.oneline(waits[0]['words'])}"
                  + (f" (+{len(waits) - 1} more)" if len(waits) > 1 else ""))
    if line["wake_wait_reason"]:
        state += f" · {textsafe.oneline(line['wake_wait_reason'])}"
    if not line["has_terminal"]:
        state += " · no terminal yet"
    return state


def cmd_team_seal(args) -> int:
    try:
        sealed.require_founder()
    except PermissionError as e:
        print(e)
        return 1
    adopted = sealed.seal_everything()
    for label, was in adopted:
        print(f"  sealed {label} ({'no seal before' if was == 'new' else 'it did not match its seal'})")
    print(f"sealed {len(adopted)} file(s) as they are now" if adopted
          else "nothing to seal: every file already matches its seal")
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
