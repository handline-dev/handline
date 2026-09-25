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
