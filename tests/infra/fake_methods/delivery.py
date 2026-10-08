"""Delivery to a structured agent (x-delivery): the receipt of chat.send, chat.send_now, chat.cancel and chat.messages for the three structured
agents this fake holds. a.transcript is idle, a.working has a turn running, a.crashed holds the operation `crashed-op`, lost after it was written.
The other methods call in here first and carry on when it answers None."""
import json
import uuid

from . import CURRENT, err

IDLE, WORKING, CRASHED = "a.transcript", "a.working", "a.crashed"
AT = "2026-10-09T00:00:00Z"
LANDING = {"queued": "queued", "sent": "typed", "unknown": "typed", "confirmed": "delivered"}


def _sig(params):
    return json.dumps({k: v for k, v in params.items() if k != "request_id"}, sort_keys=True)


def _ledger():
    st = CURRENT.ctx.state
    if "delivery" not in st:
        st["delivery"] = {"ops": {}, "next": 9000}
        _add(st["delivery"], "crashed-op", {"agent": CRASHED, "body": "in flight when it died"}, "unknown")
    return st["delivery"]


def _add(led, rid, params, state):
    led["next"] += 1
    op = {"rid": rid, "sig": _sig(params), "id": led["next"], "agent": params["agent"], "body": params["body"], "state": state, "gone": False,
          "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "handline-op:" + rid))}
    led["ops"][rid] = op
    return op


def _receipt(op):
    return {"state": op["state"], "uuid": op["uuid"]}


def _ok(said, **more):
    return "ok", {"accepted": True, "said": said, **more}


def _refused(gate, reason):
    return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": gate, "reason": reason}}


def _by_id(led, request):
    return next((o for o in led["ops"].values() if o["id"] == request), None)


def send(params):
    agent = params["agent"]
    if agent == "a.term":
        return _ok("Sent.", request=1)
    if agent not in (IDLE, WORKING, CRASHED):
        return None
    led, rid = _ledger(), params["request_id"]
    op = led["ops"].get(rid)
    if op:
        if op["sig"] != _sig(params):
            return "fail", err(-32600, "bad_request", field="request_id")
        if op["gone"]:
            return _refused("state", "that message was withdrawn")
        if op["state"] == "sent":       # the CLI's echo arrives between the send and its retry
            op["state"] = "confirmed"
        return _ok("Sent.", replayed=True, request=op["id"], receipt=_receipt(op))
    queued = agent == WORKING and params.get("delivery", "queue") == "queue"
    op = _add(led, rid, params, "queued" if queued else "sent")
    return _ok("Queued." if queued else "Sent.", request=op["id"], receipt=_receipt(op))


def send_now(params):
    op = _by_id(_ledger(), params["request"])
    if not op or op["gone"]:
        return None
    if op["state"] != "queued":
        return _refused("state", "that message is not waiting")
    op["state"] = "sent"
    return _ok("Written.", receipt=_receipt(op))


def cancel(params):
    op = _by_id(_ledger(), params["request"])
    if not op or op["gone"]:
        return None
    if op["state"] != "queued" and params.get("confirm") is not True:
        return _refused("confirm", "the agent already has this message; cancelling tells it to stop")
    op["gone"] = True
    return _ok("Taken back.")


def messages(params):
    if params["agent"] not in (IDLE, WORKING, CRASHED):
        return None
    ops = sorted((o for o in _ledger()["ops"].values() if o["agent"] == params["agent"] and not o["gone"]), key=lambda o: -o["id"])
    written = lambda o: None if o["state"] == "queued" else AT
    return "ok", {"more": False, "messages": [
        {"id": o["id"], "at": AT, "state_at": AT, "from": "founder", "to": o["agent"], "state": LANDING[o["state"]], "landing": LANDING[o["state"]],
         "sending_reason": "lost after it was written; it is not written again" if o["state"] == "unknown" else None, "body": o["body"], "answer": None,
         "dismissed": False, "delivered_at": AT if o["state"] == "confirmed" else None, "typed_at": written(o), "landed_at": AT if o["state"] == "confirmed" else None,
         "parent": None, "kind": "message", "tasks": [], "is_task_question": False, "receipt": _receipt(o)} for o in ops]}
