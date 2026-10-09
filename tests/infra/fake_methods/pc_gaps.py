"""chat.send_now and stats.outlook."""
from . import delivery, err, fake

RANGES = ("12h", "24h", "3d", "7d", "14d", "30d")


# The agents the outlook_agent_use fixtures name (--var outlook_agent, outlook_group): two agents of the group `grp`.
USE = [{"agent": "grp.one", "tokens": 9000, "cost": 3.5, "share": 0.7, "est_pct": 4.2, "fracs": {"acct.one": 0.7}},
       {"agent": "grp.two", "tokens": 3000, "cost": 1.5, "share": 0.3, "est_pct": None, "fracs": {"acct.one": 0.3}}]
CURVES = {"grp.one": [0.0, 1.5, 2.9], "grp.two": [0.0, 0.5, 1.2], "grp:grp": [0.0, 2.0, 4.1]}


@fake("chat.send_now", "stats.outlook")
def pc_gaps(role, method, params):
    if method == "stats.outlook":
        if params.get("range") not in RANGES:
            return "fail", err(-32602, "bad_request", field="range")
        pick = params.get("agent")
        if pick is not None and not isinstance(pick, str):
            return "fail", err(-32602, "bad_request", field="agent")
        out = {"range": params["range"], "now": "2026-01-01T00:00:00Z", "accounts": 0, "past": [{"at": "2026-01-01T00:00:00Z", "left_pct": 90.0}, {"at": "2026-01-01T01:00:00Z", "left_pct": 89.0}, {"at": "2026-01-01T02:00:00Z", "left_pct": 88.0}], "future": [], "runway": [], "cost_per_day": [],
               "agent_use": list(USE)}
        if pick is not None:
            out["overlay"] = [] if pick not in CURVES else CURVES[pick]
        return "ok", out
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if isinstance(params.get("request"), bool) or not isinstance(params.get("request"), int):
        return "fail", err(-32602, "bad_request", field="request")
    return delivery.send_now(params) or ("fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "request"}})
