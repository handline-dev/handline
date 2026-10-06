"""Who types at an agent's terminal: the daemon unless a person or a device took it (`input.take`). While someone
else holds it the daemon types nothing and presses nothing there (`delivery._type_gate`), and the work it owes stays
queued with its held mark. A holder who has not typed for `input_hold_idle_s` loses the hold, found out whenever the
hold is read, and the loss is recorded like a take. A call that is itself a person's control of the session
(`controlling`) is not held back by the hold."""
from __future__ import annotations

import contextlib
import threading
import time

from .. import db
from ..session import idle_close

IDLE_KEY, DEFAULT_IDLE_S = "input_hold_idle_s", 600
DAEMON = {"kind": "daemon", "name": "daemon", "since": None}
HELD_BY = " is held by "


def held_why(agent: str, name: str) -> str:
    """What the daemon's refusal to type or press at a held terminal says; callers that must tell it from another refusal match `HELD_BY`."""
    return f"{agent}'s terminal{HELD_BY}{name}"
_control = threading.local()


def idle_s() -> int:
    return idle_close.config_seconds(IDLE_KEY, DEFAULT_IDLE_S)


@contextlib.contextmanager
def controlling():
    """For the duration of a person's own call: the daemon's typing gates do not count the hold against it."""
    _control.on = True
    try:
        yield
    finally:
        _control.on = False


def _ts(t) -> str | None:
    from . import agentview
    return agentview._ts(t)


def _changed(con, agent: str) -> None:
    from . import termchannel
    holders = view(con, agent)
    db.add_event(con, agent, "input_holder", {"holders": holders})
    termchannel.holders_changed(con, agent, holders)


def holder(con, agent: str, now: int | None = None) -> dict | None:
    """The row of whoever holds `agent`'s input, or None when the daemon does; an idle hold is dropped here."""
    now = int(time.time()) if now is None else now
    row = con.execute("SELECT kind, name, since, last_typed FROM input_holders WHERE agent = ?", (agent,)).fetchone()
    if row is None:
        return None
    if now - row["last_typed"] >= idle_s():
        con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
        _changed(con, agent)
        return None
    return dict(row)


def view(con, agent: str) -> list[dict]:
    """The spec's `InputHolder` list: the person or device holding, else the daemon."""
    row = holder(con, agent)
    return [{"kind": row["kind"], "name": row["name"], "since": _ts(row["since"])}] if row else [dict(DAEMON)]


def held_by_other(con, agent: str) -> str | None:
    """The name of whoever holds the input against the daemon's own typing, or None."""
    if getattr(_control, "on", False):
        return None
    row = holder(con, agent)
    return row["name"] if row else None


def take(con, agent: str, kind: str, name: str, now: int | None = None) -> bool:
    """Hold `agent`'s input as `name`: one statement that sets the row only when there is none or it is already `name`'s, and
    says whether it did, so two takes at once cannot both win. An idle hold is dropped first."""
    now = int(time.time()) if now is None else now
    holder(con, agent, now)
    cur = con.execute("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(agent) DO UPDATE SET last_typed = excluded.last_typed WHERE input_holders.name = excluded.name",
                      (agent, kind, name, now, now))
    if cur.rowcount:
        _changed(con, agent)
    return bool(cur.rowcount)


def release(con, agent: str) -> None:
    con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
    _changed(con, agent)


def touch(con, agent: str, name: str, now: int | None = None) -> None:
    """The holder typed: the idle hold counts from now."""
    con.execute("UPDATE input_holders SET last_typed = ? WHERE agent = ? AND name = ?",
                (int(time.time()) if now is None else now, agent, name))
