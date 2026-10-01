"""Why the bridge froze, written down while it happens (T788). Always on, nothing drawn.

`bridge-stall.log` in the logs folder holds three kinds of line, each stamped:
  - a watchdog thread pings the event loop; when it has not answered for `STALL_S` it writes the
    stack of every thread (the loop's first) and again every second while it stays blocked, then
    one line when it answers;
  - `kill -USR1 <pid>` writes the stacks on demand (the bridge's pid is in its title bar and in
    `ocp bridge --pid`);
  - breadcrumbs (`crumb`, `timed`, `snapshot`) from the places worth knowing the time of.
"""
from __future__ import annotations

import asyncio
import atexit
import faulthandler
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from ..infra import paths

STALL_S = 0.5        # the loop silent for this long is a stall
PING_S = 0.1
MAX_BYTES = 2_000_000       # past this the log is moved to `.1` and starts again

_fault_file = None


def log_file() -> Path:
    return paths.home() / "logs" / "bridge-stall.log"


def pid_file() -> Path:
    return paths.home() / "logs" / "bridge.pid"


def log(line: str) -> None:
    """One stamped line, however many lines `line` has; a failing disk never reaches the bridge."""
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))
            if _fault_file is not None:
                _register_dump()        # the USR1 dump follows the new file, not the rotated one
        with path.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {line.rstrip()}\n")
    except OSError:
        pass


def crumb(what: str) -> None:
    log(f"· {what}")


@contextmanager
def timed(what: str):
    """Log how long the block took, in ms; a block that raised says so."""
    start = time.perf_counter()
    try:
        yield
    except BaseException:
        crumb(f"{what}: raised after {(time.perf_counter() - start) * 1000:.1f} ms")
        raise
    crumb(f"{what}: {(time.perf_counter() - start) * 1000:.1f} ms")


def snapshot(app, why: str) -> None:
    """The screen stack, what has focus, and how much is mounted and ticking: the numbers that grow
    over a long session."""
    try:
        stack = list(app.screen_stack)
        widgets = sum(len(list(s.walk_children(with_self=True))) for s in stack)
        timers = sum(len(w._timers) for s in stack for w in s.walk_children(with_self=True)) + len(app._timers)
        crumb(f"{why}: stack {[type(s).__name__ for s in stack]} focus {app.focused!r} "
              f"widgets {widgets} timers {timers} threads {threading.active_count()}")
    except Exception as e:  # noqa: BLE001 - a diagnostic never breaks what it watches
        crumb(f"{why}: snapshot failed: {e!r}")


def stacks(loop: int | None) -> str:
    """The stack of every thread, the event loop's thread (`loop`) first."""
    names = {t.ident: t.name for t in threading.enumerate()}
    frames = sorted(sys._current_frames().items(), key=lambda kv: kv[0] != loop)
    return "\n".join(f"--- thread {names.get(ident, '?')} ({ident}){' [event loop]' if ident == loop else ''}\n"
                     + "".join(traceback.format_stack(frame)) for ident, frame in frames)


class Watchdog(threading.Thread):
    """Pings the event loop from outside it. The loop answers by stamping `last`; the thread reads
    the stamp, so a blocked loop is seen by something that is not blocked."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(name="bridge-watchdog", daemon=True)
        self.loop = loop
        self.loop_thread = threading.get_ident()        # made from the loop's own thread
        self.last = time.monotonic()
        self.stalled_since: float | None = None
        self.dumped_at = 0.0
        self.stop = threading.Event()

    def _pong(self) -> None:
        self.last = time.monotonic()

    def check(self, now: float) -> None:
        """One look: start, continue or end a stall."""
        silent = now - self.last
        if silent > STALL_S:
            if self.stalled_since is None:
                self.stalled_since = self.last
            if now - self.dumped_at >= 1.0:
                self.dumped_at = now
                log(f"STALL: event loop silent for {silent:.1f} s\n{stacks(self.loop_thread)}")
        elif self.stalled_since is not None:
            log(f"RECOVERED: the event loop was blocked for {now - self.stalled_since:.1f} s")
            self.stalled_since = None

    def run(self) -> None:
        while not self.stop.wait(PING_S):
            try:
                self.loop.call_soon_threadsafe(self._pong)
            except RuntimeError:        # the loop is closed: the bridge is going down
                return
            self.check(time.monotonic())


def _register_dump() -> None:
    """USR1 writes every thread's stack to the log, whichever file that is now."""
    global _fault_file
    old, _fault_file = _fault_file, log_file().open("a")
    faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True)
    if old is not None:
        old.close()


def install(app) -> int:
    """Start watching the running app (from its event-loop thread): the watchdog, the USR1 dump and
    the pid file. Returns the pid."""
    pid = os.getpid()
    try:
        log_file().parent.mkdir(parents=True, exist_ok=True)
        pid_file().write_text(f"{pid}\n")
        _register_dump()
    except (OSError, ValueError, AttributeError):
        pass
    atexit.register(forget_pid, pid)
    log(f"bridge up: pid {pid} term {os.environ.get('TERM_PROGRAM', os.environ.get('TERM', '?'))} "
        f"python {sys.version.split()[0]}")
    Watchdog(asyncio.get_running_loop()).start()
    return pid


def forget_pid(pid: int) -> None:
    """Remove the pid file when the bridge goes, unless a later bridge has written its own."""
    try:
        if pid_file().read_text().strip() == str(pid):
            pid_file().unlink()
    except OSError:
        pass


def running_pid() -> int | None:
    """The pid of a bridge that is up now, if the pid file names a live process."""
    try:
        pid = int(pid_file().read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid
