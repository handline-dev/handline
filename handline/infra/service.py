"""launchd supervision for the daemon (`hl service ...`).

A daemon that only runs while a human keeps a terminal open is not a
supervisor, it is a foreground script (README: "not dying overnight without a
human"). This installs it as a LaunchAgent so launchd starts it at login and
restarts it after a crash, and gives `hl ps` / `hl doctor` a way to say which
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
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import names, paths
from .yamlcompat import ConfigError, load as load_yaml

LABEL = "dev.orcapipe.daemon"


def label() -> str:
    """The launchd label: LABEL, or OCP_SERVICE_LABEL, which a rehearsal on a scratch home sets so its job and plist are not the live service's."""
    return names.getenv("OCP_SERVICE_LABEL") or LABEL

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
# The daemon raises its own soft limit to the same (daemon.socket_server.FD_SOFT_LIMIT; a test holds them equal).
FD_SOFT_LIMIT = 4096


def plist_path() -> Path:
    return paths.user_home() / "Library" / "LaunchAgents" / f"{label()}.plist"


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
    repo's own bin/hl is what we know exists.
    """
    return str(paths.repo_root() / "bin" / "hl")


# ---------------------------------------------------------------- plist
IMPL_KEY = "daemon_impl"        # config.yaml: "python" (the default) or "go"
IMPLS = ("python", "go")


class ServiceError(Exception):
    """The plist cannot be built as asked; nothing was written."""


def daemon_impl() -> str:
    """Which daemon the service runs: the config.yaml `daemon_impl` word, python when it is absent or not one of IMPLS."""
    try:
        cfg = load_yaml(paths.config_file().read_text())
    except (OSError, ConfigError):
        return "python"
    v = cfg.get(IMPL_KEY) if isinstance(cfg, dict) else None
    return v if v in IMPLS else "python"


def go_executable() -> Path:
    """The Go daemon tools/build-go.sh builds."""
    return paths.repo_root() / "go" / "handlined"


def land_repo() -> str:
    """The checkout `hl land` merges into: OCP_LAND_REPO, else the checkout the daemon runs from (what lands.py reads)."""
    return names.getenv("OCP_LAND_REPO") or str(paths.repo_root())


def go_arguments(home: Path, allow_live_home: bool = False) -> list[str]:
    """The Go daemon's command line with the production flags. A home that is a live one (`paths.live_homes`) is the real
    install's: refused unless the caller said so, so a rehearsal can never land on it by a stray setting."""
    exe = go_executable()
    if not exe.exists():
        raise ServiceError(f"{exe} is not built (tools/build-go.sh)")
    claude = shutil.which("claude")
    if claude is None:
        raise ServiceError("no claude binary on PATH for -structured-cli")
    live = home.resolve() in paths.live_homes()
    if not live and label() == LABEL:
        raise ServiceError(f"{home} is not the live home, and the live service's label would be replaced: set OCP_SERVICE_LABEL for a rehearsal")
    if live and not allow_live_home:
        raise ServiceError(f"{home} is the live home: the Go daemon is installed there only with --allow-live-home")
    return [str(exe), "-allow-real-home" if live else "-home", *([] if live else [str(home)]),
            "-structured-cli", claude, "-structured-tick", "-terminal-tick", "-cutover", "-land-repo", land_repo()]


def _path_with_python3() -> str:
    """PATH for the job, with the directory of python3 in it: the land verbs run tools/localrefs.py."""
    path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    py = shutil.which("python3")
    if py and str(Path(py).parent) not in path.split(":"):
        path += ":" + str(Path(py).parent)
    return path


