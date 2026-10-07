"""device.pair, device.revoke and devices.list for the fake daemon's network mode (device.redeem is `FakeNetwork`'s: it needs the connection)."""
import time

from . import err, fake

WORLD: dict = {}


def reset() -> None:
    WORLD.clear()
    WORLD.update(codes={}, devices={}, socks={}, subs=[], n=0)


reset()


def emit(typ, data):
    """A devices event to every founder connection subscribed to the topic (the live-only stream, cursor 0)."""
    for push in WORLD["subs"]:
        push({"jsonrpc": "2.0", "method": "event", "params": {"cursor": 0, "topic": "devices", "type": typ, "at": "2026-10-06T00:00:00Z",
                                                            "actor": None, "agent": None, "title": None, "target": None, "data": data}})


def device_of(token):
    return next((d for d in WORLD["devices"].values() if d["token"] == token), None)


@fake("device.pair", "device.revoke", "devices.list")
def devices(role, method, params):
    if not isinstance(params.get("request_id", "x"), str):
        return "fail", err(-32600, "bad_request", field="request_id")
    if method == "device.pair":
        WORLD["n"] += 1
        code = f"{WORLD['n']:06d}-code"
        ttl = params.get("ttl_s", 300)
        WORLD["codes"][code] = time.time() + ttl
        return "ok", {"accepted": True, "code": code, "expires_at": "2026-10-06T00:00:00Z", "fingerprint": "sha256:00", "uri": f"handline://pair?code={code}"}
    if method == "device.revoke":
        dev = WORLD["devices"].pop(params.get("device"), None)
        if dev is None:
            return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "device"}}
        emit("device.revoked", {"device": dev["id"]})
        for s in WORLD["socks"].pop(dev["id"], []):
            try:
                s.shutdown(2)
                s.close()
            except OSError:
                pass
        return "ok", {"accepted": True}
    return "ok", {"devices": [{"id": d["id"], "name": d["name"], "role": "member", "created": "2026-10-06T00:00:00Z", "last_seen": None,
                               "connected": bool(WORLD["socks"].get(d["id"]))} for d in WORLD["devices"].values()]}
