#!/usr/bin/env python3
"""Run the conformance fixtures in contract/fixtures against a daemon's Unix socket.

  python3 contract/conformance.py SOCKET [--as ROLE=SOCKET ...] [--var NAME=VALUE ...] [--only TEXT] [--allow-skip] [FILE ...]

The format is in contract/README.md. Exit 0 when everything passed, 1 when a fixture failed, 2 on a usage error, 3 when
nothing failed but something was skipped (a run that skipped is not green; --allow-skip says it is meant). The same fixtures run against the Python daemon, the Go
one and the web UI's fake daemon: nothing here knows which it is talking to.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TIMEOUT_S = 5.0
NOTICE_WAIT_S = 1.0
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list, "null": type(None)}
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


class Mismatch(Exception):
    pass


def matches(want, got, saved: dict, at: str = "$") -> None:
    """Raise Mismatch unless `got` satisfies `want`. Objects match as subsets, arrays element by element."""
    if isinstance(want, dict) and any(k.startswith("$") for k in want):
        rest = {k: v for k, v in want.items() if not k.startswith("$")}
        if rest:
            matches(rest, got, saved, at)
            if want.get("$exact") and isinstance(got, dict) and set(got) - set(rest):
                raise Mismatch(f"{at}: unexpected keys {sorted(set(got) - set(rest))}")
        for op, arg in want.items():
            if not op.startswith("$"):
                continue
            if op == "$any":
                pass
            elif op == "$type":
                if arg == "timestamp":
                    ok = isinstance(got, str) and bool(TIMESTAMP.match(got))
                elif arg == "integer":
                    ok = isinstance(got, int) and not isinstance(got, bool)
                elif arg == "number":
                    ok = isinstance(got, (int, float)) and not isinstance(got, bool)
                else:
                    ok = isinstance(got, TYPES[arg]) and not (arg != "boolean" and isinstance(got, bool))
                if not ok:
                    raise Mismatch(f"{at}: wanted {arg}, got {json.dumps(got)}")
            elif op == "$oneOf":
                for alt in arg:
                    try:
                        matches(alt, got, dict(saved), at)
                        break
                    except Mismatch:
                        continue
                else:
                    raise Mismatch(f"{at}: wanted one of {json.dumps(arg)}, got {json.dumps(got)}")
            elif op == "$contains":
                if not isinstance(got, list):
                    raise Mismatch(f"{at}: wanted an array, got {json.dumps(got)}")
                for item in got:
                    try:
                        matches(arg, item, saved, at)
                        break
                    except Mismatch:
                        continue
                else:
                    raise Mismatch(f"{at}: no element matches {json.dumps(arg)}")
            elif op == "$regex":
                if not (isinstance(got, str) and re.search(arg, got)):
                    raise Mismatch(f"{at}: wanted /{arg}/, got {json.dumps(got)}")
            elif op == "$save":
                saved[arg] = got
            elif op in ("$absent", "$exact"):
                pass            # $absent is handled by the object case below, $exact above
            else:
                raise Mismatch(f"{at}: unknown matcher {op}")
        return
    if isinstance(want, dict):
        if not isinstance(got, dict):
            raise Mismatch(f"{at}: wanted an object, got {json.dumps(got)}")
        for k, w in want.items():
            if isinstance(w, dict) and w.get("$absent"):
                if k in got:
                    raise Mismatch(f"{at}.{k}: should be absent, got {json.dumps(got[k])}")
                continue
            if k not in got:
                raise Mismatch(f"{at}.{k}: missing")
            matches(w, got[k], saved, f"{at}.{k}")
        return
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            raise Mismatch(f"{at}: wanted {len(want)} elements, got {json.dumps(got)}")
        for i, (w, g) in enumerate(zip(want, got)):
            matches(w, g, saved, f"{at}[{i}]")
        return
    if want != got or type(want) is not type(got):
        raise Mismatch(f"{at}: wanted {json.dumps(want)}, got {json.dumps(got)}")


def substitute(node, vars_: dict):
    """Replace {{name}} in every string of a request with a --var or a saved value."""
    if isinstance(node, str):
        def one(m):
            if m.group(1) not in vars_:
                raise Mismatch(f"no value for {{{{{m.group(1)}}}}}: pass --var {m.group(1)}=...")
            return str(vars_[m.group(1)])
        whole = re.fullmatch(r"\{\{(\w+)\}\}", node)
        if whole and whole.group(1) in vars_ and not isinstance(vars_[whole.group(1)], str):
            return vars_[whole.group(1)]
        return re.sub(r"\{\{(\w+)\}\}", one, node)
    if isinstance(node, dict):
        return {k: substitute(v, vars_) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, vars_) for v in node]
    return node


def check_envelope(msg, notice: bool) -> None:
    """Every line the daemon sends is JSON-RPC 2.0: a reply has exactly one of result and error, a notice a method."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        raise Mismatch(f"not a JSON-RPC 2.0 message (jsonrpc must be \"2.0\"): {json.dumps(msg)}")
    if notice:
        if not isinstance(msg.get("method"), str):
            raise Mismatch(f"a notice needs a method: {json.dumps(msg)}")
    elif ("result" in msg) == ("error" in msg):
        raise Mismatch(f"a reply has exactly one of result and error: {json.dumps(msg)}")


