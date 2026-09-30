"""The header line of the home page: the daemon, then only what there is to act on."""
from __future__ import annotations

import time
from typing import NamedTuple

from rich.style import Style
from rich.text import Text

from ... import actions, db
from ...usage import load as loadmod, status
from ...infra import paths
from ...daemon import locks as dmn_locks

class Summary(NamedTuple):
    running: int                    # agents working now
    to_decide: int                  # gates: decisions only the founder makes
    to_check: int                   # tasks done and waiting for the founder's check
    for_you: int = 0                # tasks delegated to the founder that are not done yet


def summarize(con) -> Summary:
    return Summary(
        sum(1 for r in db.all_agent_states(con) if loadmod.is_running(r)),
        sum(1 for m in actions.mailbox_rows(con) if m["gate"]),
        sum(1 for t in db.tasks_needing_close(con) if actions.task_asker(con, t) == actions.HUMAN),
        len(actions.task_rows(con, owner=actions.HUMAN, state=list(actions.DELEGATED_STATES))))


def summary_text(daemon_running: bool, clock: str, s: Summary) -> Text:
    """`daemon running · 1 working · 2 to decide · 3 to check · 16:05:22`:
    each part after the daemon appears only when it is not zero; `to check` and `for you` (tasks
    delegated to the founder) are links that run the screen's `checks` and `delegated` actions."""
    out = Text()
    out.append("daemon ", "dim")
    out.append(*(("running", "green") if daemon_running else ("NOT RUNNING", "red")))
    if s.running:
        out.append(f" · {s.running} working", "dim")
    if s.to_decide:
        out.append(f" · {s.to_decide} to decide", "bold yellow")
    if s.to_check:
        out.append(" · ", "dim")
        out.append(f"{s.to_check} to check", Style(color="yellow", underline=True, meta={"@click": "screen.checks"}))
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
