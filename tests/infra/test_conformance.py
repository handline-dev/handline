"""Tests: the conformance harness (contract/conformance.py): its matchers, the fixtures' format, and how a run reports and
exits, against a small server that plays the fixtures written here. The contract fixtures run against the Go daemon
(tools/test-go.sh).

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contract"))
import conformance  # noqa: E402

SPEC = json.loads((ROOT / "contract" / "protocol.json").read_text())
METHODS = {m["name"]: m for m in SPEC["methods"]}
RANK = {r: i for i, r in enumerate(SPEC["x-roles"]["order"])}


SERVED = ("hello", "whoami", "agent.get", "devices.list")


class Server:
    """Answers hello (listing SERVED less `lacks`), whoami and agent.get (not_found, or -32601 when `broken`), and -32601 to
    anything else; `mutate` rewrites each reply. A line that is not JSON gets -32700 and the connection stays open."""

    def __init__(self, role: str, lacks=(), broken=(), mutate=None):
        self.role, self.lacks, self.broken, self.mutate = role, set(lacks), set(broken), mutate or (lambda r: r)

    def reply(self, line: bytes) -> dict:
        try:
            req = json.loads(line)
        except ValueError:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        method, rid = req.get("method"), req.get("id")
        if method == "hello":
            out = {"result": {"protocol": 1, "role": self.role, "methods": [{"name": m} for m in SERVED if m not in self.lacks]}}
        elif method == "whoami":
            out = {"result": {"role": self.role}}
        elif method == "agent.get" and method not in self.lacks | self.broken:
            out = {"error": {"code": -32001, "message": "not_found", "data": {"code": "not_found"}}}
        else:
            out = {"error": {"code": -32601, "message": "method not found"}}
        return self.mutate({"jsonrpc": "2.0", "id": rid, **out})

    def serve(self, sock: socket.socket) -> None:
        with sock, sock.makefile("rb") as lines:
            for line in lines:
                sock.sendall((json.dumps(self.reply(line)) + "\n").encode())

    def connect(self) -> socket.socket:
        a, b = socket.socketpair()
        threading.Thread(target=self.serve, args=(b,), daemon=True).start()
        return a

    def listen(self, path: str) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen()

        def accept() -> None:
            while True:
                conn, _ = srv.accept()
                threading.Thread(target=self.serve, args=(conn,), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()


def _servers(*roles: str, **kw) -> dict:
    return {r: Server(r, **kw).connect for r in roles}


HELLO = {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "t", "protocol": 1, "key": "{{founder_key}}"}}
FIXTURES = {"fixtures": [
    {"name": "hello.ok", "spec": "hello", "as": "founder", "about": "a founder's hello", "steps": [{"send": HELLO, "result": {"role": "founder"}}]},
    {"name": "whoami.agent", "spec": "whoami", "as": "agent", "about": "an agent is told its role",
     "steps": [{"send": {"jsonrpc": "2.0", "id": 1, "method": "whoami"}, "result": {"role": "agent"}}]},
    {"name": "errors.not_found", "spec": "agent.get", "as": "founder", "about": "an agent that does not exist",
     "steps": [{"send": {"jsonrpc": "2.0", "id": 1, "method": "agent.get", "params": {"name": "nope"}}, "error": {"code": -32001}}]},
]}
FILES = [Path(tempfile.mkdtemp(prefix="conformance-")) / "harness.json"]
FILES[0].write_text(json.dumps(FIXTURES))
KEY = {"founder_key": "k"}
KEYARG = ["--var", "founder_key=k"]


def _count(results, status: str) -> int:
    return sum(1 for s, _, _ in results if s == status)


def test_every_fixture_passes_and_a_role_without_a_socket_is_skipped() -> None:
    results = conformance.run_all(FILES, _servers("founder", "agent"), KEY)
    check("conformance: every fixture passes, reported by name", [(s, n) for s, _, n in results] ==
          [("pass", "hello.ok"), ("pass", "whoami.agent"), ("pass", "errors.not_found")], str(results))
    results = conformance.run_all(FILES, _servers("founder"), KEY)
    check("conformance: a fixture for a role with no socket is skipped, not passed",
          {n for s, _, n in results if s == "skip"} == {"whoami.agent"} and not _count(results, "fail"), str(results))


def test_a_wrong_reply_fails_that_fixture(td) -> None:
    path = Path(td) / "bad.json"
    path.write_text(json.dumps({"fixtures": [{"name": "bad.case", "as": "founder", "steps": [
        {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "t", "protocol": 1}}, "result": {"role": "agent"}}]}]}))
    [(status, why, name)] = conformance.run_all([path], _servers("founder"), {})
    check("conformance: a reply that differs fails, naming the path and both values",
          status == "fail" and "result.role" in why and '"agent"' in why and '"founder"' in why, why)
    path.write_text(json.dumps({"fixtures": [{"name": "bad.close", "as": "founder", "steps": [
        {"raw": "{", "error": {"code": -32700}, "closed": True}]}]}))
    [(status, why, _)] = conformance.run_all([path], _servers("founder"), {})
    check("conformance: a connection that stays open fails a closed:true step", status == "fail", why)


def test_the_command_line_runs_against_a_socket_path(td) -> None:
    try:     # a unix socket path is short, so not under td
        path = os.path.join(tempfile.mkdtemp(prefix="cf", dir="/tmp"), "s")
        Server("founder").listen(path)
    except PermissionError:
        print("note: this run may not bind a socket; the command line check was not run")
        return
    cli = [sys.executable, str(ROOT / "contract" / "conformance.py")]
    r = subprocess.run([*cli, path, *KEYARG, "--only", "hello", str(FILES[0])], capture_output=True, text=True)
    check("conformance: the command line reports per fixture and exits 0", r.returncode == 0 and "PASS  hello.ok" in r.stdout and "0 failed" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([*cli, path, "--only", "hello", str(FILES[0])], capture_output=True, text=True)
    check("conformance: the same command line without the founder key exits 1 and says it needs a var",
          r.returncode == 1 and "NEEDVAR  hello.ok" in r.stdout and "needing a var" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run(cli, capture_output=True, text=True)
    check("conformance: no socket is a usage error", r.returncode == 2)


def test_the_command_line_arguments_over_socketpairs() -> None:
    """What the socket-path test above checks, through conformance.main with the same arguments, for a run that may not bind."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        with_key = conformance.main([*KEYARG, "--only", "hello", str(FILES[0])], _servers("founder"))
    check("conformance: the command line arguments with the founder key report per fixture and exit 0",
          with_key == 0 and "PASS  hello.ok" in out.getvalue() and "0 failed" in out.getvalue(), out.getvalue())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        without = conformance.main(["--only", "hello", str(FILES[0])], _servers("founder"))
    check("conformance: the same arguments without the key exit 1 and say they need a var",
          without == 1 and "NEEDVAR  hello.ok" in out.getvalue() and "needing a var" in out.getvalue(), out.getvalue())


