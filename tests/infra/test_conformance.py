"""Tests: the conformance harness (contract/conformance.py) runs the fixtures in contract/fixtures against a fake server.

Run on its own: python3 tests/infra/test_conformance.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contract"))
import conformance  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_methods import CURRENT, HANDLERS  # noqa: E402

STATE_WORDS = ("running", "idle", "queued", "parked", "waiting", "stalled", "no_terminal")
SPEC = json.loads((ROOT / "contract" / "protocol.json").read_text())
METHODS = {m["name"]: m for m in SPEC["methods"]}
RANK = {r: i for i, r in enumerate(SPEC["x-roles"]["order"])}


def _err(code, message, **data):
    return {"code": code, "message": message, "data": {"code": message, **data}}


def _method_of(line: bytes):
    try:
        req = json.loads(line)
    except ValueError:
        return None
    return req.get("method") if isinstance(req, dict) else None


class FakeDaemon:
    """Just enough of the protocol to satisfy the first fixtures: hello, whoami, agents.list, agent.get."""

    def __init__(self, role: str, mutate=None, lacks=(), broken=(), serial=False, unread=False, upper_case=False):
        """`lacks`: methods this daemon does not have (not listed, -32601). `broken`: listed, yet answered -32601.
        `serial`: serve a connection's calls one at a time, so a waiting inbox.list holds the ones behind it.
        `upper_case`: send the state words in upper case, as the Python daemon did before the vocabulary was fixed.
        `unread`: the inbox holds an unread item (id 5), so an inbox.list that its filters do not exclude returns at once."""
        self.role, self.mutate, self.lacks, self.broken = role, mutate or (lambda reply: reply), set(lacks), set(broken)
        self.serial, self.unread, self.upper_case = serial, unread, upper_case
        self.state: dict = {}

    def connect(self) -> socket.socket:
        """A client socket to a new connection of this fake (no bind: a sandboxed run may not make a socket file)."""
        client, server = socket.socketpair()
        threading.Thread(target=self._serve, args=(server,), daemon=True).start()
        return client

    def listen(self, path: str) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(8)
        def accept():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()

    def _serve(self, conn):
        said_hello, buf, out = False, b"", threading.Lock()
        ctx = types.SimpleNamespace(id=object(), state=self.state, after=[], on_close=[])

        def send(reply):
            after = reply.pop("_notices", [])
            with out:
                try:
                    conn.sendall(json.dumps(self.mutate(reply)).encode() + b"\n")
                    for notice in after:
                        conn.sendall(json.dumps(notice).encode() + b"\n")
                except OSError:     # the client closed first
                    pass

        ctx.send = send

        def answer_later(line):
            reply, _ = self._answer(line, True)
            send(reply)
        with conn:
            try:
                self._loop(conn, ctx, send, answer_later, said_hello, buf)
            finally:
                for done in ctx.on_close:
                    done()

    def _loop(self, conn, ctx, send, answer_later, said_hello, buf):
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if said_hello and not self.serial and _method_of(line) == "inbox.list":
                    threading.Thread(target=answer_later, args=(line,), daemon=True).start()
                    continue
                CURRENT.ctx = ctx
                reply, said_hello = self._answer(line, said_hello)
                send(reply)
                for after in ctx.after:     # what may only start once the reply is on the wire: a watcher's notices
                    after()
                ctx.after.clear()

    def state_word(self, word: str) -> str:
        return word.upper() if self.upper_case else word

    def _view(self, name: str) -> dict:
        return {"name": name, "role": "", "state": self.state_word(name[4:]), "state_detail": ""}

    def _inbox(self) -> list:
        return [{"id": 5, "kind": "say", "read": False}] if self.unread else []

    def _methods(self) -> list:
        return [{"name": n, "since": 1, "role": m["x-role"], "allowed": RANK[m["x-role"]] <= RANK[self.role]}
                for n, m in METHODS.items() if n not in self.lacks]

    def _answer(self, line: bytes, said_hello: bool):
        try:
            req = json.loads(line)
        except ValueError:
            return {"jsonrpc": "2.0", "id": None, "error": _err(-32700, "bad_request")}, said_hello
        if not isinstance(req, dict):
            return {"jsonrpc": "2.0", "id": None, "error": _err(-32600, "bad_request")}, said_hello
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        def fail(e): return {"jsonrpc": "2.0", "id": rid, "error": e}
        def ok(r): return {"jsonrpc": "2.0", "id": rid, "result": r}
        if method == "hello":
            if "protocol" not in params:
                return fail(_err(-32602, "bad_request", field="protocol")), said_hello
            if params["protocol"] < 1:
                return fail({"code": -32002, "message": "refused", "data": {
                    "code": "refused", "gate": "protocol", "reason": "too old", "hint": "hl service install"}}), said_hello
            kind = {"founder": "person", "agent": "agent", "member": "device", "anonymous": "anonymous"}[self.role]
            return ok({"protocol": 1, "min_client": 1, "daemon": "fake", "schema": 1, "role": self.role,
                       "caller": {"kind": kind, "name": "x"}, "transport": "local", "methods": self._methods(),
                       "capabilities": {"chat": True, "review": False, "input_ask": False, "terminal_format": ["text", "bytes"],
                                        "terminal_hosts": {"tmux": ["text", "bytes"], "orca": ["text"]}}}), True
        if method == "batch" or not said_hello:
            return fail(_err(-32600, "bad_request")), said_hello
        if method in self.lacks or method in self.broken:
            return fail({"code": -32601, "message": "no such method", "data": {"code": "bad_request", "method": method}}), said_hello
        m = METHODS.get(method)
        if m is None or RANK[m["x-role"]] > RANK[self.role]:
            return fail({"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "role", "reason": "role"}}), said_hello
        if method == "whoami":
            return ok({"caller": {"kind": "person" if self.role == "founder" else self.role, "name": "x"}, "role": self.role,
                       "methods": self._methods()}), said_hello
        handler = HANDLERS.get(method)
        if handler is not None:
            result = handler(self.role, method, params)
            if result is not None:      # a handler that has nothing to say about the call (None) leaves it to the daemon's own branches below
                kind, payload, *notices = result        # a third item is the notices that follow the reply
                reply = (ok if kind == "ok" else fail)(payload)
                if notices:
                    reply["_notices"] = notices[0]
                return reply, said_hello
        if method == "session.stop" and params.get("mode") == "kill" and self.role == "agent":
            return fail({"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "role", "reason": "kill is for a person"}}), said_hello
        if method == "inbox.list":
            items = [i for i in self._inbox() if (not params.get("unread") or not i["read"]) and params.get("kind", i["kind"]) == i["kind"]
                     and (params.get("before") is None or i["id"] < params["before"])]
            if items:
                return ok({"items": items, "more": False}), said_hello
            time.sleep(min(params.get("wait_s", 0), 3))
            return ok({"items": [], "more": False}), said_hello
        if method == "agent.get":
            if "agent" not in params:
                return fail(_err(-32602, "bad_request", field="agent")), said_hello
            if str(params["agent"]).startswith("a.s."):
                return ok(self._view(params["agent"])), said_hello
            return fail({"code": -32001, "message": "not_found", "data": params["agent"]}), said_hello
        if method == "agents.list":
            return ok({"agents": [{"name": "a.one", "role": "x" * 160, "state": self.state_word("idle"), "state_detail": "3m"},
                                  *(self._view(f"a.s.{w}") for w in STATE_WORDS)]}), said_hello
        if method == "agent.timeline":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                return fail(_err(-32602, "bad_request", field="limit")), said_hello
            return fail({"code": -32001, "message": "not_found", "data": params.get("agent")}), said_hello
        if method in ("subscribe", "unsubscribe"):
            topics = params.get("topics")
            if not isinstance(topics, list) or not topics or not set(topics) <= set(SPEC["components"]["schemas"]["Topic"]["enum"]):
                return fail(_err(-32602, "bad_request", field="topics")), said_hello
            return ok({"cursor": 7} if method == "subscribe" else {}), said_hello
        if method == "events.feed":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                return fail(_err(-32602, "bad_request", field="limit")), said_hello
            return ok({"events": [], "cursor": 7, "gap": False}), said_hello
        return fail(_err(-32600, "bad_request")), said_hello


class FakeNetwork:
    """The network transport as the harness can show it: a connection that is unpaired until its hello carries a token device.redeem
    gave, refused (gate unpaired, nothing else in the reply) at everything but device.redeem, and closed when its device is revoked.
    It has no TLS and no pinned fingerprint: those belong to the Go listener's own tests."""

    def __init__(self):
        self.member = FakeDaemon("member")

    def connect(self) -> socket.socket:
        client, server = socket.socketpair()
        threading.Thread(target=self._serve, args=(server,), daemon=True).start()
        return client

    def _serve(self, conn):
        from fake_methods import devices as dev
        paired, buf = None, b""
        def send(reply):
            try:
                conn.sendall(json.dumps(reply).encode() + b"\n")
            except OSError:
                pass
        with conn:
            while True:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        req = json.loads(line)
                    except ValueError:
                        req = None
                    if not isinstance(req, dict):
                        send(self.member._answer(line, False)[0])
                        continue
                    rid, params = req.get("id"), req.get("params") or {}
                    def refuse():
                        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "refused", "data": {
                            "code": "refused", "gate": "unpaired", "reason": "not paired"}}})
                    if paired and not any(d["id"] == paired for d in dev.WORLD["devices"].values()):
                        return              # revoked while it was open
                    if req.get("method") == "device.redeem":
                        until = dev.WORLD["codes"].pop(params.get("code"), None)
                        if until is None or until < time.time():
                            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "refused", "data": {
                                "code": "refused", "gate": "pairing_code", "reason": "that code is not valid"}}})
                            continue
                        d = {"id": f"d{len(dev.WORLD['devices']) + 1}", "name": params.get("name", ""), "token": f"tok-{len(dev.WORLD['devices']) + 1}"}
                        dev.WORLD["devices"][d["id"]] = d
                        send({"jsonrpc": "2.0", "id": rid, "result": {"accepted": True, "token": d["token"], "device": d["id"]}})
                        continue
                    if req.get("method") == "hello" and not paired:
                        d = dev.device_of(params.get("token"))
                        if d is None:
                            refuse()
                            continue
                        paired = d["id"]
                        dev.WORLD["socks"].setdefault(paired, []).append(conn)
                        reply, _ = self.member._answer(line, False)
                        reply["result"]["caller"]["device"] = paired
                        send(reply)
                        continue
                    if not paired:
                        refuse()
                        continue
                    reply, _ = self.member._answer(line, True)
                    send(reply)


