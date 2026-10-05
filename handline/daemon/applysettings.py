"""A session started with settings that have since changed is restarted, once, at an idle moment.

What a launch reads from the settings a person edits (`launch.fingerprint_parts`: the model, effort,
mcp config, browser, permission switches, ROLE.md, base.md, the team line and rules) is read once, at
session start. The launch records each beside the session; this compares those with what the next
launch would use and, when a named setting differs, restarts the agent the way `hl restart` does
(resumed, the same conversation), never mid-turn and never over an open dialog. A level read each
tick from the row, not an edge: a change made while the agent was busy is applied when it is not.
"""
from __future__ import annotations

import json
import time

from .. import db
from ..infra import paths
from ..session import launch, term
from ..team import agents
from ..usage import status
from . import delivery, lifecycle
from .states import IDLE, QUEUED

WHO = "the daemon"
APPLIED = "settings_applied:"


def changed(agent: str, row) -> list[str]:
    """The named settings the running session was launched without, in launch order; nothing for a
    session that recorded none (a launch from before they were kept)."""
    try:
        old = json.loads(row["launch_parts"] or "null")
    except ValueError:
        return []
    if not isinstance(old, dict):
        return []
    new = launch.fingerprint_parts(agent)
    return [k for k in new if k in old and old[k] != new[k]]


def sweep(con, log, now: int) -> None:
    for agent in paths.list_agents():
        try:
            _apply(con, agent, log)
        except Exception as e:  # noqa: BLE001 - one agent's failure is not the tick's
            log(f"  {agent}: applying changed settings failed: {type(e).__name__}: {e}")


def _apply(con, agent: str, log) -> None:
    row = db.get_agent_state(con, agent)
    if not row or row["closed_at"] or not row["terminal"] or row["state"] not in (IDLE, QUEUED):
        return
    names = changed(agent, row)
    if not names:
        return
    # What this restart is for, kept: a launch that still disagrees with the settings after it (it
    # has happened with things a launch cannot reproduce) is not restarted again for the same change.
    parts = json.dumps(launch.fingerprint_parts(agent), sort_keys=True)
    if db.get_meta(con, APPLIED + agent) == parts:
        return
    if not delivery.session_present(agent) or not term.available() or not term.typeable_now(row["terminal"]):
        return          # busy, or a dialog is open: the next tick asks again
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    if not st.session_id:
        return
    ok, said = lifecycle.restart_agent(con, agent, log, st, by=WHO, applying=names, cause="settings")
    if ok:
        db.set_meta(con, APPLIED + agent, parts)
        log(f"  {agent}: restarted to apply changed settings ({', '.join(names)})")
    else:
        log(f"  {agent}: changed settings ({', '.join(names)}) not applied yet - {said}")
