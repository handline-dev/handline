"""Tests: finding the daemon (`client.ensure_daemon`), and what a hook does when the daemon is not there
(the spool, and `hook.record` that applies the same events when it is).

launchctl is the repo's one replaceable name, `service.run_launchctl`; no test here reaches the real launchd.

Run on its own: python3 tests/daemon/test_ensure_daemon.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import client, rpc, spool  # noqa: E402
from handline.infra import paths, service, sessiontoken  # noqa: E402
from handline.session import waiting  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code  # noqa: E402


class FakeLaunchd:
    """Stands in for run_launchctl and for connect: `kickstart` makes the daemon answer after `after` tries."""

    def __init__(self, installed=True, kickstart_rc=0, after=3) -> None:
        self.calls, self.installed, self.rc, self.after, self.tries, self.up = [], installed, kickstart_rc, after, 0, False
        self.real = (service.run_launchctl, service._plist_installed, client.connect)
        service.run_launchctl = self.launchctl
        service._plist_installed = lambda: self.installed
        client.connect = self.connect

    def launchctl(self, args):
        self.calls.append(args)
        self.up = self.rc == 0
        return subprocess.CompletedProcess(["launchctl", *args], self.rc, "", "launchd said no" if self.rc else "")

    def connect(self, timeout=0):
        if not self.up:
            return None
        self.tries += 1
        return object() if self.tries > self.after else None

    def restore(self) -> None:
        service.run_launchctl, service._plist_installed, client.connect = self.real


def test_ensure_daemon_asks_launchd_once_and_waits_for_the_socket(home: Path) -> None:
    support.use_home(home)
    f = FakeLaunchd(after=3)
    real = client.START_POLL_S
    client.START_POLL_S = 0.01
    try:
        got = client.ensure_daemon()
    finally:
        client.START_POLL_S = real
        f.restore()
    check("ensure: it returns the connection once the daemon answers", got is not None)
    check("ensure: after one kickstart of the service, never -k (which would kill a running one)",
          f.calls == [["kickstart", service.service_target()]], str(f.calls))
    check("ensure: and it retried while launchd started it", f.tries == 4, str(f.tries))


class UnloadedLaunchd(FakeLaunchd):
    """A service that is installed and not loaded: `kickstart` says it cannot find it until `bootstrap` loads it."""

    def __init__(self, bootstrap_rc=0, **kw) -> None:
        super().__init__(**kw)
        self.loaded, self.bootstrap_rc = False, bootstrap_rc

    def launchctl(self, args):
        self.calls.append(args)
        if args[0] == "bootstrap":
            self.loaded = self.bootstrap_rc == 0
            return subprocess.CompletedProcess(["launchctl", *args], self.bootstrap_rc, "", "Bootstrap failed: 5: Input/output error" if self.bootstrap_rc else "")
        if args[0] == "kickstart":
            self.up = self.loaded
            return subprocess.CompletedProcess(["launchctl", *args], 0 if self.loaded else 113, "",
                                               "" if self.loaded else "Could not find service \"dev.orcapipe.daemon\" in domain for user gui: 501")
        return subprocess.CompletedProcess(["launchctl", *args], 0, "", "")


def test_a_service_that_is_installed_and_not_loaded_is_loaded_then_started(home: Path) -> None:
    support.use_home(home)
    f = UnloadedLaunchd(after=1)
    real = client.START_POLL_S
    client.START_POLL_S = 0.01
    try:
        got = client.ensure_daemon()
    finally:
        client.START_POLL_S = real
        f.restore()
    verbs = [c[0] for c in f.calls]
    check("ensure: kickstart finds no service, the installed plist is bootstrapped, then it is kickstarted again", verbs[:3] == ["kickstart", "bootstrap", "enable"]
          and verbs[3:] == ["kickstart"] and got is not None, str(f.calls))
    check("ensure: it is bootstrapped into the user's domain from the installed plist, never rewritten", f.calls[1] == ["bootstrap", service.domain_target(), str(service.plist_path())], str(f.calls[1]))
    f = UnloadedLaunchd(bootstrap_rc=5)
    try:
        try:
            client.ensure_daemon(wait_s=0.2)
            err = None
        except client.DaemonDown as e:
            err = e
    finally:
        f.restore()
    check("ensure: a bootstrap launchd refuses is daemon_down with launchd's words and hl service install", err is not None
          and "Input/output error" in str(err) and "hl service install" in str(err) and [c[0] for c in f.calls] == ["kickstart", "bootstrap"], str(err))
    f = FakeLaunchd(kickstart_rc=113)
    try:
        try:
            client.ensure_daemon(wait_s=0.2)
        except client.DaemonDown:
            pass
    finally:
        f.restore()
    check("ensure: a kickstart that fails for another reason is never answered with a bootstrap", [c[0] for c in f.calls] == ["kickstart"], str(f.calls))
    f = FakeLaunchd(after=1)
    try:
        client.START_POLL_S, real = 0.01, client.START_POLL_S
        client.ensure_daemon()
    finally:
        client.START_POLL_S = real
        f.restore()
    check("ensure: a loaded service (kickstart works) is never bootstrapped", all(c[0] != "bootstrap" for c in f.calls), str(f.calls))


def test_a_daemon_that_is_there_is_not_started_again(home: Path) -> None:
    support.use_home(home)
    f = FakeLaunchd()
    f.up = True
    f.after = 0
    try:
        got = client.ensure_daemon()
    finally:
        f.restore()
    check("ensure: a connect that works asks launchd nothing", got is not None and f.calls == [])


def test_ensure_daemon_failing_is_daemon_down_with_the_log_path_and_nothing_done(home: Path) -> None:
    support.use_home(home)
    for label, kw, said in (("no service installed", {"installed": False}, "no service is installed"),
                            ("launchd refuses", {"kickstart_rc": 113}, "launchd would not start it"),
                            ("it never answers", {"after": 10 ** 9}, "did not answer")):
        f = FakeLaunchd(**kw)
        real = client.START_POLL_S
        client.START_POLL_S = 0.01
        try:
            try:
                client.ensure_daemon(wait_s=0.2)
                err = None
            except client.DaemonDown as e:
                err = e
        finally:
            client.START_POLL_S = real
            f.restore()
        check(f"daemon_down: {label}", err is not None and said in str(err), str(err))
        check(f"daemon_down: {label}: the log path is in it", err is not None and str(service.err_log()) in str(err))
    f = FakeLaunchd(installed=False)
    try:
        try:
            client.ensure_daemon()
        except client.DaemonDown:
            pass
    finally:
        f.restore()
    check("daemon_down: with no service installed launchd is not asked at all", f.calls == [])


def test_the_legacy_send_starts_the_service_when_it_is_installed_and_says_none_when_it_cannot(home: Path) -> None:
    support.use_home(home)
    f = FakeLaunchd(kickstart_rc=1)
    try:
        reply = client.send({"verb": "wake", "agent": "backend"})
    finally:
        f.restore()
    check("send: a daemon that cannot be started is the None callers already handle", reply is None and len(f.calls) == 1)
    f = FakeLaunchd(installed=False)
    try:
        reply = client.send({"verb": "wake", "agent": "backend"})
        quiet = client.send({"verb": "wake", "agent": "backend"}, start=False)
    finally:
        f.restore()
    check("send: and with no service nothing is asked of launchd", reply is None and quiet is None and f.calls == [])


def test_hooks_that_run_while_the_daemon_is_down_are_all_there_after(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    con = db.connect()
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    con.close()
    kinds = ["UserPromptSubmit", "PostToolUse", "PostToolUse", "Stop", "UserPromptSubmit", "Stop"]
    os.environ["OCP_AGENT"] = "worker"
    try:
        for i, kind in enumerate(kinds):
            real_in = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"hook_event_name": kind, "session_id": "s", "prompt": f"p{i}"}))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    evmod.record_from_stdin([kind])
            finally:
                sys.stdin = real_in
    finally:
        del os.environ["OCP_AGENT"]
    lines = spool.path().read_text().splitlines()
    check("spool: every hook event is a whole line in the file, nothing applied yet", len(lines) == len(kinds)
          and all(json.loads(ln)["agent"] == "worker" for ln in lines), str(len(lines)))
    con = db.connect()
    check("spool: the database has not heard of them", con.execute("SELECT COUNT(*) FROM events WHERE kind = 'UserPromptSubmit'").fetchone()[0] == 0)
    n = spool.ingest(con, lambda m: None)
    got = [r["kind"] for r in con.execute("SELECT kind FROM events WHERE agent = 'worker' AND kind IN ('UserPromptSubmit', 'Stop') ORDER BY id")]
    check("spool: the daemon applies them all, in the order they happened", n == len(kinds) and got == [k for k in kinds if k != "PostToolUse"], str(got))
    check("spool: and the file is gone", not spool.path().exists() and not spool.path().with_suffix(".ingesting").exists())
    check("spool: the agent's state is what the last event says", db.get_agent_state(con, "worker")["state"] == "IDLE")
    stamps = [r["ts"] for r in con.execute("SELECT ts FROM events WHERE agent = 'worker' AND kind = 'Stop'")]
    check("spool: each keeps the time the hook ran", all(abs(t - time.time()) < 60 for t in stamps) and len(stamps) == 2)
    con.close()


def test_a_spool_left_half_ingested_is_not_applied_twice(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    spool.append("worker", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "x"}, "req-1")
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "req-2")
    con = db.connect()
    work = spool.path().with_suffix(".ingesting")
    spool.path().rename(work)               # a daemon that died after taking the file, before removing it
    copy = work.read_text()
    spool.ingest(con, lambda m: None)
    work.write_text(copy)                   # the same lines again, as a crash after applying them would leave
    spool.ingest(con, lambda m: None)
    check("spool: replaying the same lines applies each request once",
          con.execute("SELECT COUNT(*) FROM events WHERE agent = 'worker' AND kind IN ('UserPromptSubmit', 'Stop')").fetchone()[0] == 2)
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "req-3")
    path = spool.path()
    path.write_text(path.read_text() + "not json at all\n")
    check("spool: a bad line is skipped and the rest applied", spool.ingest(con, lambda m: None) == 1)
    con.close()


def test_many_hooks_appending_at_once_never_mix_their_lines(home: Path) -> None:
    support.use_home(home)
    big = "x" * 20000

    def go(i):
        for j in range(10):
            spool.append("worker", "PostToolUse", {"hook_event_name": "PostToolUse", "pad": big}, f"r{i}-{j}")
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = spool.path().read_text().splitlines()
    ids = {json.loads(ln)["request_id"] for ln in lines}
    check("spool: 80 lines from 8 writers, every one whole", len(lines) == 80 and len(ids) == 80)


def test_hook_record_applies_a_hook_once_and_only_as_the_agent_itself(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    con.close()
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "backend"
    try:
        w = Wire()
        w.call("hello", {"client": "hook", "protocol": 1})
        body = {"agent": "backend", "kind": "UserPromptSubmit", "at": int(time.time()) - 5,
                "data": {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "go"}, "request_id": "h1"}
        first = w.call("hook.record", body)
        again = w.call("hook.record", body)
        other = w.call("hook.record", {**body, "agent": "frontend", "request_id": "h2"})
        bad = w.call("hook.record", {**body, "data": "no", "request_id": "h3"})
        w.close()
    finally:
        sessiontoken.agent_of = real
    con = db.connect()
    rows = con.execute("SELECT * FROM events WHERE agent = 'backend' AND kind = 'UserPromptSubmit'").fetchall()
    check("hook.record: the event is written once and the state follows it",
          first["result"] == {} and again["result"].get("replayed") is True and len(rows) == 1
          and db.get_agent_state(con, "backend")["state"] == "RUNNING", str((first, again)))
    check("hook.record: the event keeps the time the hook ran", abs(rows[0]["ts"] - (time.time() - 5)) < 30)
    check("hook.record: and no generic write event is added beside it",
          con.execute("SELECT COUNT(*) FROM events WHERE kind = 'rpc_write'").fetchone()[0] == 0)
    check("hook.record: an agent may not record as another (not_self)", _code(other) == "refused"
          and other["error"]["data"]["gate"] == "not_self", str(other))
    check("hook.record: bad data is bad_request", _code(bad) == "bad_request")
    con.close()


def test_a_hook_applied_but_never_answered_is_not_applied_again_by_its_retry(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    evmod.apply_hook(con, "backend", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "a"}, "crash-1", int(time.time()))
    db.set_agent_state(con, "backend", dmn_states.IDLE)         # the daemon died before it answered; the state has moved on
    evmod.apply_hook(con, "backend", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "a"}, "crash-1", int(time.time()))
    check("crash: the retry of an applied hook writes no second event and moves nothing",
          con.execute("SELECT COUNT(*) FROM events WHERE agent = 'backend' AND kind = 'UserPromptSubmit'").fetchone()[0] == 1
          and db.get_agent_state(con, "backend")["state"] == "IDLE")
    con.close()


def test_a_spool_replayed_after_a_crash_changes_nothing_a_second_time(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    con = db.connect()
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    for i, kind in enumerate(("PostToolUse", "Stop")):
        spool.append("worker", kind, {"hook_event_name": kind, "session_id": "s"}, f"seq-{i}")
    work = spool.path().with_suffix(".ingesting")
    copy = spool.path().read_text()
    spool.ingest(con, lambda m: None)
    check("replay: after the first pass the agent is idle", db.get_agent_state(con, "worker")["state"] == "IDLE")
    work.write_text(copy)           # the daemon died before it removed the file: the same two lines again
    spool.ingest(con, lambda m: None)
    check("replay: [PostToolUse, Stop] again leaves the agent IDLE, not RUNNING with nothing to end it",
          db.get_agent_state(con, "worker")["state"] == "IDLE", db.get_agent_state(con, "worker")["state"])
    con.close()


def test_every_kind_of_hook_line_is_a_no_op_the_second_time(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    lines = [("UserPromptSubmit", {"prompt": "go"}), ("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
             ("SessionStart", {"source": "startup", "session_id": "sess-1"}),
             ("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "rm x"}}),
             ("StopFailure", {"error": "rate_limit"}), ("Stop", {})]
    for i, (kind, extra) in enumerate(lines):
        raw = {"hook_event_name": kind, "session_id": "s", **extra}
        db.set_agent_state(con, "backend", dmn_states.QUEUED if kind == "PostToolUse" else dmn_states.IDLE)
        evmod.apply_hook(con, "backend", kind, raw, f"kind-{i}", int(time.time()))

        def snap():
            return (con.execute("SELECT COUNT(*) FROM events WHERE agent = 'backend'").fetchone()[0],
                    con.execute("SELECT COUNT(*) FROM session_starts WHERE agent = 'backend'").fetchone()[0],
                    waiting.read("backend"))
        after_first = snap()
        moved = dmn_states.PARKED
        db.set_agent_state(con, "backend", moved, wake_at=int(time.time()) + 600, detail="moved on by someone else")
        evmod.apply_hook(con, "backend", kind, raw, f"kind-{i}", int(time.time()))
        check(f"replay: {kind} applied twice writes no second event, bind or wait, and moves no state",
              snap() == after_first and db.get_agent_state(con, "backend")["state"] == moved,
              f"{snap()} vs {after_first} {db.get_agent_state(con, 'backend')['state']}")
    con.close()


def test_a_hook_that_dies_part_way_applies_nothing_and_its_retry_applies_all(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    real = evmod._mark_activity
    state = {"n": 0}

    def dies_once(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("daemon died here")
        return real(*a, **k)
    evmod._mark_activity = dies_once
    raw = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "x"}
    try:
        try:
            evmod.apply_hook(con, "backend", "UserPromptSubmit", raw, "die-1", int(time.time()))
        except RuntimeError:
            pass
        check("crash: nothing of the half-applied hook is left (no event, and its id is free)",
              con.execute("SELECT COUNT(*) FROM events WHERE kind = 'UserPromptSubmit'").fetchone()[0] == 0
              and con.execute("SELECT COUNT(*) FROM hook_applied WHERE request_id = 'die-1'").fetchone()[0] == 0)
        evmod.apply_hook(con, "backend", "UserPromptSubmit", raw, "die-1", int(time.time()))
    finally:
        evmod._mark_activity = real
    check("crash: the retry applies all of it, once", con.execute("SELECT COUNT(*) FROM events WHERE kind = 'UserPromptSubmit'").fetchone()[0] == 1
          and db.get_agent_state(con, "backend")["state"] == "RUNNING")
    con.close()


def test_a_line_the_database_could_not_take_stays_for_the_next_try_and_a_bad_line_is_skipped(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    for i in range(3):
        spool.append("worker", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": str(i)}, f"lock-{i}")
    con = db.connect()
    con.execute("PRAGMA busy_timeout = 100")
    other = sqlite3.connect(paths.db_file(), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")                        # a writer outside the daemon holds the write lock
    logs = []
    try:
        n = spool.ingest(con, logs.append)
    finally:
        other.execute("ROLLBACK")
        other.close()
    work = spool.path().with_suffix(".ingesting")
    check("locked: nothing is applied and nothing is lost: the lines are all still in the file",
          n == 0 and work.exists() and len(work.read_text().splitlines()) == 3, f"{n} {logs}")
    check("locked: it says why it stopped", any("could not take" in m and "locked" in m for m in logs), str(logs))
    n = spool.ingest(con, lambda m: None)
    got = con.execute("SELECT COUNT(*) FROM events WHERE kind = 'UserPromptSubmit'").fetchone()[0]
    check("locked: with the lock gone the next try applies all three, once, and removes the file",
          n == 3 and got == 3 and not work.exists() and not spool.path().exists(), f"{n} {got}")
    con.close()


def test_a_failure_part_way_leaves_only_the_lines_not_yet_applied(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    for i in range(4):
        spool.append("worker", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit"}, f"part-{i}")
    con = db.connect()
    from handline.daemon import rpc_hooks
    real = rpc_hooks.apply
    seen = []

    def fails_on_third(c, agent, kind, data, request_id, at):
        seen.append(request_id)
        if request_id == "part-2" and seen.count("part-2") == 1:
            raise sqlite3.OperationalError("database or disk is full")
        return real(c, agent, kind, data, request_id, at)
    rpc_hooks.apply = fails_on_third
    try:
        n = spool.ingest(con, lambda m: None)
        left = [json.loads(ln)["request_id"] for ln in spool.path().with_suffix(".ingesting").read_text().splitlines()]
        check("part way: two applied, and the failing line and the one after it are what is left", n == 2 and left == ["part-2", "part-3"], f"{n} {left}")
        n = spool.ingest(con, lambda m: None)
    finally:
        rpc_hooks.apply = real
    check("part way: the next try applies the rest, and the whole four are there once",
          n == 2 and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'UserPromptSubmit'").fetchone()[0] == 4)
    con.close()


def test_a_line_that_is_bad_is_skipped_and_the_good_ones_around_it_apply(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    spool.append("worker", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit"}, "good-1")
    with open(spool.path(), "a") as f:
        f.write("not json at all\n")
        f.write(json.dumps({"agent": "worker", "kind": "Stop"}) + "\n")                      # no data, no request id
        f.write(json.dumps({"agent": "worker", "kind": "Stop", "data": "no", "at": "x", "request_id": "bad-3"}) + "\n")
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "good-2")
    con = db.connect()
    logs = []
    n = spool.ingest(con, logs.append)
    rejected = [json.loads(ln) for ln in spool.path().with_name(spool.REJECTED).read_text().splitlines()]
    check("bad lines: the two good lines apply, the three bad ones are set aside whole, the spool file removed",
          n == 2 and not spool.path().with_suffix(".ingesting").exists() and len(rejected) == 3
          and [r["line"] for r in rejected][0] == "not json at all"
          and {r["error"] for r in rejected} == {"JSONDecodeError", "KeyError", "ValueError"}, f"{n} {logs} {rejected}")
    check("bad lines: and the log counts them", any("3 line(s) set aside" in m for m in logs), str(logs))
    con.close()


def test_an_unreadable_file_keeps_everything(home: Path) -> None:
    support.use_home(home)
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "unread-1")
    con = db.connect()
    real = Path.read_text

    def refuses(self, *a, **k):
        if self.name.endswith(".ingesting"):
            raise OSError("input/output error")
        return real(self, *a, **k)
    Path.read_text = refuses
    try:
        try:
            spool.ingest(con, lambda m: None)
            raised = False
        except OSError:
            raised = True
    finally:
        Path.read_text = real
    check("file: a file that cannot be read raises, and stays where it is",
          raised and spool.path().with_suffix(".ingesting").exists())
    con.close()


def test_an_applied_hook_id_is_kept_while_a_file_that_may_replay_it_is_waiting(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    old = int(time.time()) - 2 * 24 * 3600
    con.execute("INSERT INTO hook_applied(request_id, ts) VALUES('old-1', ?)", (old,))
    spool.path().with_suffix(".ingesting").write_text("")           # a crash-left file
    evmod.apply_hook(con, "backend", "Stop", {"hook_event_name": "Stop"}, "new-1", int(time.time()))
    check("keep: an id older than a day stays while a crash-left file waits",
          con.execute("SELECT COUNT(*) FROM hook_applied WHERE request_id = 'old-1'").fetchone()[0] == 1)
    spool.path().with_suffix(".ingesting").unlink()
    evmod.apply_hook(con, "backend", "Stop", {"hook_event_name": "Stop"}, "new-2", int(time.time()))
    check("keep: and goes when none does", con.execute("SELECT COUNT(*) FROM hook_applied WHERE request_id = 'old-1'").fetchone()[0] == 0)
    con.close()


def test_a_hook_stamped_in_the_future_counts_from_its_arrival_not_from_its_stamp(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    now = int(time.time())
    con = db.connect()
    real = time.time
    time.time = lambda: now - 10        # the future-stamped hook arrives ten seconds before the rest
    try:
        db.set_agent_state(con, "worker", dmn_states.RUNNING)
        _hook(con, "UserPromptSubmit", now - 20, "pre-1")
        _hook(con, "PostToolUse", now + 3600, "future-1")
    finally:
        time.time = real
    check("future: its stored time is its arrival, not its stamp",
          con.execute("SELECT at FROM hook_applied WHERE request_id = 'future-1'").fetchone()[0] == now - 10)
    _hook(con, "Stop", now - 1, "real-1")
    check("future: a real Stop stamped after it arrived still moves the agent",
          db.get_agent_state(con, "worker")["state"] == "IDLE", db.get_agent_state(con, "worker")["state"])
    db.set_agent_state(con, "worker", dmn_states.RUNNING)
    _hook(con, "Stop", now - 15, "early-1")
    check("future: one stamped before the future one arrived is late as before: it moves nothing",
          db.get_agent_state(con, "worker")["state"] == "RUNNING")
    con.close()


def test_pruning_keeps_each_agents_newest_applied_row(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    agents.register("other")
    now = int(time.time())
    old = now - 3 * 24 * 3600
    con = db.connect()
    for rid, agent, at in (("w-old", "worker", old - 50), ("w-new", "worker", old), ("o-only", "other", old - 5)):
        con.execute("INSERT INTO hook_applied(request_id, ts, agent, at) VALUES(?, ?, ?, ?)", (rid, old, agent, at))
    con.execute("INSERT INTO hook_applied(request_id, ts) VALUES('nobody', ?)", (old,))
    _hook(con, "Stop", now, "prune-1", agent="third")
    got = {r[0] for r in con.execute("SELECT request_id FROM hook_applied")}
    check("prune: the older row of an agent goes, its newest stays, and so does a quiet agent's only one",
          got == {"w-new", "o-only", "prune-1"}, got)
    con.close()


def test_hl_doctor_says_when_rejected_hook_lines_are_piling_up(home: Path) -> None:
    support.use_home(home)
    rejected = spool.path().with_name(spool.REJECTED)
    row = lambda: next((r for r in doctor.run().rows if "hook line" in r[1]), None)  # noqa: E731
    check("doctor: no rejected file, no note", row() is None)
    rejected.write_text("")
    check("doctor: an empty one, no note", row() is None)
    rejected.write_text(json.dumps({"rejected_at": 1760000000, "error": "KeyError", "message": "'kind'", "line": "{}"}) + "\n"
                        + json.dumps({"rejected_at": 1760000500, "error": "ValueError", "message": "x", "line": "y"}) + "\n")
    got = row()
    check("doctor: a warning with the count, the size and the oldest",
          got and got[0] == doctor.WARN and got[1].startswith("2 hook line(s)") and f"{rejected.stat().st_size} bytes" in got[1]
          and "oldest set aside" in got[1], got)
    check("doctor: and says what to do with them", got and "delete the file" in got[2] and str(rejected) in got[2], got)


def test_the_tick_applies_what_a_hook_left_while_the_daemon_ran(home: Path) -> None:
    support._daemon_home(home, 20)
    spool.append("backend", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "x"}, "tick-1")
    con = db.connect()
    from handline.daemon import evaluate
    evaluate.tick(con, lambda m: None)
    check("tick: a spooled hook event is applied by the next tick, not only at start",
          con.execute("SELECT COUNT(*) FROM events WHERE kind = 'UserPromptSubmit'").fetchone()[0] == 1 and not spool.path().exists())
    con.close()


def test_a_line_that_cannot_be_applied_is_set_aside_not_lost(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "rej-1")
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "rej-2")
    con = db.connect()
    from handline.daemon import rpc_hooks
    real = rpc_hooks.apply

    def bug_on_first(c, agent, kind, data, request_id, at):
        if request_id == "rej-1":
            raise AttributeError("a bug in applying")
        return real(c, agent, kind, data, request_id, at)
    rpc_hooks.apply = bug_on_first
    logs = []
    try:
        n = spool.ingest(con, logs.append)
    finally:
        rpc_hooks.apply = real
    side = [json.loads(ln) for ln in spool.path().with_name(spool.REJECTED).read_text().splitlines()]
    check("rejected: the line an apply bug failed on is in the rejected file, whole, with the error class and a time",
          n == 1 and len(side) == 1 and json.loads(side[0]["line"])["request_id"] == "rej-1" and side[0]["error"] == "AttributeError"
          and side[0]["rejected_at"] > 0, str(side))
    check("rejected: the next line still applied and the spool is gone", not spool.path().with_suffix(".ingesting").exists()
          and con.execute("SELECT COUNT(*) FROM events WHERE kind = 'Stop'").fetchone()[0] == 1)
    check("rejected: and the log says how many", any("1 line(s) set aside" in m for m in logs), str(logs))
    con.close()


def test_a_line_that_cannot_be_set_aside_either_stays_in_the_spool(home: Path) -> None:
    support.use_home(home)
    spool.append("worker", "Stop", {"hook_event_name": "Stop"}, "stay-1")
    con = db.connect()
    from handline.daemon import rpc_hooks
    real = rpc_hooks.apply
    rpc_hooks.apply = lambda *a: (_ for _ in ()).throw(AttributeError("bug"))
    spool.path().with_name(spool.REJECTED).mkdir()          # a folder where the file should go: it cannot be written
    try:
        spool.ingest(con, lambda m: None)
    finally:
        rpc_hooks.apply = real
    check("rejected: when the rejected file cannot be written the line stays in the spool",
          len(spool.path().with_suffix(".ingesting").read_text().splitlines()) == 1)
    con.close()


def _hook(con, kind, at, rid, agent="worker"):
    evmod.apply_hook(con, agent, kind, {"hook_event_name": kind, "session_id": "s"}, rid, at)


def test_an_older_event_arriving_late_is_recorded_and_moves_nothing(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    now = int(time.time())
    con = db.connect()
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    # a PostToolUse kept in the spool behind a lock (t=100), then a live Stop (t=200), then the tick's replay
    spool.append("worker", "PostToolUse", {"hook_event_name": "PostToolUse", "session_id": "s"}, "late-1", at=now - 100)
    con.execute("PRAGMA busy_timeout = 100")
    other = sqlite3.connect(paths.db_file(), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        spool.ingest(con, lambda m: None)
    finally:
        other.execute("ROLLBACK")
        other.close()
    db.set_agent_state(con, "worker", dmn_states.RUNNING)
    _hook(con, "Stop", now - 50, "late-live")
    check("late: the live Stop makes the agent IDLE", db.get_agent_state(con, "worker")["state"] == "IDLE")
    spool.ingest(con, lambda m: None)
    check("late: the older PostToolUse, replayed after it, leaves the agent IDLE", db.get_agent_state(con, "worker")["state"] == "IDLE",
          db.get_agent_state(con, "worker")["state"])
    check("late: and both events are recorded once", con.execute("SELECT COUNT(*) FROM events WHERE agent = 'worker' AND kind = 'Stop'").fetchone()[0] == 1
          and con.execute("SELECT COUNT(*) FROM hook_applied WHERE agent = 'worker'").fetchone()[0] == 2)
    # two live hooks arriving in the wrong order
    db.set_agent_state(con, "worker", dmn_states.RUNNING)
    _hook(con, "Stop", now - 10, "order-1")
    _hook(con, "PostToolUse", now - 20, "order-2")
    check("late: Stop (t=-10) then PostToolUse (t=-20) arriving in that order: IDLE", db.get_agent_state(con, "worker")["state"] == "IDLE")
    con.close()


def test_events_with_the_same_second_apply_in_the_order_they_arrive(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    now = int(time.time())
    con = db.connect()
    db.set_agent_state(con, "worker", dmn_states.IDLE)
    _hook(con, "Stop", now, "tie-1")
    _hook(con, "UserPromptSubmit", now, "tie-2")
    check("tie: a later arrival with the same second wins (the turn began after the stop was written)",
          db.get_agent_state(con, "worker")["state"] == "RUNNING")
    _hook(con, "Stop", now, "tie-3")
    check("tie: and so does the one after it", db.get_agent_state(con, "worker")["state"] == "IDLE")
    con.close()


def test_a_late_session_start_does_not_rebind_and_a_late_permission_request_leaves_no_wait(home: Path) -> None:
    support._daemon_home(home, 20)
    now = int(time.time())
    con = db.connect()
    _hook(con, "Stop", now, "lt-1", agent="backend")
    evmod.apply_hook(con, "backend", "PermissionRequest", {"hook_event_name": "PermissionRequest", "tool_name": "Bash",
                                                            "tool_input": {"command": "rm x"}}, "lt-2", now - 30)
    check("late: a permission request older than the newest event records no wait", waiting.read("backend") is None, str(waiting.read("backend")))
    check("late: but its event is there", con.execute("SELECT COUNT(*) FROM events WHERE kind = 'PermissionRequest'").fetchone()[0] == 1)
    con.close()


def test_hl_hook_still_works_from_an_agent_session_with_the_daemon_down(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    env = dict(os.environ, OCP_AGENT="worker", OCP_ACCOUNT="alpha", OCP_HOME=str(home), HANDLINE_HOME=str(home))
    r = subprocess.run([str(ROOT / "bin" / "hl"), "hook", "UserPromptSubmit"], capture_output=True, text=True, env=env,
                       input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "x"}))
    check("hl hook: exits 0 and answers {} with no daemon", r.returncode == 0 and r.stdout.strip() == "{}", r.stdout + r.stderr[-200:])
    check("hl hook: and the event is in the spool", spool.path().exists() and json.loads(spool.path().read_text().splitlines()[0])["kind"] == "UserPromptSubmit")


def main() -> int:
    def tests(td):
        run(lambda: test_ensure_daemon_asks_launchd_once_and_waits_for_the_socket(Path(td) / "e1"))
        run(lambda: test_a_service_that_is_installed_and_not_loaded_is_loaded_then_started(Path(td) / "e2a"))
        run(lambda: test_a_daemon_that_is_there_is_not_started_again(Path(td) / "e2"))
        run(lambda: test_ensure_daemon_failing_is_daemon_down_with_the_log_path_and_nothing_done(Path(td) / "e3"))
        run(lambda: test_the_legacy_send_starts_the_service_when_it_is_installed_and_says_none_when_it_cannot(Path(td) / "e4"))
        run(lambda: test_hooks_that_run_while_the_daemon_is_down_are_all_there_after(Path(td) / "e5"))
        run(lambda: test_a_spool_left_half_ingested_is_not_applied_twice(Path(td) / "e6"))
        run(lambda: test_many_hooks_appending_at_once_never_mix_their_lines(Path(td) / "e7"))
        run(lambda: test_hook_record_applies_a_hook_once_and_only_as_the_agent_itself(Path(td) / "e8"))
        run(lambda: test_a_hook_applied_but_never_answered_is_not_applied_again_by_its_retry(Path(td) / "e9"))
        run(lambda: test_a_spool_replayed_after_a_crash_changes_nothing_a_second_time(Path(td) / "e11"))
        run(lambda: test_every_kind_of_hook_line_is_a_no_op_the_second_time(Path(td) / "e12"))
        run(lambda: test_a_hook_that_dies_part_way_applies_nothing_and_its_retry_applies_all(Path(td) / "e13"))
        run(lambda: test_a_line_the_database_could_not_take_stays_for_the_next_try_and_a_bad_line_is_skipped(Path(td) / "e14"))
        run(lambda: test_a_failure_part_way_leaves_only_the_lines_not_yet_applied(Path(td) / "e15"))
        run(lambda: test_a_line_that_is_bad_is_skipped_and_the_good_ones_around_it_apply(Path(td) / "e16"))
        run(lambda: test_an_unreadable_file_keeps_everything(Path(td) / "e17"))
        run(lambda: test_an_applied_hook_id_is_kept_while_a_file_that_may_replay_it_is_waiting(Path(td) / "e18"))
        run(lambda: test_the_tick_applies_what_a_hook_left_while_the_daemon_ran(Path(td) / "e19"))
        run(lambda: test_a_line_that_cannot_be_applied_is_set_aside_not_lost(Path(td) / "e20"))
        run(lambda: test_a_line_that_cannot_be_set_aside_either_stays_in_the_spool(Path(td) / "e21"))
        run(lambda: test_an_older_event_arriving_late_is_recorded_and_moves_nothing(Path(td) / "e22"))
        run(lambda: test_events_with_the_same_second_apply_in_the_order_they_arrive(Path(td) / "e23"))
        run(lambda: test_a_late_session_start_does_not_rebind_and_a_late_permission_request_leaves_no_wait(Path(td) / "e24"))
        run(lambda: test_a_hook_stamped_in_the_future_counts_from_its_arrival_not_from_its_stamp(Path(td) / "e25"))
        run(lambda: test_pruning_keeps_each_agents_newest_applied_row(Path(td) / "e26"))
        run(lambda: test_hl_doctor_says_when_rejected_hook_lines_are_piling_up(Path(td) / "e27"))
        run(lambda: test_hl_hook_still_works_from_an_agent_session_with_the_daemon_down(Path(td) / "e10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