class Connection:
    def __init__(self, target):
        """`target` is a socket path, or a function returning a connected socket (a test's in-process fake)."""
        if callable(target):
            self.sock = target()
        else:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(target)
        self.sock.settimeout(TIMEOUT_S)
        self.buf = b""
        self.notices: list[dict] = []

    def send_line(self, text: str) -> None:
        self.sock.sendall(text.encode() + b"\n")

    def read_line(self, timeout: float = TIMEOUT_S) -> dict | None:
        """The next JSON line, or None on a clean close; raises socket.timeout when nothing comes."""
        self.sock.settimeout(timeout)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def reply_to(self, rid) -> dict | None:
        """Read until the reply with this id; lines without an id are notices and are kept."""
        while True:
            msg = self.read_line()
            if msg is None:
                return None
            if isinstance(msg, dict) and "method" in msg and "id" not in msg:
                check_envelope(msg, True)
                self.notices.append(msg)
                continue
            check_envelope(msg, False)
            if msg.get("id") == rid:
                return msg
            raise Mismatch(f"a reply for id {json.dumps(msg.get('id'))} while waiting for {json.dumps(rid)}")

    def notice(self, want: dict, saved: dict) -> None:
        deadline = time.time() + NOTICE_WAIT_S
        while True:
            for i, n in enumerate(self.notices):
                try:
                    matches(want, n, saved)
                except Mismatch:
                    continue
                del self.notices[: i + 1]
                return
            left = deadline - time.time()
            if left <= 0:
                raise Mismatch(f"no notice matched {json.dumps(want)}; saw {json.dumps(self.notices)}")
            try:
                msg = self.read_line(left)
            except socket.timeout:
                continue
            if msg is None:
                raise Mismatch("the connection closed while waiting for a notice")
            check_envelope(msg, True)
            self.notices.append(msg)

    def closed(self) -> bool:
        try:
            return self.read_line(NOTICE_WAIT_S) is None
        except (socket.timeout, ConnectionError):
            return False

    def close(self) -> None:
        self.sock.close()


def run_fixture(fx: dict, sockets: dict, vars_: dict) -> tuple[str, str]:
    """("pass" | "fail" | "skip", why). Each fixture gets a fresh connection on the socket of its `as` role."""
    role = fx.get("as", "founder")
    if role not in sockets:
        return "skip", f"no socket for role {role} (pass --as {role}=SOCKET)"
    saved = dict(vars_)
    try:
        conn = Connection(sockets[role])
    except OSError as e:
        return "fail", f"cannot connect to {sockets[role]}: {e}"
    try:
        for n, step in enumerate(fx["steps"], 1):
            try:
                if "raw" in step:
                    conn.send_line(step["raw"])
                    rid = None
                else:
                    req = substitute(step["send"], saved)
                    conn.send_line(json.dumps(req))
                    rid = req.get("id")
                if "closed" in step:
                    if conn.closed() != step["closed"]:
                        raise Mismatch(f"connection closed should be {step['closed']}")
                if "result" in step or "error" in step:
                    reply = conn.reply_to(rid)
                    if reply is None:
                        raise Mismatch("the connection closed without a reply")
                    if "result" in step:
                        if "result" not in reply:
                            raise Mismatch(f"wanted a result, got {json.dumps(reply)}")
                        matches(step["result"], reply["result"], saved, "result")
                    else:
                        if "error" not in reply:
                            raise Mismatch(f"wanted an error, got {json.dumps(reply)}")
                        matches(step["error"], reply["error"], saved, "error")
                for want in step.get("notices", []):
                    conn.notice(want, saved)
            except (Mismatch, socket.timeout, ConnectionError, json.JSONDecodeError) as e:
                return "fail", f"step {n}: {type(e).__name__ + ': ' if not isinstance(e, Mismatch) else ''}{e}"
        return "pass", ""
    finally:
        conn.close()


def load(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        for fx in json.loads(p.read_text())["fixtures"]:
            out.append({**fx, "file": p.name})
    return out


def run_all(files: list[Path], sockets: dict, vars_: dict, only: str | None = None) -> list[tuple[str, str, str]]:
    """[(status, fixture name, why)] for every fixture in `files`."""
    return [(*run_fixture(fx, sockets, vars_), fx["name"]) for fx in load(files) if not only or only in fx["name"]]


def main(argv: list[str], connectors: dict | None = None) -> int:
    """`connectors` (role -> function returning a connected socket) stands in for the socket paths: tests only."""
    sockets: dict = {}
    vars_: dict = {}
    only, files, rest, i, allow_skip = None, [], [], 0, False
    while i < len(argv):
        a = argv[i]
        if a in ("--as", "--var") and i + 1 < len(argv) and "=" in argv[i + 1]:
            k, v = argv[i + 1].split("=", 1)
            (sockets if a == "--as" else vars_)[k] = v
            i += 2
        elif a == "--allow-skip":
            allow_skip, i = True, i + 1
        elif a == "--only" and i + 1 < len(argv):
            only, i = argv[i + 1], i + 2
        elif a.startswith("-"):
            print(__doc__, file=sys.stderr)
            return 2
        else:
            rest.append(a)
            i += 1
    sockets.update(connectors or {})
    if not rest and "founder" not in sockets:
        print(__doc__, file=sys.stderr)
        return 2
    if "founder" not in sockets:
        sockets["founder"], rest = rest[0], rest[1:]
    files = [Path(p) for p in rest] or sorted(FIXTURES.glob("*.json"))
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for status, why, name in run_all(files, sockets, vars_, only):
        counts[status] += 1
        print(f"{status.upper():4}  {name}" + (f"  - {why}" if why else ""))
    print(f"{counts['pass']} passed, {counts['fail']} failed, {counts['skip']} skipped")
    if counts["fail"]:
        return 1
    return 3 if counts["skip"] and not allow_skip else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
