#!/usr/bin/env python3
"""Run the conformance fixtures in contract/fixtures against a daemon's Unix socket.

  python3 contract/conformance.py SOCKET [--as ROLE=SOCKET ...] [--var NAME=VALUE ...] [--only TEXT] [--allow-skip]
      [--allow-missing METHOD ...] [--allow-missing-file PATH] [--allow-needvar-file PATH] [FILE ...]

The format is in contract/README.md. Exit 0 when everything passed, 1 when a fixture failed or one was not run for want of
a method or a --var (unless its method is named by --allow-missing, repeatable, or in --allow-missing-file, one method per
line; a fixture named in --allow-needvar-file, one per line, may lack its --var, but a name there that passed or does
not exist is an error, so the list cannot rot), 2 on a usage error, 3 when nothing else is wrong but a role had no socket (--allow-skip says that is meant; it covers
nothing else). The same fixtures run against the Python daemon, the Go
one and the web UI's fake daemon: nothing here knows which it is talking to.
"""
from __future__ import annotations

import base64
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
            elif op == "$each":
                if not isinstance(got, list):
                    raise Mismatch(f"{at}: wanted an array, got {json.dumps(got)}")
                for i, item in enumerate(got):
                    matches(arg, item, saved, f"{at}[{i}]")
            elif op == "$regex":
                if not (isinstance(got, str) and re.search(arg, got)):
                    raise Mismatch(f"{at}: wanted /{arg}/, got {json.dumps(got)}")
            elif op in ("$above", "$next"):
                base = saved.get(arg)
                if not isinstance(base, int) or isinstance(base, bool):
                    raise Mismatch(f"{at}: {op} {arg} names no saved integer")
                if not (isinstance(got, int) and not isinstance(got, bool)) or (got <= base if op == "$above" else got != base + 1):
                    raise Mismatch(f"{at}: wanted {'above' if op == '$above' else 'exactly one above'} {base}, got {json.dumps(got)}")
            elif op == "$b64regex":
                try:
                    text = base64.b64decode(got, validate=True).decode("utf-8", "replace") if isinstance(got, str) else None
                except ValueError:
                    text = None
                if text is None or not re.search(arg, text):
                    raise Mismatch(f"{at}: wanted base64 of text matching /{arg}/, got {json.dumps(got)[:200]}")
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
        self.sent: set = set()  # ids sent and not yet answered
        self.sent_at: dict = {}  # id -> when it was sent
        self.early: dict = {}  # replies read while waiting for another id

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

    def reply_to(self, rid, timeout: float = TIMEOUT_S) -> dict | None:
        """Read until the reply with this id; lines without an id are notices and are kept. A reply for another id that was
        sent and not yet answered is kept for its own step (replies come in any order); any other id is a mismatch.
        `timeout` bounds the whole wait."""
        if rid in self.early:
            self.sent.discard(rid)
            return self.early.pop(rid)
        deadline = time.time() + timeout
        while True:
            msg = self.read_line(max(deadline - time.time(), 0.001))
            if msg is None:
                return None
            if isinstance(msg, dict) and "method" in msg and "id" not in msg:
                check_envelope(msg, True)
                self.notices.append(msg)
                continue
            check_envelope(msg, False)
            if msg.get("id") == rid:
                self.sent.discard(rid)
                return msg
            if msg.get("id") in self.sent:
                self.early[msg["id"]] = msg
                self.sent.discard(msg["id"])
                continue
            raise Mismatch(f"a reply for id {json.dumps(msg.get('id'))} while waiting for {json.dumps(rid)}")

    def next_notice(self, want: dict, saved: dict, wait: float) -> None:
        """The very next notice, not a later one, must match: no other may come before it."""
        deadline = time.time() + wait
        while not self.notices:
            left = deadline - time.time()
            if left <= 0:
                raise Mismatch(f"no notice came; wanted {json.dumps(want)}")
            try:
                msg = self.read_line(left)
            except socket.timeout:
                continue
            if msg is None:
                raise Mismatch("the connection closed while waiting for a notice")
            check_envelope(msg, True)
            self.notices.append(msg)
        got = self.notices.pop(0)
        matches(want, got, saved)

    def notice(self, want: dict, saved: dict, wait: float = NOTICE_WAIT_S) -> None:
        deadline = time.time() + wait
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
                raise Mismatch(f"no notice matched {json.dumps(want)}; saw {json.dumps(self.notices)[:600]}")
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


def names_used(node) -> set:
    """The {{name}}s a fixture's requests use."""
    if isinstance(node, str):
        return set(re.findall(r"\{\{(\w+)\}\}", node))
    if isinstance(node, dict):
        return set().union(*(names_used(v) for v in node.values()), set())
    if isinstance(node, list):
        return set().union(*(names_used(v) for v in node), set())
    return set()


