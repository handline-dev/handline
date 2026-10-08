"""operation.get: no operation exists here."""
from . import err, fake


@fake("operation.get")
def operations(role, method, params):
    op = params.get("operation")
    if not isinstance(op, str) or not op:
        return "fail", err(-32602, "bad_request", field="operation")
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "operation"}}
