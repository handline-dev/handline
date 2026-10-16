"""Tests: the protected store (infra/protected.py) under callers that race.

Run on its own: python3 tests/infra/test_protected_store.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import fcntl
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import protected, sessiontoken  # noqa: E402


def test_many_callers_making_the_same_namespace_at_once_all_succeed(home: Path) -> None:
    support.use_home(home)
    errors: list = []
    for n in range(40):
        gate = threading.Barrier(8)

        def make(i, name=f"ns{n}"):
            gate.wait()
            try:
                protected.put(name, f"k{i}", "v")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        threads = [threading.Thread(target=make, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    check("protected.put: eight callers making a namespace at once, forty times, none fails", not errors, str(errors[:3]))
    check("protected.put: and the folder is private", (protected.root() / "ns0").stat().st_mode & 0o777 == 0o700)


def test_session_record_waits_for_the_lock_the_go_daemon_takes(home: Path) -> None:
    support.use_home(home)
    protected.ensure(sessiontoken.NS)
    with open(protected.root() / "session-tokens.lock", "a") as lock:     # the Go daemon's sessiontoken.locked
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer = threading.Thread(target=sessiontoken.record, args=("a1",))
        writer.start()
        writer.join(0.5)
        check("sessiontoken.record: waits while the Go daemon holds the lock",
              writer.is_alive() and protected.get(sessiontoken.NS, "a1") is None)
    writer.join(5)
    check("sessiontoken.record: and writes once it is let go",
          sessiontoken.verify("a1", os.getpid()), repr(protected.get(sessiontoken.NS, "a1")))


def main() -> int:
    def tests(td):
        run(lambda: test_many_callers_making_the_same_namespace_at_once_all_succeed(Path(td) / "p1"))
        run(lambda: test_session_record_waits_for_the_lock_the_go_daemon_takes(Path(td) / "p2"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
