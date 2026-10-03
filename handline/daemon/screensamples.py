"""T592: a sample of the screen of an agent that reports background work, kept to design against.

What tells an idle agent with background work from one mid-turn is on its screen, and the
sandboxes agents work in cannot read Orca; the daemon can. Each time it reads an agent's screen and
finds background work, one sample in ten minutes goes to a file, labelled with what the db says the
agent is doing - so a screen and the state it was mislabelled with can be read side by side. No
extra read: the rows are the ones `orca.background_work` just took. The newest KEEP files stay.
"""
from __future__ import annotations

import os
import time

from .. import db
from ..infra import paths

INTERVAL_S = 600
KEEP = 30


def dir_() -> "os.PathLike[str]":
    return paths.home() / "diag" / "screens"


def record(con, agent: str, row, running: str, tail: list[str] | None, now: int | None = None) -> bool:
    """Write one sample if it is due. True when written; a failure to write is not the tick's."""
    now = int(time.time()) if now is None else now
    if not tail or now - db.get_cursor(con, f"screen_sample:{agent}") < INTERVAL_S:
        return False
    try:
        d = dir_()
        d.mkdir(parents=True, exist_ok=True)
        head = [f"agent: {agent}", f"time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}",
                f"db state: {row['state'] if row else '?'}", f"db detail: {(row['detail'] or '') if row else ''}",
                f"session says: {running}", f"rows: {len(tail)} (bottom of the screen, top first)", "-" * 60]
        path = d / f"{agent}-{now}.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # screens are private
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(head + tail) + "\n")
        for old in sorted(d.glob("*.txt"), key=lambda p: p.stat().st_mtime)[:-KEEP]:
            old.unlink()
        db.set_cursor(con, f"screen_sample:{agent}", now)
        return True
    except OSError:
        return False
