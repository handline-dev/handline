"""cycle - an account kept on one-month subscriptions, and when its month ends.

An account subscribed one month at a time stops being worth anything on its end
date, whatever its weekly window says: headroom left unspent that day is lost,
and after it the account cannot take work. Three settings in config.yaml, per
account, over one default each:

    month_cycle_<account>: true        the account is on one-month subscriptions
    sub_end_<account>: "2026-10-22"    the last day it can be used (YYYY-MM-DD),
                                       default: a month after the subscription started
    cycle_last_week_first: true        spend an ending account first (default true)
    cycle_warn_days: 3                 the founder is told this many days ahead

An account with no `month_cycle_<account>` is untouched by all of it. With the
switch and no `sub_end_<account>` the end is a month after the subscription
started, at the subscription's own time of day, read from the local profile; where
that cannot be read there is no end known and the account is not judged ended: a
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


def config_or_none() -> dict | None:
    """config.yaml, or None when it cannot be read: unlike `config`, which reads
    an unreadable file as empty, this lets a caller tell "says nothing" from
    "could not be read"."""
    from . import load
    return load.config_or_none()


def _cfg(cfg: dict | None) -> dict:
    return config() if cfg is None else cfg


def enabled(acct: str, cfg: dict | None = None) -> bool:
    return _cfg(cfg).get(f"month_cycle_{acct}") is True


def add_month(d: dt.date) -> dt.date:
    """One calendar month on, the day clamped to the shorter month's last."""
    y, m = divmod(d.month, 12)
    y, m = d.year + y, m + 1
    last = (dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, min(d.day, last))


def started(acct: str) -> dt.datetime | None:
    """The moment the current subscription started, local time, from the vendor's
    local profile (`Driver.profile`: that one field is all that is taken)."""
    from .. import vendors
    try:
        at = vendors.for_account(acct).profile(vendors.config_dir(acct)).get("subscription_started")
    except (vendors.UnknownVendor, OSError):
        return None
    return dt.datetime.fromtimestamp(at) if at else None


def _end(acct: str, cfg: dict | None) -> tuple[dt.date, float] | None:
    """(the last day, the moment the account stops being usable), or None when the
    account is not on a cycle or no end is known.

    `sub_end_<account>` is a day the founder typed: usable through all of it, so
    the moment is the start of the next day. Without one the end is a month after
    the subscription started, at the subscription's own time of day, because that
    is when it lapses; one already past is an ended account, until the profile's
    start changes (a renewal) or an end is typed. The date is quoted in config.yaml (the reader refuses an
    unquoted one as ambiguous); one that does not parse is no end, not the
    default: a typo must not end an account."""
    cfg = _cfg(cfg)
    if cfg.get(f"month_cycle_{acct}") is not True:
        return None
    v = cfg.get(f"sub_end_{acct}")
    if v is not None:
        try:
            d = dt.date.fromisoformat(v) if isinstance(v, str) else None
        except ValueError:
            d = None
        return (d, day_end(d)) if d else None
    start = started(acct)
    if not start:
        return None
    lapse = dt.datetime.combine(add_month(start.date()), start.time())
    return lapse.date(), lapse.timestamp()


def end_date(acct: str, cfg: dict | None = None) -> dt.date | None:
    """The last day the account can be used, or None (see `_end`)."""
    end = _end(acct, cfg)
    return end[0] if end else None


def end_of(acct: str, cfg: dict | None = None) -> float | None:
    """When the account stops being usable, as a timestamp, or None (see `_end`)."""
    end = _end(acct, cfg)
    return end[1] if end else None


def own_end(acct: str, cfg: dict | None = None) -> str:
    """The end day typed for this account, as written, or "" when none is."""
    v = _cfg(cfg).get(f"sub_end_{acct}")
    return v if isinstance(v, str) else ""


def clear_end(acct: str) -> None:
    """Forget the typed end, so it is a month after the subscription started again."""
    from . import load
    cfg = load.config_for_write()
    cfg.pop(f"sub_end_{acct}", None)
    load.write_config(cfg)


def end_is_default(acct: str, cfg: dict | None = None) -> bool:
    return _cfg(cfg).get(f"sub_end_{acct}") is None


def day_end(day: str | dt.date) -> float:
    """The moment a last usable day is over: the start of the day after it, local time."""
    d = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
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
    from . import load
    cfg = load.config_for_write()
    cfg[f"month_cycle_{acct}"] = on
    if end is not None:
        cfg[f"sub_end_{acct}"] = end.isoformat()
    load.write_config(cfg)
