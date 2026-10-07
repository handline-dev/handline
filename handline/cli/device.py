"""`hl device pair | list | revoke`: the founder's own commands for the phones and tablets paired to the gateway.

They talk to the gateway's local socket (`<home>/gateway.sock`, never the daemon's), as the founder: the founder key goes in `hello`, which
only the founder's own terminal can read. An agent's session has no key, so the gateway refuses it and nothing is made or revoked."""
from __future__ import annotations

import socket
import sys
import uuid
from urllib.parse import parse_qs, urlparse

from ..daemon import client
from ..infra import paths, sessiontoken, textsafe


def _gateway() -> socket.socket | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(client.TIMEOUT_S)
    try:
        s.connect(str(paths.gateway_socket_file()))
    except OSError:
        s.close()
        return None
    return s


def _call(method: str, params: dict | None = None, sock: socket.socket | None = None):
    """The gateway's answer, or None after saying why there is none."""
    key = sessiontoken.founder_key_held()
    if not key:
        print("hl device is the founder's: run it from your own terminal (this session cannot read the founder key)", file=sys.stderr)
        return None
    sock = sock or _gateway()
    if sock is None:
        print(f"no gateway is running (nothing listens on {paths.gateway_socket_file()}): start it with `handlined -gateway ...`, see go/README.md", file=sys.stderr)
        return None
    try:
        return client.call(sock, method, params, client_name="hl-device", key=key)
    except client.RpcError as e:
        gate = e.data.get("gate")
        print(f"{method}: {e}" + (f" (gate {gate})" if gate else ""), file=sys.stderr)
    except OSError as e:
        print(f"{method}: the gateway did not answer: {e}", file=sys.stderr)
    return None


def _line(text) -> str:
    return textsafe.oneline(str(text))


def cmd_device(args, sock: socket.socket | None = None) -> int:
    if args.device_cmd == "pair":
        params = {"request_id": uuid.uuid4().hex, **({"name": args.name} if args.name else {})}
        got = _call("device.pair", params, sock)
        if got is None:
            return 1
        uri = _line(got.get("uri", ""))
        query = parse_qs(urlparse(uri).query)
        print(f"code: {_line(got.get('code', ''))}   (one time, expires {_line(got.get('expires_at', ''))})")
        print(f"uri: {uri}")
        if query.get("host"):
            print(f"address: {_line(query['host'][0])}")
        fp = _line(got.get("fingerprint") or "")
        if fp:
            print(f"fingerprint: {fp}")
        else:
            print("fingerprint: none (the gateway runs plain on the Tailscale address; the tailnet encrypts it)")
        return 0
    if args.device_cmd == "list":
        got = _call("devices.list", None, sock)
        if got is None:
            return 1
        devices = got.get("devices", [])
        if not devices:
            print("no device is paired")
            return 0
        width = max(len(_line(d.get("id", ""))) for d in devices)
        for d in devices:
            print(f"{_line(d.get('id', '')):<{width}}  {_line(d.get('name', '')):<16}  "
                  f"{'connected' if d.get('connected') else 'offline':<9}  last seen {_line(d.get('last_seen') or 'never')}")
        return 0
    got = _call("device.revoke", {"device": args.device, "request_id": uuid.uuid4().hex}, sock)
    if got is None:
        return 1
    print(f"device {args.device} revoked; its connections are closed")
    return 0
