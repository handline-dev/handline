"""Stale repo copies in the Claude sessions' shared scratch root, /private/tmp/claude-<uid>.

Agents copy the repo there, node_modules and all (~1.4 GB each), to run a check, and leave the copy behind: 185
of them filled 250 GB of the disk in three days. The daemon deletes a copy nothing has touched for a day - its
check is long over by then. A copy is a directory directly under the root holding AGENTS.md and node_modules;
nothing else there is touched.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

STALE_S = 24 * 3600
EVERY_S = 3600      # the root can hold tens of thousands of entries; once an hour is plenty for a day's rule


def root() -> Path:
    return Path(f"/private/tmp/claude-{os.getuid()}")


def _is_repo_copy(d: Path) -> bool:
    return (d / "AGENTS.md").is_file() and (d / "node_modules").is_dir()


def _last_touched(d: Path) -> float:
    """The newest mtime of the copy and its top-level entries: a check writing anywhere at the top (a file, a
    __pycache__, a build dir) keeps the copy alive."""
    newest = d.lstat().st_mtime
    with os.scandir(d) as it:
        for e in it:
            try:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
    return newest


def sweep(now: float, base: Path | None = None) -> list[Path]:
    """Delete the repo copies under `base` (the scratch root) untouched for STALE_S; return what was deleted."""
    base = base or root()
    removed: list[Path] = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return removed
    for e in entries:
        d = Path(e.path)
        try:
            if not e.is_dir(follow_symlinks=False) or not _is_repo_copy(d) or now - _last_touched(d) < STALE_S:
                continue
        except OSError:
            continue    # gone between the listing and the look
        # node_modules first: a daemon restart mid-delete then leaves either a copy the next sweep still knows (AGENTS.md
        # beside what is left of node_modules) or the few MB of source, never 1.4 GB it no longer recognises.
        shutil.rmtree(d / "node_modules", ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            removed.append(d)
    return removed


def _sweep_and_log(now: float, log) -> None:
    try:
        removed = sweep(now)
    except Exception as e:  # noqa: BLE001 - off the tick's thread, the log is the only place a failure shows
        log(f"scratch sweep failed: {type(e).__name__}: {e}")
        return
    if removed:
        log(f"scratch: deleted {len(removed)} repo cop{'y' if len(removed) == 1 else 'ies'} untouched for a day: "
            + ", ".join(p.name for p in removed))


def maybe_sweep(state: dict, log, now: float | None = None) -> threading.Thread | None:
    """The daemon loop's call: a sweep at most once per EVERY_S, on a thread of its own - one copy is ~48k files and
    takes seconds to delete, and the tick must not wait on that. One sweep at a time: none starts while the last
    is still deleting. Returns the thread it started, if any."""
    now = time.time() if now is None else now
    running = state.get("thread")
    if (running and running.is_alive()) or now - state.get("at", 0) < EVERY_S:
        return None
    state["at"] = now
    t = threading.Thread(target=_sweep_and_log, args=(now, log), name="scratch-sweep", daemon=True)
    state["thread"] = t
    t.start()
    return t
