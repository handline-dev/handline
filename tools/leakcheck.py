#!/usr/bin/env python3
"""What a test run left behind. tools/check.sh gives the run a directory of its own and points TMPDIR and TMUX_TMPDIR
into it, so every temp home, tmux socket and scratch binary the run makes is under it. It is in /tmp, not the per-user
TMPDIR, so a socket under it still fits in the 104 bytes a socket path holds:
    run="$(tools/leakcheck.py begin)"      prints the directory; TMPDIR is <run>/tmp, TMUX_TMPDIR <run> itself
    tools/leakcheck.py end "$run"          exit 1, naming each, when the run left any of:
- a process whose command line names the directory (a daemon or gateway on a temp home, a fake claude, a test binary);
- a tmux server still answering on a socket under <run>/tmux-<uid>;
- a file or directory under <run>/tmp (but node's compile cache, which node keeps there on purpose);
- an entry in the real ~/.config/orcapipe/protected named after a home under the directory (a test that wrote the
  real store, not its own).
Nothing is killed or removed when something is left: the run is fixed, not swept. A clean run's directory is removed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROTECTED = Path.home() / ".config" / "orcapipe" / "protected"


def slug(path: Path) -> str:
    """The protected store's name for a home (handline/infra/protected.root_for)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def begin() -> int:
    run = Path(tempfile.mkdtemp(prefix="hlc.", dir="/tmp"))
    (run / "tmp").mkdir()
    print(run)
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


def end(run: Path) -> int:
    found = [f"process: {p}" for p in processes(run)]
    found += [f"tmux server: {s}" for s in tmux_servers(run)]
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


def main(argv: list[str]) -> int:
    if argv[:1] == ["begin"]:
        return begin()
    if argv[:1] == ["end"] and len(argv) == 2:
        return end(Path(argv[1]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
