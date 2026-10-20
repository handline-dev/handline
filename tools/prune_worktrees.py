#!/usr/bin/env python3
"""List the worktrees of this repository that are done with, and remove them when told to.

  tools/prune_worktrees.py [--home DIR]            what would be removed, and why each other one stays
  tools/prune_worktrees.py [--home DIR] --remove   remove them

A worktree is removed only when its branch is merged into main and it has no uncommitted or untracked file. One an
agent of the home works in stays (the daemon starts the agent there), and so do main's, the one this is run from, a
locked one and one on no branch. The home is --home, else HANDLINE_HOME or OCP_HOME, else ~/.handline; when its
database cannot be read, nothing is removed. The branch itself is left: `git branch -d` takes it.

The rule is go/internal/worktreeprune, which the daemon also uses when a member leaves the team; this runs it.
"""
import os
import sys
from pathlib import Path

go = Path(__file__).resolve().parent.parent / "go"
os.execvp("go", ["go", "-C", str(go), "run", "./cmd/prune-worktrees", *sys.argv[1:]])
