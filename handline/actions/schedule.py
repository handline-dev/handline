"""Per-agent schedules (T605) as the CLI and the settings screen change them: one write path."""
from __future__ import annotations

import json
import time

from ..team import schedules
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


def schedule_edit(agent: str, index: int, expr: str, message: str) -> None:
    """Replace the `index`th (1-based) schedule; ValueError when there is none or what it is given is wrong."""
    items = schedules.load(agent)
    if not 1 <= index <= len(items):
        raise ValueError(f"edit needs --index 1..{len(items)}")
    items[index - 1] = {"cron": expr, "message": message}
    _write(agent, schedules.parse(items))


def schedule_remove(agent: str, index: int) -> None:
    """Remove the `index`th (1-based) schedule; ValueError when there is none."""
    items = schedules.load(agent)
    if not 1 <= index <= len(items):
        raise ValueError(f"remove needs --index 1..{len(items)}")
    del items[index - 1]
    _write(agent, items)


WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
SCHEDULE_KINDS = ("every day", "weekdays", "every week", "every hour", "every N minutes")


def _number(text, lo: int, hi: int, what: str) -> int:
    if not str(text).strip().isdigit() or not lo <= int(text) <= hi:
        raise ValueError(f"{what} is a number from {lo} to {hi}")
    return int(text)


def schedule_cron(kind: str, hour="0", minute="0", day: int = 1, every="15") -> str:
    """The cron expression for one of `SCHEDULE_KINDS`, from the fields it uses; ValueError names
    the one that is wrong."""
    if kind == "every N minutes":
        return f"*/{_number(every, 1, 59, 'minutes')} * * * *"
    m = _number(minute, 0, 59, "minute")
    if kind == "every hour":
        return f"{m} * * * *"
    h = _number(hour, 0, 23, "hour")
    if kind == "every day":
        return f"{m} {h} * * *"
    if kind == "weekdays":
        return f"{m} {h} * * 1-5"
    if kind == "every week":
        return f"{m} {h} * * {_number(day, 0, 6, 'day')}"
    raise ValueError(f"when is one of {', '.join(SCHEDULE_KINDS)}")


def schedule_words(expr: str) -> str:
    """`expr` in words when it is one `schedule_cron` makes ("weekdays 09:00"), else `expr` itself."""
    f = expr.split()
    if len(f) != 5 or f[2] != "*" or f[3] != "*":
        return expr
    m, h, dow = f[0], f[1], f[4]
    if m.startswith("*/") and h == "*" and dow == "*" and m[2:].isdigit():
        return f"every {int(m[2:])} minutes"
    if not m.isdigit():
        return expr
    if h == "*" and dow == "*":
        return f"every hour at :{int(m):02d}"
    if not h.isdigit():
        return expr
    at = f"{int(h):02d}:{int(m):02d}"
    if dow == "*":
        return f"every day {at}"
    if dow == "1-5":
        return f"weekdays {at}"
    if dow.isdigit() and 0 <= int(dow) <= 6:
        return f"{WEEKDAYS[int(dow)]} {at}"
    return expr


def schedule_fields(expr: str) -> dict | None:
    """`expr` as the fields `schedule_cron` takes (kind, hour, minute, day, every), or None when it
    is not one it makes."""
    f = expr.split()
    if len(f) != 5 or f[2] != "*" or f[3] != "*":
        return None
    m, h, dow = f[0], f[1], f[4]
    if m.startswith("*/") and h == "*" and dow == "*" and m[2:].isdigit():
        return {"kind": "every N minutes", "every": m[2:]}
    if not m.isdigit():
        return None
    if h == "*" and dow == "*":
        return {"kind": "every hour", "minute": m}
    if not h.isdigit():
        return None
    at = {"hour": h, "minute": m}
    if dow == "*":
        return {"kind": "every day", **at}
    if dow == "1-5":
        return {"kind": "weekdays", **at}
    if dow.isdigit() and 0 <= int(dow) <= 6:
        return {"kind": "every week", "day": int(dow), **at}
    return None
