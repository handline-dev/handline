"""The header line of the home page: the daemon, then only what there is to act on."""
from __future__ import annotations

import time
from typing import NamedTuple

from rich.text import Text

from ... import actions, db
from ...usage import load as loadmod, status
from ...infra import paths
from ...daemon import locks as dmn_locks

# Usage from which an account is worth a word in the header.
TIGHT_PCT = 80
LIMIT_PCT = 95


class Summary(NamedTuple):
    running: int                    # agents working now
    to_decide: int                  # gates: decisions only the founder makes
    to_check: int                   # tasks done and waiting for the founder's check
    tight: tuple[str, str, int] | None   # (account, window, pct) of the fullest one


def tightest() -> tuple[str, str, int] | None:
    """The account and window with the highest trusted usage, when it is at
    TIGHT_PCT or more; None when every window has room or none can be read."""
    best: tuple[str, str, int] | None = None
    for name in paths.list_accounts():
        st = status.read(name)
        for window, pct in ((st.short.label, st.short_trusted), (st.long.label, st.long_trusted)):
            if pct is not None and pct >= TIGHT_PCT and (best is None or pct > best[2]):
                best = (name, window, pct)
    return best


def summarize(con) -> Summary:
    return Summary(
        sum(1 for r in db.all_agent_states(con) if loadmod.is_running(r)),
        sum(1 for m in actions.mailbox_rows(con) if m["gate"]),
        sum(1 for t in db.tasks_needing_close(con) if actions.task_asker(con, t) == actions.HUMAN),
        tightest())


def summary_text(daemon_running: bool, clock: str, s: Summary) -> Text:
    """`daemon running · 1 working · 2 to decide · 3 to check · sigma 5h 87% · 16:05:22`:
    each part after the daemon appears only when it is not zero."""
    out = Text()
    out.append("daemon ", "dim")
    out.append(*(("running", "green") if daemon_running else ("NOT RUNNING", "red")))
    if s.running:
        out.append(f" · {s.running} working", "dim")
    if s.to_decide:
        out.append(f" · {s.to_decide} to decide", "bold yellow")
    if s.to_check:
        out.append(f" · {s.to_check} to check", "yellow")
    if s.tight:
        name, window, pct = s.tight
        out.append(f" · {name} {window} {pct}%", "bold red" if pct >= LIMIT_PCT else "yellow")
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
