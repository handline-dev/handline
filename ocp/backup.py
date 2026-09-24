"""Compressed snapshots of OCP_HOME, taken when it changed (`ocp backup`).

A test run once deleted the whole of ~/.orcapipe. launchd runs `ocp backup`
every five minutes, outside any agent's sandbox, and writes into a folder
beside OCP_HOME that agents cannot write to either.

accounts/*/.claude is never copied: it holds logins and transcripts, and
credentials are never copied (KICKOFF rule 1). venv is rebuilt by `ocp venv`.
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
INTERVAL_S = 300
KEEP_ALL_S = 24 * 3600      # every snapshot of the last day
KEEP_DAILY_S = 30 * 86400   # then the first of each day, for 30 days
DB_NAMES = ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm")
PREFIX = "orcapipe-"
SUFFIX = ".tar.gz"


def backup_dir(home: Path | None = None) -> Path:
    home = home or paths.home()
    return home.with_name(home.name + "-backups")


def _skipped(rel: Path) -> bool:
    parts = rel.parts
    return (parts[:1] == ("venv",) or (len(parts) >= 3 and parts[0] == "accounts" and parts[2] == ".claude")
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
    """Keep every snapshot of the last day, then the first of each day for 30 days."""
    removed, days_seen = [], set()
    for p in sorted(dest.glob(PREFIX + "*" + SUFFIX)):
        taken = p.stat().st_mtime
        age = now - taken
        day = time.strftime("%Y%m%d", time.localtime(taken))
        if age <= KEEP_ALL_S:
            continue
        if age <= KEEP_DAILY_S and day not in days_seen:
            days_seen.add(day)
            continue
        p.unlink()
        removed.append(p)
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
