"""Two upkeep sweeps over the accounts, each read from state every tick.

A month-cycle account (`usage.cycle`) whose end date is within `cycle_warn_days`
tells the founder once, as an event keyed by account and end date: a later end
date is a new subscription and is told again. A notice to the human has no
session to type at, so it is an event.

Each account's plan is asked of its vendor again once an hour, one account per
tick, because `claude auth status` is a process and the plan is recorded on
disk for the readers that must not run one. A plan that changed (Pro to Max)
reaches the pick and the recovery estimates on the next read.
"""
from __future__ import annotations

from ..infra import paths
from ..team import accounts
from ..usage import cycle
from .. import db
from .timebox import HUMAN

PLAN_REFRESH_S = 3600


def sweep_endings(con, log, now: int) -> None:
    cfg = cycle.config()
    ahead = cycle.warn_days(cfg) * 86400
    for acct in paths.list_accounts():
        end = cycle.end_of(acct, cfg)
        if end is None or now >= end or end - now > ahead:
            continue
        day = cycle.end_date(acct, cfg)
        if db.add_event(con, HUMAN, "subscription_ending",
                        {"account": acct, "end": str(day), "left_s": int(end - now)},
                        account=acct, dedup_key=f"sub_ending:{acct}:{day}"):
            log(f"  {acct}: subscription ends {day} - founder told")


def sweep_plans(con, log, now: int) -> None:
    due = sorted((db.get_cursor(con, f"plan_checked:{a}"), a) for a in paths.list_accounts())
    if not due or now - due[0][0] < PLAN_REFRESH_S:
        return
    acct = due[0][1]
    db.set_cursor(con, f"plan_checked:{acct}", now)
    before = accounts.recorded_subscription_type(acct)
    after = accounts.subscription_type(acct, refresh=True)
    if after and before and after != before:
        db.add_event(con, acct, "plan_changed", {"from": before, "to": after}, account=acct)
        log(f"  {acct}: plan {before} -> {after}")


def sweep(con, log, now: int) -> None:
    sweep_endings(con, log, now)
    sweep_plans(con, log, now)
