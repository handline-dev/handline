"""`ocp schedule` (T605): the messages the daemon types at an agent on a cron schedule. Reading is open;
adding and removing write agent.yaml, which only the founder's terminal may."""
from __future__ import annotations

from .. import actions
from ..daemon import schedules


def _show(agent: str) -> None:
    rows = actions.schedule_rows(agent)
    if not rows:
        print(f"{agent}: no schedules")
        return
    for i, it in enumerate(rows, 1):
        print(f"{agent} {i}. {it['cron']}  next {it['next']}  - {it['message']}")


def cmd_schedule(args) -> int:
    try:
        if args.action == "list":
            for a in ([args.agent] if args.agent else agents_with_schedules()):
                _show(a)
            return 0
        if not args.agent:
            print("--agent is needed")
            return 1
        if args.action == "add":
            if not args.cron or not args.message:
                print("add needs --cron and --message")
                return 1
            actions.schedule_add(args.agent, args.cron, args.message)
        else:
            if not args.index:
                print(f"remove needs --index 1..{len(schedules.load(args.agent))}")
                return 1
            actions.schedule_remove(args.agent, args.index)
        _show(args.agent)
        return 0
    except (ValueError, FileNotFoundError, PermissionError) as e:
        print(f"error: {e}")
        return 1


def agents_with_schedules() -> list[str]:
    from ..infra import paths
    return [a for a in paths.list_agents() if schedules.of(a)]
