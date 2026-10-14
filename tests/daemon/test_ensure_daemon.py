"""Tests: finding the daemon (`client.ensure_daemon`), and what a hook does when the daemon is not there
(the spool, and `hook.record` that applies the same events when it is).

launchctl is the repo's one replaceable name, `service.run_launchctl`; no test here reaches the real launchd.

Run on its own: python3 tests/daemon/test_ensure_daemon.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import hookspool  # noqa: E402
from handline.infra import daemonclient as client
from handline.infra import paths, service, sessiontoken  # noqa: E402
from handline.session import waiting  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))


class FakeLaunchd:
    """Stands in for run_launchctl and for connect: `kickstart` makes the daemon answer after `after` tries."""

    def __init__(self, installed=True, kickstart_rc=0, after=3, label="dev.handline.test") -> None:
        self.calls, self.installed, self.rc, self.after, self.tries, self.up = [], installed, kickstart_rc, after, 0, False
        self.label_was = os.environ.pop("OCP_SERVICE_LABEL", None)
        if label:                       # a test home has a service only under a label of its own
            os.environ["OCP_SERVICE_LABEL"] = label
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
        os.environ.pop("OCP_SERVICE_LABEL", None)
        if self.label_was is not None:
            os.environ["OCP_SERVICE_LABEL"] = self.label_was


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
          f.calls == [["kickstart", f"{service.domain_target()}/dev.handline.test"]], str(f.calls))
    check("ensure: and it retried while launchd started it", f.tries == 4, str(f.tries))


def test_a_home_that_is_not_the_live_one_never_asks_launchd_for_the_live_service(home: Path) -> None:
    support.use_home(home)
    f = FakeLaunchd(label=None)
    try:
        try:
            client.ensure_daemon()
            why = None
        except client.DaemonDown as e:
            why = str(e)
        check("ensure: a test home with the live plist installed is told it is not the live home",
              why is not None and "is not the live home" in why and str(home) in why, str(why))
        check("ensure: and launchd is asked nothing", f.calls == [], str(f.calls))
        other, real_live = (home / "other").resolve(), paths.live_homes
        paths.live_homes = lambda: [other]
        try:
            check("serves: the home it is given is the one judged, here the live one asked about from a home that is not",
                  service.serves_this_home(other) and not service.serves_this_home())
        finally:
            paths.live_homes = real_live
        os.environ["OCP_SERVICE_LABEL"] = "dev.handline.test"
        check("ensure: under a label of its own the same home has a service to ask for", service.serves_this_home())
    finally:
        f.restore()


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
    check("ensure: it is bootstrapped into the user's domain from the installed plist, never rewritten", f.calls[1] == ["bootstrap", service.domain_target(), str(service.plist_path().with_name("dev.handline.test.plist"))], str(f.calls[1]))
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
    f = FakeLaunchd(installed=None)
    try:
        try:
            client.ensure_daemon()
            err = None
        except client.DaemonDown as e:
            err = e
    finally:
        f.restore()
    check("ensure: a sandbox that cannot see the plist falls through at once, asking launchd nothing", err is not None and f.calls == [], str(f.calls))
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


def test_many_hooks_appending_at_once_never_mix_their_lines(home: Path) -> None:
    support.use_home(home)
    big = "x" * 20000

    def go(i):
        for j in range(10):
            hookspool.append("worker", "PostToolUse", {"hook_event_name": "PostToolUse", "pad": big}, f"r{i}-{j}")
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = hookspool.path().read_text().splitlines()
    ids = {json.loads(ln)["request_id"] for ln in lines}
    check("spool: 80 lines from 8 writers, every one whole", len(lines) == 80 and len(ids) == 80)


def test_hl_hook_still_works_from_an_agent_session_with_the_daemon_down(home: Path) -> None:
    support.use_home(home)
    agents.register("worker")
    env = dict(os.environ, OCP_AGENT="worker", OCP_ACCOUNT="alpha", OCP_HOME=str(home), HANDLINE_HOME=str(home))
    r = subprocess.run([str(ROOT / "bin" / "hl"), "hook", "UserPromptSubmit"], capture_output=True, text=True, env=env,
                       input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "x"}))
    check("hl hook: exits 0 and answers {} with no daemon", r.returncode == 0 and r.stdout.strip() == "{}", r.stdout + r.stderr[-200:])
    check("hl hook: and the event is in the spool", hookspool.path().exists() and json.loads(hookspool.path().read_text().splitlines()[0])["kind"] == "UserPromptSubmit")


def main() -> int:
    def tests(td):
        run(lambda: test_ensure_daemon_asks_launchd_once_and_waits_for_the_socket(Path(td) / "e1"))
        run(lambda: test_a_home_that_is_not_the_live_one_never_asks_launchd_for_the_live_service(Path(td) / "notlive"))
        run(lambda: test_a_service_that_is_installed_and_not_loaded_is_loaded_then_started(Path(td) / "e2a"))
        run(lambda: test_a_daemon_that_is_there_is_not_started_again(Path(td) / "e2"))
        run(lambda: test_ensure_daemon_failing_is_daemon_down_with_the_log_path_and_nothing_done(Path(td) / "e3"))
        run(lambda: test_the_legacy_send_starts_the_service_when_it_is_installed_and_says_none_when_it_cannot(Path(td) / "e4"))
        run(lambda: test_many_hooks_appending_at_once_never_mix_their_lines(Path(td) / "e7"))
        run(lambda: test_hl_hook_still_works_from_an_agent_session_with_the_daemon_down(Path(td) / "e10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
