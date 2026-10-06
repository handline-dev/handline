"""Tests: finding the daemon (`client.ensure_daemon`), and what a hook does when the daemon is not there
(the spool, and `hook.record` that applies the same events when it is).

launchctl is the repo's one replaceable name, `service.run_launchctl`; no test here reaches the real launchd.

Run on its own: python3 tests/daemon/test_ensure_daemon.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import client, rpc, spool  # noqa: E402
from handline.infra import service, sessiontoken  # noqa: E402

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
        return subprocess.CompletedProcess(["launchctl", *args], self.rc, "", "Could not find service" if self.rc else "")

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
        run(lambda: test_a_daemon_that_is_there_is_not_started_again(Path(td) / "e2"))
        run(lambda: test_ensure_daemon_failing_is_daemon_down_with_the_log_path_and_nothing_done(Path(td) / "e3"))
        run(lambda: test_the_legacy_send_starts_the_service_when_it_is_installed_and_says_none_when_it_cannot(Path(td) / "e4"))
        run(lambda: test_hooks_that_run_while_the_daemon_is_down_are_all_there_after(Path(td) / "e5"))
        run(lambda: test_a_spool_left_half_ingested_is_not_applied_twice(Path(td) / "e6"))
        run(lambda: test_many_hooks_appending_at_once_never_mix_their_lines(Path(td) / "e7"))
        run(lambda: test_hook_record_applies_a_hook_once_and_only_as_the_agent_itself(Path(td) / "e8"))
        run(lambda: test_a_hook_applied_but_never_answered_is_not_applied_again_by_its_retry(Path(td) / "e9"))
        run(lambda: test_hl_hook_still_works_from_an_agent_session_with_the_daemon_down(Path(td) / "e10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