def saved_names(node) -> set:
    """The names a fixture's own steps `$save`, which a later request may use without a --var."""
    if isinstance(node, dict):
        return ({node["$save"]} if isinstance(node.get("$save"), str) else set()).union(*(saved_names(v) for v in node.values()), set())
    if isinstance(node, list):
        return set().union(*(saved_names(v) for v in node), set())
    return set()


def spec_methods() -> set:
    return {m["name"] for m in json.loads((HERE / "protocol.json").read_text())["methods"]}


def listed_methods(target) -> set | None:
    """The methods a daemon lists in its hello (to a caller with no key: the whole list), None when it will not say."""
    try:
        conn = Connection(target)
    except OSError:
        return None
    try:
        conn.send_line(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "hello",
                                   "params": {"client": "conformance/probe", "protocol": 1}}))
        reply = conn.reply_to(0)
        return {m["name"] for m in reply["result"]["methods"]} if reply and "result" in reply else None
    except (Mismatch, socket.timeout, ConnectionError, KeyError, TypeError, json.JSONDecodeError):
        return None
    finally:
        conn.close()


def run_fixture(fx: dict, sockets: dict, vars_: dict, methods: set | None = None) -> tuple[str, str]:
    """("pass" | "fail" | "skip" | "needvar" | "missing", why). Each fixture gets a fresh connection on the socket of its
    `as` role. "skip" is a role with no socket; "needvar" a --var not given; "missing" a `spec` method the daemon does not
    list in hello (`methods`)."""
    role = fx.get("as", "founder")
    if role not in sockets:
        return "skip", f"no socket for role {role} (pass --as {role}=SOCKET)"
    missing = sorted(names_used(fx["steps"]) - set(vars_) - saved_names(fx["steps"]))
    if missing:
        return "needvar", f"needs --var {missing[0]}=..."
    if methods is not None and fx.get("spec") in spec_methods() and fx["spec"] not in methods:
        return "missing", "method not implemented by this daemon"
    for extra in fx.get("with", {}).values():
        if extra not in sockets:
            return "skip", f"no socket for role {extra} (pass --as {extra}=SOCKET)"
    saved = dict(vars_)
    conns = {}
    try:
        conns[None] = Connection(sockets[role])
        for name, extra in fx.get("with", {}).items():
            conns[name] = Connection(sockets[extra])
    except OSError as e:
        for c in conns.values():
            c.close()
        return "fail", f"cannot connect: {e}"
    try:
        for n, step in enumerate(fx["steps"], 1):
            try:
                conn = conns[step.get("on")]
                if "sleep_s" in step:
                    time.sleep(step["sleep_s"])
                if "raw" in step:
                    conn.send_line(step["raw"])
                    rid = None
                elif "await" in step:
                    rid = step["await"]
                elif "send" not in step:
                    rid = None
                else:
                    req = substitute(step["send"], saved)
                    conn.send_line(json.dumps(req))
                    rid = req.get("id")
                    conn.sent_at[rid] = time.time()
                    if "result" not in step and "error" not in step and rid is not None:
                        conn.sent.add(rid)
                if "closed" in step:
                    if conn.closed() != step["closed"]:
                        raise Mismatch(f"connection closed should be {step['closed']}")
                if "result" in step or "error" in step:
                    reply = conn.reply_to(rid, step.get("within_s", TIMEOUT_S))
                    if reply is None:
                        raise Mismatch("the connection closed without a reply")
                    took = time.time() - conn.sent_at[rid] if rid in conn.sent_at else None
                    if "at_least_s" in step and (took is None or took < step["at_least_s"]):
                        raise Mismatch(f"the reply came after {took or 0:.1f} s, wanted at least {step['at_least_s']} s")
                    if "result" in step:
                        if "result" not in reply:
                            raise Mismatch(f"wanted a result, got {json.dumps(reply)}")
                        matches(substitute(step["result"], saved), reply["result"], saved, "result")
                    else:
                        if "error" not in reply:
                            raise Mismatch(f"wanted an error, got {json.dumps(reply)}")
                        matches(substitute(step["error"], saved), reply["error"], saved, "error")
                if "notice_next" in step:
                    conn.next_notice(step["notice_next"], saved, step.get("notices_within_s", NOTICE_WAIT_S))
                for want in step.get("notices", []):
                    conn.notice(want, saved, step.get("notices_within_s", NOTICE_WAIT_S))
            except (Mismatch, socket.timeout, ConnectionError, json.JSONDecodeError) as e:
                return "fail", f"step {n}: {type(e).__name__ + ': ' if not isinstance(e, Mismatch) else ''}{e}"
        return "pass", ""
    finally:
        for c in conns.values():
            c.close()