def _first_fixture_fails(mutate, needle: str, name: str) -> None:
    [(status, why, _)] = conformance.run_all(FILES, _servers("founder", mutate=mutate), KEY, "hello.ok")
    check(name, status == "fail" and needle in why, f"{status}: {why}")


def test_skips_for_a_missing_var_and_a_method_the_daemon_lacks() -> None:
    [(status, why, _)] = conformance.run_all(FILES, _servers("founder"), {}, "hello.ok")
    check("conformance: a fixture needing a --var is reported needvar without it", status == "needvar" and "founder_key" in why, why)
    results = conformance.run_all(FILES, _servers("founder", "agent", lacks={"agent.get"}), KEY)
    by = {n: (s, why) for s, why, n in results}
    check("conformance: a fixture whose method the daemon does not list is reported missing, with the reason",
          by["errors.not_found"] == ("missing", "method not implemented by this daemon") and by["hello.ok"][0] == "pass", str(by))
    check("conformance: nothing else is hidden by it", not [n for n, (s, _) in by.items() if s == "fail"], str(by))
    results = conformance.run_all(FILES, _servers("founder", broken={"agent.get"}), KEY, "errors.not_found")
    check("conformance: a method the daemon lists and then answers -32601 for fails",
          results[0][0] == "fail" and "-32001" in results[0][1], str(results))


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
    files = [*KEYARG, *map(str, FILES)]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        only_founder = conformance.main(files, _servers("founder"))
        allowed = conformance.main(["--allow-skip", *files], _servers("founder"))
        all_roles = conformance.main(files, _servers("founder", "agent"))
    check("conformance: a run with only the founder socket exits 3, not 0", only_founder == 3, out.getvalue())
    check("conformance: --allow-skip makes the same run exit 0", allowed == 0)
    check("conformance: a run with every role passes with exit 0", all_roles == 0)
    path = Path(td) / "all_skipped.json"
    path.write_text(json.dumps({"fixtures": [{"name": "s.one", "as": "member", "steps": [{"raw": "{", "error": {}}]}]}))
    with contextlib.redirect_stdout(io.StringIO()):
        skipped_all = conformance.main([str(path)], _servers("founder"))
    check("conformance: a run where everything skipped exits 3", skipped_all == 3)