FILES = sorted((ROOT / "contract" / "fixtures").glob("*.json"))
NOT_YET = "NOT_YET_IN_PYTHON"      # a fixture whose `about` starts with this is pinned ahead of its implementation: the Python
                                   # daemon may fail it (stale once it passes) and the fake runs below leave it out


def _without_pending() -> list[Path]:
    """FILES with the NOT_YET_IN_PYTHON fixtures cut out, for the runs against the fake."""
    out = Path(tempfile.mkdtemp(prefix="conformance-"))
    for p in FILES:
        doc = json.loads(p.read_text())
        doc["fixtures"] = [f for f in doc["fixtures"] if not f["about"].startswith(NOT_YET)]
        (out / p.name).write_text(json.dumps(doc))
    return sorted(out.glob("*.json"))


READY = _without_pending()


def _fakes(*roles: str, mutate=None, **kw) -> dict:
    from fake_methods import devices
    devices.reset()
    return {r: (FakeNetwork() if r == "unpaired" else FakeDaemon(r, mutate, **kw)).connect for r in roles}


KEY = {"founder_key": "k", **{f"state_{w}": f"a.s.{w}" for w in STATE_WORDS}, "running_agent": "a.running", "locked_agent": "a.locked", "terminal_agent": "a.term", "bytes_agent": "a.bytes", "text_only_agent": "a.orca", "terminal_handle": "t-a.term",
       "held_agent": "a.held", "walled_agent": "a.walled", "dialog_agent": "a.dialog", "shell_agent": "a.shell", "account": "acct.one", "schedule_agent": "a.sched", "new_agent": "a.fresh", "repo_dir": "/tmp", "hr_seeded": "1"}
