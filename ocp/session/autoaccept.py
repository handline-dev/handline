"""Answering the dialogs a working agent meets all day (T64), behind one global
setting, on by default: `auto_accept_dialogs` in config.yaml, `ocp auto-accept`.

Two things are answered. A dialog the founder gave a standing answer
(`team/dialog_rules`) is answered with that rule's option, by its number on the
screen just read; a dialog no rule matches is left for the founder. The
folder-trust screen is `lifecycle`'s standing consent and stays on the trust
roots, never on a rule. This setting gates both.

`_accept` answers a hook-reported tool call a driver's `accepts_tool` takes;
no current vendor takes any (T249 removed claude's one case, the browser
tool)."""
from __future__ import annotations

import time

from .. import db
from ..infra import paths
from ..team import dialog_rules
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
    """Say yes to each agent's waiting tool call its vendor accepts, and answer
    each dialog a standing rule covers. Only an agent whose hook reported such a
    tool waiting is looked at for the first, and no screen is read for the
    second while there is no rule, so an idle tick reads nothing."""
    if not enabled():
        return
    now = int(time.time()) if now is None else now
    registered = set(paths.list_agents())
    rules = {(r["vendor"], r["title"]) for r in dialog_rules.list_rules()}
    for row in db.all_agent_states(con):
        agent = row["agent"]
        if agent not in registered or not row["terminal"]:
            continue
        try:
            if not _accept(con, agent, row, now, log) and rules:
                _answer_by_rule(con, agent, row, now, log, rules)
        except Exception as e:  # noqa: BLE001 - one agent's unreadable screen is not the tick's failure
            log(f"  {agent}: auto-accept failed: {e}")


def _stuck(con, agent: str, now: int) -> bool:
    return now - db.get_cursor(con, f"autoaccept_stuck:{agent}") < STUCK_BACKOFF_S


def _accept(con, agent: str, row, now: int, log) -> bool:
    """Whether a key was pressed on a waiting tool's prompt."""
    # `of`, not the raw file: a wait left by a prompt that was answered without
    # its PostToolUse is stale, and `of` is what says so (T100).
    held = waiting.of(con, row)
    tool = (held or {}).get("tool") or ""
    if not tool or row["closed_at"] or row["state"] != "RUNNING":
        return False
    if _stuck(con, agent, now):
        return False
    with orca.driver_scope():
        if not orca.accepts_tool(row["terminal"], tool):
            return False
        outcome, title, why = orca.accept_dialog(row["terminal"], tool)
    if outcome == orca.NOT_PRESSED:
        return False
    if outcome == orca.STILL_OPEN:
        db.set_cursor(con, f"autoaccept_stuck:{agent}", now)
        db.add_event(con, agent, "dialog_accept_failed", {"title": title, "tool": tool})
        log(f"  {agent}: pressed Enter on \"{title}\" ({tool}) and it is still open")
        return True
    db.add_event(con, agent, "dialog_accepted", {"title": title, "tool": tool})
    log(f"  {agent}: said yes once to {tool} (auto-accept)")
    return True


def _answer_by_rule(con, agent: str, row, now: int, log, rules: set) -> None:
    """Press the option a standing rule names on the dialog this agent shows,
    then read the screen again to see it close. The cheap read first: only a
    dialog whose title some rule names is worth the press path's own read."""
    if row["closed_at"] or _stuck(con, agent, now):
        return
    handle = row["terminal"]
    with orca.driver_scope():
        vendor = orca._driver_for(handle).name
        blocker = orca.screen_blocker_cached(handle)
        if blocker is None or blocker.kind != orca.MODAL or (vendor, blocker.title) not in rules:
            return
        chosen: dict[str, str] = {}

        def choose(vendor: str, dialog) -> int | None:
            number = dialog_rules.match(vendor, dialog.title, list(dialog.options))
            if number is not None:
                chosen["label"] = dict(dialog.options)[number]
            return number

        outcome, title, why = orca.answer_dialog(handle, choose)
    if outcome == orca.NOT_PRESSED:
        return
    detail = {"title": title, "label": chosen.get("label", ""), "by": "rule"}
    if outcome == orca.STILL_OPEN:
        db.set_cursor(con, f"autoaccept_stuck:{agent}", now)
        db.add_event(con, agent, "dialog_accept_failed", detail)
        log(f"  {agent}: pressed '{detail['label']}' on \"{title}\" (rule) and it is still open")
        return
    db.add_event(con, agent, "dialog_accepted", detail)
    log(f"  {agent}: answered \"{title}\" with '{detail['label']}' (rule)")
