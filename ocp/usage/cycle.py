"""cycle - an account kept on one-month subscriptions, and when its month ends.

An account subscribed one month at a time stops being worth anything on its end
date, whatever its weekly window says: headroom left unspent that day is lost,
and after it the account cannot take work. Three settings in config.yaml, per
account, over one default each:

    month_cycle_<account>: true        the account is on one-month subscriptions
    sub_end_<account>: "2026-10-22"    the last day it can be used (YYYY-MM-DD)
    cycle_last_week_first: true        spend an ending account first (default true)
    cycle_warn_days: 3                 the founder is told this many days ahead

An account with no `month_cycle_<account>` is untouched by all of it. One with
the switch but no end date has no end known, so it is not judged ended: a
missing date must not shut an account that may well be live.
"""
from __future__ import annotations

import datetime as dt

DEFAULT_WARN_DAYS = 3


def config() -> dict:
    """config.yaml as the readers see it; `load` imports `status`, which imports
    this, so it is reached late."""
    from . import load
    return load._config()


def _cfg(cfg: dict | None) -> dict:
    return config() if cfg is None else cfg


def enabled(acct: str, cfg: dict | None = None) -> bool:
    return _cfg(cfg).get(f"month_cycle_{acct}") is True


def end_date(acct: str, cfg: dict | None = None) -> dt.date | None:
    """The last day the account can be used, or None when it is not on a cycle
    or no date is set. The date is quoted in config.yaml: the reader refuses
    an unquoted one as ambiguous."""
    cfg = _cfg(cfg)
    if cfg.get(f"month_cycle_{acct}") is not True:
        return None
    v = cfg.get(f"sub_end_{acct}")
    try:
        return dt.date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def end_of(acct: str, cfg: dict | None = None) -> float | None:
    """When the account stops being usable, as a timestamp: the start of the
    day after its end date, local time."""
    d = end_date(acct, cfg)
    if d is None:
        return None
    return dt.datetime.combine(d + dt.timedelta(days=1), dt.time()).timestamp()


def last_week_first(cfg: dict | None = None) -> bool:
    return _cfg(cfg).get("cycle_last_week_first") is not False


def warn_days(cfg: dict | None = None) -> int:
    v = _cfg(cfg).get("cycle_warn_days")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else DEFAULT_WARN_DAYS


def ended_why(acct: str, now: float, cfg: dict | None = None) -> str | None:
    """Why the account is out for good on `now`, None while it is not."""
    end = end_of(acct, cfg)
    if end is None or now < end:
        return None
    d = end_date(acct, cfg)
    return (f"subscription ended {d} (month cycle) - not picked; after resubscribing, "
            f"`ocp account cycle {acct} --end YYYY-MM-DD` sets the new end")


def set_cycle(acct: str, on: bool, end: dt.date | None = None) -> None:
    """Write the switch and, where given, the end date, keeping every other
    setting. Turning it off leaves the date, so turning it on again finds it."""
    from ..infra import paths
    from ..infra.yamlcompat import dump
    from . import load
    cfg = load.config_for_write()
    cfg[f"month_cycle_{acct}"] = on
    if end is not None:
        cfg[f"sub_end_{acct}"] = end.isoformat()
    paths.config_file().write_text(dump(cfg))
