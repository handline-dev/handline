"""The loop that runs ticks until stopped."""
from __future__ import annotations

import signal
import time

from .. import db
from ..infra import lock
from ..infra import service
from ..infra import textsafe
from ..session import triggers

from . import devmode, evaluate, locks, messaging, prompts, reconciler, socket_server


# ---------------------------------------------------------------- loop
def run(once: bool = False, verbose: bool = True, max_ticks: int | None = None) -> int:
    """`max_ticks` counts this process's own ticks. A dev-mode restart
    (task #64) execs a fresh process, so a run that restarts mid-way starts
    its count over at 0 and runs past `max_ticks` overall."""
    def log(msg: str) -> None:
        # Every line here can carry text an agent's own screen wrote - a
        # dialog's title among it - and this is stdout, tailed raw by whoever
        # watches the daemon log; the same hole auditor found in a request
        # body, at the sink it reaches most often.
        if verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {textsafe.oneline(msg)}", flush=True)

    # Set inside the lock below, read after it releases - restarting (task
    # #64) happens only once the lock and the db connection are given up the
    # same way a clean exit always gives them up.
    restarting = False

    # The lock decides who supervises, and records who: a daemon started by
    # hand, or orphaned when launchd reloaded the job under it, is still a
    # second supervisor.
    with lock.singleton(locks.DAEMON_LOCK) as only:
        if not only:
            other = locks.running_elsewhere()
            named = f" (pid {other})" if other else ""
            # Through the same logger as every other startup line, and sharing
            # its "daemon starting" wording, so a launchd job that respawns
            # into this refusal every ThrottleInterval is still visible to
            # crash_loop_detected - a silent `print` here is what a
            # launchd-driven crash loop looks like from the outside: healthy on
            # every check, dead the whole time.
            log(f"daemon starting - already running{named}, exiting")
            return 1
        stopping = {"now": False}
        # Separate from `stopping`: a restart also sets that flag to break
        # the loop, and `signalled` is what tells the two apart afterward -
        # a real SIGTERM/SIGINT must win over a restart whose 30s import
        # check (bin/ocp --help, subprocess.run's own timeout) was still
        # running when the signal arrived.
        signalled = {"now": False}

        def stop(signum, _frame):
            stopping["now"] = True
            signalled["now"] = True
            log(f"signal {signum} - stopping after this tick")

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, stop)
            except ValueError:
                pass  # not on the main thread

        con = db.connect()
        samples: dict = {}
        dev_state: dict = {"fp": None, "warned": None}
        srv = socket_server.SocketServer(log)
        try:
            srv.start()
        except OSError as e:
            # Same reasoning as rotate_logs and the dev-mode check below: a
            # daemon that cannot make its socket still has agents to
            # supervise, and a tick that never runs loses far more than one
            # that runs with no one able to reach it over the socket.
            log(f"daemon socket: could not start ({e}) - continuing without it")
        try:
            if not once and not max_ticks:      # a bounded run (a test, --once) has no screens to watch
                prompts.start(stopping, log)
            log("daemon starting - reconciling")
            reconciler.reconcile(con, log)
            # #2117: whatever a message could not be typed at before the last
            # restart is still owed - try it now, for any agent already free
            # to hear it, rather than waiting out its next tick.
            messaging.migrate_old_messages(con, log)
            messaging.reload_pending(con, log)

            ticks = 0
            while not stopping["now"]:
                t = evaluate.tick(con, log, samples=samples)
                ticks += 1
                # A supervisor that runs for weeks must not fill the disk.
                # Cheap when under threshold: two stat() calls and nothing
                # else. Caught separately so a rotation failure (e.g. a full
                # disk) degrades logging rather than taking the whole daemon
                # down with it.
                try:
                    service.rotate_logs()
                except OSError as e:
                    log(f"log rotation failed: {e}")
                # Same reasoning as rotate_logs above: a tree mid-checkout
                # (a merge in the main checkout unlinks and rewrites files
                # while this is exactly what runs) must degrade dev mode,
                # not the daemon.
                try:
                    should_restart = devmode._dev_mode_should_restart(dev_state, log)
                except Exception as e:  # noqa: BLE001
                    log(f"dev-mode check failed: {e}")
                    should_restart = False
                if should_restart:
                    if signalled["now"]:
                        log("dev-mode: restart skipped - a stop signal arrived during the import check")
                    else:
                        stopping["now"] = True
                        restarting = True
                if once or (max_ticks and ticks >= max_ticks):
                    break
                if not stopping["now"]:
                    time.sleep(triggers.POLL_S)
            log(f"stopped after {ticks} tick(s)")
        finally:
            srv.stop()
            con.close()

    if restarting:
        devmode._dev_mode_restart()  # execs; does not return
    return 0
