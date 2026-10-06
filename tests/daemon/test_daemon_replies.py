"""Tests: the daemon always answers (a database it cannot use is a reply with the reason, never a hangup the
client reads as "the daemon is not running"), dev mode restarts onto a tree that was still being written when
the process started, and the daemon says which code it runs.

Run on its own: python3 tests/daemon/test_daemon_replies.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import STARTED  # noqa: E402
from handline.cli import daemon as clidaemon  # noqa: E402
from handline.daemon import client, devmode, loop, rpc, socket_server  # noqa: E402
from handline.infra import gitrun  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402
from test_socket import _serve_one_roundtrip  # noqa: E402


class WrongSchema:
    """db.connect raising what a daemon on old (or new) code gets."""

    def __init__(self, kind="newer", have=2, latest=1) -> None:
        self.err = db.SchemaError("x", kind, have, latest)
        self.real = db.connect
        db.connect = self.boom

    def boom(self, *a, **k):
        raise self.err

    def restore(self) -> None:
        db.connect = self.real


def test_a_legacy_verb_the_database_blocks_is_answered_with_the_reason(home: Path) -> None:
    support.use_home(home)
    for kind, have, latest, said in (("newer", 2, 1, "newer than this daemon (2 vs 1)"), ("older", 1, 2, "older than this daemon needs (1 vs 2)")):
        w = WrongSchema(kind, have, latest)
        try:
            reply = _serve_one_roundtrip({"verb": "say", "requester": "a", "target": "b", "body": "x"})
        finally:
            w.restore()
        check(f"legacy: a database {kind} than the daemon is a reply, not a hangup", reply is not None and reply["ok"] is False, str(reply))
        check(f"legacy: and it says why, and what to run ({kind})", said in reply["said"] and "hl service install" in reply["said"], str(reply))


def test_a_json_rpc_call_the_database_blocks_is_a_typed_refusal_and_the_connection_goes_on(home: Path) -> None:
    support.use_home(home)
    w = Wire()
    hello = w.call("hello", _hp())
    check("rpc: hello needs no database and says which schema this daemon knows", hello["result"]["schema"] == db.LATEST_VERSION)
    blocked = WrongSchema()
    try:
        r = w.call("agents.list", {})
    finally:
        blocked.restore()
    d = r["error"]["data"]
    check("rpc: the call is refused at the schema gate with the reason and the fix",
          _code(r) == "refused" and d["gate"] == "schema" and "newer than this daemon" in d["reason"] and d["hint"] == "hl service install", str(r))
    check("rpc: the connection is still there", "result" in w.call("agents.list", {}))
    w.close()


def _tree_dev_mode(fps, changed_since_start):
    """The real decision with a tree that follows `fps` (one fingerprint per call) and a clock the test moves."""
    clock = {"t": 1000.0}
    state = {"fp": None, "warned": None, "seen": None}
    checks = []
    real = (devmode._code_fingerprint, devmode._dev_import_check, devmode.dev_mode_enabled, devmode._now,
            devmode._changed_since_start, devmode.QUIET_S)
    seq = iter(fps)
    cur = {"fp": None}
    devmode._code_fingerprint = lambda root: cur["fp"]
    devmode._dev_import_check = lambda log: checks.append(cur["fp"]) or True
    devmode.dev_mode_enabled = lambda: True
    devmode._now = lambda: clock["t"]
    devmode._changed_since_start = lambda root: changed_since_start
    devmode.QUIET_S = 30.0

    def step(fp, advance=0.0):
        clock["t"] += advance
        cur["fp"] = fp
        return devmode._dev_mode_should_restart(state, lambda m: None)

    def restore():
        (devmode._code_fingerprint, devmode._dev_import_check, devmode.dev_mode_enabled, devmode._now,
         devmode._changed_since_start, devmode.QUIET_S) = real
    return step, state, checks, restore


def test_a_tree_still_being_written_is_not_restarted_onto_until_it_is_quiet(home: Path) -> None:
    support.use_home(home)
    step, state, checks, restore = _tree_dev_mode([], False)
    try:
        step("v1")
        check("quiet: the tree the process started on is the baseline", step("v1", 5) is False)
        check("quiet: a change starts the wait, not a restart", step("v2", 5) is False)
        check("quiet: a tree that moves again restarts the wait", step("v3", 20) is False)
        check("quiet: still inside it, nothing is asked of the import check", step("v3", 20) is False and checks == [])
        check("quiet: once it has stood still for the whole period it is checked and restarted onto",
              step("v3", 15) is True and checks == ["v3"], str(checks))
    finally:
        restore()


def test_a_tree_that_changed_while_the_process_was_loading_is_restarted_onto_even_if_it_looks_settled(home: Path) -> None:
    support.use_home(home)
    step, state, checks, restore = _tree_dev_mode([], True)         # files newer than the process's start
    try:
        check("stale baseline: the first look at a tree written since the start is a change, after the wait", step("v9") is False)
        check("stale baseline: and the wait is the same", step("v9", 29) is False)
        check("stale baseline: then it restarts, though no later write happens", step("v9", 2) is True and checks == ["v9"], str(checks))
    finally:
        restore()


def test_a_tree_that_changes_twice_in_a_row_restarts_twice(home: Path) -> None:
    support.use_home(home)
    # Process one starts, a merge writes for two minutes, the daemon restarts onto the half-written tree;
    # process two starts on it and the merge finishes while it starts, so its first look sees the finished tree.
    one, _, checks_one, restore = _tree_dev_mode([], False)
    try:
        one("v1")
        one("v2", 5)
        check("twice: the first process restarts once the half-written tree is quiet", one("v2", 31) is True)
    finally:
        restore()
    two, _, checks_two, restore = _tree_dev_mode([], True)
    try:
        two("v3")
        check("twice: the second, started before the merge finished, restarts as well", two("v3", 31) is True and checks_two == ["v3"])
    finally:
        restore()


def test_a_file_written_after_the_process_started_is_seen_by_the_real_check(home: Path) -> None:
    support.use_home(home)
    root = home / "repo"
    (root / "handline").mkdir(parents=True)
    f = root / "handline" / "core.py"
    f.write_text("x = 1\n")
    os.utime(f, (STARTED - 100, STARTED - 100))
    check("start: a file older than the process is what it loaded", devmode._changed_since_start(root) is False)
    os.utime(f, (STARTED + 50, STARTED + 50))
    check("start: a file written after the process began to load is not", devmode._changed_since_start(root) is True)
    f.unlink()
    check("start: a file that vanished mid-merge counts for nothing", devmode._changed_since_start(root) is False)


def test_the_daemon_says_which_code_it_runs_and_keeps_saying_it_after_the_checkout_moves(home: Path) -> None:
    support.use_home(home)
    real = (list(loop._loaded), loop._head_sha)
    loop._loaded.clear()
    loop._head_sha = lambda: "aaa1111"
    try:
        w = Wire()
        first = w.call("hello", _hp())["result"]["daemon"]
        loop._head_sha = lambda: "bbb2222"          # a merge moved the checkout under a running daemon
        again = w.call("hello", _hp(), id=2)["result"]["daemon"]
        w.close()
    finally:
        loop._loaded[:] = real[0]
        loop._head_sha = real[1]
    check("sha: hello names the code the daemon started on", first == "aaa1111", first)
    check("sha: and still does once the checkout has moved", again == "aaa1111", again)


def test_hl_ps_says_which_code_the_daemon_is_on_and_when_the_checkout_has_moved_past_it(home: Path) -> None:
    support.use_home(home)
    real = (client.connect, client.call, gitrun.run)
    client.connect = lambda timeout=0: object()
    client.call = lambda sock, method, params=None, **k: {"daemon": "aaa1111"}
    try:
        gitrun.run = lambda cwd, *a, **k: (0, "aaa1111\n", "")
        same = clidaemon._code_line()
        gitrun.run = lambda cwd, *a, **k: (0, "bbb2222\n", "")
        behind = clidaemon._code_line()
        client.connect = lambda timeout=0: None
        silent = clidaemon._code_line()
    finally:
        client.connect, client.call, gitrun.run = real
    check("ps: the line names the daemon's code", same == "  code: aaa1111", same)
    check("ps: and says to restart when the checkout is elsewhere", "bbb2222" in behind and "hl service install" in behind, behind)
    check("ps: a daemon that does not answer is not guessed at", "unknown" in silent, silent)


def main() -> int:
    def tests(td):
        run(lambda: test_a_legacy_verb_the_database_blocks_is_answered_with_the_reason(Path(td) / "p1"))
        run(lambda: test_a_json_rpc_call_the_database_blocks_is_a_typed_refusal_and_the_connection_goes_on(Path(td) / "p2"))
        run(lambda: test_a_tree_still_being_written_is_not_restarted_onto_until_it_is_quiet(Path(td) / "p3"))
        run(lambda: test_a_tree_that_changed_while_the_process_was_loading_is_restarted_onto_even_if_it_looks_settled(Path(td) / "p4"))
        run(lambda: test_a_tree_that_changes_twice_in_a_row_restarts_twice(Path(td) / "p5"))
        run(lambda: test_a_file_written_after_the_process_started_is_seen_by_the_real_check(Path(td) / "p6"))
        run(lambda: test_the_daemon_says_which_code_it_runs_and_keeps_saying_it_after_the_checkout_moves(Path(td) / "p7"))
        run(lambda: test_hl_ps_says_which_code_the_daemon_is_on_and_when_the_checkout_has_moved_past_it(Path(td) / "p8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
