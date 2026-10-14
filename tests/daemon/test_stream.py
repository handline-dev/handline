"""Tests: a database made before the event stream gets its log from a plain connect.
Run on its own: python3 tests/daemon/test_stream.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _notices(w, count, timeout=5.0):
    w.a.settimeout(timeout)
    return [w.read() for _ in range(count)]


def _until(pred, secs: float = 30.0) -> bool:
    """Wait on a condition, not for a time: the limit only ends a test that would never finish."""
    end = time.monotonic() + secs
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


def test_a_database_made_before_the_stream_gets_it_from_a_plain_connect(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "v3.db"
    db.schema.connect(p).close()
    raw = __import__("sqlite3").connect(p)
    raw.execute("DROP TABLE stream_log")
    raw.execute("DELETE FROM schema_version WHERE version > 3")
    raw.commit()
    raw.close()
    con = db.schema.connect(p)
    check("stream: a version-3 database gets the log from a plain connect, as an additive step does",
          con.execute("SELECT 1 FROM sqlite_master WHERE name = 'stream_log'").fetchone() is not None
          and db.schema_version(con) == db.LATEST_VERSION)
    con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_database_made_before_the_stream_gets_it_from_a_plain_connect(Path(td) / "s10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
