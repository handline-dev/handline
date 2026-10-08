"""Tests: the daemon's open-descriptor count - logged each tick, inventoried near the limit, EMFILE said once a minute and survived.

Run on its own: python3 tests/daemon/test_fds.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import errno
import os
import socket
import sqlite3
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

import threading  # noqa: E402

from handline.daemon import fds, rpc, socket_server  # noqa: E402
from handline.infra import service  # noqa: E402


def _fresh() -> None:
    fds._state.update(near=False, said=-fds.EMFILE_EVERY_S, last=None)


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
        check("fds: the count is logged on the first tick", sum("fds: 9/10" in l for l in lines) == 1, str(lines))
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


def _serving(called: bool, wait: float) -> bool:
    """Whether a connection that said hello (and `called`, if asked) is still served `wait` seconds into silence."""
    a, b = socket.socketpair()
    c = rpc.Connection(a, lambda *_: None, None)
    c.said_hello, c.called = True, called
    t = threading.Thread(target=c.serve, daemon=True)
    t.start()
    t.join(wait)
    alive = t.is_alive()
    b.close()
    t.join(2)
    a.close()
    return alive


def test_a_connection_that_asks_for_nothing_is_closed() -> None:
    saved = rpc.FIRST_CALL_S
    rpc.FIRST_CALL_S = 0.3
    try:
        check("idle: a hello and then silence is closed after FIRST_CALL_S", not _serving(False, 3))
        check("idle: a connection that has made a call is kept for the long idle", _serving(True, 1))
    finally:
        rpc.FIRST_CALL_S = saved


def test_the_inventory_names_a_database_handle() -> None:
    with tempfile.TemporaryDirectory() as d:
        con = sqlite3.connect(str(Path(d) / "db.sqlite"))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (x)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        plain = open(Path(d) / "notes.txt", "w")
        try:
            inv = fds.inventory()
            check("fds: a sqlite connection's files (db, wal, shm) are named db", inv.get("db", 0) >= 3, str(inv))
            check("fds: an ordinary file is a file, not a db", fds.kind(plain.fileno()) == "file", fds.kind(plain.fileno()))
        finally:
            plain.close(); con.close()


def test_the_inventory_names_a_pty() -> None:
    try:
        master, slave = os.openpty()
    except OSError as e:  # a sandbox may refuse a pty
        support.skip(test_the_inventory_names_a_pty, f"cannot open a pty here: {e}")
        return
    try:
        kinds = {fds.kind(master), fds.kind(slave)}
        check("fds: a pty is a pty", kinds == {"pty"}, str(kinds))
    finally:
        os.close(master); os.close(slave)


def test_check_survives_a_failing_inventory_and_tries_it_again() -> None:
    _fresh()
    lines: list[str] = []
    saved = fds.soft_limit, fds.count, fds.inventory
    fail = {"on": True}

    def inventory():
        if fail["on"]:
            raise OSError(errno.EMFILE, "Too many open files")
        return {"socket": 9}
    fds.soft_limit, fds.count, fds.inventory = (lambda: 10), (lambda: 9), inventory
    try:
        n = fds.check(lines.append)
        check("fds: a failing inventory does not raise out of the tick", n == 9, str(n))
        check("fds: it is said as the shortage it is", any("out of file descriptors" in l for l in lines), str(lines))
        fail["on"] = False
        fds.check(lines.append)
        check("fds: the next tick takes the inventory the failed one did not", any("near the limit - socket 9" in l for l in lines), str(lines))
    finally:
        fds.soft_limit, fds.count, fds.inventory = saved
        _fresh()


def test_check_survives_a_failing_count() -> None:
    _fresh()
    lines: list[str] = []
    saved = fds.count

    def count():
        raise OSError(errno.EMFILE, "Too many open files")
    fds.count = count
    try:
        n = fds.check(lines.append)
        check("fds: a count that cannot be taken is -1, said once, not raised", n == -1 and len(lines) == 1, f"{n} {lines}")
    finally:
        fds.count = saved
        _fresh()


def test_the_count_is_logged_when_it_moves_not_every_tick() -> None:
    _fresh()
    lines: list[str] = []
    now = {"n": 10}
    saved = fds.soft_limit, fds.count
    fds.soft_limit, fds.count = (lambda: 200), (lambda: now["n"])
    try:
        fds.check(lines.append)
        now["n"] = 12
        fds.check(lines.append)
        check("fds: a small move is not logged again", len(lines) == 1, str(lines))
        now["n"] = 40
        fds.check(lines.append)
        check("fds: a move of a twentieth of the limit is", len(lines) == 2, str(lines))
    finally:
        fds.soft_limit, fds.count = saved
        _fresh()


def main() -> int:
    def tests(td):
        run(lambda: test_count_follows_opens_and_closes())
        run(lambda: test_inventory_names_the_kinds())
        run(lambda: test_check_logs_the_count_and_inventories_once_per_crossing())
        run(lambda: test_emfile_is_said_once_a_minute())
        run(lambda: test_the_plist_carries_the_limit_the_daemon_raises_itself_to())
        run(lambda: test_the_inventory_names_a_database_handle())
        run(lambda: test_the_inventory_names_a_pty())
        run(lambda: test_check_survives_a_failing_inventory_and_tries_it_again())
        run(lambda: test_check_survives_a_failing_count())
        run(lambda: test_the_count_is_logged_when_it_moves_not_every_tick())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
