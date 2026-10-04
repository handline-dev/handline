"""cycle - an account kept on one-month subscriptions, and when its month ends.

An account is renewing (the default) or ends this month: `month_cycle_<account>`
is the switch for the second, and what follows from it is below. Where either one
stands in its month is `position`.

An account subscribed one month at a time stops being worth anything on its end
date, whatever its weekly window says: headroom left unspent that day is lost,
and after it the account cannot take work. Three settings in config.yaml, per
account, over one default each:

    month_cycle_<account>: true        the account is on one-month subscriptions
    sub_end_<account>: "2026-10-22"    the last day it can be used (YYYY-MM-DD),
                                       default: the next monthly anniversary of the subscription start
    cycle_last_week_first: true        spend an ending account first (default true)
    cycle_warn_days: 3                 the founder is told this many days ahead

An account with no `month_cycle_<account>` is untouched by all of it. With the
switch and no `sub_end_<account>` the end is the next monthly anniversary of the
subscription start, at the subscription's own time of day, read from the local
profile (the start is the first subscription's and does not move at a renewal, so
the anniversary still ahead is the end of the month it is in); where the start
cannot be read there is no end known and the account is not judged ended: a missing
date must not shut an account that may well be live.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

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


def add_months(d: dt.date, n: int) -> dt.date:
    """`n` calendar months on, the day clamped to the shorter month's last."""
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    last = (dt.date(y + (m + 1) // 12, (m + 1) % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m + 1, min(d.day, last))


def add_month(d: dt.date) -> dt.date:
    return add_months(d, 1)


def started(acct: str) -> dt.datetime | None:
    """The moment the current subscription started, local time, from the vendor's
    local profile (`Driver.profile`: that one field is all that is taken)."""
    from .. import vendors
    try:
        at = vendors.for_account(acct).profile(vendors.config_dir(acct)).get("subscription_started")
    except (vendors.UnknownVendor, OSError):
        return None
    return dt.datetime.fromtimestamp(at) if at else None


def _end(acct: str, cfg: dict | None, now: float | None = None) -> tuple[dt.date, float] | None:
    """(the last day, the moment the account stops being usable), or None when the
    account is not on a cycle or no end is known.

    `sub_end_<account>` is a day the founder typed: usable through all of it, so
    the moment is the start of the next day. Without one the end is the next monthly
    anniversary of the subscription start, at its time of day, because that is when
    the month lapses. The profile's start is the first subscription's and does not
    move at a renewal, so the anniversary still ahead is taken: a default end is
    never already past. The date is quoted in config.yaml (the reader refuses an
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
    lapse = _anniversary(start, time.time() if now is None else now)
    return lapse.date(), lapse.timestamp()


def _anniversary(start: dt.datetime, now: float, back: int = 0) -> dt.datetime:
    """The first monthly anniversary of `start` after `now`, at its time of day; `back`
    months before it. Counted from the start, so a 31st clamps into a short month and
    is the 31st again after it."""
    n = 1
    while dt.datetime.combine(add_months(start.date(), n), start.time()).timestamp() <= now:
        n += 1
    return dt.datetime.combine(add_months(start.date(), n - back), start.time())


def end_date(acct: str, cfg: dict | None = None, now: float | None = None) -> dt.date | None:
    """The last day the account can be used, or None (see `_end`)."""
    end = _end(acct, cfg, now)
    return end[0] if end else None


def end_of(acct: str, cfg: dict | None = None, now: float | None = None) -> float | None:
    """When the account stops being usable, as a timestamp, or None (see `_end`)."""
    end = _end(acct, cfg, now)
    return end[1] if end else None


def own_end(acct: str, cfg: dict | None = None) -> str:
    """The end day typed for this account, as written, or "" when none is."""
    v = _cfg(cfg).get(f"sub_end_{acct}")
    return v if isinstance(v, str) else ""


def clear_end(acct: str) -> None:
    """Forget the typed end, so it is the next monthly anniversary of the subscription start again."""
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
            f"`hl account cycle {acct} --end YYYY-MM-DD` sets the new end")


@dataclass
class Position:
    """Where an account stands in its monthly cycle. `day` of `length` days since
    the cycle began; `edge` is its last day when the account ends (`ends`), or the
    day it renews; `day` is None where the subscription start is not known."""
    ends: bool
    edge: dt.date | None
    day: int | None = None
    length: int | None = None
    over: bool = False

    def line(self) -> str:
        where = f"day {self.day} of {self.length}" if self.day and self.length and not self.over else ""
        if self.ends:
            tail = (f"ended {self.edge}" if self.over else f"ends {self.edge} (one-off)") if self.edge \
                else "one-off, no end date known"
        else:
            tail = f"renews {self.edge}"
        return f"{where} - {tail}" if where else tail


def fraction(pos: "Position | None") -> float | None:
    """How far through its cycle the account is, 0 on day 1 and 1 on the last day, for the page's
    gauge to put its marker at; None where the day is not known."""
    if pos is None or not pos.day or not pos.length:
        return None
    return min(1.0, max(0.0, (pos.day - 1) / max(pos.length - 1, 1)))


def position(acct: str, now: float, cfg: dict | None = None) -> Position | None:
    """Where the account is in its monthly cycle on `now`, or None when that is
    unknown: a renewing account counts months from the subscription start, so one
    whose start cannot be read has no position. An account that ends counts its
    one cycle from the start to its last day; with no start it still knows that day."""
    ends = enabled(acct, cfg)
    start = started(acct)
    today = dt.datetime.fromtimestamp(now).date()
    if ends:
        edge, at = end_date(acct, cfg, now), end_of(acct, cfg, now)
        over = at is not None and now >= at
        if start is None or edge is None:
            return Position(True, edge, over=over)
        first = start.date() if not end_is_default(acct, cfg) else _anniversary(start, now, 1).date()
        return Position(True, edge, (today - first).days + 1, (edge - first).days + 1, over)   # the last day is a day of it
    if start is None:
        return None
    first = start.date()
    k = max(0, (today.year * 12 + today.month) - (first.year * 12 + first.month))
    while k and add_months(first, k) > today:
        k -= 1
    while add_months(first, k + 1) <= today:
        k += 1
    begin, nxt = add_months(first, k), add_months(first, k + 1)
    return Position(False, nxt, (today - begin).days + 1, (nxt - begin).days)


def set_cycle(acct: str, on: bool, end: dt.date | None = None) -> None:
    """Write the switch and, where given, the end date, keeping every other
    setting. Turning it off leaves the date, so turning it on again finds it."""
    from . import load
    cfg = load.config_for_write()
    cfg[f"month_cycle_{acct}"] = on
    if end is not None:
        cfg[f"sub_end_{acct}"] = end.isoformat()
    load.write_config(cfg)
