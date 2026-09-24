"""Compressed snapshots of OCP_HOME, taken when it changed (`ocp backup`).

A test run once deleted the whole of ~/.orcapipe. launchd runs `ocp backup`
every minute, outside any agent's sandbox, and writes into a folder
beside OCP_HOME that agents cannot write to either.

accounts/*/.claude is never copied: it holds logins and transcripts, and
credentials are never copied (KICKOFF rule 1). venv is rebuilt by `ocp venv`;
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

LABEL = "dev.orcapipe.backup"
INTERVAL_S = 60
# The ladder: the newest snapshot, and for each of these ages the one nearest
# it, so the history thins out the further back it goes. Nothing is kept
# beyond the last rung.
LADDER_S = tuple(m * 60 for m in (1, 5, 30, 60, 6 * 60, 24 * 60, 7 * 24 * 60, 30 * 24 * 60))
MAX_AGE_S = LADDER_S[-1]
DB_NAMES = ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm")
PREFIX = "orcapipe-"
SUFFIX = ".tar.gz"


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
        st = (home / rel).stat()
        h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    if db_copy is not None:
        con = sqlite3.connect(db_copy)
        try:
            for line in con.iterdump():
                h.update(line.encode())
        finally:
            con.close()
    return h.hexdigest()


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
        name = PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + SUFFIX
        out = dest / name
        partial = dest / (name + ".partial")
        with tarfile.open(partial, "w:gz") as tar:
            for rel in _files(home):
                tar.add(home / rel, arcname=str(rel), recursive=False)
            if db_copy is not None:
                tar.add(db_copy, arcname="db.sqlite")
        partial.rename(out)
        last.write_text(fp)
    prune(dest, now)
    return out


def prune(dest: Path, now: float) -> list[Path]:
    """Keep the newest snapshot and the one nearest each age on LADDER_S."""
    aged = sorted(((now - p.stat().st_mtime, p) for p in dest.glob(PREFIX + "*" + SUFFIX)),
                  key=lambda a: a[0])
    keep = {aged[0][1]} if aged else set()
    within = [a for a in aged if a[0] <= MAX_AGE_S]
    for target in LADDER_S:
        if within:
            keep.add(min(within, key=lambda a: abs(a[0] - target))[1])
    removed = [p for _, p in aged if p not in keep]
    for p in removed:
        p.unlink()
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