def _run(args, **kw) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = conformance.main([*args, *map(str, FILES)], _servers("founder", "agent", **kw))
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
    listing.write_text("agent.get\nagent.get\n")
    code, out = _run(["--allow-skip", "--allow-missing-file", str(listing), *KEYARG], lacks={"agent.get"})
    check("conformance: a name twice in --allow-missing-file is stale and fails the run", code == 1 and "STALE agent.get" in out and "more than once" in out, out)
    listing.write_text("agent.get\nhello\n")
    code, out = _run(["--allow-skip", "--allow-missing-file", str(listing), *KEYARG], lacks={"agent.get"})
    check("conformance: a name in --allow-missing-file that the daemon serves is stale and fails the run", code == 1 and "STALE hello" in out, out)
    listing.write_text("agent.get\ndevices.list\n")
    code, out = _run(["--allow-skip", "--allow-missing-file", str(listing), *KEYARG], lacks={"agent.get"})
    check("conformance: a listed name the daemon advertises in hello is stale though no fixture reaches it",
          code == 1 and "STALE devices.list" in out and "STALE agent.get" not in out, out)
    code, out = _run(["--allow-skip", "--allow-missing", "whoami", *KEYARG], lacks={"agent.get"})
    check("conformance: allowing a different method does not cover it", code == 1, out)
    code, out = _run(["--allow-skip"])
    check("conformance: a run without the founder key var and --allow-skip exits non-zero", code == 1 and "NEEDVAR" in out and "needs --var founder_key" in out, out)
    code, out = _run(["--allow-skip", *KEYARG])
    check("conformance: the summary counts each kind apart",
          "0 failed, 0 skipped by role, 0 not implemented, 0 needing a var" in out, out)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bad = conformance.main(["--allow-missing-file", str(Path(td) / "nope.txt"), *KEYARG], _servers("founder"))
    check("conformance: an unreadable --allow-missing-file is a usage error", bad == 2)
    listing = Path(td) / "needvar.txt"
    listing.write_text("# fixtures this run cannot seed\nhello.ok\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing)])
    check("conformance: --allow-needvar-file covers the listed fixtures that need a var", code == 0 and "STALE" not in out, out)
    listing.write_text("\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing)])
    check("conformance: a needvar fixture not in the list still fails", code == 1 and "not in --allow-missing" in out, out)
    listing.write_text("hello.ok\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing), *KEYARG])
    check("conformance: a listed fixture that now has its var is stale and fails the run", code == 1 and "STALE" in out, out)
    listing.write_text("no.such.fixture\n")
    code, out = _run(["--allow-skip", "--allow-needvar-file", str(listing), *KEYARG])
    check("conformance: a listed fixture that does not exist is stale", code == 1 and "STALE no.such.fixture" in out, out)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bad = conformance.main(["--allow-needvar-file", str(Path(td) / "nope.txt"), *KEYARG], _servers("founder"))
    check("conformance: an unreadable --allow-needvar-file is a usage error", bad == 2)


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
    opt = {"k": {"$oneOf": [{"$type": "integer"}, {"$absent": True}]}}
    check("conformance: a key whose $oneOf lists $absent is optional", ok(opt, {}) and ok(opt, {"k": 1}))
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
              sum(k in s for k in ("send", "raw", "await", "legacy")) + (("closed" in s) and not any(k in s for k in ("send", "raw", "await", "legacy"))) == 1 and (
                  "result" in s or "error" in s or "closed" in s or "reply" in s or ("send" in s and "await" not in s)) for s in f["steps"]) for f in fixtures))
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
        run(lambda: test_every_fixture_passes_and_a_role_without_a_socket_is_skipped())
        run(lambda: test_a_wrong_reply_fails_that_fixture(td))
        run(lambda: test_the_command_line_runs_against_a_socket_path(td))
        run(lambda: test_the_command_line_arguments_over_socketpairs())
        run(lambda: test_skips_for_a_missing_var_and_a_method_the_daemon_lacks())
        run(lambda: test_the_counter_and_base64_matchers())
        run(lambda: test_a_reply_outside_the_json_rpc_envelope_fails())
        run(lambda: test_a_run_that_skipped_is_not_green(td))
        run(lambda: test_only_role_skips_are_covered_by_allow_skip(td))
        run(lambda: test_matchers())
        run(lambda: test_fixtures_follow_the_format_and_the_spec())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
