"""transcript.events: the one structured agent this fake holds (a.transcript), with an event of each kind; a.term is a terminal agent."""
from . import err, fake

AGENT = "a.transcript"
TURN = "6f1c0a52-0000-4000-8000-000000000001"
_BODIES = [
    ("turn_start", {"turn_start": {"message": TURN, "from": "founder", "text": "run the tests"}}),
    ("text", {"text": "Running them."}),
    ("tool_use", {"tool_use": {"id": "toolu_1", "tool": "Bash", "input": {"command": "make test"}}}),
    ("permission", {"permission": {"tool_use_id": "toolu_1", "prompt": {"id": "toolu_1", "agent": AGENT, "kind": "permission", "tool": "Bash", "command": "make test",
                                                                        "reason": None, "lines": [], "options": [{"key": "allow", "label": "Allow"}, {"key": "deny", "label": "Deny"}],
                                                                        "since": "2026-10-08T21:00:02Z"}}}),
    ("permission_answer", {"permission_answer": {"tool_use_id": "toolu_1", "behavior": "allow", "by": "person", "rule": None, "message": None}}),
    ("tool_result", {"tool_result": {"tool_use_id": "toolu_1", "ok": True, "text": "ok", "truncated": False}}),
    ("error", {"error": {"code": "rate_limited", "text": "retrying"}}),
    ("turn_end", {"turn_end": {"reason": "done", "cost_usd": 0.02, "duration_ms": 4100, "usage": {"input": 900, "output": 120, "cache_read": 5000, "cache_write": 0}}}),
]
EVENTS = [{"agent": AGENT, "session": "s-1", "seq": i + 1, "turn": TURN, "at": "2026-10-08T21:00:%02dZ" % (i + 1), "kind": k, "item": 40 + i if k in ("text", "permission") else None, **b}
          for i, (k, b) in enumerate(_BODIES)]


def _int(v, lo, hi=None):
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo and (hi is None or v <= hi)


@fake("transcript.events")
def transcript(role, method, params):
    if not isinstance(params.get("agent"), str):
        return "fail", err(-32602, "bad_request", field="agent")
    if params.get("since_seq") is not None and params.get("before_seq") is not None:
        return "fail", err(-32602, "bad_request", field="since_seq")
    for key, lo in (("since_seq", 0), ("before_seq", 1)):
        if params.get(key) is not None and not _int(params[key], lo):
            return "fail", err(-32602, "bad_request", field=key)
    limit = params.get("limit", 200)
    if not _int(limit, 1, 500):
        return "fail", err(-32602, "bad_request", field="limit")
    if params["agent"] == "a.term":
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "unsupported", "reason": "a terminal agent has no transcript stream"}}
    if params["agent"] != AGENT:
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent"}}
    since, before = params.get("since_seq"), params.get("before_seq")
    if since is not None:
        page = [e for e in EVENTS if e["seq"] > since][:limit]
        more = len([e for e in EVENTS if e["seq"] > since]) > limit
    else:
        older = [e for e in EVENTS if before is None or e["seq"] < before]
        page, more = older[-limit:], len(older) > limit
    return "ok", {"events": page, "more": more, "first_seq": 1, "last_seq": len(EVENTS), "gap": False,
                  "working": {"turn_running": False, "line": None, "since": None}}
