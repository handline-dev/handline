"""Dev mode: restart the daemon when its own code changes."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..usage import load
from ..infra import paths
from ..infra import service


# ---------------------------------------------------------------- dev mode
# Task #64 (founder #2120, #2121): the daemon restarting itself, in place,
# once it notices its own code changed - so a merge no longer needs a human
# to kill and relaunch it by hand. Off by default; `ocp dev-mode on` turns it
# on (config.yaml, next to `active_cap`).
DEV_MODE_KEY = "dev_mode"


def dev_mode_enabled() -> bool:
    return bool(load._config().get(DEV_MODE_KEY))


def set_dev_mode(on: bool) -> None:
    cfg = load.config_for_write()
    cfg[DEV_MODE_KEY] = bool(on)
    load.write_config(cfg)


def _watched_files(root: Path) -> list[Path]:
    """ocp/**/*.py (subpackages such as ocp/cli/ and ocp/ui/ included), bin/ocp, requirements.txt - task #64's own list, read from
    the checkout the daemon is running from rather than named once and left
    to rot as files are added."""
    files = sorted((root / "ocp").rglob("*.py"))
    files.append(root / "bin" / "ocp")
    files.append(root / "requirements.txt")
    return files


def _code_fingerprint(root: Path) -> tuple:
    """mtime+size of every watched file - one stat() apiece, not a read,
    since this runs every tick dev mode is on. A file gone between listing
    and stat()ing it - a merge in the main checkout unlinks and rewrites
    files, and that is exactly when this runs - is left out of the
    fingerprint rather than raised: still gone or written back by the next
    tick, either way that tick's fingerprint differs from this one's."""
    found = []
    for f in _watched_files(root):
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        found.append((str(f.relative_to(root)), st.st_mtime_ns, st.st_size))
    return tuple(sorted(found))


def _dev_import_check(log) -> bool:
    """Whether the code on disk right now would still start: `bin/ocp
    --help`, the same entrypoint a restart execs, so this exercises the real
    venv selection and the real import of every module - not a read of the
    diff. Nothing it does is acted on; argparse builds the parser and exits.
    A version that fails this is not restarted into; the old process (already
    imported, already running) keeps ticking and this says why not."""
    exe = service.ocp_executable()
    try:
        res = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"dev-mode: import check could not run - staying on the running version ({e})")
        return False
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()
        why = tail[-1] if tail else f"exit {res.returncode}, no output"
        log(f"dev-mode: new code fails to import, staying on the running version - {why}")
        return False
    return True


def _dev_mode_should_restart(state: dict, log) -> bool:
    """Called once a tick. `state` is `run`'s own dict, carried across calls:
    `fp` is the fingerprint this process started (or was last found clean)
    with, `warned` is one that already failed the import check, so a version
    still broken is not re-checked and re-logged every tick until it is."""
    if not dev_mode_enabled():
        state["fp"] = None
        state["warned"] = None
        return False
    root = paths.repo_root()
    fp = _code_fingerprint(root)
    if state["fp"] is None:
        state["fp"] = fp  # dev mode just turned on, or the daemon just started - this is the baseline
        return False
    if fp == state["fp"] or fp == state["warned"]:
        return False
    if _dev_import_check(log):
        log("dev-mode: code changed - finishing this tick, then restarting")
        return True
    state["warned"] = fp
    return False


def _dev_mode_restart() -> None:
    """Exec the same entrypoint with the same arguments, in this process.

    Not a kill from outside (#2121): by the time this runs, the tick it
    interrupted has already finished, `run` has already released the daemon
    lock and closed the database connection the way a clean exit always
    does - the only difference is what runs next. `os.execv` also means
    launchd never sees an exit to restart from, so KeepAlive is irrelevant
    either way: this is the one restart path, whether launchd is supervising
    or the daemon is running by hand.
    """
    exe = service.ocp_executable()
    os.execv(exe, [exe] + sys.argv[1:])
