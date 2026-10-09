"""account.set, account.login, account.remove, hr.decide, schedule.add, schedule.edit, schedule.remove and settings.set."""
from . import err, fake

ACCOUNTS, AGENTS = ("acct.one",), ("a.sched",)
SETTINGS = {"input_hold_idle_s", "dev_mode"}
SCHEDULES: dict[int, tuple[str, str]] = {}      # id -> (cron, message), the one the add-edit-remove fixture makes


def _refused(gate="state"):
    return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": gate}}


def _not_found(what):
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": what}}


def _bad(field):
    return "fail", err(-32602, "bad_request", field=field)


@fake("account.set", "account.login", "account.login_cancel", "account.login_code", "account.remove", "hr.decide", "schedule.add", "schedule.edit", "schedule.remove", "settings.set")
def admin(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    ok = {"accepted": True, "said": "done"}
    if method in ("account.set", "account.login", "account.remove"):
        if params.get("account") not in ACCOUNTS:
            return _not_found("account")
        if method == "account.set" and "label" in params and (not isinstance(params["label"], str) or "\n" in params["label"]):
            return _bad("label")
        if method == "account.set" and isinstance(params.get("cycle_end"), str) and params["cycle_end"] == "2026-13-45":
            return _bad("cycle_end")
        if method == "account.login":
            return "ok", {**ok, "login_id": "l-1", "state": "waiting", "url": "https://claude.com/cai/oauth/authorize?fake"}
        if method == "account.remove":
            if params.get("dry_run") is True:
                return "ok", {**ok, "would_remove": True, "plan": ["would remove account"]}
            return _refused() if params.get("confirm") == params["account"] else _bad("confirm")
        return "ok", ok
    if method in ("account.login_cancel", "account.login_code"):
        return _not_found("login")
    if method == "hr.decide":
        if params.get("decision") not in ("approve", "reject"):
            return _bad("decision")
        from . import hr
        return hr.decide(params) or _not_found("proposal")
    if method == "schedule.add":
        if params.get("target") not in AGENTS:
            return _not_found("agent")
        if params.get("cron") == "not a cron":
            return _bad("cron")
        key = (params["cron"], params["message"])
        if key in SCHEDULES.values():
            return _refused()
        sid = len(SCHEDULES) * 2 + 7
        SCHEDULES[sid] = key
        return "ok", {**ok, "schedule": sid}
    if method in ("schedule.edit", "schedule.remove"):
        if params.get("schedule") not in SCHEDULES:
            return _not_found("schedule")
        if method == "schedule.remove":
            del SCHEDULES[params["schedule"]]
            return "ok", ok
        _, message = SCHEDULES.pop(params["schedule"])
        sid = params["schedule"] + 1
        SCHEDULES[sid] = (params.get("cron") or "", message)
        return "ok", {**ok, "schedule": sid}
    changes = params.get("changes")         # settings.set
    for key, v in (changes or {}).items():
        if key not in SETTINGS or (params.get("agent") is not None):
            return _bad("changes")
        if v is not None and key == "dev_mode" and not isinstance(v, bool):
            return _bad(key)
    return "ok", ok
