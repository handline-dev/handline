"""Run git and hand back what it said, the same way for every caller."""
from __future__ import annotations

import subprocess
from pathlib import Path


def run(cwd: str | Path, *args: str, timeout: int = 15) -> tuple[int | None, str, str]:
    """(returncode, stdout, stderr), untouched. The returncode is None when git
    could not be run at all (missing, or past `timeout`), and stderr says why."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "", str(e)
    return r.returncode, r.stdout, r.stderr


# The branch work is branched from and merged into. Claude Code names a session's "main branch"
# from the repo's origin/HEAD, so a clone whose origin/HEAD says another branch (the manager's own
# `orcapipe`, T935) sends every agent branching and merge-treeing from a ref that went stale.
INTEGRATION_BRANCH = "main"
# A branch forked this many commits before main's tip was not branched from main's tip.
BEHIND_WARN = 100


def point_origin_head_at_main(cwd: str | Path) -> bool:
    """Make `origin/HEAD` name `origin/main`. True when it was changed; False when it already did,
    or when there is no `origin/main` to point at (git refuses and nothing is touched)."""
    rc, out, _ = run(cwd, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip() == f"refs/remotes/origin/{INTEGRATION_BRANCH}":
        return False
    rc, _, _ = run(cwd, "remote", "set-head", "origin", INTEGRATION_BRANCH)
    return rc == 0


def commits_behind_main(cwd: str | Path) -> int | None:
    """How many commits `main` holds that `HEAD` was forked before: None with no `main`, no HEAD
    or no common ancestor."""
    rc, base, _ = run(cwd, "merge-base", "HEAD", INTEGRATION_BRANCH)
    if rc != 0 or not base.strip():
        return None
    rc, out, _ = run(cwd, "rev-list", "--count", f"{base.strip()}..{INTEGRATION_BRANCH}")
    return int(out) if rc == 0 and out.strip().isdigit() else None
