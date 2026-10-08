"""chat.send_now and stats.outlook."""
from . import delivery, err, fake

RANGES = ("12h", "24h", "3d", "7d", "14d", "30d")


@fake("chat.send_now", "stats.outlook")
def pc_gaps(role, method, params):
    if method == "stats.outlook":
        if params.get("range") not in RANGES:
            return "fail", err(-32602, "bad_request", field="range")
        return "ok", {"range": params["range"], "now": "2026-01-01T00:00:00Z", "accounts": 0, "past": [], "future": [], "runway": [], "cost_per_day": []}
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if isinstance(params.get("request"), bool) or not isinstance(params.get("request"), int):
        return "fail", err(-32602, "bad_request", field="request")
    return delivery.send_now(params) or ("fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "request"}})
