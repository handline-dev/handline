"""The header line of the home page: the daemon, then only what there is to act on."""
from __future__ import annotations

import time
from typing import NamedTuple

from rich.style import Style
from rich.text import Text

from ... import actions
from ...usage import status
from ...infra import paths
from ...daemon import client, locks as dmn_locks
from .. import api

class Summary(NamedTuple):
    running: int                    # agents working now
    to_decide: int                  # gates: decisions only the founder makes
    for_you: int = 0                # tasks delegated to the founder that are not done yet


def summarize() -> Summary:
    """The three counts, from the daemon's `agents.list`, `inbox.list` and `tasks.list`; zero when it cannot be asked."""
    try:
        running = sum(1 for a in api.call("agents.list")["agents"] if a["state"] == "running")
        to_decide = len(api.call("inbox.list", {"kind": "decision", "limit": 200})["items"])
        for_you = sum(1 for t in api.call("tasks.list", {"owner": actions.HUMAN, "limit": 200})["items"]
                      if t["state"] in actions.DELEGATED_STATES)
    except (client.RpcError, client.DaemonDown, OSError):
        return Summary(0, 0, 0)
    return Summary(running, to_decide, for_you)


def summary_text(daemon_running: bool, clock: str, s: Summary) -> Text:
    """`daemon running · 1 working · 2 to decide · 3 for you · 16:05:22`:
    each part after the daemon appears only when it is not zero; `for you` (tasks
    delegated to the founder) is a link that runs the screen's `delegated` action."""
    out = Text()
    out.append("daemon ", "dim")
    out.append(*(("running", "green") if daemon_running else ("NOT RUNNING", "red")))
    if s.running:
        out.append(f" · {s.running} working", "dim")
    if s.to_decide:
        out.append(f" · {s.to_decide} to decide", "bold yellow")
    if s.for_you:
        out.append(" · ", "dim")
        out.append(f"{s.for_you} for you", Style(color="yellow", underline=True, meta={"@click": "screen.delegated"}))
    out.append(f" · {clock}", "dim")
    return out


def daemon_running() -> bool:
    return bool(dmn_locks.running_elsewhere())


def clock() -> str:
    return time.strftime("%H:%M:%S")


def marks_on_screen() -> bool:
    """Is any usage figure drawn with a mark the legend explains?"""
    return any(status.mark(src).strip()
               for a in paths.list_accounts()
               for src in (status.read(a).short_source, status.read(a).long_source))
