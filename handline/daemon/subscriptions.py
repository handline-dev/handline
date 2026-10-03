"""Two upkeep sweeps over the accounts, each read from state every tick.

A month-cycle account (`usage.cycle`) whose end date is within `cycle_warn_days`
tells the founder once, as a task of the founder's on the board (`ask.raise_decision`),
remembered by account and end date: a later end date is a new subscription and is told
again.

Each account's plan is asked of its vendor again once an hour, one account at a
time and off the tick, because `claude auth status` is a process with a ten-second
timeout and the plan is recorded on disk for the readers that must not run one. A
plan that differs from the one seen last tick (Pro to Max) is an event, and reaches
the pick and the recovery estimates on the next read.
"""
from __future__ import annotations

import threading

from ..infra import paths
from ..team import accounts, agents, ask as askmod
from ..usage import cycle
from .. import db

PLAN_REFRESH_S = 3600
SENDER = "daemon"
_asking: threading.Thread | None = None


def sweep_endings(con, log, now: int) -> None:
    """Act only on what is known. A file that cannot be read this tick (config.yaml
    caught half written, the profile mid-rewrite) or a date that does not parse is
    not "the cycle is off": the founder's notice stays as it is."""
    cfg = cycle.config_or_none()
    if cfg is None:
        log("  subscriptions: config.yaml unreadable - notices left as they are")
        return
    ahead = cycle.warn_days(cfg) * 86400
    for acct in paths.list_accounts():
        told = db.get_meta(con, f"sub_ending_told:{acct}")
        if not cycle.enabled(acct, cfg):
            _settle(con, log, acct, "the month cycle was turned off")
            db.clear_meta(con, f"sub_ending_told:{acct}")      # turned on again, the same date is news again
            continue
        if told and cycle.day_end(told) <= now:
            _settle(con, log, acct, "the subscription ended; the account is shown as ended")
        end = cycle.end_of(acct, cfg)
        if end is None:
            continue
        day = str(cycle.end_date(acct, cfg))
        if now >= end:
            _settle(con, log, acct, "the subscription ended; the account is shown as ended")
            continue
        if day != told:
            _settle(con, log, acct, "the account's end date changed")
        if end - now > ahead or told == day:
            continue
        rid = askmod.raise_decision(
            con, _opener(con, acct),
            f"{acct} subscription ends {day}\n\n"
            f"Account {acct}'s one-month subscription ends {day} (its last usable day), in "
            f"{(end - now) // 3600} h. After that it is shown as ended and no agent is placed on it; "
            f"it is not removed. To keep it, resubscribe and run "
            f"`ocp account cycle {acct} --end YYYY-MM-DD`; to let it end, do nothing. "
            f"This task closes by itself once the account has ended or its end date changes.",
            "subscription ending")
        row = con.execute("SELECT task_id FROM task_requests WHERE request_id = ?", (rid,)).fetchone()
        if row:
            db.set_meta(con, f"sub_ending_task:{acct}", str(row["task_id"]))
        db.set_meta(con, f"sub_ending_told:{acct}", day)
        log(f"  {acct}: subscription ends {day} - founder told")


def _settle(con, log, acct: str, why: str) -> None:
    """Close the founder's notice for this account: it is about an end date
    that has passed or no longer holds. The daemon marks it done as the founder's
    own card, and says so in the reason."""
    from .. import actions
    key = f"sub_ending_task:{acct}"
    tid = db.get_meta(con, key)
    if not tid:
        return
    ok, msg = actions.mark_task_done(con, int(tid), actions.HUMAN, reason=f"closed by the daemon: {why}")
    db.clear_meta(con, key)
    log(f"  {acct}: subscription notice T{tid} closed ({why})" if ok else f"  {acct}: notice T{tid}: {msg}")


def _opener(con, acct: str) -> str:
    """Whom the founder's task is opened by: an agent on the account, else any
    agent, since the board and the home count are drawn per listed agent."""
    names = paths.list_agents()
    return next((n for n in names if agents.current_account(n, con) == acct),
                names[0] if names else SENDER)


def _ask(acct: str, log) -> None:
    try:
        if not accounts.subscription_type(acct, refresh=True):
            log(f"  {acct}: no plan recorded and none read (not logged in, or auth status failed)")
    except Exception as e:  # noqa: BLE001 - a plan not read now is asked again next hour
        log(f"  {acct}: plan question failed: {type(e).__name__}: {e}")


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
    _asking = threading.Thread(target=_ask, args=(due[0][1], log), daemon=True)
    _asking.start()


def wait_for_plan_ask() -> None:
    """Tests: let the plan question the last tick started finish."""
    if _asking is not None:
        _asking.join()


def sweep(con, log, now: int) -> None:
    sweep_endings(con, log, now)
    sweep_plans(con, log, now)
