"""launchd supervision for the daemon (`ocp service ...`).

A daemon that only runs while a human keeps a terminal open is not a
supervisor, it is a foreground script (README: "not dying overnight without a
human"). This installs it as a LaunchAgent so launchd starts it at login and
restarts it after a crash, and gives `ocp ps` / `ocp doctor` a way to say which
of the two is supervising (daemon.running_elsewhere() names it; the daemon lock
is what stops both of them running at once).

launchctl calls go through `run_launchctl` alone, so tests can replace it and
never touch the real launchd. Building and reading the plist needs no
launchctl call at all - it is dict in, plistlib out, parsed back the same way.
"""
from __future__ import annotations

import gzip
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

from . import paths

LABEL = "dev.orcapipe.daemon"

# launchd restarts a crashing job as fast as it can fail, unless told
# otherwise (scout, request #14: colima's bare KeepAlive=true leaked
# thousands of children this way). ThrottleInterval bounds that rate. It is
# not the SuccessfulExit-gated form some services use, because that only
# restarts after a *clean* exit - exactly wrong for a daemon whose entire
# purpose is to come back from a crash.
THROTTLE_INTERVAL_S = 30

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5


# ---------------------------------------------------------------- paths
def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_dir(home: Path | None = None) -> Path:
    return (home or paths.home()) / "logs"


def out_log(home: Path | None = None) -> Path:
    return log_dir(home) / "daemon.out.log"


def err_log(home: Path | None = None) -> Path:
    return log_dir(home) / "daemon.err.log"


def ocp_executable() -> str:
    """Absolute path to the ocp entrypoint.

    A launchd job inherits almost none of the login environment, so PATH is
    not reliable enough to find a symlink someone installed there once; the
    repo's own bin/ocp is what we know exists.
    """
    return str(paths.repo_root() / "bin" / "ocp")


# ---------------------------------------------------------------- plist
def build_plist(home: Path | None = None) -> dict:
    home = home or paths.home()
    return {
        "Label": LABEL,
        # bin/ocp's own "#!/usr/bin/env python3" shebang resolves the
        # interpreter through PATH at run time, not sys.executable pinned at
        # install time - a python upgrade or a removed venv would otherwise
        # leave this pointed at an interpreter that no longer exists, and the
        # failure would be a job that silently never starts.
        "ProgramArguments": [ocp_executable(), "daemon"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_INTERVAL_S,
        "StandardOutPath": str(out_log(home)),
        "StandardErrorPath": str(err_log(home)),
        "WorkingDirectory": str(paths.repo_root()),
        # launchd jobs start with a nearly empty environment - without this
        # block the daemon cannot find claude/orca (PATH), resolve ~ for
        # anything that uses it (HOME), or find the right ~/.orcapipe (OCP_HOME).
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "OCP_HOME": str(home),
        },
    }


def write_plist(path: Path | None = None, home: Path | None = None) -> Path:
    path = path or plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_plist(home), f)
    return path


def read_plist(path: Path | None = None) -> dict:
    path = path or plist_path()
    with open(path, "rb") as f:
        return plistlib.load(f)


# ---------------------------------------------------------------- launchctl
def run_launchctl(args: list[str]) -> subprocess.CompletedProcess:
    """The only place that shells out to launchctl - tests replace this name."""
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)


def domain_target() -> str:
    return f"gui/{os.getuid()}"


def service_target() -> str:
    return f"{domain_target()}/{LABEL}"


def install(home: Path | None = None) -> tuple[bool, str]:
    home = home or paths.home()
    log_dir(home).mkdir(parents=True, exist_ok=True)
    was_loaded = status()["loaded"]
    prior = plist_path().read_bytes() if plist_path().exists() else None
    path = write_plist(home=home)
    # bootstrap fails if the label is already loaded; bootout first so
    # `install` is idempotent (reinstalling after editing config, say).
    if was_loaded:
        run_launchctl(["bootout", service_target()])
    res = run_launchctl(["bootstrap", domain_target(), str(path)])
    if res.returncode != 0:
        # A daemon that was supervised and running before this call must not
        # end up unsupervised because the new plist failed to load - put the
        # old one back and try to restore what was working.
        if was_loaded and prior is not None:
            path.write_bytes(prior)
            run_launchctl(["bootstrap", domain_target(), str(path)])
        return False, (res.stderr or res.stdout or "launchctl bootstrap failed").strip()
    run_launchctl(["enable", service_target()])
    return True, f"installed {path}, label {LABEL}"


