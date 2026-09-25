"""Compressed snapshots of OCP_HOME, taken when it changed (`ocp backup`).

A test run once deleted the whole of ~/.orcapipe. launchd runs `ocp backup`
every minute, outside any agent's sandbox, and writes into a folder
beside OCP_HOME that agents cannot write to either.

accounts/*/.claude is never copied: it holds logins and transcripts, and
credentials are never copied (AGENTS.md rule 1). venv is rebuilt by `ocp venv`;
logs/ and locks/ are left out.
The database is copied with SQLite's backup API, so a snapshot taken while the
daemon writes is still consistent.
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from . import paths, service
from . import timefmt

LABEL = "dev.orcapipe.backup"
INTERVAL_S = 60
# The ladder: up to each age, the first snapshot of every clock-aligned slot of
# that width (age limit, slot width, both in seconds). A snapshot's slot never
# changes as it ages, so what is kept stays kept; the newest is always kept, and
# nothing is kept past the last limit. Slot widths divide the next, so a
# snapshot kept at one width is the first of its slot at the next.
LADDER_S = tuple((limit * 60, width * 60) for limit, width in (
    (5, 1), (30, 5), (60, 30), (6 * 60, 60), (24 * 60, 6 * 60),
    (7 * 24 * 60, 24 * 60), (30 * 24 * 60, 7 * 24 * 60)))
MAX_AGE_S = LADDER_S[-1][0]
DB_NAMES = ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm")
PREFIX = "orcapipe-"
SUFFIX = ".tar.gz"
PARTIAL = ".partial"
PARTIAL_STALE_S = 600   # a run still writing one is younger than this


def backup_dir(home: Path | None = None) -> Path:
    home = home or paths.home()
    return home.with_name(home.name + "-backups")


def _skipped(rel: Path) -> bool:
    parts = rel.parts
    # logs/ and locks/ change on every run (this job's own log among them), so
    # counting them would make every run a change.
    return (parts[:1] in (("venv",), ("logs",), ("locks",)) or (len(parts) >= 3 and parts[0] == "accounts" and parts[2] == ".claude")
            or (len(parts) == 1 and parts[0] in DB_NAMES))


def _files(home: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(home):
        rel_root = Path(root).relative_to(home)
        dirs[:] = sorted(d for d in dirs if not _skipped(rel_root / d))
        out += [rel_root / f for f in sorted(files) if not _skipped(rel_root / f)]
    return out


def _copy_db(home: Path, dest: Path) -> None:
    src = sqlite3.connect(f"file:{home / 'db.sqlite'}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()


def _fingerprint(home: Path, db_copy: Path | None) -> str:
    h = hashlib.sha256()
    for rel in _files(home):
        if rel.name == "status.json":
            continue  # rewritten by every live session; kept in the snapshot, not a change
        try:
            st = (home / rel).stat()
        except FileNotFoundError:
            continue  # went between the walk and here: a change the next run will see
        h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    if db_copy is not None:
        con = sqlite3.connect(db_copy)
        try:
            for line in con.iterdump():
                h.update(line.encode())
        finally:
            con.close()
    return h.hexdigest()


def _write(home: Path, dest: Path, db_copy: Path | None, now: float) -> Path:
    """Write the archive under a name nothing else has, and return it.

    Built in a partial of its own and linked into place, so two runs in one
    second (a manual `ocp backup` racing launchd) each keep theirs: `link`
    refuses a name that exists, where a rename would replace it. A file that
    vanishes between the walk and the read is left out - it is gone, and
    whatever replaced it is the next run's change - and any other failure
    removes the partial, since nothing else ever would.
    """
    stem = PREFIX + timefmt.stamp(now, "%Y%m%d-%H%M%S")
    fd, tmp = tempfile.mkstemp(prefix=stem + ".", suffix=PARTIAL, dir=dest)
    partial = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            for rel in _files(home):
                try:
                    tar.add(home / rel, arcname=str(rel), recursive=False)
                except FileNotFoundError:
                    continue
            if db_copy is not None:
                tar.add(db_copy, arcname="db.sqlite")
        n = 1
        while True:
            out = dest / (stem + ("" if n == 1 else f"-{n}") + SUFFIX)
            try:
                os.link(partial, out)
                return out
            except FileExistsError:
                n += 1
    finally:
        partial.unlink(missing_ok=True)


def snapshot(home: Path | None = None, dest: Path | None = None,
             now: float | None = None) -> Path | None:
    """Write one snapshot if OCP_HOME changed since the last one; its path, or None."""
    home = home or paths.home()
    dest = dest or backup_dir(home)
    now = time.time() if now is None else now
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        db_copy = Path(td) / "db.sqlite"
        if (home / "db.sqlite").exists():
            _copy_db(home, db_copy)
        else:
            db_copy = None
        fp = _fingerprint(home, db_copy)
        last = dest / ".last-fingerprint"
        if last.exists() and last.read_text() == fp:
            return None
        out = _write(home, dest, db_copy, now)
        last.write_text(fp)
    prune(dest, now)
    return out


def expired(taken: list[float], now: float) -> set[float]:
    """Which of these snapshot times LADDER_S lets go."""
    if not taken:
        return set()
    keep = {max(taken)}
    first: dict[tuple[int, int], float] = {}
    for t in sorted(taken):
        for limit, width in LADDER_S:
            if now - t <= limit:
                # Earliest in the slot wins, whatever width the older ones were kept at.
                first.setdefault((width, int(t // width)), t)
                break
    keep |= set(first.values())
    return {t for t in taken if t not in keep}


def prune(dest: Path, now: float) -> list[Path]:
    """Delete the snapshots in `dest` that LADDER_S lets go."""
    found = {}
    for p in dest.glob(PREFIX + "*" + SUFFIX):
        try:
            found[p] = p.stat().st_mtime
        except FileNotFoundError:
            pass  # a concurrent prune already took it
    gone = expired(list(found.values()), now)
    removed = [p for p, t in sorted(found.items()) if t in gone]
    for p in removed:
        p.unlink(missing_ok=True)  # a concurrent prune may already have taken it
    # A run killed mid-write leaves its partial behind, and it matches no
    # snapshot name. A live run's is young.
    for p in dest.glob(PREFIX + "*" + PARTIAL):
        try:
            if now - p.stat().st_mtime > PARTIAL_STALE_S:
                p.unlink()
        except FileNotFoundError:
            pass  # its own run finished it
    return removed


# ---------------------------------------------------------------- launchd
def plist_path() -> Path:
    return paths.user_home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(home: Path | None = None) -> dict:
    home = home or paths.home()
    return {
        "Label": LABEL,
        "ProgramArguments": [service.ocp_executable(), "backup"],
        "StartInterval": INTERVAL_S,
        "RunAtLoad": True,
        "StandardOutPath": str(service.log_dir(home) / "backup.log"),
        "StandardErrorPath": str(service.log_dir(home) / "backup.log"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "OCP_HOME": str(home),
        },
    }


def install(home: Path | None = None) -> tuple[bool, str]:
    home = home or paths.home()
    service.log_dir(home).mkdir(parents=True, exist_ok=True)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_plist(home), f)
    target = f"{service.domain_target()}/{LABEL}"
    service.run_launchctl(["bootout", target])
    res = service.run_launchctl(["bootstrap", service.domain_target(), str(path)])
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "launchctl bootstrap failed").strip()
    return True, f"installed {path}: a snapshot every {INTERVAL_S}s into {backup_dir(home)}"
