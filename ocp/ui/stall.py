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
import faulthandler
import fcntl
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
_pid_fd: int | None = None      # held open for the lock


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
        _claim_pid_file(pid)
        _register_dump()
    except (OSError, ValueError, AttributeError):
        pass
    log(f"bridge up: pid {pid} term {os.environ.get('TERM_PROGRAM', os.environ.get('TERM', '?'))} "
        f"python {sys.version.split()[0]}")
    Watchdog(asyncio.get_running_loop()).start()
    return pid


def _claim_pid_file(pid: int) -> None:
    """Write `pid` into the pid file and hold a lock on it for as long as this process lives. The
    kernel drops the lock however the process dies (exit, SIGHUP, kill -9), so a file left behind
    is told from a live bridge by the lock, not by whether its pid is alive: pids get reused. A
    second bridge cannot take the lock; the file keeps naming the first."""
    global _pid_fd
    fd = os.open(pid_file(), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return
    os.ftruncate(fd, 0)
    os.write(fd, f"{pid}\n".encode())
    _pid_fd = fd


def started_at() -> float | None:
    """When the bridge named by the pid file took its lock (the file is written then)."""
    try:
        return pid_file().stat().st_mtime
    except OSError:
        return None


def close_bridge(pid: int, grace: float = 5.0) -> bool:
    """Ask the bridge `pid` to end (SIGTERM, then SIGKILL after `grace` s) and wait for its lock to go."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if running_pid() != pid:
                return True
            time.sleep(0.05)
    return running_pid() != pid


def running_pid() -> int | None:
    """The pid of the bridge that is up now: the pid file names it and something holds its lock."""
    try:
        fd = os.open(pid_file(), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return int(os.read(fd, 32).decode().strip() or 0) or None      # held: a bridge is up
        return None                                                      # free: left behind
    except ValueError:
        return None
    finally:
        os.close(fd)
