"""What the status line says now that it collects nothing (D11).

The slot was ocp's data source: Claude Code ran our collector once per turn and
we kept the fields we needed out of the payload. Everything it carried is now
pulled from the session's own transcript and from the usage endpoint, so the
slot is free - and a status line is a place a human looks, which is the one
thing the collector never used it for. It printed `alpha · 5h 41% · 7d 12%`:
an account, in a terminal whose whole screen belongs to one agent.

So it now says what that agent is doing, and it is rendered from **ocp's** view
rather than the session's, because the useful facts are the ones the session
cannot see: which request it was given, how many are behind it, whether the
daemon has parked it, and whether it is sitting on a permission prompt nobody
has answered.

The line is written to a file and the status line command only prints it. That
is not indirection for its own sake: the command is re-run on a refresh
interval for every live session, so at nineteen agents it runs about a hundred
times a minute. Measured on this machine: a Python start is ~70ms of CPU
apiece, which is 13% of a core spent on a status line for ever; reading a file
from shell is ~5ms. The file is written where the work already happens - every
daemon tick, and on the hook events that change what it would say.
"""
from __future__ import annotations

import time

from . import db, paths, status, textsafe, waiting

# A terminal truncates what it cannot fit, and this is one line in a UI we do
# not control, so it is capped rather than trusted to wrap somewhere sensible.
MAX_LEN = 160

# What a permission prompt looks like on the line. It is first because it is
# the only thing here a human has to act on.
NEEDS_YOU = "NEEDS YOU"


def render(con, agent: str, now: float | None = None) -> str:
    """One line about this agent, for whoever is looking at its terminal."""
    now = time.time() if now is None else now
    row = db.get_agent_state(con, agent)
    parts = [agent]

    held = waiting.of(row) if row else None
    if held:
        parts.append(f"{NEEDS_YOU}: {held.get('tool') or 'a prompt'}")
    elif row and row["state"]:
        age = status.ago(now - row["updated"]) if row["updated"] else "?"
        parts.append(f"{row['state']}{'·closed' if row['closed_at'] else ''} {age}")

    head = db.head_of_queue(con, agent)
    if head is not None:
        parts.append(f"#{head['id']} from {head['requester']}")
    depth = db.queue_depth(con, agent)
    if depth > 1:
        parts.append(f"+{depth - 1} queued")

    acct = row["account"] if row and row["account"] else None
    if acct:
        st = status.read_for_agent(agent, acct)
        five = "?" if st.trusted_pct_5h is None else f"{st.trusted_pct_5h}%"
        usage = f"{acct} 5h {five}{status.mark(st.pct_5h_source).strip()}"
        if st.ctx_pct is not None:
            usage += f" · ctx {st.ctx_pct}%"
        parts.append(usage)

    return textsafe.oneline(" · ".join(parts))[:MAX_LEN]


def write(con, agent: str) -> bool:
    """Leave the line where the status line command will find it.

    Never fails loudly: this is a display, and an agent whose line could not be
    written is not an agent with a problem.
    """
    p = paths.agent_line_file(agent)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".txt.tmp")
        tmp.write_text(render(con, agent) + "\n")
        tmp.replace(p)
        return True
    except (OSError, ValueError):
        return False


def write_all(con) -> None:
    for agent in paths.list_agents():
        write(con, agent)