def uninstall() -> tuple[bool, str]:
    path = plist_path()
    run_launchctl(["bootout", service_target()])
    existed = path.exists()
    path.unlink(missing_ok=True)
    return True, (f"removed {path}" if existed else f"{LABEL} was not installed")


def status() -> dict:
    """installed: the plist exists. loaded: launchd knows the label. pid/last_exit
    come from `launchctl print`, when it is loaded."""
    installed = plist_path().exists()
    result = {"installed": installed, "loaded": False, "pid": None, "last_exit_code": None}
    if not installed:
        return result
    res = run_launchctl(["print", service_target()])
    if res.returncode != 0:
        return result
    result["loaded"] = True
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("pid = "):
            try:
                result["pid"] = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("last exit code = "):
            result["last_exit_code"] = line.split("=", 1)[1].strip()
    return result


def supervisor_of(pid: int | None) -> str:
    """Which process is watching the daemon at `pid` (8.4: only one may run).

    Used by `ocp ps` so a human can tell a launchd-run daemon apart from one
    they started by hand in a terminal - the two must never both be ticking.
    """
    st = status()
    if pid is None:
        if st["installed"] and not st["loaded"]:
            return "not running (service installed but not loaded - see `ocp service status`)"
        return "not running"
    if st["loaded"] and st["pid"] == pid:
        return f"launchd ({LABEL})"
    return "hand-started (not supervised by launchd)"


# ---------------------------------------------------------------- crash loop
# Bytes of tail read for crash-loop detection, bounded so this stays cheap at
# the rotation threshold (LOG_MAX_BYTES) instead of reading the whole file.
TAIL_SCAN_BYTES = 200_000


def _tail_lines(path: Path, max_bytes: int = TAIL_SCAN_BYTES) -> list[str]:
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # the seek point likely lands mid-line
    return lines


def crash_loop_detected(home: Path | None = None, window_s: int = 300, threshold: int = 4) -> bool:
    """A supervisor that respawns faster than it can do useful work is not
    supervising, it is thrashing. There is no launchd-exposed restart counter
    to read (scout, request #14), so this counts our own startup line in the
    log instead - a real crash loop leaves several `daemon starting` lines
    (whether it got to reconcile or exited straight away because another
    daemon already held the lock - both are launchd restarting us) within
    one `window_s` window.
    """
    p = out_log(home)
    if not p.exists():
        return False
    now = time.localtime()
    hits = 0
    for line in _tail_lines(p):
        if "daemon starting" not in line:
            continue
        if not line.startswith("["):
            continue
        try:
            ts = time.strptime(line[1:9], "%H:%M:%S")
        except ValueError:
            continue
        # Only a same-day, HH:MM:SS clock is available; a window this short
        # (minutes) never spans midnight in practice, and a false negative
        # there is safe - crash-loop restarts keep happening on the next tick.
        delta = (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec) - \
                (ts.tm_hour * 3600 + ts.tm_min * 60 + ts.tm_sec)
        if 0 <= delta <= window_s:
            hits += 1
    return hits >= threshold


# ---------------------------------------------------------------- log rotation
def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES, backups: int = LOG_BACKUPS) -> bool:
    """Copy-truncate rotation.

    launchd holds StandardOutPath/StandardErrorPath open for the job's whole
    life with O_APPEND, so renaming the file out from under it would not move
    where the next write lands - it would keep writing to the renamed inode.
    O_APPEND writes re-measure the file's size on every call, so truncating it
    in place is enough to make the daemon's next line land back at offset 0,
    the same trick logrotate's `copytruncate` uses - including its caveat:
    whatever is written between the copy and the truncate is lost, since
    there is no way to pause the writer for a plain file. Not atomic.
    """
    if not path.exists() or path.stat().st_size < max_bytes:
        return False
    oldest = path.with_name(f"{path.name}.{backups}.gz")
    oldest.unlink(missing_ok=True)
    for n in range(backups - 1, 0, -1):
        src = path.with_name(f"{path.name}.{n}.gz")
        if src.exists():
            src.rename(path.with_name(f"{path.name}.{n + 1}.gz"))
    with open(path, "rb") as f_in, gzip.open(path.with_name(f"{path.name}.1.gz"), "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    fd = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(fd, 0)
    finally:
        os.close(fd)
    return True


def rotate_logs(home: Path | None = None) -> None:
    rotate_log(out_log(home))
    rotate_log(err_log(home))


# ---------------------------------------------------------------- logs
def tail(path: Path, lines: int = 60) -> str:
    if not path.exists():
        return f"(no log at {path})"
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
