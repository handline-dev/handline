"""daemon.status: is the daemon up, what it runs, which terminal host it drives and how healthy, how many agents are
in each state and how much waits in the founder's inbox. It serves the web UI's status bar; the spec maps no hl command to it."""
from __future__ import annotations

import time

from .. import actions, db
from ..infra import paths
from ..session import term
from . import agentview, rpc

STARTED_AT = time.time()


def _host() -> dict:
    name = term.host_name()
    try:
        handles = term.live_terminals()
    except Exception as e:  # noqa: BLE001 - a host that breaks is a health reading, not a failed call
        return {"name": name, "health": "down", "detail": f"{type(e).__name__}: {e}"[:200]}
    if handles is None:
        return {"name": name, "health": "degraded", "detail": "the host did not answer"}
    return {"name": name, "health": "ok", "detail": f"{len(handles)} terminal(s)"}


def _status(ctx, params) -> dict:
    con = db.connect()
    try:
        have = {v["name"]: v["state"] for v in agentview.stored_all(con)}
        names = paths.list_agents()
        by_state: dict[str, int] = {}
        for n in names:
            state = have.get(n) or agentview.compute(con, n)["state"]
            by_state[state] = by_state.get(state, 0) + 1
        unread = len(actions.mailbox_rows(con))
    finally:
        con.close()
    return {"alive": True, "version": rpc._loaded_sha(), "protocol": rpc.PROTOCOL, "schema": db.LATEST_VERSION,
            "started_at": agentview._ts(STARTED_AT), "terminal_host": _host(),
            "agents": {"total": len(names), "by_state": by_state}, "inbox": {"unread": unread}}


rpc.METHODS["daemon.status"] = rpc.Method(_status, "agent")
