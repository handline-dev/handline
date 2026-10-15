"""A private tmux server's end: the server killed and its socket file removed, which tmux leaves behind.

A test's tmux server calls setsid, so killing the test's process group does not reach it: `sweep` ends the ones
whose owner, named by the pid in the socket's name, is gone without its teardown.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

LIVE = ("ocp", "default")      # the agents' server and a bare `tmux`'s: never ours to end


def path(name: str) -> Path:
    return Path(os.environ.get("TMUX_TMPDIR") or "/tmp").resolve() / f"tmux-{os.getuid()}" / name


def stop(name: str, tmux: str = "tmux", env: dict | None = None) -> None:
    if name in LIVE:
        raise ValueError(f"not a private tmux server: {name}")
    subprocess.run([tmux, "-L", name, "kill-server"], capture_output=True, env=env, timeout=20)
    path(name).unlink(missing_ok=True)


def sweep(prefix: str, tmux: str = "tmux", env: dict | None = None) -> None:
    """Stop every `<prefix><pid>` server whose pid is no longer running."""
    try:
        names = [p.name for p in path("x").parent.iterdir()]
    except OSError:
        return
    for name in names:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", name)
        if m and not _running(int(m.group(1))):
            stop(name, tmux, env)


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