def build_plist(home: Path | None = None, impl: str | None = None, allow_live_home: bool = False) -> dict:
    home = home or paths.home()
    impl = impl or daemon_impl()
    # bin/ocp's own bash wrapper picks the interpreter at run time (the
    # venv's python3 if it is ready, PATH's otherwise), not sys.executable
    # pinned at install time - a python upgrade or a removed venv would
    # otherwise leave this pointed at an interpreter that no longer
    # exists, and the failure would be a job that silently never starts.
    program = go_arguments(home, allow_live_home) if impl == "go" else [ocp_executable(), "daemon"]
    return {
        "Label": label(),
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_INTERVAL_S,
        # launchd's soft limit is 256 descriptors, as many as the daemon's connection cap alone.
        "SoftResourceLimits": {"NumberOfFiles": FD_SOFT_LIMIT},
        "StandardOutPath": str(out_log(home)),
        "StandardErrorPath": str(err_log(home)),
        "WorkingDirectory": str(paths.repo_root()),
        # launchd jobs start with a nearly empty environment - without this
        # block the daemon cannot find claude/orca (PATH), resolve ~ for
        # anything that uses it (HOME), or find the right ~/.orcapipe (OCP_HOME).
        "EnvironmentVariables": {
            "PATH": _path_with_python3() if impl == "go" else os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "OCP_HOME": str(home),
            **({"OCP_LAND_REPO": land_repo()} if impl == "go" else {}),
        },
    }


def write_plist(path: Path | None = None, home: Path | None = None, allow_live_home: bool = False) -> Path:
    path = path or plist_path()
    plist = build_plist(home, allow_live_home=allow_live_home)       # built first: a refusal leaves the old file alone
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    return path


def read_plist(path: Path | None = None) -> dict:
    path = path or plist_path()
    with open(path, "rb") as f:
        return plistlib.load(f)


def is_installed_checkout() -> bool:
    """Whether this code is the checkout the launchd service runs, by the program path in its plist. True
    when no service is installed (a first setup has nothing to protect); False when the plist is there
    and names another checkout, or cannot be read: a second clone, a worktree and a sandbox that cannot
    see the plist all refuse."""
    try:
        program = Path(read_plist()["ProgramArguments"][0])
    except FileNotFoundError:
        return True
    except Exception:  # noqa: BLE001 - unreadable, or not a plist this code wrote: not the installed one
        return False
    return program.resolve().parent.parent == paths.repo_root().resolve()


# ---------------------------------------------------------------- launchctl
def run_launchctl(args: list[str]) -> subprocess.CompletedProcess:
    """The only place that shells out to launchctl - tests replace this name."""
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)


def domain_target() -> str:
    return f"gui/{os.getuid()}"


def service_target() -> str:
    return f"{domain_target()}/{label()}"


# launchd sends the old job SIGTERM on bootout and SIGKILL after its ExitTimeOut (20s by default).
UNLOAD_WAIT_S = 30.0


def _await_unloaded(timeout: float | None = None) -> bool:
    """Whether launchd has let go of the label within `timeout`. bootout returns while the old daemon
    is still exiting and launchd keeps the label until it has: a bootstrap in that window fails with
    `5: Input/output error`, and so does the restore of the old plist, leaving no job loaded."""
    deadline = time.monotonic() + (UNLOAD_WAIT_S if timeout is None else timeout)
    while status()["loaded"]:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def install(home: Path | None = None, allow_live_home: bool = False) -> tuple[bool, str]:
    home = home or paths.home()
    log_dir(home).mkdir(parents=True, exist_ok=True)
    was_loaded = status()["loaded"]
    prior = plist_path().read_bytes() if plist_path().exists() else None
    try:
        path = write_plist(home=home, allow_live_home=allow_live_home)
    except ServiceError as e:
        return False, str(e)
    # bootstrap fails if the label is already loaded; bootout first so
    # `install` is idempotent (reinstalling after editing config, say).
    if was_loaded:
        run_launchctl(["bootout", service_target()])
        if not _await_unloaded():
            return False, (f"the old job was still exiting after {UNLOAD_WAIT_S:.0f}s; launchd still has "
                           f"{label()} - run `hl service install` again once `hl service status` says not loaded")
    res = run_launchctl(["bootstrap", domain_target(), str(path)])
    if res.returncode != 0:
        # A daemon that was supervised and running before this call must not
        # end up unsupervised because the new plist failed to load - put the
        # old one back and try to restore what was working.
        if was_loaded and prior is not None:
            path.write_bytes(prior)
            run_launchctl(["bootstrap", domain_target(), str(path)])
        return False, (res.stderr or res.stdout or "launchctl bootstrap failed").strip()
    if not status()["loaded"]:
        return False, f"launchctl bootstrap succeeded but launchd does not list {label()}"
    run_launchctl(["enable", service_target()])
    return True, f"installed {path}, label {label()}, daemon {daemon_impl()}"


