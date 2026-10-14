"""Tests: what every write method gets from the connection (contract/protocol.md, "Request id" and
"Roles"): a request_id, a reply kept a day and replayed, the actor on its event, whoami, and not_self.

Run on its own: python3 tests/daemon/test_rpc_writes.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _as_agent(name):
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: name
    return real


def test_a_database_made_before_the_reply_store_gets_it_at_migration(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "v1.db"
    con = db.connect(p)
    con.execute("DROP TABLE rpc_replies")
    con.execute("DELETE FROM schema_version WHERE version > 1")
    con.close()
    con = db.connect(p, migrating=True)
    has = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'rpc_replies'").fetchone()
    check("store: a version-1 database gets the table from migration 2", has is not None and db.schema_version(con) == db.LATEST_VERSION)
    con.close()


def _effect_callers(request_id: str) -> list[str]:
    con = db.connect()
    try:
        return [r["caller"] for r in con.execute("SELECT caller FROM write_effects WHERE request_id = ?", (request_id,))]
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_database_made_before_the_reply_store_gets_it_at_migration(Path(td) / "w8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
