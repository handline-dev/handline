"""Tests: the daemon's open-descriptor count - logged each tick, inventoried near the limit, EMFILE said once a minute and survived.

Run on its own: python3 tests/daemon/test_fds.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import errno
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

from handline.daemon import fds, socket_server  # noqa: E402
from handline.infra import service  # noqa: E402
from handline.team import spawn  # noqa: E402


def _fresh() -> None:
    fds._state.update(near=False, said=-fds.EMFILE_EVERY_S)


def test_count_follows_opens_and_closes() -> None:
    before = fds.count()
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(5)]
    during = fds.count()
    for fd in held:
        os.close(fd)
    check("fds: five opens are five more descriptors", during == before + 5, f"{before} {during}")
    check("fds: closing them brings the count back flat", fds.count() == before, f"{before} {fds.count()}")


def test_inventory_names_the_kinds() -> None:
    a, b = socket.socketpair()
    r, w = os.pipe()
    try:
        inv = fds.inventory()
        check("fds: the inventory counts sockets and pipes apart", inv.get("socket", 0) >= 2 and inv.get("pipe", 0) >= 2, str(inv))
    finally:
        os.close(r); os.close(w); a.close(); b.close()


def test_check_logs_the_count_and_inventories_once_per_crossing() -> None:
    _fresh()
    lines: list[str] = []
    now = {"n": 9}
    fds.soft_limit, fds.count, saved = (lambda: 10), (lambda: now["n"]), (fds.soft_limit, fds.count)
    try:
        fds.check(lines.append)
        fds.check(lines.append)
        check("fds: the count is logged every tick", sum("fds: 9/10" in l for l in lines) == 2, str(lines))
        check("fds: near the limit the inventory is logged once, not every tick", sum("near the limit" in l for l in lines) == 1, str(lines))
        now["n"] = 2
        fds.check(lines.append)
        now["n"] = 9
        fds.check(lines.append)
        check("fds: it is logged again after the count fell back and crossed again", sum("near the limit" in l for l in lines) == 2, str(lines))
    finally:
        fds.soft_limit, fds.count = saved
        _fresh()


def test_emfile_is_said_once_a_minute() -> None:
    _fresh()
    lines: list[str] = []
    e = OSError(errno.EMFILE, "Too many open files")
    fds.say_exhaustion(lines.append, e, "spawning", now=1000)
    fds.say_exhaustion(lines.append, e, "spawning", now=1030)
    check("fds: a second EMFILE within the minute is not said", len(lines) == 1, str(lines))
    fds.say_exhaustion(lines.append, e, "spawning", now=1061)
    check("fds: it is said again after the minute", len(lines) == 2, str(lines))
    check("fds: EMFILE and ENFILE are exhaustion, ENOENT is not",
          fds.is_exhaustion(e) and not fds.is_exhaustion(OSError(errno.ENOENT, "x")))
    _fresh()


def test_the_plist_carries_the_limit_the_daemon_raises_itself_to() -> None:
    plist = service.build_plist()
    check("fds: the launchd plist sets the soft descriptor limit the daemon raises itself to",
          plist.get("SoftResourceLimits", {}).get("NumberOfFiles") == socket_server.FD_SOFT_LIMIT, str(plist.get("SoftResourceLimits")))


def test_a_spawn_without_descriptors_is_a_refusal_not_a_raise() -> None:
    real = subprocess.run

    def boom(*a, **k):
        raise OSError(errno.EMFILE, "Too many open files")
    subprocess.run = boom
    try:
        code, out, err = spawn._run(["git", "status"])
    finally:
        subprocess.run = real
    check("fds: a spawn step that cannot get a descriptor comes back as a refusal", code != 0 and "Too many open files" in err, f"{code} {err}")


def main() -> int:
    def tests(td):
        run(test_count_follows_opens_and_closes)
        run(test_inventory_names_the_kinds)
        run(test_check_logs_the_count_and_inventories_once_per_crossing)
        run(test_emfile_is_said_once_a_minute)
        run(test_the_plist_carries_the_limit_the_daemon_raises_itself_to)
        run(test_a_spawn_without_descriptors_is_a_refusal_not_a_raise)
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
