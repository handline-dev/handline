#!/usr/bin/env python3
"""What a test run left behind. tools/check.sh gives the run a directory of its own and points TMPDIR and TMUX_TMPDIR
into it, so every temp home, tmux socket and scratch binary the run makes is under it. It is in /tmp, not the per-user
TMPDIR, so a socket under it still fits in the 104 bytes a socket path holds:
    run="$(tools/leakcheck.py begin)"      prints the directory; TMPDIR is <run>/tmp, TMUX_TMPDIR <run> itself
    tools/leakcheck.py end [--interrupted] "$run"   exit 1, naming each, when the run left any of:
- a process whose command line names the directory (a daemon or gateway on a temp home, a fake claude, a test binary);
- a tmux server still answering on a socket under <run>/tmux-<uid>;
- a file or directory under <run>/tmp (but node's compile cache, which node keeps there on purpose), unless --interrupted:
  a test stopped by a signal runs no cleanups, and its files go with the run's directory;
- an entry in the real ~/.config/orcapipe/protected named after a home under the directory (a test that wrote the
  real store, not its own).
tools/leakcheck.py run [--dir DIR] CMD... does both around one command, and exits 1 if it failed, was stopped or left anything. With --dir
the run's directory is DIR, emptied first, not a new one: CI's Go job keeps the same TMPDIR each run, as go test's cache needs.
Nothing is killed or removed when something is left: the run is fixed, not swept. A clean run's directory is removed.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROTECTED = Path.home() / ".config" / "orcapipe" / "protected"


def slug(path: Path) -> str:
    """The protected store's name for a home (handline/infra/protected.root_for)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def new_run(at: Path | None = None) -> Path:
    if at is None:
        run = Path(tempfile.mkdtemp(prefix="hlc.", dir="/tmp"))
    else:
        run = at.resolve()
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(parents=True)
    (run / "tmp").mkdir()
    return run


def begin() -> int:
    print(new_run())
    return 0


def processes(run: Path) -> list[str]:
    names = {str(run.resolve()), str(run.resolve()).removeprefix("/private")}
    out = subprocess.run(["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines()
            if any(n in line for n in names) and str(os.getpid()) != line.split()[0]]


def tmux_servers(run: Path) -> list[str]:
    left = []
    for sock in sorted(run.glob("tmux-*/*")):
        if sock.is_socket() and subprocess.run(["tmux", "-S", str(sock), "list-sessions"], capture_output=True).returncode == 0:
            left.append(str(sock))
    return left


def end(run: Path, interrupted: bool = False) -> int:
    left = processes(run)
    for _ in range(50 if interrupted else 0):  # what the signal reached may still be exiting
        if not left:
            break
        time.sleep(0.1)
        left = processes(run)
    found = [f"process: {p}" for p in left]
    found += [f"tmux server: {s}" for s in tmux_servers(run)]
    if not interrupted:
        found += [f"temp file: {p}" for p in sorted((run / "tmp").iterdir()) if p.name != "node-compile-cache"]
    mark = slug(run.resolve())
    if PROTECTED.is_dir():
        found += [f"real protected store: {p}" for p in sorted(PROTECTED.iterdir()) if mark in p.name]
    if found:
        print(f"tools/leakcheck.py: the run left {len(found)} thing(s) behind (kept for you to read, under {run}):", file=sys.stderr)
        for line in found:
            print(f"  {line[:300]}", file=sys.stderr)
        return 1
    shutil.rmtree(run, ignore_errors=True)
    print("tools/leakcheck.py: the run left nothing behind", file=sys.stderr)
    return 0


def run_one(cmd: list[str], at: Path | None = None) -> int:
    """Off a terminal the command runs in a session of its own, and a signal to this process goes to all of it. On a terminal it
    keeps the terminal, which a step may need (stty, a password prompt), and Ctrl-C reaches all of it there already."""
    run = new_run(at)
    group = not sys.stdin.isatty()
    child = subprocess.Popen(cmd, env={**os.environ, "TMPDIR": f"{run}/tmp/", "TMUX_TMPDIR": str(run)}, start_new_session=group)
    stopped = []

    def stop(sig: int, _frame: object) -> None:
        stopped.append(sig)
        if group:
            os.killpg(child.pid, sig)
        else:
            child.send_signal(sig)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    rc = child.wait()
    return 1 if end(run, interrupted=bool(stopped)) or rc or stopped else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["begin"]:
        return begin()
    if argv[:1] == ["end"] and len(argv) == 2:
        return end(Path(argv[1]))
    if argv[:2] == ["end", "--interrupted"] and len(argv) == 3:
        return end(Path(argv[2]), interrupted=True)
    if argv[:2] == ["run", "--dir"] and len(argv) > 3:
        return run_one(argv[3:], Path(argv[2]))
    if argv[:1] == ["run"] and len(argv) > 1:
        return run_one(argv[1:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
