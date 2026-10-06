"""Tests: the protected store (infra/protected.py) under callers that race.

Run on its own: python3 tests/infra/test_protected_store.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import protected  # noqa: E402


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


def main() -> int:
    def tests(td):
        run(lambda: test_many_callers_making_the_same_namespace_at_once_all_succeed(Path(td) / "p1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
