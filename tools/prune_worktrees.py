#!/usr/bin/env python3
"""List the worktrees of this repository that are done with, and remove them when told to.

  tools/prune_worktrees.py [--home DIR]            what would be removed, and why each other one stays
  tools/prune_worktrees.py [--home DIR] --remove   remove them

A worktree is removed only when its branch is merged into main and it has no uncommitted or untracked file. One an
agent of the home works in stays (the daemon starts the agent there), and so do main's, the one this is run from, a
locked one and one on no branch. The home is --home, else HANDLINE_HOME or OCP_HOME, else ~/.handline; when its
database cannot be read, nothing is removed. The branch itself is left: `git branch -d` takes it.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def worktrees() -> list[dict[str, str]]:
    """One dict per worktree from `git worktree list --porcelain`, main's first: path, and branch, locked, ... when it has them."""
    out, cur = [], {}
    for line in git("worktree", "list", "--porcelain").stdout.splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        cur["path" if key == "worktree" else key] = value
    return out


def agent_worktrees(home: Path) -> set[str]:
    """Every worktree the home's database records for an agent, as real paths. Raises when it cannot be read."""
    con = sqlite3.connect(f"file:{home / 'db.sqlite'}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT worktree, extra_worktrees FROM agent_state").fetchall()
    finally:
        con.close()
    found = set()
    for first, extra in rows:
        for recorded in [first, *(json.loads(extra) if extra else [])]:
            if recorded:
                found.add(os.path.realpath(recorded.split("::", 1)[-1]))   # `hl launch` records <repo id>::<path>
    return found


def why_kept(wt: dict[str, str], agents: set[str], here: str) -> str | None:
    """Why the worktree stays, or None when it may be removed."""
    path = os.path.realpath(wt["path"])
    if "bare" in wt or "prunable" in wt:
        return "not a checkout"
    if path == here:
        return "this is run from it"
    if path in agents:
        return "an agent works in it"
    if "locked" in wt:
        return "locked"
    branch = wt.get("branch", "").removeprefix("refs/heads/")
    if not branch:
        return "on no branch"
    if branch == "main":
        return "main"
    if git("merge-base", "--is-ancestor", branch, "main").returncode != 0:
        return f"{branch} is not merged into main"
    status = git("status", "--porcelain", cwd=wt["path"])
    if status.returncode != 0 or status.stdout.strip():
        return "uncommitted or untracked files"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", help="the home whose agents' worktrees stay")
    ap.add_argument("--remove", action="store_true", help="remove them; without it nothing is changed")
    args = ap.parse_args()
    home = Path(args.home or os.environ.get("HANDLINE_HOME") or os.environ.get("OCP_HOME") or "~/.handline").expanduser()
    try:
        agents = agent_worktrees(home)
    except (sqlite3.Error, ValueError) as e:
        print(f"cannot read the agents' worktrees from {home}: {e}; nothing is removed", file=sys.stderr)
        return 1
    here = os.path.realpath(git("rev-parse", "--show-toplevel").stdout.strip())
    failed = 0
    for wt in worktrees()[1:]:      # the first is the repository's own checkout
        why = why_kept(wt, agents, here)
        if why:
            print(f"keep    {wt['path']}: {why}")
        elif not args.remove:
            print(f"remove  {wt['path']}")
        else:
            # No --force: git refuses a worktree that became dirty since the check.
            done = git("worktree", "remove", wt["path"])
            print(f"removed {wt['path']}" if done.returncode == 0 else f"FAILED  {wt['path']}: {done.stderr.strip()}")
            failed += done.returncode != 0
    if not args.remove:
        print("nothing is changed: --remove removes the ones marked remove", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
