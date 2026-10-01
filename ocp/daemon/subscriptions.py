"""Two upkeep sweeps over the accounts, each read from state every tick.

A month-cycle account (`usage.cycle`) whose end date is within `cycle_warn_days`
tells the founder once, as a message that asks nothing (`ask.report`), remembered
by account and end date: a later end date is a new subscription and is told again.

Each account's plan is asked of its vendor again once an hour, one account at a
time and off the tick, because `claude auth status` is a process with a ten-second
timeout and the plan is recorded on disk for the readers that must not run one. A
plan that differs from the one seen last tick (Pro to Max) is an event, and reaches
the pick and the recovery estimates on the next read.
"""
from __future__ import annotations

import threading

from ..infra import paths
from ..team import accounts, ask as askmod
from ..usage import cycle
from .. import db

PLAN_REFRESH_S = 3600
SENDER = "daemon"
_asking: threading.Thread | None = None


def sweep_endings(con, log, now: int) -> None:
    cfg = cycle.config()
    ahead = cycle.warn_days(cfg) * 86400
    for acct in paths.list_accounts():
        end = cycle.end_of(acct, cfg)
        if end is None or now >= end or end - now > ahead:
            continue
        day = str(cycle.end_date(acct, cfg))
        key = f"sub_ending_told:{acct}"
        if db.get_meta(con, key) == day:
            continue
        askmod.report(con, SENDER,
                      f"Account {acct}'s one-month subscription ends {day} (last usable day), in "
                      f"{(end - now) // 3600} h. After it the account is shown as ended and no agent is "
                      f"placed on it; it is not removed. Resubscribing, then "
                      f"`ocp account cycle {acct} --end YYYY-MM-DD` sets the next end.",
                      "subscription ending")
        db.set_meta(con, key, day)
        log(f"  {acct}: subscription ends {day} - founder told")


def _ask(acct: str) -> None:
    try:
        accounts.subscription_type(acct, refresh=True)
    except Exception:  # noqa: BLE001 - a plan not read now is asked again next hour
        pass


def sweep_plans(con, log, now: int) -> None:
    global _asking
    for acct in paths.list_accounts():
        plan, key = accounts.recorded_subscription_type(acct), f"plan_seen:{acct}"
        seen = db.get_meta(con, key)
        if plan and plan != seen:
            db.set_meta(con, key, plan)
            if seen:
                db.add_event(con, acct, "plan_changed", {"from": seen, "to": plan}, account=acct)
                log(f"  {acct}: plan {seen} -> {plan}")
    if _asking is not None and _asking.is_alive():
        return
    due = sorted((db.get_cursor(con, f"plan_checked:{a}"), a) for a in paths.list_accounts())
    if not due or now - due[0][0] < PLAN_REFRESH_S:
        return
    db.set_cursor(con, f"plan_checked:{due[0][1]}", now)
    _asking = threading.Thread(target=_ask, args=(due[0][1],), daemon=True)
    _asking.start()


def wait_for_plan_ask() -> None:
    """Tests: let the plan question the last tick started finish."""
    if _asking is not None:
        _asking.join()


def sweep(con, log, now: int) -> None:
    sweep_endings(con, log, now)
    sweep_plans(con, log, now)
