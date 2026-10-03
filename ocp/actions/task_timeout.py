"""A task's timeout as a screen shows it, from the same columns the daemon's clock reads
(`timeout_s`, `timeout_at`; see `daemon.timebox.sweep_task_timeouts`).

The clock runs only while a task is doing. When it passes its deadline the daemon tells the
opener and starts the next stretch, so `timeout_at` is never far in the past. Each notice
records the deadline it missed and the one that replaced it: how long a task has been over is
read by following those back from the deadline now in force."""
from __future__ import annotations

import json
import time

from .. import db
from ..infra import timefmt

NOTICE = "task_timeout_exceeded"


def _notices(con, tid: int) -> list[dict]:
    """The deadline missed and the one that replaced it, for each notice of this task, newest first."""
    out = []
    for r in con.execute(f"SELECT payload FROM events WHERE kind = ? AND {db.EVENT_TASK} = ? "
                         "ORDER BY id DESC", (NOTICE, tid)):
        p = json.loads(r["payload"])
        if "deadline" in p and "next" in p:
            out.append(p)
    return out


def task_timeout(con, row, state: str, now: float | None = None) -> dict:
    """{"length": seconds, "default": the length is the daemon's default, "state": "length" |
    "left" | "over", "left": seconds to the deadline (doing, in time), "over": seconds past
    the first deadline missed, "start": when this stretch began counting, "end": its deadline,
    "told": how many notices this overrun has had}. Only a doing task has a clock."""
    from .tasks import DEFAULT_TASK_TIMEOUT_S       # tasks imports this module
    now = time.time() if now is None else now
    length = row["timeout_s"]
    out = {"length": int(length or DEFAULT_TASK_TIMEOUT_S), "default": length is None, "state": "length",
           "left": None, "over": None, "start": None, "end": None, "told": 0}
    if state != "doing" or row["timeout_at"] is None:
        return out
    end, box = int(row["timeout_at"]), out["length"]
    out.update(start=end - box, end=end)
    # Notices that lead to the deadline now in force: an edit replaces it, and ends the chain.
    first, told, want = None, 0, end
    for n in _notices(con, row["id"]):
        if n["next"] != want:
            break
        first, told, want = n["deadline"], told + 1, n["deadline"]
    if first is None and end <= now:
        first = end
    if first is None:
        out.update(state="left", left=int(end - now))
    else:
        out.update(state="over", over=int(now - first), told=told)
    return out


def timeout_text(t: dict) -> str:
    """The board's cell: "4h" before it runs, "1h12m left" while it does, "over by 20m" past it."""
    if t["state"] == "left":
        return f"{timefmt.span(t['left'])} left"
    if t["state"] == "over":
        return f"over by {timefmt.span(t['over'])}"
    return timefmt.span(t["length"])


def timeout_lines(t: dict) -> list[str]:
    """The task page's lines: the length, when it began counting and when it ends."""
    line = f"timeout {timefmt.span(t['length'])}" + (" (the default)" if t["default"] else "")
    if t["start"] is None:
        return [line + " - counts while the task is doing"]
    out = [line, f"counting since {timefmt.stamp(t['start'])}   ends {timefmt.stamp(t['end'])}"]
    if t["state"] == "left":
        out[1] += f"   {timefmt.span(t['left'])} left"
    elif t["state"] == "over":
        out[1] += f"   over by {timefmt.span(t['over'])}" + (f", opener told {t['told']}x" if t["told"] else "")
    return out
