"""agent.strip and chat.refs: no agent is known to the fake, so a name is not_found and a missing one bad_request."""
from . import err, fake


@fake("agent.strip", "chat.refs")
def ui_reads(role, method, params):
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        return "fail", err(-32602, "bad_request", field="agent")
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "agent", "name": name}}
