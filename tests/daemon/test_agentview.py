"""Tests: a database made before the per-agent view gets its table from a plain connect.
Run on its own: python3 tests/daemon/test_agentview.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_a_database_made_before_the_view_gets_it_from_a_plain_connect(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "v2.db"
    dbschema = db.schema
    dbschema.connect(p).close()
    raw = __import__("sqlite3").connect(p)
    raw.execute("DROP TABLE agent_view")
    raw.execute("DELETE FROM schema_version WHERE version > 2")
    raw.commit()
    raw.close()
    con = dbschema.connect(p)
    check("view: a version-2 database gets the table from a plain connect, as an additive step does",
          con.execute("SELECT 1 FROM sqlite_master WHERE name = 'agent_view'").fetchone() is not None
          and db.schema_version(con) == db.LATEST_VERSION)
    con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_database_made_before_the_view_gets_it_from_a_plain_connect(Path(td) / "a6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
