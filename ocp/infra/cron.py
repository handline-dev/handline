"""A five-field cron expression (minute hour day-of-month month day-of-week), in local time (T605).

`*`, `*/n`, `a`, `a-b`, `a-b/n` and comma lists, and nothing else: no names, no `@daily`. Day-of-week is
0-6 with Sunday 0 (7 is Sunday too). As in cron, when both day fields are restricted a day matches if
either does."""
from __future__ import annotations

from datetime import datetime, timedelta

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("minute", "hour", "day of month", "month", "day of week")
# The furthest `next_after` looks: an expression that matches nothing in this long (Feb 31) has no next time.
_HORIZON_DAYS = 366 * 8


class Cron:
    def __init__(self, expr: str, fields: list[set[int]], dom_any: bool, dow_any: bool) -> None:
        self.expr, self.fields, self.dom_any, self.dow_any = expr, fields, dom_any, dow_any

    def day_ok(self, dt: datetime) -> bool:
        dom = dt.day in self.fields[2]
        dow = (dt.weekday() + 1) % 7 in self.fields[4]
        if self.dom_any or self.dow_any:
            return dom and dow
        return dom or dow


def _field(text: str, lo: int, hi: int, name: str) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        body, slash, rest = part.partition("/")
        if slash:
            if not rest.isdigit() or int(rest) < 1:
                raise ValueError(f"{name}: a step is a positive number, not {rest!r}")
            step = int(rest)
        if body == "*":
            a, b = lo, hi
        elif "-" in body:
            x, _, y = body.partition("-")
            if not (x.isdigit() and y.isdigit()):
                raise ValueError(f"{name}: {part!r} is not a range")
            a, b = int(x), int(y)
        elif body.isdigit():
            a = int(body)
            b = hi if slash else a
        else:
            raise ValueError(f"{name}: {part!r} is not a number, a range or *")
        if a < lo or b > hi or a > b:
            raise ValueError(f"{name}: {part!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> Cron:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("a cron expression has five fields: minute hour day-of-month month day-of-week")
    fields = [_field(p, lo, hi, n) for p, (lo, hi), n in zip(parts, _RANGES, _NAMES)]
    fields[4] = {d % 7 for d in fields[4]}
    return Cron(" ".join(parts), fields, parts[2].startswith("*"), parts[4].startswith("*"))


def next_after(cron: Cron, ts: int) -> int | None:
    """The first minute after `ts` the expression names, as a timestamp, or None within eight years."""
    dt = datetime.fromtimestamp(ts).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = dt + timedelta(days=_HORIZON_DAYS)
    while dt < end:
        if dt.month not in cron.fields[3]:
            dt = (dt.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif not cron.day_ok(dt):
            dt = dt.replace(hour=0, minute=0) + timedelta(days=1)
        elif dt.hour not in cron.fields[1]:
            dt = dt.replace(minute=0) + timedelta(hours=1)
        elif dt.minute not in cron.fields[0]:
            dt += timedelta(minutes=1)
        else:
            return int(dt.timestamp())
    return None
