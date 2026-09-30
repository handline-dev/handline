"""Per-agent schedules (T605) as the CLI and the settings screen change them: one write path."""
from __future__ import annotations

import json
import time

from ..daemon import schedules
from ..infra import cron
from ..team import agents


def next_run(item: dict) -> str:
    """When `item` next fires, in local time, or "never"."""
    n = cron.next_after(cron.parse(item["cron"]), int(time.time()))
    return time.strftime("%m-%d %H:%M", time.localtime(n)) if n else "never"


def schedule_rows(agent: str) -> list[dict]:
    """The agent's schedules as {cron, message, next}; ValueError when its value is bad."""
    return [{**it, "next": next_run(it)} for it in schedules.load(agent)]


def _write(agent: str, items: list[dict]) -> None:
    cfg = agents.read(agent)
    if items:
        cfg["schedules"] = json.dumps(items)
    else:
        cfg.pop("schedules", None)
    agents.write(agent, cfg)


def schedule_add(agent: str, expr: str, message: str) -> None:
    """Add a schedule; ValueError says what is wrong with the cron or the message."""
    _write(agent, schedules.parse(schedules.load(agent) + [{"cron": expr, "message": message}]))


def schedule_remove(agent: str, index: int) -> None:
    """Remove the `index`th (1-based) schedule; ValueError when there is none."""
    items = schedules.load(agent)
    if not 1 <= index <= len(items):
        raise ValueError(f"remove needs --index 1..{len(items)}")
    del items[index - 1]
    _write(agent, items)
