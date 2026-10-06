"""Dev mode: restart the daemon when its own code changes."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .. import STARTED
from ..usage import load
from ..infra import paths
from ..infra import service


# ---------------------------------------------------------------- dev mode
# Task #64 (founder #2120, #2121): the daemon restarting itself, in place,
# once it notices its own code changed - so a merge no longer needs a human
# to kill and relaunch it by hand. Off by default; `hl dev-mode on` turns it
# on (config.yaml, next to `active_cap`).
DEV_MODE_KEY = "dev_mode"


def dev_mode_enabled() -> bool:
    return bool(load._config().get(DEV_MODE_KEY))


def set_dev_mode(on: bool) -> None:
    cfg = load.config_for_write()
    cfg[DEV_MODE_KEY] = bool(on)
    load.write_config(cfg)


def _watched_files(root: Path) -> list[Path]:
    """handline/**/*.py (subpackages such as handline/cli/ and handline/ui/
    included), bin/ocp, requirements.txt - task #64's own list, read from
    the checkout the daemon is running from rather than named once and left
    to rot as files are added."""
    files = sorted((root / "handline").rglob("*.py"))
    files.append(root / "bin" / "hl")
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


# How long the tree must stand unchanged before the daemon restarts onto it. A merge writes files over minutes;
# a restart in the middle imports half old and half new code, and an import check that happens to pass proves
# nothing about a tree still being written. Long enough to outlast a pause between a merge's writes, short
# enough that a merged change is live within the minute.
QUIET_S = 30.0
_STALE = ("code newer than this process",)      # a baseline no fingerprint equals
_now = time.monotonic


def _changed_since_start(root: Path) -> bool:
    """Whether any watched file was written after this process began to load the code, so that what it
    imported may not be what is on disk now, however the tree looked when it first checked."""
    for f in _watched_files(root):
        try:
            if f.stat().st_mtime > STARTED:
                return True
        except FileNotFoundError:
            continue
    return False


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
    still broken is not re-checked and re-logged every tick until it is. `seen` is the
    fingerprint of the change last reported."""
    if not dev_mode_enabled():
        state["fp"] = None
        state["warned"] = None
        state["seen"] = None
        state.pop("pending", None)
        state.pop("pending_since", None)
        return False
    root = paths.repo_root()
    fp = _code_fingerprint(root)
    if state["fp"] is None:
        # The baseline is the tree this process loaded. Taken now it would be the tree as of the first tick,
        # which a merge still writing when the process started has changed since: a file written after the
        # process began to load is newer than what it runs, so the baseline is stale and the tree is a change.
        state["fp"] = _STALE if _changed_since_start(root) else fp
        if state["fp"] is not _STALE:
            return False
    if fp == state["fp"] or fp == state["warned"]:
        state.pop("pending", None)
        return False
    # A tree still changing is not restarted onto: wait until it has stood still for QUIET_S.
    if state.get("pending") != fp:
        state["pending"], state["pending_since"] = fp, _now()
    if _now() - state["pending_since"] < QUIET_S:
        return False
    if fp == state.get("seen"):
        return True             # already found to import, and not asked again
    if _dev_import_check(log):
        log("dev-mode: code changed - finishing this tick, then restarting")
        state["seen"] = fp      # what a caller that declines the restart takes as its baseline
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
