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
    db.add_event(con, agent, "input_holder", {"holders": view(con, agent)})


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


def take(con, agent: str, kind: str, name: str, now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    con.execute("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES(?,?,?,?,?) "
                "ON CONFLICT(agent) DO UPDATE SET last_typed = excluded.last_typed",
                (agent, kind, name, now, now))
    _changed(con, agent)


def release(con, agent: str) -> None:
    con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
    _changed(con, agent)


def touch(con, agent: str, name: str, now: int | None = None) -> None:
    """The holder typed: the idle hold counts from now."""
    con.execute("UPDATE input_holders SET last_typed = ? WHERE agent = ? AND name = ?",
                (int(time.time()) if now is None else now, agent, name))