def expand(node, method: str):
    """`node` with "@method" replaced by the method's name in every string."""
    if isinstance(node, str):
        return node.replace("@method", method)
    if isinstance(node, dict):
        return {k: expand(v, method) for k, v in node.items()}
    if isinstance(node, list):
        return [expand(v, method) for v in node]
    return node


def load(paths: list[Path]) -> list[dict]:
    """Fixtures from the files. A fixture with `for_each: "method"` has its steps repeated for every method of protocol.json
    (less `except`) and for one name it does not have, "@method" standing for the name, so a new method cannot escape it."""
    out = []
    for p in paths:
        for fx in json.loads(p.read_text())["fixtures"]:
            if fx.get("for_each") == "method":
                names = sorted(spec_methods() - set(fx.get("except", []))) + ["no.such_method"]
                lead = [s for s in fx["steps"] if not s.get("each")]
                each = [{k: v for k, v in s.items() if k != "each"} for s in fx["steps"] if s.get("each")]
                fx = {**fx, "steps": lead + [expand(s, n) for n in names for s in each]}
            out.append({**fx, "file": p.name})
    return out


def run_all(files: list[Path], sockets: dict, vars_: dict, only: str | None = None) -> list[tuple[str, str, str]]:
    """[(status, fixture name, why)] for every fixture in `files`."""
    listed = {}
    out = []
    for fx in load(files):
        if only and only not in fx["name"]:
            continue
        role = fx.get("as", "founder")
        if role in sockets and role not in listed and role not in ("unpaired", "member"):
            listed[role] = listed_methods(sockets[role])
        out.append((*run_fixture(fx, sockets, vars_, listed.get(role)), fx["name"]))
    return out


def read_methods(path: str) -> set:
    """One method per line; blank lines and #-comments are ignored."""
    lines = (ln.split("#", 1)[0].strip() for ln in Path(path).read_text().splitlines())
    return {ln for ln in lines if ln}


def main(argv: list[str], connectors: dict | None = None) -> int:
    """`connectors` (role -> function returning a connected socket) stands in for the socket paths: tests only."""
    sockets: dict = {}
    vars_: dict = {}
    only, files, rest, i, allow_skip, allow_missing, allow_needvar = None, [], [], 0, False, set(), set()
    while i < len(argv):
        a = argv[i]
        if a in ("--as", "--var") and i + 1 < len(argv) and "=" in argv[i + 1]:
            k, v = argv[i + 1].split("=", 1)
            (sockets if a == "--as" else vars_)[k] = v
            i += 2
        elif a == "--allow-skip":
            allow_skip, i = True, i + 1
        elif a == "--allow-missing" and i + 1 < len(argv):
            allow_missing.add(argv[i + 1])
            i += 2
        elif a == "--allow-missing-file" and i + 1 < len(argv):
            try:
                allow_missing |= read_methods(argv[i + 1])
            except OSError as e:
                print(f"cannot read {argv[i + 1]}: {e}", file=sys.stderr)
                return 2
            i += 2
        elif a == "--allow-needvar-file" and i + 1 < len(argv):
            try:
                allow_needvar |= read_methods(argv[i + 1])
            except OSError as e:
                print(f"cannot read {argv[i + 1]}: {e}", file=sys.stderr)
                return 2
            i += 2
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
    spec_of = {fx["name"]: fx.get("spec") for fx in load(files)}
    counts = {"pass": 0, "fail": 0, "skip": 0, "missing": 0, "needvar": 0}
    unallowed = 0
    stale = {n for n in allow_needvar if not only and n not in spec_of}
    for status, why, name in run_all(files, sockets, vars_, only):
        counts[status] += 1
        if name in allow_needvar and status != "needvar":
            stale.add(name)
        allowed = status == "needvar" and name in allow_needvar
        if status in ("missing", "needvar") and not allowed and spec_of[name] not in allow_missing:
            unallowed += 1
            why += f" (not in --allow-missing: {spec_of[name]})"
        print(f"{status.upper():4}  {name}" + (f"  - {why}" if why else ""))
    print(f"{counts['pass']} passed, {counts['fail']} failed, {counts['skip']} skipped by role, "
          f"{counts['missing']} not implemented, {counts['needvar']} needing a var ({unallowed} of those not allowed)")
    for name in sorted(stale):
        print(f"STALE {name}  - in --allow-needvar-file but it did not need a var (passed, failed, or no such fixture): delete the line")
    if counts["fail"] or unallowed or stale:
        return 1
    return 3 if counts["skip"] and not allow_skip else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