KEYARG = ["--var", "founder_key=k", *(x for w in STATE_WORDS for x in ("--var", f"state_{w}=a.s.{w}")), "--var", "running_agent=a.running", "--var", "locked_agent=a.locked", "--var", "terminal_agent=a.term", "--var", "bytes_agent=a.bytes", "--var", "text_only_agent=a.orca", "--var", "terminal_handle=t-a.term",
          "--var", "held_agent=a.held", "--var", "walled_agent=a.walled", "--var", "dialog_agent=a.dialog", "--var", "shell_agent=a.shell", "--var", "account=acct.one",
          "--var", "schedule_agent=a.sched", "--var", "new_agent=a.fresh", "--var", "repo_dir=/tmp", "--var", "hr_seeded=1"]


def _count(results, status: str) -> int:
    return sum(1 for s, _, _ in results if s == status)


def test_the_fixtures_pass_against_a_fake_daemon() -> None:
    results = conformance.run_all(READY, _fakes("founder", "agent", "anonymous", "unpaired"), KEY)
    bad = [(n, why) for s, why, n in results if s != "pass"]
    check("conformance: every fixture passes against the fake daemon", not bad and results, str(bad))
    check("conformance: each fixture is reported by name", {"hello.ok", "errors.not_found"} <= {n for _, _, n in results})


def test_a_role_without_a_socket_is_skipped_not_passed() -> None:
    results = conformance.run_all(READY, _fakes("founder"), KEY)
    skipped = {n for s, _, n in results if s == "skip"}
    check("conformance: fixtures for roles with no socket are skipped", {"whoami.agent", "errors.refused_by_role"} <= skipped and not _count(results, "fail"), str(results))


