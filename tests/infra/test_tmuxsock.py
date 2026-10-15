"""Tests: tools/tmuxsock.py ends a private tmux server and leaves no socket file, on a real tmux.

Run on its own: tools/test.sh tests/infra/test_tmuxsock.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
import tmuxsock  # noqa: E402


def _start(name: str) -> bool:
    return subprocess.run([support.REAL_TMUX, "-L", name, "new-session", "-d", "sleep 60"], capture_output=True).returncode == 0


def _alive(name: str) -> bool:
    return subprocess.run([support.REAL_TMUX, "-L", name, "list-sessions"], capture_output=True).returncode == 0


def test_stop_ends_the_server_and_removes_its_socket() -> None:
    name = f"tmuxsocktest{os.getpid()}"
    if not _start(name):
        print("SKIPPED  tmuxsock: tmux cannot start a server here")
        return
    try:
        check("tmuxsock: the server made its socket", tmuxsock.path(name).exists(), str(tmuxsock.path(name)))
        tmuxsock.stop(name, support.REAL_TMUX)
        check("tmuxsock.stop: the server is gone", not _alive(name))
        check("tmuxsock.stop: and so is its socket file", not tmuxsock.path(name).exists(), str(tmuxsock.path(name)))
    finally:
        tmuxsock.stop(name, support.REAL_TMUX)


def test_sweep_ends_only_the_servers_whose_owner_is_gone() -> None:
    gone = subprocess.Popen(["true"])
    gone.wait()
    prefix = f"tmuxsweeptest{os.getpid()}x"
    dead, live = f"{prefix}{gone.pid}", f"{prefix}{os.getpid()}"
    if not (_start(dead) and _start(live)):
        print("SKIPPED  tmuxsock: tmux cannot start a server here")
        return
    try:
        tmuxsock.sweep(prefix, support.REAL_TMUX)
        check("tmuxsock.sweep: a server whose owner exited is gone, socket and all", not _alive(dead) and not tmuxsock.path(dead).exists())
        check("tmuxsock.sweep: one whose owner runs is left alone", _alive(live))
    finally:
        tmuxsock.stop(dead, support.REAL_TMUX)
        tmuxsock.stop(live, support.REAL_TMUX)


def test_stop_refuses_the_live_servers() -> None:
    for name in tmuxsock.LIVE:
        try:
            tmuxsock.stop(name, support.REAL_TMUX)
            refused = False
        except ValueError:
            refused = True
        check(f"tmuxsock.stop: refuses the {name} server", refused)


def main() -> int:
    def tests(td):
        run(lambda: test_stop_refuses_the_live_servers())
        if support.REAL_TMUX is None:
            for t in (test_stop_ends_the_server_and_removes_its_socket, test_sweep_ends_only_the_servers_whose_owner_is_gone):
                skip(t, "tmux is not installed")
            return
        run(lambda: test_stop_ends_the_server_and_removes_its_socket())
        run(lambda: test_sweep_ends_only_the_servers_whose_owner_is_gone())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
