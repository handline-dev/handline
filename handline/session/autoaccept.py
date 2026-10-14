"""Answering the dialogs a working agent meets all day (T64), behind one global
setting, on by default: `auto_accept_dialogs` in config.yaml, `hl auto-accept`.

A dialog the founder gave a standing answer (`team/dialog_rules`) is answered
with that rule's option, by its number on the screen just read; a dialog no rule
matches is left for the founder. The folder-trust screen is `lifecycle`'s
standing consent and stays on the trust roots, never on a rule.
"""
from __future__ import annotations

import time

from .. import db
from ..team import dialog_rules
from . import idle_close, term

KEY = "auto_accept_dialogs"
# A prompt that Enter did not clear is left alone this long, not pressed again
# on every tick.
STUCK_BACKOFF_S = 300


def enabled() -> bool:
    return idle_close.config_flag(KEY, True)


def set_enabled(on: bool) -> None:
    idle_close.set_config_flag(KEY, on)


def _stuck(con, agent: str, now: int) -> bool:
    return now - db.get_cursor(con, f"autoaccept_stuck:{agent}") < STUCK_BACKOFF_S


def answer_by_rule(con, agent: str, handle: str, log, now: int | None = None) -> bool:
    """A launch's use of the standing rules, for a dialog it meets before the sweep
    does: True when a rule's option was pressed and the dialog closed."""
    rules = {(r["vendor"], r["title"]) for r in dialog_rules.list_rules()}
    row = db.get_agent_state(con, agent)
    if not rules or not row or row["terminal"] != handle:
        return False
    return _answer_by_rule(con, agent, row, int(time.time()) if now is None else now, log, rules)


def _answer_by_rule(con, agent: str, row, now: int, log, rules: set) -> bool:
    """Press the option a standing rule names on the dialog this agent shows,
    then read the screen again to see it close; whether it closed. The cheap read
    first: only a dialog whose title some rule names is worth the press path's own read."""
    if row["closed_at"] or _stuck(con, agent, now):
        return False
    handle = row["terminal"]
    with term.driver_scope():
        vendor = term._driver_for(handle).name
        blocker = term.screen_blocker_cached(handle)
        if blocker is None or blocker.kind != term.MODAL or (vendor, blocker.title) not in rules:
            return False
        chosen: dict[str, str] = {}

        def choose(vendor: str, dialog) -> int | None:
            number = dialog_rules.match(vendor, dialog.title, list(dialog.options))
            if number is not None:
                chosen["label"] = dict(dialog.options)[number]
            return number

        outcome, title, why = term.answer_dialog(handle, choose)
    if outcome == term.NOT_PRESSED:
        return False
    detail = {"title": title, "label": chosen.get("label", ""), "by": "rule"}
    if outcome == term.STILL_OPEN:
        db.set_cursor(con, f"autoaccept_stuck:{agent}", now)
        db.add_event(con, agent, "dialog_accept_failed", detail)
        log(f"  {agent}: pressed '{detail['label']}' on \"{title}\" (rule) and it is still open")
        return False
    db.add_event(con, agent, "dialog_accepted", detail)
    log(f"  {agent}: answered \"{title}\" with '{detail['label']}' (rule)")
    return True
