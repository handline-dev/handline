"""A task's timeout as a screen shows it, from the same columns the daemon's clock reads
(`timeout_s`, `timeout_at`; see `daemon.timebox.sweep_task_timeouts`).

The clock runs only while a task is doing. When it passes its deadline the daemon tells the
opener and starts the next stretch, so `timeout_at` is never far in the past: how long a task
has been over is read from the notices already sent, each one a stretch after the last."""
from __future__ import annotations

import time

from .. import db
from ..infra import timefmt

NOTICE = "task_timeout_exceeded"
CHAIN_SLACK_S = 300     # a notice goes out on a tick, so it comes a little after its deadline
START_SLACK_S = 30      # how near a notice is to the stretch it began
EDIT_SLACK_S = 2        # `edit --timeout` restarts the clock at the moment it stamps the edit


def _notices(con, tid: int) -> list[int]:
    """When each timeout notice of this task went out, newest first."""
    return [r["ts"] for r in con.execute(
        f"SELECT ts FROM events WHERE kind = ? AND {db.EVENT_TASK} = ? ORDER BY ts DESC", (NOTICE, tid))]


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
    if end > now:
        # A notice sent in this stretch's own first moments is the one that began it: the task is over.
        chain = [t for t in _notices(con, row["id"])]
        first = None
        for t in chain:
            if first is None:
                edited = row["edited_at"]
                if abs(t - (end - box)) > START_SLACK_S \
                        or (edited and edited >= t and abs(edited - (end - box)) <= EDIT_SLACK_S):
                    break           # the latest notice did not start this stretch: an edit has since extended it
                first = t
            elif first - t <= box + CHAIN_SLACK_S:
                first = t
            else:
                break
        if first is None:
            out.update(state="left", left=int(end - now))
        else:
            out.update(state="over", over=int(now - first), told=sum(1 for t in chain if t >= first))
    else:
        out.update(state="over", over=int(now - end), told=0)
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
