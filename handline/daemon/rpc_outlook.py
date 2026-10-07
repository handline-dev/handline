"""stats.outlook: the week left of the accounts together (recorded, then guessed), each account's runway and what the agents
cost per day. It serves the PC app's outlook tab, which the TUI draws from `actions.weekly_outlook`, `status.runway` and
`actions.daily_agent_cost`; no hl command serves it. `range` is how far back and ahead the headroom curve goes, and how many days
the cost covers (under a day counts as one)."""
from __future__ import annotations

import time

from .. import actions, db
from ..infra import paths
from ..usage import load as loadmod, status
from . import agentview, rpc

_HOURS = {"12h": 12, "24h": 24, "3d": 72, "7d": 168, "14d": 336, "30d": 720}


def _range(params) -> str:
    r = params.get("range")
    if not isinstance(r, str) or r not in _HOURS:
        raise rpc.bad_request(f"range is one of {', '.join(_HOURS)}", "range", rpc.INVALID_PARAMS)
    return r


def _points(series) -> list[dict]:
    return [{"at": agentview._ts(t), "left_pct": None if v is None else round(float(v), 2)} for t, v in series]


def _runs_out(st, name: str, now: int, ld) -> int | None:
    """Seconds until the window that runs out first does, as `describe_runway` picks it; None when there is no reading, no
    burn to go by, or the window resets first."""
    r = status.runway(st, status.read_burn(name), now, ld)
    if r is None:
        return None
    if r.left_short == 0 or r.left_long == 0:
        return 0
    five = r.eta_s if r.burning and not r.past_reset else None
    if r.long_s is not None and (five is None or r.long_s < five):
        return int(r.long_s)
    return None if five is None else int(five)


def _outlook(ctx, params) -> dict:
    name = _range(params)
    hours, now = _HOURS[name], int(time.time())
    days = max(1, hours // 24)
    con = db.connect()
    try:
        out = actions.weekly_outlook(con, now, hours)
        cost = actions.daily_agent_cost(con, now, days)
        loads = loadmod.read_all()
        runway = []
        for acct in paths.list_accounts():
            st = status.read(acct)
            runway.append({"account": acct, "runs_out_in_seconds": None if st.unmetered else _runs_out(st, acct, now, loads.get(acct)),
                           "week_resets_at": agentview._ts(st.long.reset)})
    finally:
        con.close()
    return {"range": name, "now": agentview._ts(now), "accounts": out.accounts, "past": _points(out.past),
            "future": _points(out.future), "runway": runway,
            "cost_per_day": [{"day": d, "agents": {a: round(c, 4) for a, c in sorted(per.items())}} for d, per in sorted(cost.items())]}


rpc.METHODS["stats.outlook"] = rpc.Method(_outlook, "member")
