"""`ocp schedule` (T605): the messages the daemon types at an agent on a cron schedule. Reading is open;
adding and removing write agent.yaml, which only the founder's terminal may."""
from __future__ import annotations

import json
import sys
import time

from ..daemon import schedules
from ..infra import cron
from ..team import agents


def _next(item: dict) -> str:
    n = cron.next_after(cron.parse(item["cron"]), int(time.time()))
    return time.strftime("%m-%d %H:%M", time.localtime(n)) if n else "never"


def _show(agent: str) -> None:
    items = schedules.of(agent)
    if not items:
        print(f"{agent}: no schedules")
        return
    for i, it in enumerate(items, 1):
        print(f"{agent} {i}. {it['cron']}  next {_next(it)}  - {it['message']}")


def _write(agent: str, items: list[dict]) -> None:
    cfg = agents.read(agent)
    if items:
        cfg["schedules"] = json.dumps(items)
    else:
        cfg.pop("schedules", None)
    agents.write(agent, cfg)


def cmd_schedule(args) -> int:
    try:
        if args.action == "list":
            for a in ([args.agent] if args.agent else agents_with_schedules()):
                _show(a)
            return 0
        if not args.agent:
            print("--agent is needed", file=sys.stderr)
            return 1
        items = schedules.of(args.agent)
        if args.action == "add":
            if not args.cron or not args.message:
                print("add needs --cron and --message", file=sys.stderr)
                return 1
            items = schedules.parse(items + [{"cron": args.cron, "message": args.message}])
        else:
            if not args.index or not 1 <= args.index <= len(items):
                print(f"remove needs --index 1..{len(items)}", file=sys.stderr)
                return 1
            del items[args.index - 1]
        _write(args.agent, items)
        _show(args.agent)
        return 0
    except (ValueError, FileNotFoundError, PermissionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def agents_with_schedules() -> list[str]:
    from ..infra import paths
    return [a for a in paths.list_agents() if schedules.of(a)]
