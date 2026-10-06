"""Tests: the conformance harness (contract/conformance.py) runs the fixtures in contract/fixtures against a fake server.

Run on its own: python3 tests/infra/test_conformance.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contract"))
import conformance  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_methods import HANDLERS  # noqa: E402

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

    def __init__(self, role: str, mutate=None, lacks=(), broken=(), serial=False, unread=False):
        """`lacks`: methods this daemon does not have (not listed, -32601). `broken`: listed, yet answered -32601.
        `serial`: serve a connection's calls one at a time, so a waiting inbox.list holds the ones behind it.
        `unread`: the inbox holds an unread item (id 5), so an inbox.list that its filters do not exclude returns at once."""
        self.role, self.mutate, self.lacks, self.broken = role, mutate or (lambda reply: reply), set(lacks), set(broken)
        self.serial, self.unread = serial, unread

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

        def send(reply):
            with out:
                conn.sendall(json.dumps(self.mutate(reply)).encode() + b"\n")

        def answer_later(line):
            reply, _ = self._answer(line, True)
            send(reply)
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
                    if said_hello and not self.serial and _method_of(line) == "inbox.list":
                        threading.Thread(target=answer_later, args=(line,), daemon=True).start()
                        continue
                    reply, said_hello = self._answer(line, said_hello)
                    send(reply)

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
            kind = {"founder": "person", "agent": "agent", "anonymous": "anonymous"}[self.role]
            return ok({"protocol": 1, "min_client": 1, "daemon": "fake", "schema": 1, "role": self.role,
                       "caller": {"kind": kind, "name": "x"}, "transport": "local", "methods": self._methods(),
                       "capabilities": {"chat": True, "review": False, "input_ask": False, "terminal_format": ["text"]}}), True
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
                kind, payload = result
                return (ok if kind == "ok" else fail)(payload), said_hello
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
            return fail({"code": -32001, "message": "not_found", "data": params["agent"]}), said_hello
        if method == "agents.list":
            return ok({"agents": [{"name": "a.one", "role": "x" * 160, "state": "IDLE", "state_detail": "3m"}]}), said_hello
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


FILES = sorted((ROOT / "contract" / "fixtures").glob("*.json"))


def _fakes(*roles: str, mutate=None, **kw) -> dict:
    return {r: FakeDaemon(r, mutate, **kw).connect for r in roles}


KEY = {"founder_key": "k", "running_agent": "a.running", "locked_agent": "a.locked", "terminal_agent": "a.term", "terminal_handle": "t-a.term",
       "held_agent": "a.held", "walled_agent": "a.walled", "dialog_agent": "a.dialog"}
KEYARG = ["--var", "founder_key=k", "--var", "running_agent=a.running", "--var", "locked_agent=a.locked", "--var", "terminal_agent=a.term", "--var", "terminal_handle=t-a.term",
          "--var", "held_agent=a.held", "--var", "walled_agent=a.walled", "--var", "dialog_agent=a.dialog"]


def _count(results, status: str) -> int:
    return sum(1 for s, _, _ in results if s == status)


def test_the_fixtures_pass_against_a_fake_daemon() -> None:
    results = conformance.run_all(FILES, _fakes("founder", "agent", "anonymous"), KEY)
    bad = [(n, why) for s, why, n in results if s != "pass"]
    check("conformance: every fixture passes against the fake daemon", not bad and results, str(bad))
    check("conformance: each fixture is reported by name", {"hello.ok", "errors.not_found"} <= {n for _, _, n in results})


def test_a_role_without_a_socket_is_skipped_not_passed() -> None:
    results = conformance.run_all(FILES, _fakes("founder"), KEY)
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
    results = conformance.run_all(FILES, _fakes("founder", "anonymous", lacks={"agent.get"}), KEY)
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
    failed = {n: why for n, (s, why) in by.items() if s == "fail"}
    check("conformance: no fixture fails against the Python daemon", not failed, str(failed))
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
        only_founder = conformance.main(KEYARG, _fakes("founder"))
        allowed = conformance.main(["--allow-skip", *KEYARG], _fakes("founder"))
        all_roles = conformance.main(KEYARG, _fakes("founder", "agent", "anonymous"))
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
        code = conformance.main(args, _fakes("founder", "agent", "anonymous", **fake))
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
          all(f.get("as", "founder") in RANK and f.get("about") and f["steps"] and all(
              sum(k in s for k in ("send", "raw", "await")) == 1 and (
                  "result" in s or "error" in s or "closed" in s or ("send" in s and "await" not in s)) for s in f["steps"]) for f in fixtures))
    for f in fixtures:
        pending = set()
        for s in f["steps"]:
            if "send" in s and not ({"result", "error", "closed"} & set(s)):
                pending.add(s["send"]["id"])
            pending.discard(s.get("await"))
        check(f"conformance: {f['name']} awaits every call it only sends", not pending, str(pending))
    sent = {s["send"]["method"] for f in fixtures for s in f["steps"] if "send" in s}
    check("conformance: every method a fixture sends is in the spec", sent <= set(METHODS), str(sent - set(METHODS)))
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
        run(lambda: test_a_waiting_call_that_holds_the_connection_fails_the_framing_fixture())
        run(lambda: test_a_reply_outside_the_json_rpc_envelope_fails())
        run(lambda: test_a_run_that_skipped_is_not_green(td))
        run(lambda: test_only_role_skips_are_covered_by_allow_skip(td))
        run(lambda: test_matchers())
        run(lambda: test_fixtures_follow_the_format_and_the_spec())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