def test_a_wrong_reply_fails_that_fixture(td) -> None:
    path = Path(td) / "bad.json"
    path.write_text(json.dumps({"fixtures": [{"name": "bad.case", "as": "founder", "steps": [
        {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "t", "protocol": 1}}, "result": {"role": "agent"}}]}]}))
    [(status, why, name)] = conformance.run_all([path], _fakes("founder"), {})
    check("conformance: a reply that differs fails, naming the path and both values",
          status == "fail" and "result.role" in why and '"agent"' in why and '"founder"' in why, why)
    path.write_text(json.dumps({"fixtures": [{"name": "bad.close", "as": "founder", "steps": [
        {"raw": "{", "error": {"code": -32700}, "closed": True}]}]}))
    [(status, why, _)] = conformance.run_all([path], _fakes("founder"), {})
    check("conformance: a connection that stays open fails a closed:true step", status == "fail", why)


def test_the_command_line_runs_against_a_socket_path(td) -> None:
    try:     # a unix socket path is short, so not under td
        path = os.path.join(tempfile.mkdtemp(prefix="cf", dir="/tmp"), "s")
        FakeDaemon("founder").listen(path)
    except PermissionError:
        print("note: this run may not bind a socket; the command line check was not run")
        return
    cli = [sys.executable, str(ROOT / "contract" / "conformance.py")]
    r = subprocess.run([*cli, path, *KEYARG, "--only", "hello"], capture_output=True, text=True)
    check("conformance: the command line reports per fixture and exits 0", r.returncode == 0 and "PASS  hello.ok" in r.stdout and "0 failed" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([*cli, path, "--only", "hello"], capture_output=True, text=True)
    check("conformance: the same command line without the founder key exits 1 and says it needs a var",
          r.returncode == 1 and "NEEDVAR  hello.ok" in r.stdout and "needing a var" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([sys.executable, str(ROOT / "contract" / "conformance.py")], capture_output=True, text=True)
    check("conformance: no socket is a usage error", r.returncode == 2)


def test_the_command_line_arguments_over_socketpairs() -> None:
    """What the socket-path test above checks, through conformance.main with the same arguments, for a run that may not bind."""
    founder = _fakes("founder")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        with_key = conformance.main([*KEYARG, "--only", "hello"], founder)
    check("conformance: the command line arguments with the founder key report per fixture and exit 0",
          with_key == 0 and "PASS  hello.ok" in out.getvalue() and "0 failed" in out.getvalue(), out.getvalue())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        without = conformance.main(["--only", "hello"], founder)
    check("conformance: the same arguments without the key exit 1 and say they need a var",
          without == 1 and "NEEDVAR  hello.ok" in out.getvalue() and "needing a var" in out.getvalue(), out.getvalue())


def _first_fixture_fails(mutate, needle: str, name: str) -> None:
    [(status, why, _)] = conformance.run_all(FILES, _fakes("founder", mutate=mutate), KEY, "hello.ok")
    check(name, status == "fail" and needle in why, f"{status}: {why}")


def test_skips_for_a_missing_var_and_a_method_the_daemon_lacks() -> None:
    [(status, why, _)] = conformance.run_all(FILES, _fakes("founder"), {}, "hello.ok")
    check("conformance: a fixture needing a --var is reported needvar without it", status == "needvar" and "founder_key" in why, why)
    results = conformance.run_all(READY, _fakes("founder", "anonymous", lacks={"agent.get"}), KEY)
    by = {n: (s, why) for s, why, n in results}
    check("conformance: a fixture whose method the daemon does not list is reported missing, with the reason",
          by["errors.not_found"] == ("missing", "method not implemented by this daemon")
          and by["errors.invalid_params"][0] == "missing" and by["hello.ok"][0] == "pass", str(by))
    check("conformance: nothing else is hidden by it", not [n for n, (s, _) in by.items() if s == "fail"], str(by))
    results = conformance.run_all(FILES, _fakes("founder", broken={"agent.get"}), KEY, "errors.not_found")
    check("conformance: a method the daemon lists and then answers -32601 for fails",
          results[0][0] == "fail" and "-32001" in results[0][1], str(results))


def test_the_fixtures_run_against_the_python_daemon(home) -> None:
    from handline.daemon import socket_server
    from handline.infra import sessiontoken
    from handline.session import term
    use_home(home)
    term.live_terminals = lambda: None      # daemon.status asks the host; a test reaches no real one
    sessiontoken.ensure_founder_key()
    key = sessiontoken.founder_key_held()

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    results = conformance.run_all(FILES, {"founder": connect, "anonymous": connect}, {"founder_key": key})
    by = {n: (s, why) for s, why, n in results}
    # A fixture whose `about` starts NOT_YET_IN_PYTHON pins what the spec added and the Python daemon has not got: it may fail
    # (or be missing, for a new method) here, and is a stale marker once it passes.
    not_yet = {f["name"] for p in FILES for f in json.loads(p.read_text())["fixtures"] if f["about"].startswith(NOT_YET)}
    failed = {n: why for n, (s, why) in by.items() if s == "fail" and n not in not_yet}
    check("conformance: no fixture fails against the Python daemon", not failed, str(failed))
    stale = sorted(n for n in not_yet if by.get(n, ("",))[0] == "pass")
    check("conformance: a NOT_YET_IN_PYTHON fixture that passes has its marker removed", not stale, str(stale))
    check("conformance: the Python daemon passes the hello, whoami and error fixtures it can",
          all(by[n][0] == "pass" for n in ("hello.ok", "hello.must_come_first", "whoami.founder", "errors.parse_error")), str(by))
    check("conformance: the terminal channel it now has reports the text format, and passes", by["terminal.format_is_text"][0] == "pass", str(by))
    print("python daemon:", ", ".join(f"{s.upper()} {n}" for n, (s, _) in sorted(by.items())))


def test_a_waiting_call_that_holds_the_connection_fails_the_framing_fixture() -> None:
    names = ("framing.waiting_call_does_not_block", "framing.waiting_call_filters_hold", "framing.replies_matched_by_id")
    results = conformance.run_all(FILES, {"founder": FakeDaemon("founder").connect}, KEY, "framing.")
    check("conformance: a daemon that serves calls concurrently passes the pipelined fixtures",
          [(s, n) for s, _, n in results] == [("pass", n) for n in names], str(results))
    results = conformance.run_all(FILES, {"founder": FakeDaemon("founder", serial=True).connect}, KEY, "framing.waiting")
    check("conformance: a daemon that answers a waiting call first fails the pipelined fixture",
          results[0][0] == "fail" and "timed out" in results[0][1], str(results))
    for serial in (True, False):
        results = conformance.run_all(FILES, {"founder": FakeDaemon("founder", serial=serial, unread=True).connect}, KEY, "framing.waiting")
        check(f"conformance: unread items in the inbox do not let a {'serial' if serial else 'concurrent'} daemon pass the waiting fixtures",
              [s for s, _, _ in results] == ["pass", "pass"] if not serial else all(s == "fail" for s, _, _ in results) and "timed out" in results[0][1], str(results))
    old = json.loads((ROOT / "contract" / "fixtures" / "framing.json").read_text())["fixtures"][0]
    del old["steps"][1]["send"]["params"]["before"]
    path = Path(tempfile.mkdtemp()) / "old.json"
    path.write_text(json.dumps({"fixtures": [old]}))
    results = conformance.run_all([path], {"founder": FakeDaemon("founder", unread=True).connect}, KEY)
    check("conformance: the wait fixture without its before filter fails on a daemon whose unread item ends the wait at once",
          results[0][0] == "fail" and "at least" in results[0][1], str(results))


def test_the_device_fixtures_pass_against_a_fake_network_and_catch_a_leak() -> None:
    from fake_methods import devices
    path = [ROOT / "contract" / "fixtures" / "devices.json"]
    def run(network):
        devices.reset()
        return conformance.run_all(path, {"founder": FakeDaemon("founder").connect, "unpaired": network.connect}, KEY)
    results = run(FakeNetwork())
    check("conformance: the device fixtures pass against a fake network that refuses the unpaired", all(s == "pass" for s, _, _ in results), str(results))
    check("conformance: one step per method of protocol.json is sent unpaired", len(conformance.load(path)[0]["steps"]) >= len(METHODS) + 1)

    class Leaky(FakeNetwork):       # answers an unpaired hello with the method list, as a daemon that forgot the rule would
        def _serve(self, conn):
            self.member._answer = (lambda orig: lambda line, said: orig(line, True))(self.member._answer)
            super()._serve(conn)
    class OpenAfterRevoke(FakeNetwork):     # keeps a revoked device's connection open
        def _serve(self, conn):
            from fake_methods import devices as dev
            real = dev.WORLD["socks"]
            super()._serve(conn)
    leaky = FakeNetwork()
    orig = leaky.connect
    def leaking():
        client, server = socket.socketpair()
        def serve():
            buf = b""
            with server:
                while True:
                    chunk = server.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        reply, _ = FakeDaemon("member")._answer(line, True)
                        server.sendall(json.dumps(reply).encode() + b"\n")
        threading.Thread(target=serve, daemon=True).start()
        return client
    leaky.connect = leaking
    results = run(leaky)
    check("conformance: a network that answers an unpaired caller fails the unpaired fixture", dict((n, s) for s, _, n in results)["devices.unpaired_gets_nothing"] == "fail", str(results))


def test_state_words_in_upper_case_fail_the_fixtures() -> None:
    path = [ROOT / "contract" / "fixtures" / "state_words.json"]
    results = conformance.run_all(path, {"founder": FakeDaemon("founder").connect}, KEY)
    check("conformance: the state word fixtures pass for lower-case words", [s for s, _, _ in results] == ["pass"] * len(results) and len(results) == 1 + len(STATE_WORDS), str(results))
    results = conformance.run_all(path, {"founder": FakeDaemon("founder", upper_case=True).connect}, KEY)
    check("conformance: a daemon that sends IDLE and RUNNING fails every one of them", all(s == "fail" for s, _, _ in results) and results, str(results))


def test_the_bytes_fixtures_pass_against_the_fake_tmux_and_catch_a_hole() -> None:
    path = [ROOT / "contract" / "fixtures" / "terminal_bytes.json"]
    results = conformance.run_all(path, {"founder": FakeDaemon("founder").connect, "agent": FakeDaemon("agent").connect}, KEY)
    check("conformance: the bytes fixtures pass against the fake tmux terminal", all(s == "pass" for s, _, _ in results) and results, str(results))

    def hole(reply):        # a daemon that skips a seq on the holder notice
        if reply.get("method") == "holder":
            reply["params"]["seq"] += 1
        return reply
    results = dict((n, s) for s, _, n in conformance.run_all(path, {"founder": FakeDaemon("founder", hole).connect}, KEY, "terminal.bytes."))
    check("conformance: a seq that skips fails the fixtures that follow the counter", results["terminal.bytes.output_follows_the_snapshot"] == "fail"
          and results["terminal.bytes.resize_gives_watchers_a_snapshot"] == "fail", str(results))

    def repeat(reply):      # a daemon that sends the first output again after the snapshot
        if reply.get("method") == "output":
            reply["params"]["seq"] = 100
        return reply
    results = dict((n, s) for s, _, n in conformance.run_all(path, {"founder": FakeDaemon("founder", repeat).connect}, KEY, "terminal.bytes.reconnect"))
    check("conformance: output with a seq at or below the snapshot's fails the reconnect fixture", results["terminal.bytes.reconnect_from_a_snapshot"] == "fail", str(results))


def test_the_counter_and_base64_matchers() -> None:
    saved = {"s": 4}
    for want, got, ok in (({"$next": "s"}, 5, True), ({"$next": "s"}, 6, False), ({"$above": "s"}, 5, True), ({"$above": "s"}, 4, False),
                          ({"$above": "s"}, True, False), ({"$above": "nope"}, 5, False),
                          ({"$b64regex": "bytes-42"}, base64.b64encode(b"\r\nbytes-42\r\n").decode(), True), ({"$b64regex": "bytes-42"}, "ZZZ", False),
                          ({"$b64regex": "x"}, 7, False)):
        try:
            conformance.matches(want, got, saved)
            passed = True
        except conformance.Mismatch:
            passed = False
        check(f"conformance: matcher {json.dumps(want)} on {json.dumps(got)[:20]} is {'a match' if ok else 'a mismatch'}", passed == ok)


def test_a_reply_outside_the_json_rpc_envelope_fails() -> None:
    _first_fixture_fails(lambda r: {**r, "jsonrpc": "1.0"}, "jsonrpc", "conformance: a reply with jsonrpc 1.0 fails")
    _first_fixture_fails(lambda r: {k: v for k, v in r.items() if k != "jsonrpc"}, "jsonrpc", "conformance: a reply without jsonrpc fails")
    _first_fixture_fails(lambda r: {**r, "error": {"code": -32603, "message": "internal"}}, "exactly one", "conformance: a reply with both result and error fails")
    _first_fixture_fails(lambda r: {k: v for k, v in r.items() if k not in ("result", "error")}, "exactly one", "conformance: a reply with neither fails")
    a, b = socket.socketpair()
    b.sendall(b'{"jsonrpc": "1.0", "method": "event", "params": {}}\n')
    conn = conformance.Connection(lambda: a)
    try:
        conn.reply_to(1)
        bad = False
    except conformance.Mismatch:
        bad = True
    check("conformance: a notice with jsonrpc 1.0 fails", bad)


def test_a_run_that_skipped_is_not_green(td) -> None:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        only_founder = conformance.main([*KEYARG, *map(str, READY)], _fakes("founder"))
        allowed = conformance.main(["--allow-skip", *KEYARG, *map(str, READY)], _fakes("founder"))
        all_roles = conformance.main([*KEYARG, *map(str, READY)], _fakes("founder", "agent", "anonymous", "unpaired"))
    check("conformance: a run with only the founder socket exits 3, not 0", only_founder == 3, out.getvalue())
    check("conformance: --allow-skip makes the same run exit 0", allowed == 0)
    check("conformance: a run with every role passes with exit 0", all_roles == 0)
    path = Path(td) / "all_skipped.json"
    path.write_text(json.dumps({"fixtures": [{"name": "s.one", "as": "member", "steps": [{"raw": "{", "error": {}}]}]}))
    with contextlib.redirect_stdout(io.StringIO()):
        skipped_all = conformance.main([str(path)], _fakes("founder"))
    check("conformance: a run where everything skipped exits 3", skipped_all == 3)


def _run(args, **fake) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = conformance.main([*args, *map(str, READY)], _fakes("founder", "agent", "anonymous", "unpaired", **fake))
    return code, out.getvalue()


def test_only_role_skips_are_covered_by_allow_skip(td) -> None:
    code, out = _run(["--allow-skip", *KEYARG], lacks={"agent.get"})
    check("conformance: a daemon that omits a method its fixtures pin fails even with --allow-skip", code == 1 and "MISSING" in out and "not in --allow-missing: agent.get" in out, out)
    code, out = _run(["--allow-skip", "--allow-missing", "agent.get", *KEYARG], lacks={"agent.get"})
    check("conformance: the same run with --allow-missing for that method exits 0", code == 0, out)
    listing = Path(td) / "missing.txt"
    listing.write_text("# methods Go lacks\n\nagent.get  # soon\n")
    code, out = _run(["--allow-skip", "--allow-missing-file", str(listing), *KEYARG], lacks={"agent.get"})
    check("conformance: --allow-missing-file lists methods one per line, comments allowed", code == 0, out)
    code, out = _run(["--allow-skip", "--allow-missing", "whoami", *KEYARG], lacks={"agent.get"})
    check("conformance: allowing a different method does not cover it", code == 1, out)
    code, out = _run(["--allow-skip"])
    check("conformance: a run without the founder key var and --allow-skip exits non-zero", code == 1 and "NEEDVAR" in out and "needs --var founder_key" in out, out)
    code, out = _run(["--allow-skip", *KEYARG])
    check("conformance: the summary counts each kind apart",
          "0 failed, 0 skipped by role, 0 not implemented, 0 needing a var" in out, out)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bad = conformance.main(["--allow-missing-file", str(Path(td) / "nope.txt"), *KEYARG], _fakes("founder"))
    check("conformance: an unreadable --allow-missing-file is a usage error", bad == 2)
    _, out = _run(["--allow-skip"])
    names = [ln.split()[1] for ln in out.splitlines() if ln.startswith("NEEDVAR")]
    listing = Path(td) / "needvar.txt"
    listing.write_text("# fixtures this run cannot seed\n" + "\n".join(sorted(names)) + "\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing)])
    check("conformance: --allow-needvar-file covers the listed fixtures that need a var", code == 0 and "STALE" not in out, out)
    listing.write_text("\n".join(sorted(names)[1:]) + "\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing)])
    check("conformance: a needvar fixture not in the list still fails", code == 1 and "not in --allow-missing" in out, out)
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing), *KEYARG])
    check("conformance: a listed fixture that now has its var is stale and fails the run", code == 1 and "STALE" in out, out)
    listing.write_text("no.such.fixture\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing), *KEYARG])
    check("conformance: a listed fixture that does not exist is stale", code == 1 and "STALE no.such.fixture" in out, out)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bad = conformance.main(["--allow-needvar-file", str(Path(td) / "nope.txt"), *KEYARG], _fakes("founder"))
    check("conformance: an unreadable --allow-needvar-file is a usage error", bad == 2)


def test_matchers() -> None:
    def ok(want, got, saved=None):
        try:
            conformance.matches(want, got, saved if saved is not None else {})
            return True
        except conformance.Mismatch:
            return False
    saved: dict = {}
    check("conformance: objects match as subsets", ok({"a": 1}, {"a": 1, "b": 2}) and not ok({"a": 1}, {"b": 2}))
    check("conformance: arrays match element by element", ok([1, 2], [1, 2]) and not ok([1], [1, 2]))
    check("conformance: a bool is not an integer", not ok({"$type": "integer"}, True) and ok({"$type": "boolean"}, True))
    check("conformance: $contains, $oneOf, $regex, $absent",
          ok({"$contains": {"n": 1}}, [{"n": 0}, {"n": 1}]) and not ok({"$contains": 3}, [1, 2])
          and ok({"$oneOf": ["a", "b"]}, "b") and ok({"$regex": "^ab"}, "abc") and ok({"k": {"$absent": True}}, {}) and not ok({"k": {"$absent": True}}, {"k": 1}))
    check("conformance: $save keeps the value for {{name}}", ok({"$save": "x", "$type": "integer"}, 7, saved) and
          conformance.substitute({"id": "{{x}}", "s": "n{{x}}"}, saved) == {"id": 7, "s": "n7"})
    check("conformance: $each needs every element to match", ok({"$each": {"n": 1}}, [{"n": 1}, {"n": 1}]) and ok({"$each": {"n": 1}}, [])
          and not ok({"$each": {"n": 1}}, [{"n": 1}, {"n": 2}]))
    check("conformance: $exact refuses extra keys, beside plain keys", ok({"$exact": True, "a": 1}, {"a": 1})
          and not ok({"$exact": True, "a": 1}, {"a": 1, "b": 2}) and ok({"$type": "object", "a": 1}, {"a": 1, "b": 2}))
    check("conformance: timestamps are RFC 3339 UTC", ok({"$type": "timestamp"}, "2026-10-06T09:00:00Z") and not ok({"$type": "timestamp"}, "yesterday"))


def test_fixtures_follow_the_format_and_the_spec() -> None:
    files = sorted((ROOT / "contract" / "fixtures").glob("*.json"))
    fixtures = conformance.load(files)
    names = [f["name"] for f in fixtures]
    check("conformance: fixture names are unique", len(names) == len(set(names)))
    spec_keys = set(METHODS) | {k for k in SPEC if k.startswith("x-")}
    check("conformance: every fixture names the spec method or x- section it pins", all(f.get("spec") in spec_keys for f in fixtures),
          str([f["name"] for f in fixtures if f.get("spec") not in spec_keys]))
    check("conformance: every fixture has a role, an about line and steps that expect something",
          all(f.get("as", "founder") in RANK | {"unpaired": 0} and f.get("about") and f["steps"] and all(
              sum(k in s for k in ("send", "raw", "await")) + (("closed" in s) and not any(k in s for k in ("send", "raw", "await"))) == 1 and (
                  "result" in s or "error" in s or "closed" in s or ("send" in s and "await" not in s)) for s in f["steps"]) for f in fixtures))
    check("conformance: the extra connections of a fixture have roles, and every step's `on` names one",
          all(set(f.get("with", {}).values()) <= set(RANK) | {"unpaired"} and {s.get("on") for s in f["steps"]} - {None} <= set(f.get("with", {}))
              for f in fixtures))
    for f in fixtures:
        pending = set()
        for s in f["steps"]:
            if "send" in s and not ({"result", "error", "closed"} & set(s)):
                pending.add(s["send"]["id"])
            pending.discard(s.get("await"))
        check(f"conformance: {f['name']} awaits every call it only sends", not pending, str(pending))
    sent = {s["send"]["method"] for f in fixtures for s in f["steps"] if "send" in s}
    check("conformance: every method a fixture sends is in the spec (or the one it does not have)", sent - {"no.such_method"} <= set(METHODS), str(sent - set(METHODS)))
    refused = {f["spec"] for f in fixtures for s in f["steps"] if "error" in s}
    check("conformance: hello and whoami each have a refusal or error case", {"hello", "whoami"} <= refused, str(refused))


def main() -> int:
    def tests(td):
        run(lambda: test_the_fixtures_pass_against_a_fake_daemon())
        run(lambda: test_a_role_without_a_socket_is_skipped_not_passed())
        run(lambda: test_a_wrong_reply_fails_that_fixture(td))
        run(lambda: test_the_command_line_runs_against_a_socket_path(td))
        run(lambda: test_the_command_line_arguments_over_socketpairs())
        run(lambda: test_skips_for_a_missing_var_and_a_method_the_daemon_lacks())
        run(lambda: test_the_fixtures_run_against_the_python_daemon(Path(td) / 'pyd'))
        run(lambda: test_the_device_fixtures_pass_against_a_fake_network_and_catch_a_leak())
        run(lambda: test_state_words_in_upper_case_fail_the_fixtures())
        run(lambda: test_the_bytes_fixtures_pass_against_the_fake_tmux_and_catch_a_hole())
        run(lambda: test_the_counter_and_base64_matchers())
        run(lambda: test_a_waiting_call_that_holds_the_connection_fails_the_framing_fixture())
        run(lambda: test_a_reply_outside_the_json_rpc_envelope_fails())
        run(lambda: test_a_run_that_skipped_is_not_green(td))
        run(lambda: test_only_role_skips_are_covered_by_allow_skip(td))
        run(lambda: test_matchers())
        run(lambda: test_fixtures_follow_the_format_and_the_spec())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
