"""Acting on an agent: unstall."""
from __future__ import annotations

from ..session import states as dmn_states
from .. import db


def unstall(con, agent: str) -> str:
    """Clear a stall, which is the one state the daemon will not leave alone."""
    row = db.get_agent_state(con, agent)
    if not row or row["state"] != dmn_states.STALLED:
        return f"{agent}: not STALLED ({row['state'] if row else 'no state'})"
    db.set_agent_state(con, agent, dmn_states.QUEUED, detail="cleared by a human")
    for counter in ("failed_attempts", "throttle_count", "wake_failures"):
        db.reset_counter(con, agent, counter)
    db.clear_cursor(con, f"dialog_since:{agent}")
    db.clear_cursor(con, f"dialog_title:{agent}")
    return f"{agent}: STALLED -> QUEUED"
