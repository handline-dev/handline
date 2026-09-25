"""Answering the dialogs a working agent meets all day (T64), behind one global
setting, on by default: `auto_accept_dialogs` in config.yaml, `ocp auto-accept`.

Two dialogs, each answered on evidence from two places. A browser tool call is
said yes to once when the permission hook reported that very tool as waiting AND
the screen is that tool's prompt with the plain "Yes" highlighted
(`Driver.accepting_key`); "don't ask again" is never chosen. The folder-trust
screen is `lifecycle`'s standing consent, which this setting also gates."""
from __future__ import annotations

import time

from .. import db
from ..infra import paths
from . import idle_close, orca, waiting

KEY = "auto_accept_dialogs"
# A prompt that Enter did not clear is left alone this long, not pressed again
# on every tick.
STUCK_BACKOFF_S = 300


def enabled() -> bool:
    return idle_close.config_flag(KEY, True)


def set_enabled(on: bool) -> None:
    idle_close.set_config_flag(KEY, on)


def sweep(con, log, now: int | None = None) -> None:
    """Say yes to each agent's waiting tool call its vendor accepts. Only an
    agent whose hook reported such a tool waiting is looked at, so a tick
    nobody waits on reads no screen."""
    if not enabled():
        return
    now = int(time.time()) if now is None else now
    registered = set(paths.list_agents())
    for row in db.all_agent_states(con):
        agent = row["agent"]
        if agent not in registered or not row["terminal"]:
            continue
        try:
            _accept(con, agent, row, now, log)
        except Exception as e:  # noqa: BLE001 - one agent's unreadable screen is not the tick's failure
            log(f"  {agent}: auto-accept failed: {e}")


def _accept(con, agent: str, row, now: int, log) -> None:
    # `of`, not the raw file: a wait left by a prompt that was answered without
    # its PostToolUse is stale, and `of` is what says so (T100).
    held = waiting.of(con, row)
    tool = (held or {}).get("tool") or ""
    if not tool or row["closed_at"] or row["state"] != "RUNNING":
        return
    stuck_key = f"autoaccept_stuck:{agent}"
    if now - db.get_cursor(con, stuck_key) < STUCK_BACKOFF_S:
        return
    with orca.driver_scope():
        if not orca.accepts_tool(row["terminal"], tool):
            return
        outcome, title, why = orca.accept_dialog(row["terminal"], tool)
    if outcome == orca.NOT_PRESSED:
        return
    if outcome == orca.STILL_OPEN:
        db.set_cursor(con, stuck_key, now)
        db.add_event(con, agent, "dialog_accept_failed", {"title": title, "tool": tool})
        log(f"  {agent}: pressed Enter on \"{title}\" ({tool}) and it is still open")
        return
    db.add_event(con, agent, "dialog_accepted", {"title": title, "tool": tool})
    log(f"  {agent}: said yes once to {tool} (auto-accept)")
