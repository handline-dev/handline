"""accounts.list, team.get, hr.list, schedules.list, stats.get and settings.get: empty answers of the shape the spec gives, and the refusals the fixtures pin."""
from . import err, fake


@fake("accounts.list", "team.get", "hr.list", "schedules.list", "stats.get", "settings.get")
def lists(role, method, params):
    if method == "accounts.list":
        return "ok", {"accounts": []}
    if method == "team.get":
        return "ok", {"agents": [], "edges": []}
    if method == "hr.list":
        state = params.get("state")
        if state is not None and state not in ("open", "approved", "rejected"):
            return "fail", err(-32602, "bad_request", field="state")
        return "ok", {"proposals": []}
    if method == "schedules.list":
        return "ok", {"schedules": []}
    if method == "stats.get":
        if params.get("window") not in ("24h", "7d"):
            return "fail", err(-32602, "bad_request", field="window")
        return "ok", {"window": params["window"], "since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z", "tasks_done": 0,
                      "working_seconds": {}, "moves": 0, "handoffs": 0, "blocks": 0, "per_account": []}
    return "ok", {"timings": {}, "terminal_host": {"choice": "fake", "available": [], "health": "ok"},
                  "listener": {"enabled": False, "bind": "loopback", "encryption": "none", "fingerprint": None},
                  "trust_folders": [], "hooks": {"installed": False, "accounts": []},
                  "values": {"active_cap": 2, "park_timeout_s": 600}}