def load() -> tuple[bool, str]:
    """Load the installed plist into launchd, as `install` does but without writing it: for a service that is installed and not
    loaded (after `launchctl bootout`, or a login that did not load it). (True, "") or (False, launchd's own words)."""
    res = run_launchctl(["bootstrap", domain_target(), str(plist_path())])
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "launchctl bootstrap failed").strip()
    run_launchctl(["enable", service_target()])
    return True, ""


def uninstall() -> tuple[bool, str]:
    path = plist_path()
    run_launchctl(["bootout", service_target()])
    existed = path.exists()
    path.unlink(missing_ok=True)
    return True, (f"removed {path}" if existed else f"{label()} was not installed")


def _plist_installed() -> bool | None:
    """True or False when the plist is seen to be there or not; None when this
    process may not look (a sandboxed agent cannot stat ~/Library/LaunchAgents)."""
    try:
        plist_path().stat()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def status() -> dict:
    """installed: the plist exists, or launchd has the label. loaded: launchd knows
    the label. pid/last_exit come from `launchctl print`, when it is loaded.

    A plist this process may not look at is no evidence of "not installed": launchd
    is asked, or a daemon it is running would be called "hand-started" (T431)."""
    seen = _plist_installed()
    result = {"installed": bool(seen), "loaded": False, "pid": None, "last_exit_code": None}
    if seen is False:
        return result
    res = run_launchctl(["print", service_target()])
    if res.returncode != 0:
        return result
    result["installed"] = result["loaded"] = True
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

    Used by `hl ps` so a human can tell a launchd-run daemon apart from one
    they started by hand in a terminal - the two must never both be ticking.
    """
    st = status()
    if pid is None:
        if st["installed"] and not st["loaded"]:
            return "not running (service installed but not loaded - see `hl service status`)"
        return "not running"
    if st["loaded"] and st["pid"] == pid:
        return f"launchd ({label()})"
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


# The Go daemon's start lines, from the beginning of the line: the log's date and time, then `handlined`.
_GO_START_LINE = re.compile(r"\d{4}/\d\d/\d\d \d\d:\d\d:\d\d handlined(: | \S+ serving )")


def crash_loop_detected(home: Path | None = None, window_s: int = 300, threshold: int = 4) -> bool:
    """A supervisor that respawns faster than it can do useful work is not
    supervising, it is thrashing. There is no launchd-exposed restart counter
    to read (scout, request #14), so this counts our own startup line in the
    log instead - a real crash loop leaves several `daemon starting` lines
    (whether it got to reconcile or exited straight away because another
    daemon already held the lock - both are launchd restarting us) within
    one `window_s` window.
    """
    go = daemon_impl() == "go"
    p = err_log(home) if go else out_log(home)      # the Go daemon logs to stderr, with the date first
    if not p.exists():
        return False
    now = time.localtime()
    hits = 0
    for line in _tail_lines(p):
        if go:
            # "2026/10/10 13:45:00 handlined <sha> serving ..." is its line for a start that got up; a start that
            # refuses (lock held, database, bad flag) dies with "2026/10/10 13:45:00 handlined: <why>" instead.
            if not _GO_START_LINE.match(line):
                continue
            clock = line[11:19]
        else:
            if "daemon starting" not in line or not line.startswith("["):
                continue
            clock = line[1:9]
        try:
            ts = time.strptime(clock, "%H:%M:%S")
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
